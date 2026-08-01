# Roadmap

Phase 1 (this repo) ships the vertical walking skeleton. Later phases deepen
each area. The architecture (interfaces for Plaid/LLM, normalized schema,
service layer) is designed so these drop in without rework.

| Phase | Theme | Highlights |
|------|-------|------------|
| **1 ✅** | Walking skeleton | Auth (JWT), Plaid link+sync, rule/AI categorization + learning loop, dashboard |
| 2 | Categorization depth | Full merchant DB + aliases, anomaly detection, robust dedup, confidence tuning |
| 3 | Budgets / Goals / Subscriptions | Budget CRUD + alerts, goals + completion forecast, recurring/subscription detection + cancel suggestions |
| 4 | Net worth & Investments | Plaid holdings, assets/liabilities, crypto/property, net-worth snapshots + charts, allocation/P&L/dividends |
| 5 | AI assistant + semantic search | Guardrailed tool-calling assistant over structured data; embeddings + pgvector NL search |
| 6 | Forecasting / Analytics / Reports | Balance & cash-flow forecasts; trends & lifestyle inflation; PDF/CSV reports + AI summaries |
| 7 | Notifications & jobs | Scheduler (auto-sync + reminders), bill/budget/low-balance alerts, digests, webhook hardening |
| 8 | Security & DevOps | Rate limiting, audit logs, secrets mgmt, OWASP pass, GitHub Actions CI, AWS (RDS/ECS/IaC) |
| 9 | Future | Receipt OCR, voice, family budgets, multi-currency, travel mode, tax/retirement/estate planning |

## Data model growth
Phase 1 tables: `users, categories, merchants, category_rules, plaid_items,
accounts, transactions`. Later phases add: `budgets, goals, recurring_payments,
subscriptions, investments, holdings, assets, liabilities, net_worth_snapshots,
monthly_reports, ai_insights, notifications, settings, audit_logs` — each keyed
on `user_id`, added via incremental Alembic migrations.
