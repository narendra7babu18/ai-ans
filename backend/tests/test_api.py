import asyncio
import json
import os
import sqlite3
import time
from uuid import uuid4

import httpx
import jwt
from cryptography.fernet import Fernet
from httpx import ASGITransport

from app.credential_store import CredentialStore
from app.main import app

TEST_JWT_SECRET = "test-only-signing-secret-for-ai-ans"
TEST_SUPABASE_URL = "https://auth.example.test"
TEST_USER_ID = "c132a530-9f17-4ca7-bc44-a94f5adad746"
os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET


def auth_headers(user_id: str = TEST_USER_ID, email: str = "owner@example.test") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": "authenticated",
            "iss": f"{TEST_SUPABASE_URL}/auth/v1",
            "exp": int(time.time()) + 300,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def request(method: str, url: str, **kwargs: object) -> httpx.Response:
    headers = kwargs.pop("headers", None)
    if headers is None:
        headers = auth_headers()
    kwargs["headers"] = headers

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


def test_health_reports_service_status() -> None:
    response = request("GET", "/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_backend_root_identifies_api_in_local_development() -> None:
    response = request("GET", "/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "AI ANS API",
        "docs": "/docs",
        "health": "/health",
    }


def test_model_list_always_has_demo_mode() -> None:
    response = request("GET", "/api/v1/models")

    assert response.status_code == 200
    assert response.json()["models"][0]["id"] == "demo"


def test_model_list_includes_gemini_when_key_is_configured(monkeypatch) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setattr("app.main.GEMINI_MODEL", "test-gemini-model")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")

    response = request("GET", "/api/v1/models")

    assert response.status_code == 200
    gemini = response.json()["models"][1]
    assert gemini["id"] == "gemini"
    assert gemini["name"] == "test-gemini-model"
    assert gemini["provider"] == "Google Gemini"
    assert gemini["configured"] is True


def test_configured_ollama_is_default_unless_user_selected_another_model(
    monkeypatch, tmp_path
) -> None:
    store, config = save_provider(
        monkeypatch,
        tmp_path,
        provider_id="ollama",
        display_name="Local Ollama",
        protocol="openai",
        base_url="http://127.0.0.1:11434/v1",
        model_id="llama3.1:8b",
        api_key="",
    )
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")

    response = request("GET", "/api/v1/models")
    models = response.json()["models"]
    ollama_model = next(model for model in models if model.get("provider_id") == config.id)
    assert ollama_model["is_default"] is True

    store.set_default_model(TEST_USER_ID, "gemini")
    response = request("GET", "/api/v1/models")
    models = response.json()["models"]
    assert next(model for model in models if model["id"] == "gemini")["is_default"] is True
    assert not any(
        model.get("provider_id") == config.id and model["is_default"] for model in models
    )


def test_loopback_guest_can_use_server_gemini_without_supabase(monkeypatch) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setattr("app.main.GEMINI_MODEL", "test-model")
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.delenv("AI_ANS_SERVER_PROVIDER_EMAIL", raising=False)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    models = request("GET", "/api/v1/models", headers={})
    providers = request("GET", "/api/v1/providers", headers={})

    assert models.status_code == 200
    assert any(model["id"] == "gemini" for model in models.json()["models"])
    assert providers.status_code == 200
    assert providers.json()["credential_storage_available"] is False
    server_gemini = providers.json()["providers"][0]
    assert server_gemini["id"] == "gemini-env"
    assert server_gemini["managed_by_server"] is True
    assert server_gemini["key_saved"] is True

    transport = httpx.MockTransport(
        lambda provider_request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"candidates":[{"content":{"parts":[{"text":"Local Gemini reply"}]}}]}\n\n',
        )
    )
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        lambda **kwargs: original_async_client(
            **({"transport": transport} if "transport" not in kwargs else {}),
            **kwargs,
        ),
    )

    response = request(
        "POST",
        "/api/v1/chat/completions",
        headers={},
        json={"model": "gemini", "provider_id": "gemini", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert "Local Gemini reply" in response.text


def test_guest_cannot_access_local_gemini_on_render(monkeypatch) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setenv("RENDER_SERVICE_ID", "render-service")

    response = request("GET", "/api/v1/models", headers={})

    assert response.status_code == 401


def test_demo_chat_streams_transparently_labeled_response() -> None:
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "demo", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    tokens = [
        json.loads(line[6:])["text"]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    full_response = "".join(tokens)
    assert "local preview does not call an AI provider" in full_response
    assert "Your message was:" in full_response


def test_chat_rejects_assistant_as_last_message() -> None:
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "demo", "messages": [{"role": "assistant", "content": "Hello"}]},
    )

    assert response.status_code == 422


def test_gemini_request_fails_explicitly_when_key_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "")
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "gemini", "messages": [{"role": "user", "content": "Hello"}]},
    )

    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert events[0]["type"] == "error"
    assert "GEMINI_API_KEY" in events[0]["text"]


def test_gemini_stream_uses_server_key_and_returns_provider_tokens(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setattr("app.main.GEMINI_MODEL", "test-model")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")
    observed: dict[str, str] = {}

    def provider_response(provider_request: httpx.Request) -> httpx.Response:
        observed["authorization"] = provider_request.headers["x-goog-api-key"]
        observed["url"] = str(provider_request.url)
        observed["body"] = provider_request.content.decode()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"candidates":[{"content":{"parts":[{"text":"A real "}]}}]}\n\n'
                'data: {"candidates":[{"content":{"parts":[{"text":"answer."}]}}]}\n\n'
            ).encode(),
        )

    transport = httpx.MockTransport(provider_response)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        lambda **kwargs: original_async_client(
            **({"transport": transport} if "transport" not in kwargs else {}),
            **kwargs,
        ),
    )

    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "gemini", "messages": [{"role": "user", "content": "Answer me"}]},
    )

    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert observed["authorization"] == "test-server-secret"
    assert observed["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "test-model:streamGenerateContent?alt=sse"
    )
    assert json.loads(observed["body"])["contents"] == [
        {"role": "user", "parts": [{"text": "Answer me"}]}
    ]
    assert [item["text"] for item in events if item["type"] == "token"] == [
        "A real ",
        "answer.",
    ]
    assert events[-1]["type"] == "done"


def test_gemini_authentication_errors_explain_how_to_fix_the_key(monkeypatch) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"error": {"message": "secret provider detail"}})
    )
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        lambda **kwargs: original_async_client(
            **({"transport": transport} if "transport" not in kwargs else {}),
            **kwargs,
        ),
    )

    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "gemini", "messages": [{"role": "user", "content": "Hello"}]},
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert events[0]["type"] == "error"
    assert "API key" in events[0]["text"]
    assert "secret provider detail" not in events[0]["text"]


def test_gemini_403_explains_project_billing_without_enabling_it(monkeypatch) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "test-server-secret")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(403, json={"error": {"message": "project unavailable"}})
    )
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        lambda **kwargs: original_async_client(
            **({"transport": transport} if "transport" not in kwargs else {}),
            **kwargs,
        ),
    )

    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={"model": "gemini", "messages": [{"role": "user", "content": "Hello"}]},
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert events[0]["type"] == "error"
    assert "free tier is unavailable" in events[0]["text"]
    assert "https://aistudio.google.com/billing" in events[0]["text"]
    assert "Do not enable paid billing" in events[0]["text"]


def test_unknown_api_route_returns_json_404_not_the_frontend() -> None:
    response = request("GET", "/api/v1/unknown")

    assert response.status_code == 404
    assert response.json()["detail"] == "API endpoint not found."


def save_provider(monkeypatch, tmp_path, **overrides: str) -> tuple[CredentialStore, object]:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))
    store = CredentialStore()
    payload: dict[str, object] = {
        "provider_id": "openai",
        "display_name": "Test OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_id": "test-model",
        "api_key": "top-secret-test-key",
        "organization": "",
        "enabled": True,
    }
    payload.update(overrides)
    return store, store.save(payload, TEST_USER_ID)


def mock_provider_client(monkeypatch, transport: httpx.MockTransport) -> None:
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        lambda **kwargs: original_async_client(
            **({"transport": transport} if "transport" not in kwargs else {}),
            **kwargs,
        ),
    )


def test_provider_catalog_includes_native_and_compatible_presets() -> None:
    response = request("GET", "/api/v1/providers/catalog")

    assert response.status_code == 200
    presets = {item["id"]: item for item in response.json()["providers"]}
    assert presets["ollama"]["protocol"] == "openai"
    assert presets["anthropic"]["protocol"] == "anthropic"
    assert presets["gemini"]["protocol"] == "gemini"
    assert "deepseek" in presets and "perplexity" in presets


def test_private_endpoints_require_a_valid_supabase_user_token(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_SERVICE_ID", "test-render-service")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    missing = request("GET", "/api/v1/models", headers={})

    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    unauthorized = request(
        "GET",
        "/api/v1/models",
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    accepted = request("GET", "/api/v1/models")

    assert missing.status_code == 401
    assert unauthorized.status_code == 401
    assert accepted.status_code == 200


def test_supabase_token_with_wrong_audience_or_issuer_is_rejected() -> None:
    headers = auth_headers()
    token = headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        token,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
        audience="authenticated",
        issuer=f"{TEST_SUPABASE_URL}/auth/v1",
    )
    claims["aud"] = "other-project"
    wrong_audience = jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")
    response = request(
        "GET",
        "/api/v1/models",
        headers={"Authorization": f"Bearer {wrong_audience}"},
    )

    assert response.status_code == 401


def test_provider_settings_are_isolated_between_signed_in_users(monkeypatch, tmp_path) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))
    _store, saved = save_provider(monkeypatch, tmp_path)

    owner_models = request("GET", "/api/v1/models")
    other_user_id = str(uuid4())
    other_headers = auth_headers(other_user_id, "another@example.test")
    other_models = request("GET", "/api/v1/models", headers=other_headers)
    other_provider = request(
        "GET",
        f"/api/v1/providers/{saved.id}/test",
        headers=other_headers,
    )

    assert owner_models.status_code == 200
    owner_model = next(
        model
        for model in owner_models.json()["models"]
        if model.get("provider_id") == saved.id
    )
    assert owner_model["provider_type"] == "openai"
    assert other_models.status_code == 200
    assert not any(model.get("provider_id") == saved.id for model in other_models.json()["models"])
    assert other_provider.status_code == 404


def test_credential_store_rejects_cross_account_provider_id_replacement(tmp_path) -> None:
    store = CredentialStore(tmp_path, Fernet.generate_key().decode())
    config = store.save(
        {
            "provider_id": "openai",
            "display_name": "Owner's provider",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_id": "model-a",
            "api_key": "owner-key",
            "organization": "",
            "enabled": True,
        },
        TEST_USER_ID,
    )

    try:
        store.save(
            {
                "id": config.id,
                "provider_id": "openai",
                "display_name": "Other user's overwrite",
                "protocol": "openai",
                "base_url": "https://api.openai.com/v1",
                "model_id": "model-b",
                "api_key": "attacker-key",
                "organization": "",
                "enabled": True,
            },
            str(uuid4()),
        )
    except ValueError as error:
        assert "another account" in str(error)
    else:
        raise AssertionError("Another account must not overwrite an existing provider.")

    saved = store.get(config.id, TEST_USER_ID)
    assert saved is not None
    assert saved[0].display_name == "Owner's provider"
    assert saved[0].api_key == "owner-key"


def test_credential_store_migrates_legacy_preferences_without_exposing_them(tmp_path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                encrypted_config BLOB NOT NULL,
                status TEXT NOT NULL DEFAULT 'not_tested',
                status_message TEXT NOT NULL DEFAULT '',
                checked_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO preferences (key, value) VALUES ('default_model', 'configured:legacy')"
        )

    store = CredentialStore(tmp_path, Fernet.generate_key().decode())

    assert store.get_default_model("legacy") == "configured:legacy"
    assert store.get_default_model(TEST_USER_ID) is None


def test_credential_store_encrypts_replaces_and_deletes_api_key(tmp_path) -> None:
    encryption_key = Fernet.generate_key().decode()
    store = CredentialStore(tmp_path, encryption_key)
    config = store.save(
        {
            "provider_id": "openai",
            "display_name": "Private provider",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_id": "model-a",
            "api_key": "never-plaintext-in-db",
            "organization": "",
            "enabled": True,
        },
        TEST_USER_ID,
    )

    database_bytes = store.database_path.read_bytes()
    assert b"never-plaintext-in-db" not in database_bytes
    replaced = store.save(
        {
            "id": config.id,
            "provider_id": "openai",
            "display_name": "Private provider",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_id": "model-b",
            "api_key": "",
            "organization": "",
            "enabled": True,
        },
        TEST_USER_ID,
    )
    saved = store.get(config.id, TEST_USER_ID)
    assert replaced.model_id == "model-b"
    assert saved is not None and saved[0].api_key == "never-plaintext-in-db"
    assert store.get(config.id, str(uuid4())) is None
    assert store.delete(config.id, TEST_USER_ID)
    assert store.get(config.id, TEST_USER_ID) is None


def test_credential_store_rejects_bad_encryption_key(tmp_path) -> None:
    try:
        CredentialStore(tmp_path, "not-a-fernet-key")
    except RuntimeError as error:
        assert "valid Fernet key" in str(error)
    else:
        raise AssertionError("Invalid encryption keys must not be accepted.")


def test_openai_compatible_chat_stream_uses_saved_server_key(monkeypatch, tmp_path) -> None:
    store, config = save_provider(monkeypatch, tmp_path)
    observed: dict[str, str] = {}

    def provider_response(provider_request: httpx.Request) -> httpx.Response:
        observed["authorization"] = provider_request.headers.get("authorization", "")
        observed["url"] = str(provider_request.url)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Adapter "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"works."}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
        )

    mock_provider_client(monkeypatch, httpx.MockTransport(provider_response))
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": f"configured:{config.id}",
            "provider_id": config.id,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert observed["authorization"] == "Bearer top-secret-test-key"
    assert observed["url"] == "https://api.openai.com/v1/chat/completions"
    assert [item["text"] for item in events if item["type"] == "token"] == [
        "Adapter ",
        "works.",
    ]
    assert events[-1]["type"] == "done"
    assert "top-secret-test-key" not in response.text


def test_chat_stream_forwards_shared_screen_as_vision_image(monkeypatch, tmp_path) -> None:
    _store, config = save_provider(
        monkeypatch,
        tmp_path,
        provider_id="ollama",
        display_name="Local vision model",
        protocol="openai",
        base_url="http://127.0.0.1:11434/v1",
        model_id="llava",
        api_key="local-proxy-key",
    )
    observed: dict[str, object] = {}

    def provider_response(provider_request: httpx.Request) -> httpx.Response:
        observed["authorization"] = provider_request.headers.get("authorization", "")
        observed["payload"] = json.loads(provider_request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"I can see the screen."}}]}\n\ndata: [DONE]\n\n',
        )

    mock_provider_client(monkeypatch, httpx.MockTransport(provider_response))
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": config.model_id,
            "provider_id": config.id,
            "image_data": "data:image/jpeg;base64,aGVsbG8=",
            "messages": [{"role": "user", "content": "What is on my screen?"}],
        },
    )
    content = observed["payload"]["messages"][-1]["content"]

    assert response.status_code == 200
    assert observed["authorization"] == "Bearer local-proxy-key"
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,aGVsbG8="},
    }


def test_chat_rejects_malformed_screen_image() -> None:
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": "demo",
            "image_data": "data:image/svg+xml;base64,PHN2Zz4=",
            "messages": [{"role": "user", "content": "Read this"}],
        },
    )

    assert response.status_code == 422


def test_incident_chat_adds_retrieved_context_as_untrusted_system_message(
    monkeypatch, tmp_path
) -> None:
    _store, config = save_provider(monkeypatch, tmp_path)
    async def retrieve(_owner: str, _query: str) -> list[dict[str, str | int]]:
        return [
            {
                "filename": "incidents.csv",
                "chunk_index": 0,
                "text": "INC-4: database outage; resolved by restarting the primary.",
            }
        ]

    monkeypatch.setattr("app.main.retrieve_incidents", retrieve)
    observed: dict[str, object] = {}

    def provider_response(provider_request: httpx.Request) -> httpx.Response:
        observed["messages"] = json.loads(provider_request.content)["messages"]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"The incident was resolved."}}]}\n\ndata: [DONE]\n\n',
        )

    mock_provider_client(monkeypatch, httpx.MockTransport(provider_response))
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": config.model_id,
            "provider_id": config.id,
            "incident_rag": True,
            "messages": [{"role": "user", "content": "How was the outage fixed?"}],
        },
    )
    system_message = observed["messages"][0]

    assert response.status_code == 200
    assert system_message["role"] == "system"
    assert "untrusted evidence" in system_message["content"]
    assert "[Source: incidents.csv, chunk 1]" in system_message["content"]
    assert "INC-4" in system_message["content"]


def test_anthropic_native_stream_uses_native_auth_and_event_format(monkeypatch, tmp_path) -> None:
    _store, config = save_provider(
        monkeypatch,
        tmp_path,
        provider_id="anthropic",
        display_name="Test Claude",
        protocol="anthropic",
        base_url="https://api.anthropic.com/v1",
        model_id="claude-test",
    )
    observed: dict[str, str] = {}

    def provider_response(provider_request: httpx.Request) -> httpx.Response:
        observed["key"] = provider_request.headers.get("x-api-key", "")
        observed["version"] = provider_request.headers.get("anthropic-version", "")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","delta":{"text":"Native "}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","delta":{"text":"stream."}}\n\n'
            ).encode(),
        )

    mock_provider_client(monkeypatch, httpx.MockTransport(provider_response))
    response = request(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": config.model_id,
            "provider_id": config.id,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert observed == {
        "key": "top-secret-test-key",
        "version": "2023-06-01",
    }
    assert [item["text"] for item in events if item["type"] == "token"] == [
        "Native ",
        "stream.",
    ]


def test_provider_list_and_test_endpoints_never_return_api_key(monkeypatch, tmp_path) -> None:
    store, config = save_provider(monkeypatch, tmp_path)
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"data": [{"id": config.model_id}]})
    )
    mock_provider_client(monkeypatch, transport)

    listed = request("GET", "/api/v1/providers")
    tested = request("POST", f"/api/v1/providers/{config.id}/test")

    assert listed.status_code == 200
    assert listed.json()["providers"][0]["key_saved"] is True
    assert "top-secret-test-key" not in listed.text
    assert tested.status_code == 200
    assert tested.json()["connected"] is True
    assert store.get(config.id, TEST_USER_ID)[1] == "connected"


def test_existing_gemini_environment_key_is_masked_and_testable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.main.GEMINI_API_KEY", "existing-gemini-secret")
    monkeypatch.setattr("app.main.GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("AI_ANS_SERVER_PROVIDER_EMAIL", "owner@example.test")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-test",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )
    )
    mock_provider_client(monkeypatch, transport)
    listed = request("GET", "/api/v1/providers")
    tested = request("POST", "/api/v1/providers/gemini-env/test")

    assert listed.status_code == 200
    environment_provider = listed.json()["providers"][0]
    assert environment_provider["managed_by_server"] is True
    assert environment_provider["key_saved"] is True
    assert "existing-gemini-secret" not in listed.text
    assert tested.status_code == 200
    assert tested.json()["connected"] is True


def test_provider_connection_errors_are_normalized_and_do_not_leak_details(
    monkeypatch,
    tmp_path,
) -> None:
    _store, config = save_provider(monkeypatch, tmp_path)
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"error": {"message": "secret upstream detail"}})
    )
    mock_provider_client(monkeypatch, transport)

    response = request(
        "POST",
        f"/api/v1/providers/{config.id}/test",
    )

    assert response.status_code == 401
    assert "API key" in response.json()["detail"]
    assert "secret upstream detail" not in response.text


def test_provider_rejects_insecure_remote_http_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AI_ANS_DATA_DIR", str(tmp_path))
    response = request(
        "POST",
        "/api/v1/providers",
        json={
            "provider_id": "custom",
            "display_name": "Unsafe custom endpoint",
            "protocol": "openai",
            "base_url": "http://example.com/v1",
            "model_id": "model",
            "api_key": "test-key",
        },
    )

    assert response.status_code == 422
