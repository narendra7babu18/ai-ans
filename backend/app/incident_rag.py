from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.credential_store import data_directory


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 1_000_000
MAX_PDF_PAGES = 500
CHUNK_SIZE = 1_000
CHUNK_OVERLAP = 150
SUPPORTED_EXTENSIONS = {".csv", ".json", ".pdf", ".txt"}
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_HUGGINGFACE_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class IncidentFileError(ValueError):
    pass


class IncidentBackendError(RuntimeError):
    pass


def _backend() -> str:
    backend = os.getenv("AI_ANS_RAG_BACKEND", "ollama").strip().casefold()
    if backend not in {"ollama", "chroma-hf"}:
        raise IncidentBackendError("AI_ANS_RAG_BACKEND must be 'ollama' or 'chroma-hf'.")
    return backend


def _index_directory() -> Path:
    return data_directory() / "incidents"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    directory = _index_directory()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(directory / "incidents.sqlite3")
    except sqlite3.Error as error:
        raise IncidentBackendError("Incident index storage is unavailable.") from error
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS incident_files (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                backend TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incident_chunks (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL REFERENCES incident_files(id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incident_chunks_owner_idx
                ON incident_chunks(owner_id, file_id);
            """
        )
        with connection:
            yield connection
    except sqlite3.Error as error:
        raise IncidentBackendError("Incident index storage is unavailable.") from error
    finally:
        connection.close()


def _safe_filename(filename: str) -> str:
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def parse_incident_file(filename: str, content: bytes) -> str:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise IncidentFileError("Use CSV, JSON, PDF, or plain-text incident files.")
    if not content:
        raise IncidentFileError("The uploaded file is empty.")

    if suffix == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            pages = PdfReader(io.BytesIO(content)).pages
            if len(pages) > MAX_PDF_PAGES:
                raise IncidentFileError("PDFs must contain 500 pages or fewer.")
            extracted_pages = []
            total_characters = 0
            for page in pages:
                page_text = page.extract_text() or ""
                total_characters += len(page_text)
                if total_characters > MAX_EXTRACTED_CHARACTERS:
                    raise IncidentFileError("The extracted text exceeds the 1 MB indexing limit.")
                extracted_pages.append(page_text)
            text = "\n".join(extracted_pages)
        except (PdfReadError, OSError) as error:
            raise IncidentFileError("This PDF could not be read.") from error
    else:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise IncidentFileError("Files must use UTF-8 text encoding.") from error

        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(decoded))
            if not reader.fieldnames:
                raise IncidentFileError("The CSV must include a header row.")
            records = []
            for index, row in enumerate(reader, start=1):
                fields = [f"{key}: {value or ''}" for key, value in row.items() if key]
                if fields:
                    records.append(f"Incident record {index}\n" + "\n".join(fields))
            text = "\n\n".join(records)
        elif suffix == ".json":
            try:
                text = json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
            except json.JSONDecodeError as error:
                raise IncidentFileError("The JSON file is not valid JSON.") from error
        else:
            text = decoded

    text = text.strip()
    if not text:
        raise IncidentFileError("No readable incident text was found in the file.")
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise IncidentFileError("The extracted text exceeds the 1 MB indexing limit.")
    return text


def _split_text(text: str, backend: str) -> list[str]:
    if backend == "chroma-hf":
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError as error:
            raise IncidentBackendError(
                "The optional Chroma/Hugging Face dependencies could not be loaded."
            ) from error
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            add_start_index=True,
        )
        return splitter.split_text(text)

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + CHUNK_SIZE // 2, end)
            if newline > start:
                end = newline
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def _normalize(vector: list[object]) -> list[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not norm or not all(math.isfinite(value) for value in values):
        raise IncidentBackendError("The embedding service returned an invalid vector.")
    return [value / norm for value in values]


def _ollama_embedding_url() -> str:
    url = os.getenv(
        "AI_ANS_OLLAMA_EMBEDDING_URL",
        "http://127.0.0.1:11434/api/embed",
    ).strip()
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise IncidentBackendError(
            "Use the local Ollama embedding endpoint or a protected HTTPS endpoint."
        )
    return url


async def _ollama_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = os.getenv("AI_ANS_OLLAMA_EMBEDDING_MODEL", DEFAULT_OLLAMA_EMBEDDING_MODEL).strip()
    if not model:
        raise IncidentBackendError("Configure AI_ANS_OLLAMA_EMBEDDING_MODEL.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=8)) as client:
            response = await client.post(
                _ollama_embedding_url(),
                json={"model": model, "input": texts},
            )
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise IncidentBackendError(
            "Could not reach Ollama embeddings. Start Ollama and pull the configured embedding model."
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise IncidentBackendError("Ollama returned an invalid embedding response.") from error
    embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise IncidentBackendError("Ollama returned an invalid embedding response.")
    if not all(isinstance(vector, list) for vector in embeddings):
        raise IncidentBackendError("Ollama returned an invalid embedding vector.")
    try:
        return [_normalize(vector) for vector in embeddings]
    except (TypeError, ValueError) as error:
        raise IncidentBackendError("Ollama returned an invalid embedding vector.") from error


@lru_cache(maxsize=1)
def _huggingface_embeddings():
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as error:
        raise IncidentBackendError(
            "The optional Chroma/Hugging Face dependencies could not be loaded."
        ) from error
    return HuggingFaceEmbeddings(
        model_name=os.getenv(
            "AI_ANS_EMBEDDING_MODEL",
            DEFAULT_HUGGINGFACE_EMBEDDING_MODEL,
        ),
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


async def _embed_texts(texts: list[str], backend: str) -> list[list[float]]:
    if backend == "ollama":
        return await _ollama_embeddings(texts)
    embeddings = await asyncio.to_thread(_huggingface_embeddings)
    try:
        vectors = await asyncio.to_thread(embeddings.embed_documents, texts)
        return [_normalize(vector) for vector in vectors]
    except (OSError, RuntimeError, ValueError) as error:
        raise IncidentBackendError("Hugging Face embeddings could not process this incident file.") from error


def _chroma_collection(owner_id: str):
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as error:
        raise IncidentBackendError(
            "The optional Chroma/Hugging Face dependencies could not be loaded."
        ) from error

    owner_hash = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32]
    directory = _index_directory() / "chroma"
    directory.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(directory),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name=f"incidents_{owner_hash}",
        metadata={"hnsw:space": "cosine"},
    )


async def index_incident_file(
    owner_id: str,
    filename: str,
    content: bytes,
) -> dict[str, str | int]:
    text = parse_incident_file(filename, content)
    backend = _backend()
    chunks = _split_text(text, backend)
    if not chunks:
        raise IncidentFileError("No searchable text was found in the file.")
    vectors = await _embed_texts(chunks, backend)
    if len(vectors) != len(chunks):
        raise IncidentBackendError("The embedding service returned an incomplete response.")

    file_id = uuid4().hex
    ids = [uuid4().hex for _ in chunks]
    uploaded_at = datetime.now(timezone.utc).isoformat()
    safe_name = _safe_filename(filename)
    try:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO incident_files (id, owner_id, filename, uploaded_at, backend)
                VALUES (?, ?, ?, ?, ?)
                """,
                (file_id, owner_id, safe_name, uploaded_at, backend),
            )
            connection.executemany(
                """
                INSERT INTO incident_chunks (id, file_id, owner_id, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (chunk_id, file_id, owner_id, index, chunk, json.dumps(vector))
                    for index, (chunk_id, chunk, vector) in enumerate(zip(ids, chunks, vectors))
                ],
            )
    except sqlite3.Error as error:
        raise IncidentBackendError("The incident index could not be saved to disk.") from error

    if backend == "chroma-hf":
        try:
            await asyncio.to_thread(
                _chroma_collection(owner_id).add,
                ids=ids,
                documents=chunks,
                embeddings=vectors,
                metadatas=[
                    {
                        "owner_id": owner_id,
                        "file_id": file_id,
                        "filename": safe_name,
                        "chunk_index": index,
                        "uploaded_at": uploaded_at,
                    }
                    for index in range(len(chunks))
                ],
            )
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            with _connect() as connection:
                connection.execute("DELETE FROM incident_files WHERE id = ? AND owner_id = ?", (file_id, owner_id))
            raise IncidentBackendError(
                "The optional Chroma index could not be loaded or updated."
            ) from error

    return {"id": file_id, "filename": safe_name, "chunks": len(chunks)}


def list_incident_files(owner_id: str) -> list[dict[str, str | int]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT incident_files.id, incident_files.filename, incident_files.uploaded_at,
                   incident_files.backend, COUNT(incident_chunks.id) AS chunks
            FROM incident_files
            LEFT JOIN incident_chunks ON incident_chunks.file_id = incident_files.id
            WHERE incident_files.owner_id = ?
            GROUP BY incident_files.id
            ORDER BY incident_files.uploaded_at DESC
            """,
            (owner_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "filename": row["filename"],
            "uploaded_at": row["uploaded_at"],
            "backend": row["backend"],
            "chunks": row["chunks"],
        }
        for row in rows
    ]


def delete_incident_file(owner_id: str, file_id: str) -> bool:
    with _connect() as connection:
        file_record = connection.execute(
            "SELECT backend FROM incident_files WHERE id = ? AND owner_id = ?",
            (file_id, owner_id),
        ).fetchone()
        if file_record is None:
            return False
        chunk_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM incident_chunks WHERE file_id = ? AND owner_id = ?",
                (file_id, owner_id),
            )
        ]
    if file_record["backend"] == "chroma-hf":
        _chroma_collection(owner_id).delete(ids=chunk_ids)
    with _connect() as connection:
        connection.execute(
            "DELETE FROM incident_files WHERE id = ? AND owner_id = ?",
            (file_id, owner_id),
        )
    return True


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


async def retrieve_incidents(
    owner_id: str,
    query: str,
    limit: int = 5,
) -> list[dict[str, str | int]]:
    backend = _backend()
    query_vectors = await _embed_texts([query], backend)
    if len(query_vectors) != 1:
        raise IncidentBackendError("The embedding service returned an incomplete response.")
    query_vector = query_vectors[0]
    if backend == "chroma-hf":
        collection = _chroma_collection(owner_id)
        if not collection.count():
            return []
        matches = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_vector],
            n_results=limit,
            where={"owner_id": owner_id},
            include=["metadatas", "documents"],
        )
        metadatas = matches["metadatas"][0] if matches["metadatas"] else []
        documents = matches["documents"][0] if matches["documents"] else []
        return [
            {
                "filename": str(metadata["filename"]),
                "chunk_index": int(metadata["chunk_index"]),
                "text": text,
            }
            for metadata, text in zip(metadatas, documents)
            if metadata.get("owner_id") == owner_id
        ]

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT incident_files.filename, incident_files.backend,
                   incident_chunks.chunk_index, incident_chunks.content, incident_chunks.embedding
            FROM incident_chunks
            JOIN incident_files ON incident_files.id = incident_chunks.file_id
            WHERE incident_chunks.owner_id = ? AND incident_files.owner_id = ?
                  AND incident_files.backend = ?
            """,
            (owner_id, owner_id, backend),
        ).fetchall()
    scored = [
        (
            _cosine_similarity(query_vector, json.loads(row["embedding"])),
            row,
        )
        for row in rows
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "filename": row["filename"],
            "chunk_index": row["chunk_index"],
            "text": row["content"],
        }
        for _score, row in scored[:limit]
    ]
