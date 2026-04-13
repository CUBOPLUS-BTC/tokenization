# Scripts

Development utility scripts for setup, seeding, linting, and local workflows.

## Database migrations (Alembic)

Alembic is configured at repository root with:

- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/`

### Install migration tooling

```bash
pip install -r scripts/requirements-migrations.txt
```

### Environment variables required by Alembic

Alembic resolves database settings from environment variables.

When running from repository root, `alembic/env.py` loads env files in this order (if present):

1. `.env`
2. `infra/.env`
3. `infra/.env.<profile>` (from `ENV_PROFILE`, defaults to `local`)

Use one of these approaches before running migration commands:

- Define `DATABASE_URL` directly.
- Or define all `POSTGRES_*` variables used by `alembic.ini`: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.

Note: `POSTGRES_PASSWORD_FILE` is resolved by service settings, but Alembic does not read secret files automatically.
For Alembic commands, set `DATABASE_URL` or provide explicit `POSTGRES_PASSWORD`.

Recommended for local development: keep these values in root `.env`.

### Naming convention for migration files

Configured in `alembic.ini` (`file_template`) as:

`YYYYMMDD_HHMM_<revision>_<slug>.py`

Use concise, action-oriented slugs such as:

- `create_users_table`
- `add_order_status_index`
- `drop_legacy_column`

### Create a new migration

Autogenerate from metadata:

```bash
alembic revision --autogenerate -m "add users audit columns"
```

Manual empty migration:

```bash
alembic revision -m "add escrow dispute fields"
```

### Apply and rollback migrations

```bash
alembic upgrade head
alembic downgrade -1
```

### Validate on a clean local database (zero -> head)

```bash
docker compose -f infra/docker-compose.local.yml up -d postgres
alembic downgrade base
alembic upgrade head
alembic current
```

Expected result: database migrates from empty/base state to latest schema revision without errors.
