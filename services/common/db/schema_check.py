"""Fail-fast checks that the database schema exists before serving traffic."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_schema_ready(
    engine: AsyncEngine,
    required_tables: Iterable[str],
    required_columns: Mapping[str, Iterable[str]] | None = None,
) -> None:
    """Raise ``RuntimeError`` if any ``required_tables`` are missing from ``public``.

    Used at service startup so an empty or unmigrated database does not appear
    "healthy" while returning errors on every request.
    """
    required = frozenset(required_tables)
    if not required:
        return

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        existing = {row[0] for row in result}

    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            "database schema not migrated: missing table(s) "
            f"{missing}. Run the migrate sidecar first (e.g. "
            "`docker compose -f infra/docker-compose.local.yml up -d --force-recreate --build migrate`)."
        )

    required_columns = required_columns or {}
    if not required_columns:
        return

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
        existing_columns: dict[str, set[str]] = {}
        for table_name, column_name in result:
            existing_columns.setdefault(str(table_name), set()).add(str(column_name))

    missing_columns = {
        table_name: sorted(set(columns) - existing_columns.get(table_name, set()))
        for table_name, columns in required_columns.items()
    }
    missing_columns = {
        table_name: columns for table_name, columns in missing_columns.items() if columns
    }
    if missing_columns:
        raise RuntimeError(
            "database schema not migrated: missing column(s) "
            f"{missing_columns}. Run the migrate sidecar first (e.g. "
            "`docker compose -f infra/docker-compose.local.yml up -d --force-recreate --build migrate`)."
        )
