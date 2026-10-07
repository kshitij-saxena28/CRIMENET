# Deployment

**Demo:** SQLite + NetworkX, single process. `./run.sh` (Linux/macOS), `RUN_WINDOWS.bat`, or `docker compose up --build`.

## Docker

1. `cp .env.example .env`, then set at least `SECRET_KEY` (and `ADMIN_PASSWORD`). Use `APP_ENV=production` for anything real.
2. `docker compose up --build`. The API and UI share one image and one `.env`; the database and evidence live in the `dcn-data` volume (`/data`). Ports are bound to localhost — put a TLS reverse proxy in front.
3. The containers run as a non-root user. On first start read the initial account passwords from the API container logs (`docker compose logs api`).

## Production path

PostgreSQL + Neo4j + Redis (shared rate-limit store / job queue) + managed secret store + TLS reverse proxy + approved identity provider. The prototype has no migration framework: `ensure_schema_columns()` only adds missing columns; adopt Alembic before a real schema change. Run a single API worker until the rate limiter and audit-chain lock are moved to shared storage. See `docs/security.md` for the remaining hardening list.
