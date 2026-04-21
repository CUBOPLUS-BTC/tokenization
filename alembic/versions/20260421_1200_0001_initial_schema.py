"""initial schema (squashed from services/common/db/metadata.py)

Revision ID: 0001
Revises:
Create Date: 2026-04-21 12:00:00
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


_REPO_ROOT = Path(__file__).resolve().parents[2]
_METADATA_PATH = _REPO_ROOT / "services" / "common" / "db" / "metadata.py"
_spec = importlib.util.spec_from_file_location("_alembic_squash_metadata", _METADATA_PATH)
_meta_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(_meta_mod)  # type: ignore[union-attr]
metadata: sa.MetaData = _meta_mod.metadata

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind=bind)
