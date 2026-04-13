from __future__ import annotations

from dotenv import load_dotenv

import os
import re
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from services.common import metadata  # noqa: E402


_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_profile() -> str:
    profile = os.getenv("ENV_PROFILE", "local").strip().lower()
    return profile if profile in {"local", "staging", "production"} else "local"


def _load_alembic_env_files() -> None:
    repo_root = _repo_root()
    infra_dir = repo_root / "infra"
    profile = _env_profile()

    for env_file in [
        repo_root / ".env",
        infra_dir / ".env",
        infra_dir / f".env.{profile}",
    ]:
        if env_file.exists():
            load_dotenv(env_file, override=False)


def _expand_env_placeholders(value: str) -> str:
    missing_keys: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        resolved = os.getenv(key)
        if resolved is None or resolved.strip() == "":
            missing_keys.add(key)
            return ""
        return resolved

    expanded = _ENV_VAR_PATTERN.sub(_replace, value)
    if missing_keys:
        missing_joined = ", ".join(sorted(missing_keys))
        raise ValueError(
            "Missing required environment variables for Alembic sqlalchemy.url: "
            f"{missing_joined}. Set DATABASE_URL or define all POSTGRES_* variables."
        )

    return expanded


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    return _expand_env_placeholders(config.get_main_option("sqlalchemy.url"))


_load_alembic_env_files()
config.set_main_option("sqlalchemy.url", _database_url())
target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
