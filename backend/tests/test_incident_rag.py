from __future__ import annotations

import asyncio
import sqlite3
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from httpx import ASGITransport

from app.incident_rag import (
    IncidentBackendError,
    IncidentFileError,
    _ollama_embeddings,
    index_incident_file,
    list_incident_files,
    parse_incident_file,
    retrieve_incidents,
)
from app.main import app


SUPABASE_URL = "https://auth.example.test"
TEST_USER_ID = "c132a530-9f17-4ca7-bc44-a94f5adad746"
JWT_SECRET = "test-only-signing-secret-for-ai-ans"


class FakeEmbeddings:
    vocabulary = ("database", "network", "outage", "resolved", "printer")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [float(lowered.count(word)) for word in self.vocabulary]


async def fake_embed_texts(texts: list[str], _backend: str) -> list[list[float]]:
    embedder = FakeEmbeddings()
    return [embedder.embed_query(text) for text in texts]


def headers(user_id: str = TEST_USER_ID) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "email": "owner@example.test",
            "aud": "authenticated",
            "iss": f"{SUPABASE_URL}/auth/v1",
            "exp": int(time.time()) + 300,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def request(method: str, url: str, **kwargs: object) -> httpx.Response:
    request_headers = kwargs.pop("headers", None)
    if request_headers is None:
        request_headers = headers()
    kwargs["headers"] = request_headers

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


@pytest.fixture(autouse=True)
def auth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)


def test_incident_file_parsing_rejects_bad_json_and_unsupported_types() -> None:
    with pytest.raises(IncidentFileError, match="not valid JSON"):
        parse_incident_file("incident.json", b"{bad")
    with pytest.raises(IncidentFileError, match="Use CSV"):
        parse_incident_file("incident.exe", b"not an incident file")


def test_ollama_embeddings_reject_invalid_vectors(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[list[float] | None]]:
            return {"embeddings": [[3.0, 4.0], None]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr("app.incident_rag.httpx.AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(IncidentBackendError, match="invalid embedding vector"):
        asyncio.run(_ollama_embeddings(["incident one", "incident two"]))


def test_ollama_embeddings_use_configured_model_and_normalize(monkeypatch) -> None:
    monkeypatch.setenv("AI_ANS_OLLAMA_EMBEDDING_MODEL", "test-embedding-model")
    observed: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[list[float]]]:
            return {"embeddings": [[3.0, 4.0]]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> Response:
            observed["url"] = url
            observed["payload"] = kwargs.get("json")
            return Response()

    monkeypatch.setattr("app.incident_rag.httpx.AsyncClient", lambda **_kwargs: Client())

    embeddings = asyncio.run(_ollama_embeddings(["incident text"]))

    assert observed["url"] == "http://127.0.0.1:11434/api/embed"
    assert observed["payload"] == {
        "model": "test-embedding-model",
        "input": ["incident text"],
    }
    assert embeddings == [[0.6, 0.8]]


def test_incident_storage_errors_are_reported(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))

    def fail_to_connect(*_args: object, **_kwargs: object):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr("app.incident_rag.sqlite3.connect", fail_to_connect)

    with pytest.raises(IncidentBackendError, match="storage is unavailable"):
        list_incident_files(TEST_USER_ID)


def test_csv_incident_upload_is_searchable_and_isolated_by_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.incident_rag._embed_texts", fake_embed_texts)

    response = request(
        "POST",
        "/api/v1/incidents",
        files={
            "file": (
                "incidents.csv",
                b"id,summary,resolution\nINC-1,Database outage,Restarted primary database",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    uploaded = response.json()
    assert uploaded["filename"] == "incidents.csv"
    assert uploaded["chunks"] == 1

    files = request("GET", "/api/v1/incidents")
    other_user_files = request("GET", "/api/v1/incidents", headers=headers(str(uuid4())))
    matches = asyncio.run(retrieve_incidents(TEST_USER_ID, "database outage"))

    assert files.status_code == 200
    assert files.json()["files"][0]["id"] == uploaded["id"]
    assert other_user_files.status_code == 200
    assert other_user_files.json()["files"] == []
    assert matches[0]["filename"] == "incidents.csv"
    assert "INC-1" in matches[0]["text"]

    removed = request("DELETE", f"/api/v1/incidents/{uploaded['id']}")
    assert removed.status_code == 200
    assert request("GET", "/api/v1/incidents").json()["files"] == []


def test_incident_upload_rejects_unsupported_and_oversized_files() -> None:
    unsupported = request(
        "POST",
        "/api/v1/incidents",
        files={"file": ("dump.exe", b"no", "application/octet-stream")},
    )
    oversized = request(
        "POST",
        "/api/v1/incidents",
        files={"file": ("large.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )

    assert unsupported.status_code == 422
    assert oversized.status_code == 413
