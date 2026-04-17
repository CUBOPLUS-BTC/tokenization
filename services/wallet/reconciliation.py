from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import uuid

import grpc
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.config import Settings
from common.metrics import record_business_event
from common.db.metadata import (
    wallets as wallets_table,
    onchain_deposits as deposits_table,
    transactions as transactions_table,
)
from .liquid_rpc import ElementsRPCError, get_liquid_rpc
from .db import (
    list_imported_wallet_addresses,
    list_pending_lightning_receives,
    list_pending_onchain_withdrawals,
    list_wallets,
    recompute_lightning_balance,
    update_transaction_status,
    update_transaction_status_by_txid,
)
from lnd_client import LNDClient

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _confirmation_threshold(settings: Settings) -> int:
    if settings.bitcoin_network == "mainnet":
        return 6
    if settings.bitcoin_network in {"testnet", "signet"}:
        return 3
    return 1


def _to_sats(amount_btc: object) -> int:
    return int(round(float(amount_btc or 0) * 100_000_000))


async def _credit_confirmed_deposit(
    conn: AsyncConnection,
    *,
    deposit_id: uuid.UUID,
    wallet_id: uuid.UUID,
    txid: str,
    amount_sat: int,
    confirmations: int,
) -> bool:
    now = _utc_now()
    credited = await conn.execute(
        sa.update(deposits_table)
        .where(deposits_table.c.id == deposit_id)
        .where(deposits_table.c.status != "credited")
        .values(
            confirmations=confirmations,
            status="credited",
            credited_at=now,
        )
        .returning(deposits_table.c.id)
    )
    if credited.fetchone() is None:
        await conn.rollback()
        return False

    await conn.execute(
        sa.update(wallets_table)
        .where(wallets_table.c.id == wallet_id)
        .values(
            onchain_balance_sat=wallets_table.c.onchain_balance_sat + amount_sat,
            updated_at=now,
        )
    )
    await conn.execute(
        sa.insert(transactions_table).values(
            id=uuid.uuid4(),
            wallet_id=wallet_id,
            type="deposit",
            amount_sat=amount_sat,
            direction="in",
            status="confirmed",
            txid=txid,
            description="On-chain deposit",
            created_at=now,
            confirmed_at=now,
        )
    )
    await conn.commit()
    record_business_event(
        "wallet_onchain_deposit",
        labels={"amount_sat": str(amount_sat), "txid": txid},
    )
    return True


async def reconcile_deposits(engine: AsyncEngine, settings: Settings) -> None:
    liquid_rpc = get_liquid_rpc(settings)
    confirmation_threshold = _confirmation_threshold(settings)

    async with engine.connect() as conn:
        address_rows = await list_imported_wallet_addresses(conn)

    if not address_rows:
        return

    address_map = {
        row.address: {"wallet_address_id": row.id, "wallet_id": row.wallet_id}
        for row in address_rows
    }

    try:
        unspents = await liquid_rpc.listunspent(0, 9_999_999, list(address_map.keys()))
    except ElementsRPCError as exc:
        logger.warning("Deposit reconciliation skipped because Elements RPC is unavailable: %s", exc)
        return

    async with engine.connect() as conn:
        for utxo in unspents:
            address = utxo.get("address")
            txid = utxo.get("txid")
            vout = utxo.get("vout")
            confirmations = int(utxo.get("confirmations", 0) or 0)
            amount_sat = _to_sats(utxo.get("amount", 0))

            if not address or txid is None or vout is None or amount_sat <= 0:
                continue

            address_info = address_map.get(address)
            if address_info is None:
                continue

            existing_result = await conn.execute(
                sa.select(deposits_table)
                .where(deposits_table.c.txid == txid)
                .where(deposits_table.c.vout == vout)
            )
            existing = existing_result.fetchone()

            if existing is None:
                inserted = await conn.execute(
                    sa.insert(deposits_table)
                    .values(
                        id=uuid.uuid4(),
                        wallet_id=address_info["wallet_id"],
                        wallet_address_id=address_info["wallet_address_id"],
                        txid=txid,
                        vout=vout,
                        amount_sat=amount_sat,
                        confirmations=confirmations,
                        status="pending",
                        created_at=_utc_now(),
                    )
                    .returning(deposits_table.c.id, deposits_table.c.wallet_id)
                )
                inserted_row = inserted.fetchone()
                await conn.commit()
                if inserted_row is None:
                    continue

                if confirmations >= confirmation_threshold:
                    await _credit_confirmed_deposit(
                        conn,
                        deposit_id=inserted_row.id,
                        wallet_id=inserted_row.wallet_id,
                        txid=txid,
                        amount_sat=amount_sat,
                        confirmations=confirmations,
                    )
                continue

            existing_status = existing.status
            if confirmations < confirmation_threshold:
                await conn.execute(
                    sa.update(deposits_table)
                    .where(deposits_table.c.id == existing.id)
                    .values(confirmations=confirmations)
                )
                await conn.commit()
                continue

            if existing_status in {"pending", "confirmed"}:
                await _credit_confirmed_deposit(
                    conn,
                    deposit_id=existing.id,
                    wallet_id=existing.wallet_id,
                    txid=txid,
                    amount_sat=existing.amount_sat,
                    confirmations=confirmations,
                )
            else:
                await conn.execute(
                    sa.update(deposits_table)
                    .where(deposits_table.c.id == existing.id)
                    .values(confirmations=confirmations)
                )
                await conn.commit()


async def reconcile_withdrawals(engine: AsyncEngine, settings: Settings) -> None:
    liquid_rpc = get_liquid_rpc(settings)
    confirmation_threshold = _confirmation_threshold(settings)

    async with engine.connect() as conn:
        pending_rows = await list_pending_onchain_withdrawals(conn)

    if not pending_rows:
        return

    async with engine.connect() as conn:
        for row in pending_rows:
            txid = row.txid
            if not txid:
                continue

            try:
                tx = await liquid_rpc.gettransaction(txid)
            except ElementsRPCError as exc:
                logger.warning("Unable to refresh withdrawal %s confirmations: %s", txid, exc)
                continue

            if int(tx.get("confirmations", 0) or 0) < confirmation_threshold:
                continue

            confirmed_at = _utc_now()
            await update_transaction_status_by_txid(
                conn,
                wallet_id=row.wallet_id,
                txid=txid,
                status="confirmed",
                confirmed_at=confirmed_at,
            )
            record_business_event(
                "wallet_onchain_withdrawal_confirmed",
                labels={"txid": txid},
            )


async def sync_wallet_lightning_state(
    conn: AsyncConnection,
    wallet_id: str,
    lnd_client: LNDClient,
) -> int:
    for row in await list_pending_lightning_receives(conn, wallet_id):
        payment_hash = row.ln_payment_hash
        if not payment_hash:
            continue

        try:
            invoice = lnd_client.lookup_invoice(r_hash_str=payment_hash)
        except grpc.RpcError as exc:
            if exc.code() != grpc.StatusCode.NOT_FOUND:
                logger.warning("Unable to refresh Lightning invoice %s: %s", payment_hash, exc)
            continue
        except Exception as exc:
            logger.warning("Unexpected Lightning sync error for invoice %s: %s", payment_hash, exc)
            continue

        if invoice.state == 1 or bool(getattr(invoice, "settle_date", 0)):
            settled_at = getattr(invoice, "settle_date", 0)
            confirmed_at = (
                datetime.fromtimestamp(settled_at, tz=timezone.utc)
                if settled_at
                else _utc_now()
            )
            await update_transaction_status(conn, row.id, "confirmed", confirmed_at=confirmed_at)
        elif invoice.state == 2:
            await update_transaction_status(conn, row.id, "failed")

    return await recompute_lightning_balance(conn, wallet_id)


async def sync_lightning_balance(engine: AsyncEngine, lnd_client: LNDClient) -> None:
    node_local_balance_sat: int | None = None
    try:
        channel_balance = lnd_client.channel_balance()
        node_local_balance_sat = int(getattr(channel_balance.local_balance, "sat", 0))
    except Exception as exc:
        logger.warning("Unable to fetch node-wide Lightning channel balance: %s", exc)

    async with engine.connect() as conn:
        wallet_rows = await list_wallets(conn)

    total_wallet_balance_sat = 0
    async with engine.connect() as conn:
        for wallet in wallet_rows:
            try:
                balance_sat = await sync_wallet_lightning_state(conn, str(wallet.id), lnd_client)
            except Exception as exc:
                logger.warning("Failed to sync Lightning state for wallet %s: %s", wallet.id, exc)
                continue
            total_wallet_balance_sat += balance_sat

    if node_local_balance_sat is not None and total_wallet_balance_sat > node_local_balance_sat:
        logger.warning(
            "Wallet Lightning ledger balance %s exceeds node local balance %s",
            total_wallet_balance_sat,
            node_local_balance_sat,
        )

    record_business_event(
        "wallet_lightning_balance_sync",
        labels={"wallet_count": len(wallet_rows), "total_balance_sat": total_wallet_balance_sat},
    )


async def reconciliation_loop(engine: AsyncEngine, settings: Settings) -> None:
    """Poll Bitcoin Core for imported-address deposits and withdrawal confirmations."""
    interval = 30 if settings.bitcoin_network == "regtest" else 60
    logger.info("Starting on-chain reconciliation loop (interval=%ss)", interval)
    while True:
        try:
            await reconcile_deposits(engine, settings)
            await reconcile_withdrawals(engine, settings)
        except asyncio.CancelledError:
            logger.info("On-chain reconciliation loop cancelled")
            break
        except Exception as exc:
            logger.error("Unhandled exception in on-chain reconciliation loop: %s", exc)
        await asyncio.sleep(interval)


async def lightning_sync_loop(engine: AsyncEngine, lnd_client: LNDClient) -> None:
    """Refresh per-wallet Lightning ledger state from LND invoice/payment status."""
    interval = 30
    logger.info("Starting Lightning wallet sync loop")
    while True:
        try:
            await sync_lightning_balance(engine, lnd_client)
        except asyncio.CancelledError:
            logger.info("Lightning sync loop cancelled")
            break
        except Exception as exc:
            logger.error("Unhandled exception in Lightning sync loop: %s", exc)
        await asyncio.sleep(interval)
