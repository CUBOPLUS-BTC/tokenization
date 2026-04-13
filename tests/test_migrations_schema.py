from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL is required for migration schema tests.", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def _reset_database(engine: sa.Engine) -> None:
    metadata = sa.MetaData()

    with engine.begin() as connection:
        metadata.reflect(bind=connection)
        metadata.drop_all(bind=connection)


def _column_map(inspector: sa.Inspector, table_name: str) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspector.get_columns(table_name)}


def _constraint_names(constraints: list[dict[str, object]]) -> set[str]:
    return {constraint["name"] for constraint in constraints if constraint.get("name")}


def _assert_foreign_key(
    foreign_keys: list[dict[str, object]],
    *,
    name: str,
    constrained_columns: list[str],
    referred_table: str,
    referred_columns: list[str],
) -> None:
    matching_foreign_keys = [foreign_key for foreign_key in foreign_keys if foreign_key.get("name") == name]

    assert len(matching_foreign_keys) == 1

    foreign_key = matching_foreign_keys[0]
    assert foreign_key["name"] == name
    assert foreign_key["constrained_columns"] == constrained_columns
    assert foreign_key["referred_table"] == referred_table
    assert foreign_key["referred_columns"] == referred_columns


@pytest.fixture(scope="module")
def inspector() -> sa.Inspector:
    config = _alembic_config()
    engine = sa.create_engine(DATABASE_URL)

    try:
        _reset_database(engine)
        command.upgrade(config, "head")
        yield sa.inspect(engine)
    finally:
        command.downgrade(config, "base")
        engine.dispose()


def test_target_tables_exist(inspector: sa.Inspector) -> None:
    table_names = set(inspector.get_table_names())

    assert {
        "users",
        "nostr_identities",
        "wallets",
        "transactions",
        "assets",
        "tokens",
        "token_balances",
        "orders",
        "trades",
        "escrows",
        "treasury",
        "courses",
        "enrollments",
    }.issubset(table_names)


def test_users_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "users")
    unique_constraints = _constraint_names(inspector.get_unique_constraints("users"))
    indexes = _constraint_names(inspector.get_indexes("users"))
    checks = _constraint_names(inspector.get_check_constraints("users"))

    assert columns["email"]["nullable"] is True
    assert columns["display_name"]["nullable"] is False
    assert columns["role"]["nullable"] is False
    assert columns["role"]["default"] is not None
    assert columns["is_verified"]["default"] is not None
    assert columns["deleted_at"]["nullable"] is True

    assert "uq_users_email" in unique_constraints
    assert "ix_users_role" in indexes
    assert "ck_users_role_allowed" in checks


def test_nostr_identities_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "nostr_identities")
    unique_constraints = _constraint_names(inspector.get_unique_constraints("nostr_identities"))
    foreign_keys = inspector.get_foreign_keys("nostr_identities")

    assert columns["user_id"]["nullable"] is False
    assert columns["pubkey"]["nullable"] is False
    assert columns["created_at"]["default"] is not None

    assert "uq_nostr_identities_pubkey" in unique_constraints
    _assert_foreign_key(
        foreign_keys,
        name="fk_nostr_identities_user_id_users",
        constrained_columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )


def test_wallets_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "wallets")
    unique_constraints = _constraint_names(inspector.get_unique_constraints("wallets"))
    foreign_keys = inspector.get_foreign_keys("wallets")
    checks = _constraint_names(inspector.get_check_constraints("wallets"))

    assert columns["user_id"]["nullable"] is False
    assert columns["onchain_balance_sat"]["default"] is not None
    assert columns["lightning_balance_sat"]["default"] is not None
    assert columns["encrypted_seed"]["nullable"] is False
    assert columns["derivation_path"]["default"] is not None

    assert "uq_wallets_user_id" in unique_constraints
    assert "ck_wallets_balances_non_negative" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_wallets_user_id_users",
        constrained_columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )


def test_transactions_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "transactions")
    indexes = _constraint_names(inspector.get_indexes("transactions"))
    foreign_keys = inspector.get_foreign_keys("transactions")
    checks = _constraint_names(inspector.get_check_constraints("transactions"))

    assert columns["wallet_id"]["nullable"] is False
    assert columns["type"]["nullable"] is False
    assert columns["amount_sat"]["nullable"] is False
    assert columns["direction"]["nullable"] is False
    assert columns["status"]["default"] is not None
    assert columns["confirmed_at"]["nullable"] is True

    assert {
        "ix_transactions_wallet_id",
        "ix_transactions_type",
        "ix_transactions_status",
        "ix_transactions_created_at",
    }.issubset(indexes)
    assert {
        "ck_transactions_amount_positive",
        "ck_transactions_direction_allowed",
        "ck_transactions_status_allowed",
        "ck_transactions_type_allowed",
    }.issubset(checks)
    _assert_foreign_key(
        foreign_keys,
        name="fk_transactions_wallet_id_wallets",
        constrained_columns=["wallet_id"],
        referred_table="wallets",
        referred_columns=["id"],
    )


def test_assets_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "assets")
    indexes = _constraint_names(inspector.get_indexes("assets"))
    foreign_keys = inspector.get_foreign_keys("assets")
    checks = _constraint_names(inspector.get_check_constraints("assets"))

    assert columns["owner_id"]["nullable"] is False
    assert columns["name"]["nullable"] is False
    assert columns["description"]["nullable"] is False
    assert columns["category"]["nullable"] is False
    assert columns["valuation_sat"]["nullable"] is False
    assert columns["documents_url"]["nullable"] is True
    assert columns["status"]["default"] is not None

    assert {"ix_assets_owner_id", "ix_assets_status", "ix_assets_category"}.issubset(indexes)
    assert {
        "ck_assets_category_allowed",
        "ck_assets_status_allowed",
        "ck_assets_ai_score_range",
    }.issubset(checks)
    _assert_foreign_key(
        foreign_keys,
        name="fk_assets_owner_id_users",
        constrained_columns=["owner_id"],
        referred_table="users",
        referred_columns=["id"],
    )


def test_tokens_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "tokens")
    indexes = _constraint_names(inspector.get_indexes("tokens"))
    unique_constraints = _constraint_names(inspector.get_unique_constraints("tokens"))
    foreign_keys = inspector.get_foreign_keys("tokens")

    assert columns["asset_id"]["nullable"] is False
    assert columns["taproot_asset_id"]["nullable"] is False
    assert columns["total_supply"]["nullable"] is False
    assert columns["circulating_supply"]["default"] is not None
    assert columns["unit_price_sat"]["nullable"] is False
    assert columns["minted_at"]["default"] is not None
    assert columns["created_at"]["default"] is not None
    assert "metadata" in columns
    assert "metadata_json" not in columns

    assert "ix_tokens_asset_id" in indexes
    assert "uq_tokens_taproot_asset_id" in unique_constraints
    _assert_foreign_key(
        foreign_keys,
        name="fk_tokens_asset_id_assets",
        constrained_columns=["asset_id"],
        referred_table="assets",
        referred_columns=["id"],
    )


def test_token_balances_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "token_balances")
    indexes = _constraint_names(inspector.get_indexes("token_balances"))
    unique_constraints = _constraint_names(inspector.get_unique_constraints("token_balances"))
    foreign_keys = inspector.get_foreign_keys("token_balances")
    checks = _constraint_names(inspector.get_check_constraints("token_balances"))

    assert columns["user_id"]["nullable"] is False
    assert columns["token_id"]["nullable"] is False
    assert columns["balance"]["default"] is not None
    assert columns["updated_at"]["default"] is not None

    assert {"ix_token_balances_user_id", "ix_token_balances_token_id"}.issubset(indexes)
    assert "uq_token_balances_user_token" in unique_constraints
    assert "ck_token_balances_balance_non_negative" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_token_balances_user_id_users",
        constrained_columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    _assert_foreign_key(
        foreign_keys,
        name="fk_token_balances_token_id_tokens",
        constrained_columns=["token_id"],
        referred_table="tokens",
        referred_columns=["id"],
    )


def test_orders_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "orders")
    indexes = _constraint_names(inspector.get_indexes("orders"))
    foreign_keys = inspector.get_foreign_keys("orders")
    checks = _constraint_names(inspector.get_check_constraints("orders"))

    assert columns["user_id"]["nullable"] is False
    assert columns["token_id"]["nullable"] is False
    assert columns["side"]["nullable"] is False
    assert columns["quantity"]["nullable"] is False
    assert columns["price_sat"]["nullable"] is False
    assert columns["filled_quantity"]["default"] is not None
    assert columns["status"]["default"] is not None
    assert columns["created_at"]["default"] is not None
    assert columns["updated_at"]["default"] is not None

    assert {"ix_orders_user_id", "ix_orders_token_id"}.issubset(indexes)
    assert {
        "ck_orders_side_allowed",
        "ck_orders_quantity_positive",
        "ck_orders_price_sat_positive",
        "ck_orders_status_allowed",
    }.issubset(checks)
    _assert_foreign_key(
        foreign_keys,
        name="fk_orders_user_id_users",
        constrained_columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    _assert_foreign_key(
        foreign_keys,
        name="fk_orders_token_id_tokens",
        constrained_columns=["token_id"],
        referred_table="tokens",
        referred_columns=["id"],
    )


def test_trades_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "trades")
    indexes = _constraint_names(inspector.get_indexes("trades"))
    foreign_keys = inspector.get_foreign_keys("trades")
    checks = _constraint_names(inspector.get_check_constraints("trades"))

    assert columns["buy_order_id"]["nullable"] is False
    assert columns["sell_order_id"]["nullable"] is False
    assert columns["token_id"]["nullable"] is False
    assert columns["quantity"]["nullable"] is False
    assert columns["price_sat"]["nullable"] is False
    assert columns["total_sat"]["nullable"] is False
    assert columns["fee_sat"]["nullable"] is False
    assert columns["status"]["default"] is not None
    assert columns["created_at"]["default"] is not None
    assert columns["settled_at"]["nullable"] is True

    assert {"ix_trades_token_id", "ix_trades_status"}.issubset(indexes)
    assert "ck_trades_status_allowed" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_trades_buy_order_id_orders",
        constrained_columns=["buy_order_id"],
        referred_table="orders",
        referred_columns=["id"],
    )
    _assert_foreign_key(
        foreign_keys,
        name="fk_trades_sell_order_id_orders",
        constrained_columns=["sell_order_id"],
        referred_table="orders",
        referred_columns=["id"],
    )
    _assert_foreign_key(
        foreign_keys,
        name="fk_trades_token_id_tokens",
        constrained_columns=["token_id"],
        referred_table="tokens",
        referred_columns=["id"],
    )


def test_escrows_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "escrows")
    indexes = _constraint_names(inspector.get_indexes("escrows"))
    unique_constraints = _constraint_names(inspector.get_unique_constraints("escrows"))
    foreign_keys = inspector.get_foreign_keys("escrows")
    checks = _constraint_names(inspector.get_check_constraints("escrows"))

    assert columns["trade_id"]["nullable"] is False
    assert columns["multisig_address"]["nullable"] is False
    assert columns["buyer_pubkey"]["nullable"] is False
    assert columns["seller_pubkey"]["nullable"] is False
    assert columns["platform_pubkey"]["nullable"] is False
    assert columns["locked_amount_sat"]["nullable"] is False
    assert columns["status"]["default"] is not None
    assert columns["expires_at"]["nullable"] is False
    assert columns["created_at"]["default"] is not None
    assert columns["updated_at"]["default"] is not None

    assert "ix_escrows_status" in indexes
    assert "uq_escrows_trade_id" in unique_constraints
    assert "ck_escrows_status_allowed" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_escrows_trade_id_trades",
        constrained_columns=["trade_id"],
        referred_table="trades",
        referred_columns=["id"],
    )


def test_treasury_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "treasury")
    indexes = _constraint_names(inspector.get_indexes("treasury"))
    foreign_keys = inspector.get_foreign_keys("treasury")
    checks = _constraint_names(inspector.get_check_constraints("treasury"))

    assert columns["source_trade_id"]["nullable"] is True
    assert columns["type"]["nullable"] is False
    assert columns["amount_sat"]["nullable"] is False
    assert columns["balance_after_sat"]["nullable"] is False
    assert columns["description"]["nullable"] is True
    assert columns["created_at"]["default"] is not None

    assert {"ix_treasury_type", "ix_treasury_created_at"}.issubset(indexes)
    assert "ck_treasury_type_allowed" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_treasury_source_trade_id_trades",
        constrained_columns=["source_trade_id"],
        referred_table="trades",
        referred_columns=["id"],
    )


def test_courses_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "courses")
    checks = _constraint_names(inspector.get_check_constraints("courses"))

    assert columns["title"]["nullable"] is False
    assert columns["description"]["nullable"] is False
    assert columns["content_url"]["nullable"] is False
    assert columns["category"]["nullable"] is False
    assert columns["difficulty"]["nullable"] is False
    assert columns["is_published"]["default"] is not None
    assert columns["created_at"]["default"] is not None
    assert columns["updated_at"]["default"] is not None

    assert {"ck_courses_category_allowed", "ck_courses_difficulty_allowed"}.issubset(checks)


def test_enrollments_schema_matches_spec(inspector: sa.Inspector) -> None:
    columns = _column_map(inspector, "enrollments")
    unique_constraints = _constraint_names(inspector.get_unique_constraints("enrollments"))
    foreign_keys = inspector.get_foreign_keys("enrollments")
    checks = _constraint_names(inspector.get_check_constraints("enrollments"))

    assert columns["user_id"]["nullable"] is False
    assert columns["course_id"]["nullable"] is False
    assert columns["progress"]["default"] is not None
    assert columns["enrolled_at"]["default"] is not None
    assert columns["completed_at"]["nullable"] is True

    assert "uq_enrollments_user_course" in unique_constraints
    assert "ck_enrollments_progress_range" in checks
    _assert_foreign_key(
        foreign_keys,
        name="fk_enrollments_user_id_users",
        constrained_columns=["user_id"],
        referred_table="users",
        referred_columns=["id"],
    )
    _assert_foreign_key(
        foreign_keys,
        name="fk_enrollments_course_id_courses",
        constrained_columns=["course_id"],
        referred_table="courses",
        referred_columns=["id"],
    )
