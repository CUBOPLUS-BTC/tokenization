from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys
import traceback
from typing import Any
from urllib.parse import quote_plus, urlsplit, urlunsplit
import uuid

import bcrypt
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine


REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = REPO_ROOT / "services" / "common" / "db" / "metadata.py"
USERS_TABLE = None


def _repo_relative_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _resolve_secret(secret_value: str | None, file_path: str | None) -> str | None:
    if file_path:
        secret_path = _repo_relative_path(file_path)
        if not secret_path.exists():
            raise ValueError(f"Secret file does not exist: {secret_path}")
        return secret_path.read_text(encoding="utf-8").strip()
    if secret_value is None:
        return None
    value = secret_value.strip()
    return value or None


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _build_sync_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL", "").strip()
    if explicit_url and "REDACTED" not in explicit_url:
        return explicit_url

    postgres_user = _required_env("POSTGRES_USER")
    postgres_host = _required_env("POSTGRES_HOST")
    postgres_port = _required_env("POSTGRES_PORT")
    postgres_db = _required_env("POSTGRES_DB")
    postgres_password = _resolve_secret(
        os.getenv("POSTGRES_PASSWORD"),
        os.getenv("POSTGRES_PASSWORD_FILE"),
    )
    if not postgres_password:
        raise ValueError(
            "POSTGRES_PASSWORD or POSTGRES_PASSWORD_FILE is required when DATABASE_URL is absent or masked."
        )

    quoted_user = quote_plus(postgres_user)
    quoted_password = quote_plus(postgres_password)
    return (
        "postgresql+pg8000://"
        f"{quoted_user}:{quoted_password}@{postgres_host}:{postgres_port}/{postgres_db}"
    )


def _sanitize_database_url(url: str) -> str:
    """Return a copy of ``url`` with the password masked.

    Used for logging so operators can see which host/db/user were targeted
    without leaking credentials in CI or Coolify logs.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-database-url>"
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, hostinfo = netloc.rsplit("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            masked_userinfo = f"{user}:***"
        else:
            masked_userinfo = userinfo
        netloc = f"{masked_userinfo}@{hostinfo}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _make_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    for prefix in ("postgresql+", "postgres+"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url.split("://", 1)[1]
    for prefix in ("postgresql://", "postgres://"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url[len(prefix):]
    return sync_url


def _load_users_table() -> Any:
    global USERS_TABLE
    if USERS_TABLE is not None:
        return USERS_TABLE

    spec = importlib.util.spec_from_file_location("_bootstrap_db_metadata", METADATA_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load metadata module from {METADATA_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_bootstrap_db_metadata", module)
    spec.loader.exec_module(module)
    USERS_TABLE = module.users
    return USERS_TABLE


def _row_value(row: object, key: str, default: object | None = None):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)


async def _generate_referral_code(conn, users_table) -> str:
    for _ in range(10):
        candidate = os.urandom(8).hex()[:10].upper()
        result = await conn.execute(
            sa.select(users_table.c.id).where(users_table.c.referral_code == candidate)
        )
        if result.fetchone() is None:
            return candidate
    raise RuntimeError("unable_to_generate_unique_referral_code")


@dataclass(frozen=True)
class BootstrapAdminConfig:
    email: str
    password: str
    display_name: str


def _load_bootstrap_admin_config() -> BootstrapAdminConfig | None:
    email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
    password = _resolve_secret(
        os.getenv("INITIAL_ADMIN_PASSWORD"),
        os.getenv("INITIAL_ADMIN_PASSWORD_FILE"),
    )
    display_name = os.getenv("INITIAL_ADMIN_DISPLAY_NAME", "Platform Administrator").strip() or "Platform Administrator"

    if not email and not password:
        return None
    if not email or not password:
        raise ValueError(
            "INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD or INITIAL_ADMIN_PASSWORD_FILE must be set together."
        )
    return BootstrapAdminConfig(email=email, password=password, display_name=display_name)


def _count_public_tables(sync_url: str) -> int:
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as connection:
            result = connection.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
            row = result.fetchone()
            return int(row[0]) if row is not None else 0
    finally:
        engine.dispose()


def run_migrations() -> None:
    sync_url = _build_sync_database_url()
    os.environ["DATABASE_URL"] = sync_url
    print(f"Using DATABASE_URL={_sanitize_database_url(sync_url)}", flush=True)

    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(alembic_config, "head")

    try:
        table_count = _count_public_tables(sync_url)
        print(f"Public schema tables after migration: {table_count}", flush=True)
    except Exception as exc:  # noqa: BLE001 - diagnostic-only, never block on this
        print(f"Warning: could not count public tables: {exc}", file=sys.stderr, flush=True)


async def seed_initial_data() -> None:
    admin_config = _load_bootstrap_admin_config()
    if admin_config is None:
        print("Skipping seeders: INITIAL_ADMIN_EMAIL/INITIAL_ADMIN_PASSWORD not configured.")
        return

    users_table = _load_users_table()
    sync_url = _build_sync_database_url()
    async_url = _make_async_url(sync_url)
    engine = create_async_engine(async_url, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            existing_result = await conn.execute(
                sa.select(users_table)
                .where(sa.func.lower(users_table.c.email) == admin_config.email)
                .limit(1)
            )
            existing_row = existing_result.fetchone()

            password_hash = bcrypt.hashpw(
                admin_config.password.encode("utf-8"),
                bcrypt.gensalt(),
            ).decode("utf-8")
            now = datetime.now(tz=timezone.utc)

            if existing_row is None:
                referral_code = await _generate_referral_code(conn, users_table)
                await conn.execute(
                    sa.insert(users_table).values(
                        id=uuid.uuid4(),
                        email=admin_config.email,
                        password_hash=password_hash,
                        display_name=admin_config.display_name,
                        role="admin",
                        is_verified=True,
                        referral_code=referral_code,
                        created_at=now,
                        updated_at=now,
                    )
                )
                print(f"Created initial admin user: {admin_config.email}")
                return

            updates: dict[str, object] = {}
            if _row_value(existing_row, "role") != "admin":
                updates["role"] = "admin"
            if not bool(_row_value(existing_row, "is_verified", False)):
                updates["is_verified"] = True
            if _row_value(existing_row, "display_name") != admin_config.display_name:
                updates["display_name"] = admin_config.display_name
            if not _row_value(existing_row, "password_hash"):
                updates["password_hash"] = password_hash
            if _row_value(existing_row, "deleted_at") is not None:
                updates["deleted_at"] = None

            if not updates:
                print(f"Initial admin already present: {admin_config.email}")
                return

            updates["updated_at"] = now
            await conn.execute(
                sa.update(users_table)
                .where(users_table.c.id == _row_value(existing_row, "id"))
                .values(**updates)
            )
            print(f"Synchronized initial admin user: {admin_config.email}")
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Alembic migrations and idempotent database seeders."
    )
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Run only Alembic migrations.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Run only idempotent seeders.",
    )
    args = parser.parse_args()
    if args.migrate_only and args.seed_only:
        parser.error("--migrate-only and --seed-only cannot be used together.")
    return args


def main() -> int:
    try:
        args = parse_args()
        profile = os.getenv("ENV_PROFILE", "local").strip().lower() or "local"
        print(f"Database bootstrap started for profile={profile}", flush=True)

        if not args.seed_only:
            run_migrations()
            print("Alembic migrations applied successfully.", flush=True)

        if not args.migrate_only:
            asyncio.run(seed_initial_data())
            print("Seed phase completed.", flush=True)

        return 0
    except SystemExit:
        raise
    except BaseException:
        # Flush both streams so Coolify captures the full traceback before the
        # container is torn down.
        print("Database bootstrap failed:", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())