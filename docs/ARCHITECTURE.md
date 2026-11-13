# AI ANS architecture and Phase 1 plan

## Scope and status

This document records the architecture and roadmap. The repository includes a Vite/React chat UI, Supabase Google OAuth sign-in, FastAPI endpoints that verify Supabase JWTs, clearly labeled demo mode, server-side Gemini, native Anthropic and OpenAI-compatible adapters, per-user browser-local chats, an encrypted SQLite provider vault, and account-scoped IT incident RAG using local Ollama embeddings and SQLite vector search. An optional ChromaDB + LangChain + Hugging Face backend is available when its native dependencies are permitted. Screen reading uses a one-message desktop screenshot shared through the browser; action suggestions are limited to confirmed actions within the AI ANS page. Any Google account may sign up; configure Supabase policies and operational protections before treating the service as production-ready. Multi-model comparison, automatic routing, distributed rate limits, usage accounting, and hosted deployment remain pending.

## Current provider integration

The backend registry contains provider presets for Gemini, Anthropic, Ollama, and services exposing an OpenAI-compatible chat-completion/model-list interface. Gemini uses Google's native streaming API; Anthropic uses its Messages API; compatible providers use `/models` and `/chat/completions`. Presets supply endpoint defaults only: the owner supplies the exact model ID, valid credentials, and any required reachable endpoint. Availability, model access, pricing, and quotas depend on each provider account and are not implied by the preset.

Users sign in with Google via Supabase Auth using the PKCE flow. The browser stores Supabase auth session tokens in session storage; it never stores provider API keys. FastAPI verifies each access token's signature, issuer, audience, expiry, and UUID subject using Supabase's JWKS (or an explicitly configured legacy HS256 secret). All application APIs except `/health` require a verified user. The provider vault associates each row and default-model preference with the verified Supabase subject, encrypts provider config with Fernet, and applies owner filters to every read/update/delete. Existing legacy vault rows remain under a non-user `legacy` owner and are not exposed to new accounts. The global environment Gemini key is only available to the Google account matching `AI_ANS_SERVER_PROVIDER_EMAIL`.

The SQLite vault and browser-local chat history are per account. Incident files are parsed and chunked on the backend; vectors are created by local Ollama embeddings by default and stored in owner-filtered SQLite records. The optional ChromaDB backend uses a Chroma collection named from a one-way hash of the verified owner ID and Hugging Face sentence-transformer embeddings. Incident content and vectors are not encrypted at rest; use storage approved for the uploaded material. Browser chat data is namespaced by Supabase user UUID so another signed-in account in the same browser cannot read it through the app UI. Google sign-in credentials and provider API credentials are separate: AI ANS never collects Google passwords.

Desktop capture requires an explicit browser screen-share selection. A downscaled JPEG snapshot is transmitted only when the user sends a message while sharing is active. The selected model/provider receives that image, so use a vision-capable model and do not share screens containing information you do not want sent to that provider. The assistant may suggest an allowlisted action in the AI ANS page; the user must confirm each action, and no desktop, other-app, terminal, or destructive-file actions are supported.

Render's default/free filesystem is not a durable vault. The backend refuses provider-vault operations on Render unless `AI_ANS_DATA_DIR` is configured, but that path must also be an actually attached persistent volume or external database. A configured path by itself is not persistence. Ollama must be reachable from the Render backend through an authenticated HTTPS endpoint; `localhost` on Render is not the owner's computer. Do not expose Ollama's unauthenticated port to the public internet.

## Architecture

```text
Browser
  React + TypeScript + Vite
       | HTTPS; Supabase Auth access token
       v
  FastAPI service on Render (future hosted deployment)
       |-- Supabase Google OAuth session / JWT verification
       |-- verified user ID scoped provider credential and model access
       |-- versioned REST API and streamed chat responses
       |-- provider adapters and model registry
       |-- rate limits, validation, audit and usage events
       |
       +--> Supabase PostgreSQL + pgvector
       +--> official AI provider APIs (server-side credentials)
       +--> authorized search API (a later phase)

GitHub repository --connected deployment--> Render
```

### Technology choices

| Area | Choice | Reason |
| --- | --- | --- |
| Web UI | React, TypeScript, Vite | Familiar component model, static build, and broad ecosystem. |
| Styling | Responsive custom CSS, Framer Motion, Lucide | Fine-grained control for the brand design, restrained motion, and consistent icons. The first UI uses CSS rather than adding an unused styling framework. |
| API | FastAPI, Pydantic, HTTPX | Typed request/response validation and asynchronous HTTP/streaming support. |
| Persistence | Per-account SQLite incident vectors; optional ChromaDB collections; encrypted SQLite provider vault | Local semantic search with owner isolation; provider secrets are encrypted separately. |
| Authentication | Supabase Auth Google OAuth, PKCE, and FastAPI JWT verification | Google owns credential entry; backend derives each owner ID from a verified token. |
| Source control / hosting | GitHub / Render | GitHub is the source of truth; Render can build from a connected repo and auto-deploy pushes. |
| Tests | Pytest; Vitest and React Testing Library; Playwright later | Test each layer at the point it is introduced. |

The default Ollama/SQLite path uses the core FastAPI dependencies. The optional ChromaDB/ONNX Runtime path requires a compatible Python runtime and system policy that permits its native wheels; it was not executable on the current Windows machine. Confirm the hosting platform's currently supported Python runtime before production use.

## Planned project structure

```text
AI ans/
├── frontend/
│   ├── src/
│   │   ├── app/                 # Routes and application shell
│   │   ├── components/          # Shared UI components
│   │   ├── features/            # Chat, models, research, settings
│   │   ├── lib/                 # API client and shared utilities
│   │   └── styles/              # Design tokens and global styles
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
├── backend/
│   ├── app/
│   │   ├── api/v1/              # Versioned HTTP routes
│   │   ├── core/                # Settings, security, logging
│   │   ├── db/                  # Database setup and repositories
│   │   ├── models/              # Persistence models
│   │   ├── providers/           # Provider interface and adapters
│   │   ├── schemas/             # Pydantic API schemas
│   │   └── services/            # Chat, usage, search, documents
│   ├── migrations/              # Alembic revisions
│   ├── tests/
│   └── pyproject.toml
├── docs/
│   └── ARCHITECTURE.md
├── .env.example
├── .gitignore
└── README.md
```

The frontend and backend remain separate deployable services in one GitHub repository. Do not add a directory until its phase is ready to implement; this avoids empty scaffolding that can drift from the working code.

## Database schema plan

Use UUID primary keys, UTC timestamps, foreign keys, indexes for common filters, and migrations for all schema changes. Supabase Auth owns credentials and identities; the application must not store password hashes.

| Table | Planned purpose and important relationships |
| --- | --- |
| `profiles` | One row per `auth.users.id`; display name, preferences, and a server-managed role. |
| `conversations` | Owner, title, optional folder, pinned flag, model selection, and timestamps. |
| `messages` | Conversation, role, content, status, model/provider metadata, and token usage; paginate by creation time and ID. |
| `folders` | User-owned conversation groups. |
| `feedback` | User, message, rating, optional comment, and moderation status. |
| `memories` | User-owned, opt-in memory entries with source, status, and timestamps. |
| `documents` | User-owned file metadata, processing status, size/type, and storage object ID; file bytes go in private object storage, not a public URL. |
| `document_chunks` | Document, chunk text, provenance, and `pgvector` embedding; enforce ownership through the parent document. |
| `usage_events` | User, provider/model, request ID, token counts, and measured cost; append-only for auditability. |
| `subscriptions` | Provider customer/subscription identifiers, plan, status, and billing period; never store card numbers. |
| `audit_logs` | Actor, action, resource ID, and timestamp for security-sensitive changes; avoid logging secrets or message bodies. |

Provider API secrets are supplied by each signed-in user and Fernet-encrypted at rest in SQLite. Keep `CREDENTIAL_ENCRYPTION_KEY` out of the database and back it up separately. Owner scoping uses the verified Supabase user UUID. For hosted use, mount durable storage or use an external database and configure HTTPS. This implementation does not provide per-user envelope encryption, distributed rate limits, account recovery UX, complete audit records, or cost accounting.

## API design

All application endpoints use `/api/v1`. The backend exposes `/health` for service health; it must not reveal credentials or internal configuration.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/models` | List enabled models and capabilities from the server-side model registry. |
| `GET /api/v1/conversations` | List the signed-in user's conversations with pagination. |
| `POST /api/v1/conversations` | Create a conversation. |
| `GET /api/v1/conversations/{id}/messages` | Fetch a user's messages for an owned conversation. |
| `POST /api/v1/chat/completions` | Send a message; optionally stream events, with explicit provider/model selection. |
| `POST /api/v1/chat/completions/{request_id}/cancel` | Request cancellation of a supported in-flight response. |
| `POST /api/v1/feedback` | Record feedback for an owned response. |
| `POST /api/v1/documents` | Later: validate and create a private upload; enforce size/type limits. |
| `GET /api/v1/usage` | Later: return the signed-in user's usage and quota. |

Require authentication on private endpoints, validate every body and identifier, enforce ownership on every read/write, paginate potentially large results, and apply per-user rate limits before provider calls. Use a documented streaming event format, surface provider failures explicitly, and never silently switch models.

## Provider feasibility and limits

OpenAI, Anthropic, Google Gemini, DeepSeek, Mistral, Qwen, Groq-hosted models, and Hugging Face offer official API/inference integration paths. The specific models, regional availability, quotas, context windows, terms, and prices change over time and must be fetched or maintained from verified official provider data. Integration requires the appropriate account, API credentials, and often paid usage; a consumer chat subscription does not imply API access.

The current adapter supports model listing and streaming generation for Gemini, Anthropic Messages, and OpenAI-compatible APIs. Other preset names use the compatible protocol only; verify the provider's official endpoint, account access, request compatibility, and exact model ID before use. Pricing is not integrated or presented as verified. Add adapter tests with mock transports and never silently substitute a provider or model.

## Security and privacy boundaries

- Keep all provider secrets on the backend, outside the Git repository; `.env` is ignored and `.env.example` contains empty placeholders.
- Use HTTPS in deployment, strict CORS allowlists, secure headers, short-lived auth tokens, and least-privilege service credentials.
- Verify Supabase JWT signatures and claims on the server. Derive the user ID from the verified token, never trust a user ID supplied by the browser.
- Apply owner filters and database row-level policies to every user's data; test cross-user access denial.
- Validate and limit request sizes, uploads, file types, and processing time. Store uploads privately and scan/parse them in isolated workers.
- Treat prompts, documents, model output, and fetched web pages as untrusted. Sanitize rendered Markdown/HTML and never execute model-generated code in the API process.
- For future web search, defend against SSRF, unsafe redirects, private-network IPs, and prompt injection in fetched pages.
- Do not train shared models from private chats. Provide explicit controls before enabling memory, export, or deletion features.
- Avoid logging access tokens, API keys, raw uploaded documents, or full private conversations. Record security events with request IDs and minimal metadata.

## Free-tier and hosting comparison

Pricing and quotas change; check each provider's current plan before deployment. Free is for learning/testing, not a reliability promise.

| Option | Suitable use | Important current trade-off |
| --- | --- | --- |
| Render Free | Preferred: static frontend plus a Python web service from the GitHub repository. | Free web services spin down after 15 minutes without inbound traffic and can take about a minute to wake. Free service disks are ephemeral. Current docs list 750 shared free instance-hours per workspace/month; usage beyond included bandwidth/build limits can suspend services or incur charges depending on account setup. |
| Supabase Free | Auth and PostgreSQL/`pgvector` while developing. | Current pricing lists 500 MB database, 1 GB file storage, 5 GB egress, up to 2 active projects, and pausing after a week of inactivity. It is not a permanent backup/retention guarantee. |
| Vercel Hobby | Alternative for a personal static frontend. | Free Hobby tier is intended for personal/non-commercial use and is not required for this Render-first deployment. |

**Recommended learning setup:** GitHub + Render Free for a demo frontend/API, and Supabase Free for auth/data while testing. This is not a durable production configuration: the database may pause, the Render API sleeps, uploaded local files do not persist, and AI provider calls can cost money. Never put a payment method or production user data on a service without understanding the provider's limits and billing.

## Render + GitHub deployment path

1. Create or choose a GitHub repository and push the tested project.
2. In Render, connect the GitHub account and grant access to that specific repository.
3. Add a Render Blueprint (`render.yaml`) when both frontend and backend have tested build/start commands. Keep secrets out of the Blueprint; enter them in Render's environment settings.
4. Configure the backend web service and static site, health check, frontend API URL, backend CORS origin, and database connection in Render/Supabase.
5. Deploy and verify the HTTPS frontend, backend health check, authentication, and a mock chat before configuring any paid provider.
6. Link the selected GitHub branch for automatic deploys on pushes. Review deployment logs and usage after each deploy.

Do not deploy the current Phase 1 state: no app build or health endpoint exists yet. Do not use Render's free local filesystem for user uploads or a database.

## Phased implementation checklist

- [x] **Phase 1 — Architecture and setup:** inspect local tools/workspace; document design choices, project layout, data/API plan, provider feasibility, security, hosting limits, and workflow.
- [x] **Phase 2 — Frontend preview:** responsive AI ANS design system, accessible chat UI, browser-local history, and labeled unavailable features.
- [x] **Phase 3 — Backend preview:** FastAPI health/models endpoints, bounded request schemas, and environment-based server configuration. Database and account support remain pending.
- [x] **Phase 4 — Demo chat:** stateless mock streaming endpoint and frontend event-stream handling. Authentication and tenant isolation remain pending.
- [x] **Phase 5 — Initial provider:** a Google Gemini streaming adapter reads a server-only API key and configurable model; adapter behavior is tested with a mocked provider. Anthropic, other providers, and usage controls remain pending.
- [x] **Phase 6 — Provider expansion (private preview):** Gemini, Anthropic Messages, OpenAI-compatible adapter, provider presets, model discovery/testing, encrypted single-owner key vault, and chat selection. Multi-user authentication and verified pricing remain pending.
- [ ] **Phase 7 — Multi-model:** parallel responses, comparison, and attributed synthesis.
- [ ] **Phase 8 — Research:** authorized search provider, source retrieval, citations, and SSRF/prompt-injection defenses.
- [ ] **Phase 9 — Documents and memory:** private object storage, bounded parsing, `pgvector` retrieval, consent and deletion controls.
- [ ] **Phase 10 — Advanced tools:** voice, image, coding, and isolated data-analysis capabilities.
- [ ] **Phase 11 — Plans/admin:** distributed quotas, usage accounting, protected admin functions; payments remain off until explicitly configured.
- [ ] **Phase 12 — Hardening/deployment:** starter Render Blueprint exists. Production hardening, CI, hosted deployment, persistent storage, and production readiness review remain pending.

Each phase must leave the app runnable, pass the tests introduced for it, and document any feature that is not implemented. Do not claim accuracy, security certification, uptime, or provider capability without evidence.

## Official hosting references

- [Render free instances and limitations](https://render.com/docs/free)
- [Render Blueprints](https://render.com/docs/blueprint-spec)
- [Render Git provider connection](https://render.com/docs/git-provider)
- [Render automatic deploys](https://render.com/docs/deploys)
- [Supabase pricing and quotas](https://supabase.com/pricing)
- [Vercel pricing](https://vercel.com/pricing)
