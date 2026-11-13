from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.providers import ProviderConfig


def data_directory() -> Path:
    configured = os.getenv("AI_ANS_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.getenv("RENDER_SERVICE_ID"):
        raise RuntimeError(
            "Set AI_ANS_DATA_DIR to a persistent mounted volume before saving provider credentials on Render."
        )
    if os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "AI ANS"
    return Path.home() / ".local" / "share" / "ai-ans"


class CredentialStore:
    def __init__(self, directory: Path | None = None, encryption_key: str | None = None) -> None:
        key = encryption_key if encryption_key is not None else os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
        if not key:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required to manage saved provider keys.")
        try:
            self._cipher = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key.") from error
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.directory / "providers.sqlite3"
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'legacy',
                    encrypted_config BLOB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_tested',
                    status_message TEXT NOT NULL DEFAULT '',
                    checked_at TEXT
                )
                """
            )
            provider_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(providers)").fetchall()
            }
            if "owner_id" not in provider_columns:
                connection.execute(
                    "ALTER TABLE providers ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            preference_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(preferences)").fetchall()
            }
            if preference_columns and "owner_id" not in preference_columns:
                connection.execute("ALTER TABLE preferences RENAME TO preferences_legacy")
                connection.execute(
                    """
                    CREATE TABLE preferences (
                        owner_id TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (owner_id, key)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO preferences (owner_id, key, value)
                    SELECT 'legacy', key, value FROM preferences_legacy
                    """
                )
                connection.execute("DROP TABLE preferences_legacy")
            elif not preference_columns:
                connection.execute(
                    """
                    CREATE TABLE preferences (
                        owner_id TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (owner_id, key)
                    )
                    """
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _decode(self, row: sqlite3.Row) -> tuple[ProviderConfig, str, str, str | None]:
        try:
            payload = json.loads(self._cipher.decrypt(row["encrypted_config"]).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("A saved provider credential could not be decrypted.") from error
        config = ProviderConfig(**payload)
        return config, row["status"], row["status_message"], row["checked_at"]

    def list(self, owner_id: str) -> list[tuple[ProviderConfig, str, str, str | None]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM providers WHERE owner_id = ? ORDER BY rowid",
                (owner_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, provider_id: str, owner_id: str) -> tuple[ProviderConfig, str, str, str | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ? AND owner_id = ?",
                (provider_id, owner_id),
            ).fetchone()
        return self._decode(row) if row else None

    def save(self, payload: dict[str, Any], owner_id: str) -> ProviderConfig:
        provider_id = payload.get("id") or str(uuid4())
        old = self.get(provider_id, owner_id)
        if provider_id and old is None:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT owner_id FROM providers WHERE id = ?",
                    (provider_id,),
                ).fetchone()
            if existing is not None:
                raise ValueError("This provider configuration belongs to another account.")
        if not payload.get("api_key") and old:
            payload["api_key"] = old[0].api_key
        payload["id"] = provider_id
        config = ProviderConfig(**payload)
        encrypted = self._cipher.encrypt(
            json.dumps(
                {
                    "id": config.id,
                    "provider_id": config.provider_id,
                    "display_name": config.display_name,
                    "protocol": config.protocol,
                    "base_url": config.base_url,
                    "model_id": config.model_id,
                    "api_key": config.api_key,
                    "organization": config.organization,
                    "enabled": config.enabled,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO providers (id, owner_id, encrypted_config, status, status_message, checked_at)
                VALUES (?, ?, ?, 'not_tested', '', NULL)
                ON CONFLICT(id) DO UPDATE SET
                    encrypted_config = excluded.encrypted_config,
                    status = 'not_tested',
                    status_message = '',
                    checked_at = NULL
                """,
                (config.id, owner_id, encrypted),
            )
        return config

    def delete(self, provider_id: str, owner_id: str) -> bool:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM providers WHERE id = ? AND owner_id = ?",
                (provider_id, owner_id),
            ).rowcount
            connection.execute(
                "DELETE FROM preferences WHERE owner_id = ? AND key = 'default_model' AND value = ?",
                (owner_id, f"configured:{provider_id}"),
            )
        return deleted > 0

    def update_status(self, provider_id: str, owner_id: str, status: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE providers SET status = ?, status_message = ?, checked_at = CURRENT_TIMESTAMP
                WHERE id = ? AND owner_id = ?
                """,
                (status, message, provider_id, owner_id),
            )

    def get_default_model(self, owner_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE owner_id = ? AND key = 'default_model'",
                (owner_id,),
            ).fetchone()
        return row["value"] if row else None

    def set_default_model(self, owner_id: str, model_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO preferences (owner_id, key, value) VALUES (?, 'default_model', ?)
                ON CONFLICT(owner_id, key) DO UPDATE SET value = excluded.value
                """,
                (owner_id, model_id),
            )
