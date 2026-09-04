---
title: AI Finance Backend
emoji: 💸
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# AI Finance — backend

FastAPI service for the AI Personal Finance app. The mobile client talks to
`/api/v1`; `GET /health` is the liveness probe.

The YAML block above is Hugging Face Spaces configuration — it is what makes
this directory publishable as a Docker Space. See
[Deploying](#deploying-to-hugging-face-spaces) below.

## Running locally

From the repo root, `docker compose up --build` brings up Postgres and this
service together. For a host run, see the root `README.md`.

## Deploying to Hugging Face Spaces

The Space's repo root must contain the Dockerfile, so publish this directory as
a subtree rather than the whole monorepo:

```bash
# once
huggingface-cli login
git remote add space https://huggingface.co/spaces/<owner>/<space-name>

# every deploy
git subtree push --prefix backend space main
```

The container runs `alembic upgrade head` before starting uvicorn, so a deploy
migrates the database itself.

### Database

Any Postgres reachable over TLS works; Neon's free tier is the intended target.
Paste the connection string Neon gives you straight into `DATABASE_URL` — the
settings layer rewrites `postgres://` to the async driver and strips the
libpq-only `sslmode`/`channel_binding` parameters that asyncpg rejects.

### Environment

Set these under *Settings → Variables and secrets*. Anything holding a
credential belongs in **Secrets**, not Variables.

| Key | Notes |
| --- | --- |
| `DATABASE_URL` | Neon connection string |
| `JWT_SECRET` | `openssl rand -hex 32` — the app refuses to boot in production with the dev default |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Changing it later makes stored Plaid/SnapTrade tokens undecryptable |
| `LLM_API_KEY` | OpenRouter key. Leave unset to run with AI off — every feature falls back to its rules-based answer |
| `LLM_MODEL` | Defaults to a free model; see the root `.env.example` |
| `ENVIRONMENT` | `production` (also hides `/docs` and `/openapi.json`) |
| `DEBUG` | `false` |
| `CORS_ORIGINS` | Explicit origins if a web client is ever added |
| `PLAID_*`, `SNAPTRADE_*` | Optional — both degrade to mock clients when unset |
| `FINNHUB_API_KEY` | Company news for the AI committee. Leave unset to run with research off — every surface falls back to figures-only |
| `TAVILY_API_KEY` | Macro/world web search. One call a day for the whole app; optional and independent of Finnhub |

### Free-tier behaviour worth knowing

- **The Space sleeps** after roughly 48 hours without traffic. The next request
  wakes it and can take up to a minute.
- **Neon suspends compute** after 5 minutes idle, resuming in a few hundred
  milliseconds. The engine is configured with `pool_pre_ping` and a short
  `pool_recycle` so a suspended database never surfaces as a dead connection.
- **Long requests**: the Spaces gateway drops requests that run past ~60s. The
  committee is one LLM call per ticker precisely so Deploy-Cash stays under it.
