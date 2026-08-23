# AI Personal Finance

[![CI](https://github.com/AskariJafri/ai-finance/actions/workflows/ci.yml/badge.svg)](https://github.com/AskariJafri/ai-finance/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-178-blue.svg)](backend/tests)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

An AI-powered personal finance app: imports transactions from Plaid, categorizes
them (rules + local LLM), and surfaces spending/net-worth insights. Built to
production shape (clean architecture, typed, tested) so it can grow from a
single user to many.

This repository currently implements **Phase 1 — the vertical walking
skeleton**: auth → Plaid link → transaction sync → categorization (with a
learning loop) → dashboard. See `docs/ROADMAP.md` for the phased plan of
everything else. Since then the budget (50/30/20) and reports layer, the Invest
module, brokerage linking, and the conversational assistant have landed —
goals, forecasting, notifications and cloud deployment have not.

## Architecture

```
ai-finance/
├── backend/     FastAPI, Python 3.12+, async SQLAlchemy, Postgres (Clean Architecture)
├── mobile/      React Native (Expo) + TypeScript
├── docker-compose.yml
└── .env.example
```

Backend dependency rule: `api → services → repositories / integrations / ai → db / domain`.
Domain is framework-free; services depend on **interfaces** (Plaid gateway, LLM
provider), wired via FastAPI DI — so Plaid and the LLM are swappable and every
layer is unit-testable.

Key design choices:
- **Money is integer cents** everywhere (never floats).
- **Plaid access tokens are Fernet-encrypted at rest**; bank credentials are
  never stored (Plaid captures them).
- **The AI never invents numbers** — dashboard figures come from SQL aggregates;
  the LLM is only used to categorize unknown merchants (and, later, to explain
  data).
- **Learning loop**: a user category correction becomes a high-priority rule, so
  the same merchant is auto-categorized next time (no AI call).

## Quick start (Docker)

```bash
cp .env.example .env            # works out-of-the-box with the MOCK Plaid client
docker compose up --build       # Postgres + API (migrations run automatically)
# API:      http://localhost:8000
# Swagger:  http://localhost:8000/docs
```

With no Plaid credentials set, the backend uses a built-in **mock bank** that
seeds realistic accounts/transactions — so you can exercise the whole flow with
zero external setup.

### Enable AI

Inference is hosted, not local. Get a free key at
[openrouter.ai/keys](https://openrouter.ai/keys) (no credit card) and set it:

```bash
LLM_API_KEY=sk-or-...            # in .env
```

Leave it blank and the app still runs — every AI feature falls back to its
rules-based answer rather than failing.

`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` configure the preferred endpoint,
and the client is plain OpenAI-compatible HTTP, so Groq, Cerebras or the Hugging
Face router work by changing those three values.

**Free-model quota:** 20 requests/minute and 50/day (1,000/day once you've
bought $10 of credits at any point). Costs are one request per chat message, per
advice generation, and per committee ticker — so a single portfolio plan, which
deliberates once per holding and once per candidate, can spend ten of the fifty.

**`LLM_FALLBACKS`** holds an ordered list of further endpoints, tried when the
preferred one cannot answer:

```bash
LLM_FALLBACKS=[{"base_url":"https://api.groq.com/openai/v1","model":"openai/gpt-oss-120b",
                "api_key":"gsk_...","max_tokens":2000,"max_concurrency":2,
                "extra_body":{"reasoning_effort":"low"}}]
```

Each free tier is metered on its own key, so every entry is another allowance.
`max_tokens`, `max_concurrency` and `extra_body` are per-endpoint because rate
limits are: Groq allows 1,000 requests a day but only 8,000 tokens a minute, and
reserves `max_tokens` against that — so the global defaults never fit, and
`reasoning_effort` (which gpt-oss honours) more than halves what a committee
verdict costs. Check your key's own `/models` list before picking one; model
availability differs per account.
The order is preference order — the primary answers whenever it can, and a
provider that reports its daily quota spent is skipped for the rest of the day
rather than being retried on every call. With no fallbacks configured nothing
changes; with none of them reachable, every feature still degrades to its
rules-based answer as before.

## Backend — local dev

```bash
cd backend
python -m venv .venv && ./.venv/Scripts/activate      # Windows
# source .venv/bin/activate                            # macOS/Linux
pip install -e ".[dev]"

# Postgres (or `docker compose up postgres -d`) then:
cp ../.env.example .env
alembic upgrade head
uvicorn app.main:app --reload
pytest                                                 # 13 tests, no network needed
```

### Try the flow with curl (mock Plaid)
```bash
BASE=http://localhost:8000/api/v1
TOKENS=$(curl -s $BASE/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"supersecret123"}')
ACCESS=$(echo "$TOKENS" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $ACCESS"

curl -s -X POST $BASE/plaid/link/token -H "$AUTH"
curl -s -X POST $BASE/plaid/link/exchange -H "$AUTH" -H 'content-type: application/json' \
  -d '{"public_token":"public-sandbox-anything"}'
curl -s $BASE/dashboard/summary -H "$AUTH"
curl -s $BASE/transactions -H "$AUTH"
```

## Using real Plaid (Sandbox)

1. Put your `PLAID_CLIENT_ID` / `PLAID_SECRET` in `.env` (keep `PLAID_ENV=sandbox`).
2. Restart the API — it now uses the real Plaid client.
3. Run the **mobile dev build** (below) and complete Plaid Link with Sandbox
   credentials (`user_good` / `pass_good`).

> **Note:** Plaid Link is a native SDK and **does not run in Expo Go**. Use the
> Expo **dev build** (below). A Hosted-Link WebView fallback is documented in
> `mobile/README.md`.

## Connecting a real brokerage (Wealthsimple / Questrade)

Wealthsimple publishes no developer API. [SnapTrade](https://snaptrade.com)
is the aggregator that fronts it — it both reads positions and places orders,
and the user authorizes on SnapTrade's portal so this app never handles broker
credentials. (Unofficial wrappers exist; they need your actual Wealthsimple
password, breach the ToS, and are not used here.)

1. Get keys at [dashboard.snaptrade.com/signup](https://dashboard.snaptrade.com/signup)
   — free tier covers 5 connections for personal use.
2. Put `SNAPTRADE_CLIENT_ID` / `SNAPTRADE_CONSUMER_KEY` in `.env`.
3. Restart the API. Positions now sync; **orders still cannot be placed.**
4. To allow live orders, set `SNAPTRADE_TRADING_ENABLED=true`.

Leave the keys blank to use the mock broker — a simulated Wealthsimple TFSA
with CAD ETF positions and instant fills. Every test runs against it.

### Live trading safety model

Three independent gates, all of which must pass before a real order is placed:

| Gate | Where |
|------|-------|
| `SNAPTRADE_TRADING_ENABLED=true` **and** real credentials | `.env` |
| The linked account granted trade access | `BrokerageConnection.trading_enabled` |
| A human approved *this specific order* | `POST /brokerage/orders/{id}/approve` |

The AI committee, auto-trader and rebalancer can only ever produce `PROPOSED`
orders — no engine path can set `APPROVED`. There is deliberately no
bulk-approve endpoint. Without a link (or with the kill switch off) the app
behaves exactly as before: it proposes, you trade at your broker, you mark it
executed.

## Ask — the conversational assistant

The **Ask** tab answers two kinds of question in one thread: *"how am I doing?"*
against your own figures, and *"what does this term mean?"*.

Grounding works the same way as everywhere else in the app — **every number in
the prompt is computed in SQL (or from real market data) before the model sees
it**. `FinanceContextBuilder` renders a compact snapshot (cash flow, 50/30/20
split, positions largest-first, watchlist); the LLM is only ever asked to
explain figures it was handed. Each section is guarded independently, so a
portfolio lookup failing on stale market data doesn't take the answer with it.

Definitions never depend on the model at all. `app/content/` holds hand-written,
reviewed explainers — `invest_explainers.py` (portfolio metrics, also served to
the app's metric tooltips) and `money_explainers.py` (cash, budgeting, TFSA vs
RRSP, market vs limit orders). Matched entries go into the prompt as the
authoritative wording so the model paraphrases reviewed copy instead of
inventing a definition.

Because the model may be unconfigured, rate limited or simply having a bad day,
replies degrade in three steps and a turn never 500s:

| `source` | Meaning |
|----------|---------|
| `ai` | the model answered, grounded in the context block |
| `glossary` | model unavailable — a reviewed explainer is served verbatim |
| `fallback` | neither matched — says so plainly, points at the tabs that still work |

The user's own message is always persisted, so the thread stays coherent
whichever path a reply takes. The composer's **MY DATA** toggle sends
`include_finances=false`, so a purely educational question can be asked without
your balances entering the prompt. The assistant never places trades.

## Mobile

See [`mobile/README.md`](mobile/README.md) for the Expo dev-build setup, env
configuration (API base URL), and screens (Auth, Link Bank, Dashboard,
Transactions, Invest, Ask).

## Deploying

The backend is stateless — no filesystem writes, no background workers, no
broker — so it deploys as a single container against a managed Postgres. See
[`backend/README.md`](backend/README.md) for the Hugging Face Spaces + Neon
walkthrough, both on free tiers. The container migrates the database on start,
and refuses to boot in production while the placeholder `JWT_SECRET` or
`ENCRYPTION_KEY` are still in place.

## Testing

```bash
cd backend && pytest        # unit (crypto, JWT, categorization) + integration (API + mock Plaid)
```

## Endpoints (v1)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/register` `/auth/login` `/auth/refresh` | JWT access/refresh |
| GET  | `/auth/me` | current user |
| POST | `/plaid/link/token` | create Plaid Link token |
| POST | `/plaid/link/exchange` | exchange public token, store encrypted, sync |
| POST | `/plaid/sync` | incremental re-sync |
| POST | `/plaid/webhook` | Plaid webhook receiver |
| GET  | `/accounts` | linked accounts + balances |
| GET  | `/transactions` | list transactions |
| GET  | `/transactions/categories` | available categories |
| PATCH| `/transactions/{id}/category` | correct category (learning loop) |
| GET  | `/dashboard/summary` | balance, spend, income, savings rate, breakdown |
| GET  | `/settings/plaid` | Plaid credential status (user / env / mock) |
| PUT  | `/settings/plaid` | store your own Plaid keys (secret encrypted) |
| DELETE | `/settings/plaid` | remove your keys (fall back to mock) |
| POST | `/brokerage/link` | start a brokerage link (returns SnapTrade portal URL) |
| POST | `/brokerage/link/complete` | store the accounts the user authorized |
| GET  | `/brokerage/connections` | linked brokerage accounts |
| DELETE | `/brokerage/connections` | unlink (keeps manually-entered holdings) |
| POST | `/brokerage/sync` | mirror broker positions into holdings |
| POST | `/brokerage/orders/{id}/approve` | **approve + place a real order** |
| POST | `/brokerage/orders/{id}/refresh` | poll broker, settle holdings on fill |
| GET  | `/chat/status` | assistant availability + starter prompts for this user |
| GET  | `/chat/sessions` | past chat threads (newest activity first) |
| POST | `/chat/sessions` | start an empty thread |
| DELETE | `/chat/sessions/{id}` | delete a thread and its messages |
| GET  | `/chat/sessions/{id}/messages` | full transcript of one thread |
| POST | `/chat/messages` | ask a question (one LLM request per message) |

## Bring your own Plaid keys

You don't have to edit server config. In the app's **Settings** tab you can set
the **server URL** and paste your Plaid `client_id` / `secret` (choose sandbox
or production). Keys are encrypted on the server and used for that user's Plaid
calls; with no keys set, the app uses the built-in mock bank.

## Android APK

Building the APK is a compile step (native Plaid module) and needs the Android
toolchain **or** an Expo account for a cloud build. See
[`docs/BUILD_APK.md`](docs/BUILD_APK.md) — the EAS path needs no local Android SDK.
```
