# AI ANS

**One Question. Your choice of AI.**

AI ANS keeps its existing React + TypeScript chat and FastAPI backend. Users sign in with Google through Supabase Auth before the application or its API is available. Provider keys are encrypted at rest and isolated to the signed-in Supabase user. Gemini, native Anthropic messages, and OpenAI-compatible services such as Ollama are supported.

## What works

- Existing responsive chat, browser-local conversation history, Markdown rendering, and streamed replies.
- Gemini using the existing server-side `GEMINI_API_KEY`.
- Native Anthropic message streaming.
- OpenAI-compatible chat/model-list APIs, including Ollama's `/v1` API.
- Provider presets for OpenAI, DeepSeek, Mistral, Groq, OpenRouter, Together AI, Fireworks, Hugging Face Inference, Cohere, xAI, Perplexity, and Ollama. Enter the exact model ID supported by the account/endpoint; presets do not imply that an account, model, quota, or free access is available.
- Settings > AI Providers: save encrypted provider credentials, replace a key, disable/delete entries, discover models, test the selected model, and choose a default.
- The chat model picker lists this account's configured models and every supported provider preset; choosing an unconfigured provider opens its setup form instead of sending a request that cannot work.
- Guests can skip sign-in for local development. Localhost requests may use the server-side Gemini key and provider vault, but guest access is limited to loopback clients and disabled on Render. Public/deployed workspaces require Google sign-in.
- Voice input uses the browser's speech-recognition support and microphone permission; assistant replies can be read aloud by browser speech synthesis. Text/code attachments are read locally and limited to a small excerpt before being included in a chat request.
- My library lists pinned chats; Research opens a separately confirmed Google Search for the current draft or latest question.
- Google OAuth sign-in before chat or provider management. Backend APIs validate Supabase access-token signatures; users see only their own saved provider credentials and model choices.
- Google access/refresh tokens are held in browser session storage for the OAuth session. Provider API keys are never stored in browser storage, URLs, frontend source, or API responses.
- Incident RAG accepts CSV, JSON, PDF, and text incident files and searches account-isolated semantic indexes. The default uses local Ollama embeddings and SQLite. An optional ChromaDB + LangChain + Hugging Face backend is available when its native dependencies are permitted.
- Screen reading is opt-in: the browser’s native screen-share picker grants access, and a resized snapshot is sent to the selected vision-capable provider only with a message. AI ANS page actions are allowlisted and require explicit confirmation; there is no control of the desktop or other apps.
- The explicitly labelled demo mode still works without any provider.

Multi-model comparison, automatic routing, pricing/usage accounting, and production-grade rate limiting are not implemented. Google sign-up is open to any Google account unless restricted in Supabase; configure quotas and monitor costs before inviting users.

## Run locally on Windows

From the project directory, create the app environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
Set-Location frontend
npm.cmd ci
```

For the optional ChromaDB + Hugging Face RAG backend only, use a compatible Python runtime and install it with `python -m pip install -r backend\requirements-rag-optional.txt`. ChromaDB/ONNX Runtime currently lack a Python 3.15 Windows wheel, and local Windows Application Control may also need to permit their native extensions. The default Ollama + SQLite backend does not need those packages.

In the first terminal, start FastAPI:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
Set-Location frontend
npm.cmd run dev
```

Open <http://localhost:5173> for the frontend. Keep both terminals running. In local development, the backend root at <http://localhost:8000> identifies the API; open <http://localhost:8000/docs> for its interactive API documentation.

To serve the production frontend from FastAPI instead, build it first with `npm.cmd run build` and set `AI_ANS_SERVE_FRONTEND=true` in the backend environment before starting the server. With this setting off (the default), a leftover frontend build will not make the backend root display the same UI as the Vite development server.

For incident search, keep Ollama running and pull the embedding model with `ollama pull nomic-embed-text`. Incident files and vectors are stored under the backend data directory; use storage approved for your incident data. To use screen reading, choose a vision-capable model (for example, an Ollama vision model), select **Share desktop**, and choose the entire screen in the browser picker. Only send a message when you want its current screenshot analyzed.

## Provider dashboard setup

The existing Gemini key remains in `%LOCALAPPDATA%\AI ANS\settings.env`; this project does not move, print, or overwrite it. Add the following **new** settings to that file without deleting existing lines:

```dotenv
CREDENTIAL_ENCRYPTION_KEY=your-generated-fernet-key
SUPABASE_URL=https://your-project.supabase.co
```

Generate values with:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the Fernet key private and back it up securely. If it is lost, saved provider credentials cannot be recovered. The Supabase project URL is public configuration; it is not a service-role key. Provider keys entered in **AI Providers** are encrypted before being written to a SQLite file under `%LOCALAPPDATA%\AI ANS`.

## Enable Google/Gmail sign-in

1. Create a Supabase project and copy its **Project URL** and **publishable/anon key** (never use the service-role key in the frontend).
2. In Google Cloud Console, create an OAuth client ID for a Web application. Add the Supabase callback URL shown in Supabase Auth → Providers → Google to the Google client's **Authorized redirect URIs**.
3. In Supabase Auth → Providers → Google, enable the provider and enter the Google OAuth client ID and client secret.
4. In Supabase Auth → URL Configuration, set the Site URL to your app URL. Add local callback URLs (`http://localhost:5173` and `http://localhost:8000`) and the deployed HTTPS origin to the redirect allow list.
5. Copy `frontend\.env.example` to `frontend\.env.local`, then set `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. Add `SUPABASE_URL` to the backend settings above. Restart FastAPI/Vite after changing environment files.
6. Sign in with **Continue with Google**. The app does not collect Google passwords. Supabase verifies Google sign-in; FastAPI independently verifies the signed access token before accepting API requests.

The app uses PKCE and browser session storage for short-lived Supabase session tokens; these are authentication tokens, not provider API keys. Provider keys remain backend-only and are encrypted at rest. The SQLite vault filters every provider read/write by the verified Supabase user ID. Legacy single-owner vault entries are not automatically assigned to a new account.

The existing server-configured Gemini key is restricted to `AI_ANS_SERVER_PROVIDER_EMAIL`: set that to the exact verified Google account email that owns the key. It is not made available to every new Google user. Other users can add their own Gemini keys in provider settings. The existing local Gemini key remains untouched.

To keep using the existing local Gemini key after login, add `AI_ANS_SERVER_PROVIDER_EMAIL=your-google-account@gmail.com` to `%LOCALAPPDATA%\AI ANS\settings.env` after configuring Google sign-in. This deliberately prevents the shared server-side key from being exposed to every account.

## Use Ollama

1. Install Ollama from [ollama.com/download](https://ollama.com/download).
2. Pull a model that fits your computer, for example:

   ```powershell
   ollama pull llama3.1:8b
   ```

3. Keep Ollama running. For local testing, add Ollama in **AI Providers** and use `http://127.0.0.1:11434/v1` with the exact installed model name. Ollama does not require an API key in the standard local setup.
4. Discover models, select the exact model ID, save the provider, and test it. The first configured Ollama model becomes the default unless you have already chosen a different default in AI Providers.
5. Local Ollama does not need an API key. You can enter your own key for hosted providers, or for a protected Ollama endpoint, in the same provider settings form.

### Ollama on a remote server for Render

Render's backend cannot reach `localhost` on your Windows computer. Run Ollama on a separate server and provide AI ANS a reachable **HTTPS** OpenAI-compatible URL (normally ending in `/v1`). Install a TLS reverse proxy and authentication or private network access in front of Ollama. **Do not expose Ollama's unauthenticated port 11434 directly to the public internet.** Enter the protected HTTPS URL in the Ollama provider settings. A tunnel from a personal PC is not a reliable production service.

The Render Blueprint uses a paid `0.5c-512mb` web service and mounts a 1 GB persistent disk at `/var/data`, with `AI_ANS_DATA_DIR` set to that path. This preserves the SQLite provider vault and incident data across deploys. Render charges for paid compute and persistent storage; review the price shown in your Render account before creating the service.

Before the first deploy, configure `SUPABASE_URL`, `VITE_SUPABASE_URL`, and `VITE_SUPABASE_ANON_KEY` in Render and configure the deployed HTTPS origin in Supabase's URL allow list. Google OAuth must also be enabled in Supabase. Without these settings, the deployed sign-in button will be disabled and protected API requests will fail. The Render Blueprint enables `AI_ANS_SERVE_FRONTEND` so the built UI is served from the public root. Set `CREDENTIAL_ENCRYPTION_KEY` as a private Render environment secret. If deploying a shared Gemini key, also set `AI_ANS_SERVER_PROVIDER_EMAIL` to its owner's verified email. Local AppData keys are not deployed. Never put service-role keys, Google OAuth client secrets, Gemini keys, or encryption keys in the frontend or GitHub.

## Commands to verify

Backend tests (mock provider APIs; no paid calls):

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
```

Frontend type-check/build and lint:

```powershell
Set-Location frontend
npm.cmd run build
npm.cmd run lint
```

Gemini can still be blocked by Google's project eligibility, permission, quota, or billing rules. AI ANS does not enable provider billing. A successful connection test verifies model-list access; it does not promise generation quota or free use.

## Relevant files

- `backend\app\main.py` — FastAPI chat, models, provider dashboard, and access control.
- `backend\app\providers.py` — provider presets, endpoint validation, Gemini/Anthropic/OpenAI-compatible adapters, and normalized errors.
- `backend\app\credential_store.py` — Fernet-encrypted SQLite credential vault.
- `backend\tests\test_api.py` — mocked API, security, streaming, registry, and vault tests.
- `frontend\src\ProvidersSettings.tsx` — provider management dashboard.
- `frontend\src\App.tsx` and `frontend\src\App.css` — existing chat integration and matching navy-blue styling.
- `render.yaml` — existing Render Blueprint; configure persistent storage and secrets directly in Render before using provider settings.
