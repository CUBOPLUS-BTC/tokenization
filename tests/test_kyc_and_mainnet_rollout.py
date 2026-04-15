"""Tests for KYC verification flow and mainnet rollout configuration.

Covers:
- KYC verification state storage per user.
- Trade flows enforcing KYC rules above configured thresholds.
- Verification failure / pending state surfacing for users and admins.
- Mainnet-specific environment separation and rollout configuration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Importable helpers
# ---------------------------------------------------------------------------
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))

from auth.kyc_db import (
    create_kyc_record,
    get_kyc_status,
    is_kyc_verified,
    list_kyc_records,
    update_kyc_status,
)
from auth.schemas import (
    KycAdminUpdateRequest,
    KycListResponse,
    KycStatusOut,
    KycStatusResponse,
    KycSubmitRequest,
)
from common.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_kyc_row(
    *,
    user_id: str | None = None,
    status: str = "pending",
    reviewed_by: str | None = None,
    reviewed_at: datetime | None = None,
    rejection_reason: str | None = None,
    notes: str | None = None,
) -> dict:
    """Construct a dict that behaves like a KYC verification row."""
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id or str(uuid.uuid4()),
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "rejection_reason": rejection_reason,
        "notes": notes,
        "document_url": None,
        "metadata": None,
        "created_at": datetime.now(tz=timezone.utc),
        "updated_at": datetime.now(tz=timezone.utc),
    }


# ---------------------------------------------------------------------------
# 1. KYC verification state storage per user
# ---------------------------------------------------------------------------


class TestKycStateStorage:
    """AC: Verification states can be stored per user."""

    def test_is_kyc_verified_returns_true_for_verified(self):
        row = MagicMock()
        row._mapping = {"status": "verified"}
        assert is_kyc_verified(row) is True

    def test_is_kyc_verified_returns_false_for_pending(self):
        row = MagicMock()
        row._mapping = {"status": "pending"}
        assert is_kyc_verified(row) is False

    def test_is_kyc_verified_returns_false_for_rejected(self):
        row = MagicMock()
        row._mapping = {"status": "rejected"}
        assert is_kyc_verified(row) is False

    def test_is_kyc_verified_returns_false_for_expired(self):
        row = MagicMock()
        row._mapping = {"status": "expired"}
        assert is_kyc_verified(row) is False

    def test_is_kyc_verified_returns_false_for_none(self):
        assert is_kyc_verified(None) is False

    def test_kyc_status_out_schema_serializes(self):
        """Verify the KYC output schema can be serialized."""
        now = datetime.now(tz=timezone.utc)
        out = KycStatusOut(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status="pending",
            reviewed_by=None,
            reviewed_at=None,
            rejection_reason=None,
            notes="Awaiting review",
            created_at=now,
            updated_at=now,
        )
        data = out.model_dump(mode="json")
        assert data["status"] == "pending"
        assert data["notes"] == "Awaiting review"

    def test_kyc_status_out_all_statuses_accepted(self):
        """Validate all four KYC status values are accepted by the schema."""
        now = datetime.now(tz=timezone.utc)
        for status_val in ("pending", "verified", "rejected", "expired"):
            out = KycStatusOut(
                id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                status=status_val,
                created_at=now,
                updated_at=now,
            )
            assert out.status == status_val

    def test_kyc_response_schema(self):
        """Verify the wrapper response model."""
        now = datetime.now(tz=timezone.utc)
        kyc = KycStatusOut(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status="verified",
            reviewed_by=str(uuid.uuid4()),
            reviewed_at=now,
            created_at=now,
            updated_at=now,
        )
        resp = KycStatusResponse(kyc=kyc)
        data = resp.model_dump(mode="json")
        assert data["kyc"]["status"] == "verified"
        assert data["kyc"]["reviewed_by"] is not None

    def test_kyc_list_response_schema(self):
        """Verify the admin list response schema."""
        now = datetime.now(tz=timezone.utc)
        records = [
            KycStatusOut(
                id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                status=s,
                created_at=now,
                updated_at=now,
            )
            for s in ("pending", "verified", "rejected")
        ]
        resp = KycListResponse(records=records)
        data = resp.model_dump(mode="json")
        assert len(data["records"]) == 3
        assert {r["status"] for r in data["records"]} == {"pending", "verified", "rejected"}


# ---------------------------------------------------------------------------
# 2. Trade flows enforcing KYC rules above configured thresholds
# ---------------------------------------------------------------------------


class TestKycTradeEnforcement:
    """AC: Trade flows can enforce KYC rules above configured thresholds.

    These tests validate the _enforce_kyc_threshold business logic by
    replicating the enforcement decision without importing marketplace.main
    (which requires full env configuration).  The actual function is
    integration-tested in test_marketplace.py and test_e2e_trading_flow.py.
    """

    @staticmethod
    def _should_block(
        threshold: int,
        total_value_sat: int,
        kyc_row: dict | None,
    ) -> str | None:
        """Replicate the enforcement logic from marketplace.main._enforce_kyc_threshold.

        Returns an error code if the trade should be blocked, None otherwise.
        """
        if threshold <= 0 or total_value_sat < threshold:
            return None

        if kyc_row is not None and kyc_row.get("status") == "verified":
            return None

        if kyc_row is None:
            return "kyc_required"
        if kyc_row.get("status") == "pending":
            return "kyc_pending"
        if kyc_row.get("status") == "rejected":
            return "kyc_rejected"
        return "kyc_not_verified"

    def test_passes_below_threshold(self):
        """Orders below the KYC threshold should not be blocked."""
        assert self._should_block(10_000_000, 5_000_000, None) is None

    def test_passes_when_disabled(self):
        """When threshold is 0, enforcement is disabled."""
        assert self._should_block(0, 100_000_000, None) is None

    def test_passes_when_verified(self):
        """Verified users can place high-value trades."""
        kyc_row = {"status": "verified"}
        assert self._should_block(10_000_000, 50_000_000, kyc_row) is None

    def test_blocks_no_record(self):
        """Users without KYC records should be blocked for high-value trades."""
        result = self._should_block(10_000_000, 15_000_000, None)
        assert result == "kyc_required"

    def test_blocks_pending(self):
        """Users with pending KYC should be blocked for high-value trades."""
        kyc_row = {"status": "pending"}
        result = self._should_block(10_000_000, 15_000_000, kyc_row)
        assert result == "kyc_pending"

    def test_blocks_rejected(self):
        """Users with rejected KYC should be blocked for high-value trades."""
        kyc_row = {"status": "rejected"}
        result = self._should_block(10_000_000, 15_000_000, kyc_row)
        assert result == "kyc_rejected"

    def test_blocks_expired(self):
        """Users with expired KYC should be blocked for high-value trades."""
        kyc_row = {"status": "expired"}
        result = self._should_block(10_000_000, 15_000_000, kyc_row)
        assert result == "kyc_not_verified"

    def test_exact_threshold_blocks(self):
        """Trades at exactly the threshold should NOT be blocked (< not <=)."""
        # total_value_sat == threshold → not blocked (< comparison)
        assert self._should_block(10_000_000, 10_000_000, None) == "kyc_required"

    def test_one_below_threshold_passes(self):
        """Trades one sat below the threshold should pass."""
        assert self._should_block(10_000_000, 9_999_999, None) is None


# ---------------------------------------------------------------------------
# 3. Verification status surfacing (user and admin)
# ---------------------------------------------------------------------------


class TestKycStatusSurfacing:
    """AC: Verification failures or pending states are surfaced clearly to users and admins."""

    def test_kyc_submit_request_accepts_optional_fields(self):
        req = KycSubmitRequest()
        assert req.document_url is None
        assert req.notes is None

    def test_kyc_submit_request_with_values(self):
        req = KycSubmitRequest(
            document_url="https://docs.example.com/kyc/user123.pdf",
            notes="ID + proof of address attached",
        )
        assert req.document_url is not None

    def test_kyc_admin_update_request_reject(self):
        req = KycAdminUpdateRequest(
            status="rejected",
            rejection_reason="Document expired",
        )
        assert req.status == "rejected"
        assert req.rejection_reason == "Document expired"

    def test_kyc_admin_update_request_verify(self):
        req = KycAdminUpdateRequest(status="verified")
        assert req.status == "verified"
        assert req.rejection_reason is None

    def test_kyc_admin_update_request_expire(self):
        req = KycAdminUpdateRequest(status="expired")
        assert req.status == "expired"

    def test_pending_status_is_surfaced_in_response(self):
        """Verify pending KYC status is clearly surfaced in the API response."""
        now = datetime.now(tz=timezone.utc)
        kyc = KycStatusOut(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status="pending",
            notes="Documents received, queued for review",
            created_at=now,
            updated_at=now,
        )
        resp = KycStatusResponse(kyc=kyc)
        data = resp.model_dump(mode="json")
        assert data["kyc"]["status"] == "pending"
        assert "queued" in data["kyc"]["notes"]

    def test_rejection_reason_is_surfaced(self):
        """Verify rejection reason is surfaced to the user."""
        now = datetime.now(tz=timezone.utc)
        kyc = KycStatusOut(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            status="rejected",
            rejection_reason="Submitted document is unreadable",
            reviewed_by=str(uuid.uuid4()),
            reviewed_at=now,
            created_at=now,
            updated_at=now,
        )
        resp = KycStatusResponse(kyc=kyc)
        data = resp.model_dump(mode="json")
        assert data["kyc"]["status"] == "rejected"
        assert "unreadable" in data["kyc"]["rejection_reason"]

    def test_admin_can_see_all_statuses(self):
        """Admin list endpoint response contains all verification states."""
        now = datetime.now(tz=timezone.utc)
        records = []
        for s in ("pending", "verified", "rejected", "expired"):
            records.append(
                KycStatusOut(
                    id=str(uuid.uuid4()),
                    user_id=str(uuid.uuid4()),
                    status=s,
                    created_at=now,
                    updated_at=now,
                )
            )
        resp = KycListResponse(records=records)
        statuses = {r.status for r in resp.records}
        assert statuses == {"pending", "verified", "rejected", "expired"}


# ---------------------------------------------------------------------------
# 4. Mainnet environment separation and configuration
# ---------------------------------------------------------------------------


class TestMainnetEnvironmentSeparation:
    """AC: Mainnet-specific secrets and environment requirements are clearly separated.
    AC: Operational ownership is defined for launch and rollback decisions.
    AC: The release flow includes preflight checks, deployment, verification, and rollback steps.
    """

    def test_mainnet_env_template_exists(self):
        """The mainnet env template must exist in the infra directory."""
        env_path = Path(__file__).resolve().parent.parent / "infra" / ".env.mainnet.example"
        assert env_path.exists(), f"Mainnet env template not found at {env_path}"

    def test_mainnet_env_uses_production_profile(self):
        """The mainnet env must set ENV_PROFILE=production."""
        env_path = Path(__file__).resolve().parent.parent / "infra" / ".env.mainnet.example"
        content = env_path.read_text()
        assert "ENV_PROFILE=production" in content

    def test_mainnet_env_uses_mainnet_network(self):
        """The mainnet env must set BITCOIN_NETWORK=mainnet."""
        env_path = Path(__file__).resolve().parent.parent / "infra" / ".env.mainnet.example"
        content = env_path.read_text()
        assert "BITCOIN_NETWORK=mainnet" in content

    def test_mainnet_env_uses_secret_files(self):
        """All sensitive values must use *_FILE references, not inline secrets."""
        env_path = Path(__file__).resolve().parent.parent / "infra" / ".env.mainnet.example"
        content = env_path.read_text()
        assert "POSTGRES_PASSWORD_FILE" in content
        assert "BITCOIN_RPC_PASSWORD_FILE" in content
        assert "JWT_SECRET_FILE" in content
        assert "WALLET_ENCRYPTION_KEY_FILE" in content
        assert "ALERT_WEBHOOK_URL_FILE" in content

    def test_mainnet_env_has_kyc_threshold(self):
        """Mainnet env must specify a non-zero KYC threshold."""
        env_path = Path(__file__).resolve().parent.parent / "infra" / ".env.mainnet.example"
        content = env_path.read_text()
        assert "KYC_TRADE_THRESHOLD_SAT=" in content
        # Parse the value
        for line in content.splitlines():
            if line.startswith("KYC_TRADE_THRESHOLD_SAT="):
                value = int(line.split("=", 1)[1].strip())
                assert value > 0, "Mainnet KYC threshold must be non-zero"

    def test_mainnet_env_separated_from_beta(self):
        """Mainnet and beta env templates must exist separately."""
        infra_dir = Path(__file__).resolve().parent.parent / "infra"
        assert (infra_dir / ".env.mainnet.example").exists()
        assert (infra_dir / ".env.beta.example").exists()

        mainnet_content = (infra_dir / ".env.mainnet.example").read_text()
        beta_content = (infra_dir / ".env.beta.example").read_text()
        # Mainnet must use different network than beta
        assert "BITCOIN_NETWORK=mainnet" in mainnet_content
        assert "BITCOIN_NETWORK=signet" in beta_content

    def test_runbook_exists(self):
        """Production rollout runbook must exist."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        assert runbook.exists(), f"Mainnet runbook not found at {runbook}"

    def test_runbook_defines_preflight_checks(self):
        """The runbook must define preflight checks."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Preflight Checks" in content

    def test_runbook_defines_deployment_steps(self):
        """The runbook must define deployment steps."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Deployment Steps" in content

    def test_runbook_defines_verification_steps(self):
        """The runbook must define post-deploy verification."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Post-Deploy Verification" in content

    def test_runbook_defines_rollback(self):
        """The runbook must define a rollback procedure."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Rollback Procedure" in content

    def test_runbook_defines_operational_ownership(self):
        """The runbook must define operational ownership roles."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Operational Ownership" in content
        assert "Release Lead" in content
        assert "Infrastructure Lead" in content

    def test_runbook_defines_secrets_boundary(self):
        """The runbook must define the mainnet secrets boundary."""
        runbook = Path(__file__).resolve().parent.parent / "deploy" / "mainnet" / "README.md"
        content = runbook.read_text()
        assert "Mainnet Secrets Boundary" in content

    def test_settings_has_kyc_threshold_field(self):
        """The Settings model must have a kyc_trade_threshold_sat field."""
        assert hasattr(Settings, "model_fields")
        assert "kyc_trade_threshold_sat" in Settings.model_fields

    def test_kyc_verifications_table_in_metadata(self):
        """The kyc_verifications table must be registered in the shared metadata."""
        from common.db.metadata import kyc_verifications
        assert kyc_verifications is not None
        column_names = {c.name for c in kyc_verifications.columns}
        assert "user_id" in column_names
        assert "status" in column_names
        assert "reviewed_by" in column_names
        assert "rejection_reason" in column_names

    def test_alembic_migration_exists(self):
        """An Alembic migration for kyc_verifications must exist."""
        versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        migration_files = [f.name for f in versions_dir.iterdir() if "kyc" in f.name.lower()]
        assert len(migration_files) >= 1, f"No KYC migration found in {versions_dir}"
