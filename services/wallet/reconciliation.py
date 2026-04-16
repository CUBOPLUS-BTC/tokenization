import asyncio
import logging
from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from common.config import Settings
from common.metrics import record_business_event
from common.db.metadata import (
    wallets as wallets_table,
    wallet_addresses as wallet_addresses_table,
    onchain_deposits as deposits_table,
    transactions as transactions_table
)
from .bitcoin_rpc import get_bitcoin_rpc
from .lnd_client import LNDClient

logger = logging.getLogger(__name__)

async def reconcile_deposits(engine: AsyncEngine, settings: Settings) -> None:
    bitcoin_rpc = get_bitcoin_rpc(settings)
    
    # Threshold based on network
    conf_threshold = 1 if settings.bitcoin_network == "regtest" else 3
    if settings.bitcoin_network == "mainnet":
        conf_threshold = 6

    try:
        async with engine.connect() as conn:
            # 1. Query all imported addresses
            result = await conn.execute(
                sa.select(wallet_addresses_table.c.id, wallet_addresses_table.c.address, wallet_addresses_table.c.wallet_id)
                .where(wallet_addresses_table.c.imported_to_node == True)
            )
            addresses_map = {row.address: {"id": row.id, "wallet_id": row.wallet_id} for row in result}
            
            if not addresses_map:
                return

            # 2. Query listunspent from Bitcoin Core for these addresses
            # listunspent(minconf, maxconf, addresses)
            # We want all unspent, even 0-conf to track pending
            unspents = await bitcoin_rpc.listunspent(0, 9999999, list(addresses_map.keys()))

            now = datetime.now(timezone.utc)

            for utxo in unspents:
                address = utxo.get("address")
                txid = utxo.get("txid")
                vout = utxo.get("vout")
                amount_btc = utxo.get("amount", 0)
                amount_sat = int(amount_btc * 100_000_000)
                confirmations = utxo.get("confirmations", 0)

                address_info = addresses_map.get(address)
                if not address_info:
                    continue
                
                wallet_id = address_info["wallet_id"]
                wallet_address_id = address_info["id"]

                # 3. Check if deposit already exists
                dep_result = await conn.execute(
                    sa.select(deposits_table.c.id, deposits_table.c.status)
                    .where(deposits_table.c.txid == txid)
                    .where(deposits_table.c.vout == vout)
                )
                existing_deposit = dep_result.fetchone()

                if not existing_deposit:
                    # Insert as pending
                    await conn.execute(
                        sa.insert(deposits_table)
                        .values(
                            id=uuid.uuid4(),
                            wallet_id=wallet_id,
                            wallet_address_id=wallet_address_id,
                            txid=txid,
                            vout=vout,
                            amount_sat=amount_sat,
                            confirmations=confirmations,
                            status="pending" if confirmations < conf_threshold else "confirmed",
                            created_at=now,
                        )
                    )
                    await conn.commit()
                else:
                    # Update confirmations if changed
                    # (Simplified: we just update it if we queried it, or only if it crossed threshold)
                    deposit_id = existing_deposit.id
                    deposit_status = existing_deposit.status
                    
                    if deposit_status == "pending" and confirmations >= conf_threshold:
                        # Transaction gets confirmed!
                        # Credit balance and update stats atomically
                        
                        await conn.execute(
                            sa.update(deposits_table)
                            .where(deposits_table.c.id == deposit_id)
                            .where(deposits_table.c.status == "pending")
                            .values(
                                confirmations=confirmations,
                                status="credited",
                                credited_at=now
                            )
                        )
                        
                        # Add to wallet onchain balance
                        await conn.execute(
                            sa.update(wallets_table)
                            .where(wallets_table.c.id == wallet_id)
                            .values(onchain_balance_sat=wallets_table.c.onchain_balance_sat + amount_sat)
                        )
                        
                        # Add transaction ledger entry
                        await conn.execute(
                            sa.insert(transactions_table)
                            .values(
                                id=uuid.uuid4(),
                                wallet_id=wallet_id,
                                type="deposit",
                                amount_sat=amount_sat,
                                direction="in",
                                status="confirmed",
                                txid=txid,
                                description=f"On-chain deposit",
                                created_at=now,
                                confirmed_at=now,
                            )
                        )
                        await conn.commit()
                        
                        logger.info("Credited deposit %s:%s for %s sats to wallet %s", txid, vout, amount_sat, wallet_id)
                        record_business_event("wallet_onchain_deposit", metadata={"amount_sat": amount_sat, "txid": txid})
                    else:
                        # Just update confirmations
                        await conn.execute(
                            sa.update(deposits_table)
                            .where(deposits_table.c.id == deposit_id)
                            .values(confirmations=confirmations)
                        )
                        await conn.commit()

    except Exception as exc:
        logger.error("Error during deposit reconciliation: %s", exc)

async def reconciliation_loop(engine: AsyncEngine, settings: Settings) -> None:
    """Background task to poll Bitcoin RPC for deposits periodically."""
    interval = 30 if settings.bitcoin_network == "regtest" else 60
    logger.info("Starting deposit reconciliation loop (interval=%ss)", interval)
    while True:
        try:
            await reconcile_deposits(engine, settings)
        except asyncio.CancelledError:
            logger.info("Deposit reconciliation loop cancelled")
            break
        except Exception as e:
            logger.error("Unhandled exception in reconciliation loop: %s", e)
        await asyncio.sleep(interval)

async def sync_lightning_balance(engine: AsyncEngine, lnd_client: LNDClient) -> None:
    try:
        # LND is the source of truth for real Lightning balance
        resp = lnd_client.channel_balance()
        # Lightning balance = sum of local channel balances
        ln_balance = resp.local_balance.sat
        
        # In a hybrid model, we synchronize it to the wallets table as a cached view.
        # Assuming single-tenant or shared platform wallet for now:
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(wallets_table)
                .values(lightning_balance_sat=ln_balance)
            )
    except Exception as exc:
        logger.warning("Failed to sync Lightning balance: %s", exc)

async def lightning_sync_loop(engine: AsyncEngine, lnd_client: LNDClient) -> None:
    """Background task to periodically sync Lightning balance from LND."""
    interval = 30
    logger.info("Starting Lightning sync loop")
    while True:
        try:
            await sync_lightning_balance(engine, lnd_client)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Unhandled exception in LN sync loop: %s", e)
        await asyncio.sleep(interval)
