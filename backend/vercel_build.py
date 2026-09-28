"""Deploy-time database work, for hosts that have no startup hook.

The container path runs ``alembic upgrade head`` in its ``CMD`` (see
``Dockerfile``) and seeds the taxonomy in the FastAPI lifespan. Neither
survives a move to serverless: there is no single boot to hang them off, and a
lifespan migration would have every concurrent cold start racing the same
schema. Vercel's build step is the honest equivalent — it runs once per deploy,
before any traffic, in a container that can reach the database.

Guarded on ``VERCEL_ENV`` so that preview deploys, which share the production
``DATABASE_URL`` unless you give them their own, never migrate it. Only a
production deploy touches the schema.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

TARGET_ENV = "production"


def main() -> int:
    vercel_env = os.environ.get("VERCEL_ENV")
    if vercel_env != TARGET_ENV:
        print(f"VERCEL_ENV={vercel_env!r}; skipping migrations (only {TARGET_ENV} migrates).")
        return 0

    # Migrations want a real session, not a pooled one. Neon's integration sets
    # DATABASE_URL to the PgBouncer endpoint, which is right for request
    # traffic and wrong for DDL: in transaction pooling a connection is not
    # held across statements, which is the one thing a migration needs. The
    # direct endpoint is handed to us alongside it, so prefer it here — for the
    # seeding below too, which is why this is set before app.main is imported.
    unpooled = os.environ.get("DATABASE_URL_UNPOOLED")
    if unpooled:
        os.environ["DATABASE_URL"] = unpooled
        print("Using the direct (unpooled) endpoint for migrations.")

    print("Running alembic upgrade head ...")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

    # Imported rather than reimplemented: the taxonomy this inserts is the same
    # one the container seeds, and two copies of it would drift.
    from app.main import seed_reference_data

    print("Seeding reference data ...")
    asyncio.run(seed_reference_data())
    print("Build-time database work complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
