# Nostr Zap Campaigns Specification

## Objective

Allow a platform user to create an advertising campaign that listens for Nostr activity matching configured identifiers or tags and automatically sends zaps to qualifying participants until the campaign budget is exhausted.

The campaign must support two funding modes:

- `intraledger`: the user funds the campaign from the platform internal wallet.
- `external`: the user funds the campaign by paying a Lightning invoice from an external wallet into a campaign-specific balance controlled by the platform.

## Current State Review

After reviewing the codebase, the important findings are:

- `services/nostr/main.py` only publishes internal platform events to relays and publishes classifieds. It does not subscribe to relay events.
- `services/nostr/relay_client.py` only sends WebSocket messages. It does not keep long-lived subscriptions, handle `REQ`, `EVENT`, `EOSE`, or relay reconnect state for inbound traffic.
- `services/wallet/main.py` can already create and pay Lightning invoices, but `POST /lightning/payments` is a user-triggered endpoint that enforces user auth and optional 2FA per payment.
- `services/auth` already maps Nostr pubkeys to platform users through `nostr_identities`.
- There is no schema for campaigns, campaign balances, Nostr match deduplication, or zap executions.

Conclusion: the current Nostr service is only an outbound publisher. The requested feature needs a new inbound relay consumer, a campaign ledger, and a controlled way for the Nostr service to spend pre-authorized campaign funds.

## Key Product Decision

The `external wallet` mode cannot mean "the backend spends directly from an arbitrary wallet the user owns outside the platform" unless the user delegates custody or an API that can sign outgoing payments, which the current platform does not support and should not assume.

The safe implementation is:

- `intraledger`: move sats from the user's internal wallet into a reserved campaign balance.
- `external`: create a campaign funding Lightning invoice and require the user to pay that invoice from any external wallet.

In both modes, once funded, the platform pays zaps from its own Lightning node against a campaign-scoped reserved balance.

## Recommended Service Ownership

### Nostr service owns

- Campaign CRUD
- Relay subscriptions and event ingestion
- Trigger matching
- Deduplication
- Zap request construction
- Campaign execution worker
- Campaign payout state machine

### Wallet service owns

- Campaign funding reservation from internal wallet
- Campaign funding invoices for external wallet top-ups
- Actual Lightning payment execution
- Financial transaction ledger entries

## Proposed Architecture

### 1. Campaign lifecycle

1. User creates draft campaign.
2. User defines:
   - trigger type: hashtag, exact tag, text identifier, author pubkey list, event kind list
   - reward per unique user in sats
   - total budget in sats
   - optional max rewards per user
   - start/end time
   - funding source
3. User funds campaign:
   - `intraledger`: reserve sats from internal wallet in one operation authorized with 2FA.
   - `external`: platform issues Lightning invoice; campaign becomes active after invoice settlement.
4. Nostr worker subscribes to relays and scans events.
5. When an event matches campaign rules:
   - normalize and validate event
   - compute match fingerprint
   - enforce deduplication and per-user limits
   - reserve reward amount from campaign available balance
   - resolve recipient zap endpoint
   - build NIP-57 zap request
   - request invoice from recipient LNURL-pay endpoint
   - pay invoice from platform LND
   - persist receipt/outcome
6. When available balance is below reward threshold or end time is reached, campaign is paused/completed.

### 2. Runtime components inside `services/nostr/`

- `db.py`
  - campaign queries and state transitions
- `campaign_schemas.py`
  - campaign API models
- `relay_subscriber.py`
  - long-lived relay subscriptions, reconnects, `REQ/CLOSE`
- `matcher.py`
  - evaluate whether a Nostr event matches campaign rules
- `zap_client.py`
  - NIP-57 flow: LNURL discovery, zap request event, invoice callback, receipt parsing
- `campaign_worker.py`
  - processes matched events and executes payouts safely
- `wallet_client.py`
  - internal HTTP client to wallet service for reserve/fund/pay operations

## Data Model

Add these tables to shared metadata.

### `nostr_campaigns`

- `id`
- `user_id`
- `name`
- `status` with values `draft`, `funding_pending`, `active`, `paused`, `completed`, `exhausted`, `cancelled`, `failed`
- `funding_mode` with values `intraledger`, `external`
- `reward_amount_sat`
- `budget_total_sat`
- `budget_reserved_sat`
- `budget_spent_sat`
- `budget_refunded_sat`
- `max_rewards_per_user`
- `start_at`
- `end_at`
- `created_at`
- `updated_at`

### `nostr_campaign_triggers`

- `id`
- `campaign_id`
- `trigger_type` with values `hashtag`, `tag`, `content_substring`, `author_pubkey`, `event_kind`
- `operator` with values `equals`, `contains`, `in`
- `value`
- `case_sensitive`
- `created_at`

### `nostr_campaign_fundings`

- `id`
- `campaign_id`
- `wallet_id` nullable for external mode before user mapping
- `funding_mode`
- `amount_sat`
- `status` with values `pending`, `confirmed`, `cancelled`, `refunded`
- `ln_payment_hash` nullable
- `transaction_id` nullable
- `created_at`
- `confirmed_at`

### `nostr_campaign_matches`

- `id`
- `campaign_id`
- `relay_url`
- `event_id`
- `event_pubkey`
- `event_kind`
- `match_fingerprint`
- `status` with values `matched`, `ignored`, `reserved`, `paid`, `failed`
- `ignore_reason` nullable
- `created_at`

Recommended unique constraints:

- unique `(campaign_id, event_id)`
- unique `(campaign_id, match_fingerprint)`

`match_fingerprint` should usually be based on `(campaign_id, recipient_pubkey, normalized_trigger_value)` so the same user cannot farm repeated payouts unless the campaign explicitly allows it.

### `nostr_campaign_payouts`

- `id`
- `campaign_id`
- `match_id`
- `recipient_pubkey`
- `recipient_lud16` nullable
- `recipient_lud06` nullable
- `zap_request_event_id`
- `zap_invoice`
- `payment_hash`
- `amount_sat`
- `fee_sat`
- `status` with values `pending`, `succeeded`, `failed`
- `failure_reason` nullable
- `created_at`
- `settled_at`

## Wallet Changes

The current wallet API is not enough for bot execution because every outgoing payment is treated as a direct user action.

### Add internal-only wallet capabilities

#### 1. Reserve campaign balance from internal wallet

`POST /internal/campaign-funds/reserve`

Behavior:

- validate authenticated internal caller
- move `amount_sat` from `wallets.lightning_balance_sat` into campaign reserved balance
- create ledger entries
- idempotent by request key

#### 2. Create campaign funding invoice

`POST /internal/campaign-funds/invoice`

Behavior:

- create Lightning invoice tied to `campaign_id`
- persist pending funding record
- when settled, credit campaign reserved balance

#### 3. Pay Lightning invoice from campaign balance

`POST /internal/campaign-funds/pay`

Behavior:

- validate campaign has enough available reserved balance
- decode invoice first
- optionally enforce max fee policy
- pay via LND
- deduct settled amount plus fee from campaign reserved balance
- create financial ledger row linked to campaign payout
- idempotent by `campaign_id + payment_hash` or caller idempotency key

### Ledger recommendation

Do not overload only `wallets.lightning_balance_sat` for campaign accounting. Keep campaign money separately auditable.

Recommended additions:

- `campaign_reserved_balance_sat` tracked in `nostr_campaigns`
- wallet transaction descriptions including `campaign_id`
- optional new transaction type `campaign_funding` and `campaign_payout`

## Nostr Relay Ingestion

### Required behavior

- connect to multiple relays
- issue `REQ` with filters derived from active campaigns
- reconnect automatically
- checkpoint processed positions or rely on dedupe keys in DB
- validate event shape before matching

### Recommended first-phase filters

Start with only public notes and hashtags:

- `kind = 1`
- optional `#t` hashtag filters when possible

This keeps the first release small and avoids DMs/reposts/reactions until the pipeline is stable.

## Zap Execution Flow

To send real zaps, implement NIP-57-compatible behavior.

### Happy path

1. Find recipient profile metadata event and extract `lud16` or `lud06`.
2. Resolve LNURL-pay endpoint.
3. Build zap request event containing:
   - recipient pubkey
   - amount
   - referenced event id when applicable
   - relays list
4. Call LNURL callback with zap request and amount.
5. Receive BOLT11 invoice.
6. Decode invoice and verify:
   - amount matches expected reward
   - destination is present
   - invoice not expired
7. Pay via wallet internal campaign endpoint.
8. Persist payment hash and mark payout succeeded.

### Failure cases

- no `lud16/lud06`
- LNURL callback unavailable
- invalid invoice
- payment failure
- campaign out of funds during concurrent execution

All failures should be persisted without retry storms.

## Abuse Controls

This feature is highly abuse-prone. Minimum controls:

- one reward per recipient pubkey by default
- optional one reward per event id and one reward per author per campaign window
- minimum account age on Nostr metadata if available
- relay allowlist
- max sat per campaign
- max sat per payout
- max payouts per minute per campaign
- cooldown per pubkey
- manual pause switch
- dry-run mode before activation
- full audit log for every reserve, match, ignore, pay, refund

## Idempotency and Concurrency

Critical rule: matching and payout must be two-phase.

### Phase A: reserve

- lock campaign row
- ensure `available_balance >= reward_amount_sat`
- insert match row if not already seen
- move match status to `reserved`

### Phase B: execute

- request zap invoice
- pay invoice
- on success mark payout `succeeded` and increment `budget_spent_sat`
- on failure release reservation back to available campaign balance

Without this, concurrent relay workers can overspend the campaign.

## API Surface

Recommended public endpoints in `services/nostr/main.py`:

- `POST /campaigns`
- `GET /campaigns`
- `GET /campaigns/{campaign_id}`
- `POST /campaigns/{campaign_id}/activate`
- `POST /campaigns/{campaign_id}/pause`
- `POST /campaigns/{campaign_id}/cancel`
- `POST /campaigns/{campaign_id}/fund/intraledger`
- `POST /campaigns/{campaign_id}/fund/external`
- `GET /campaigns/{campaign_id}/matches`
- `GET /campaigns/{campaign_id}/payouts`

## Event Topics

Add Redis topics for observability and optional realtime UI:

- `nostr.campaign.created`
- `nostr.campaign.activated`
- `nostr.campaign.funded`
- `nostr.campaign.match_detected`
- `nostr.campaign.payout.succeeded`
- `nostr.campaign.payout.failed`
- `nostr.campaign.exhausted`

## Recommended Rollout Plan

### Phase 1

- Campaign CRUD
- Internal wallet reservation for `intraledger`
- External funding invoice
- Relay subscriber for hashtag matching on kind `1`
- One reward per pubkey
- Lightning payment without full NIP-57 receipt publishing

### Phase 2

- Full NIP-57 zap request flow
- Better relay filtering and reconnects
- Match analytics and campaign dashboard
- Refund remaining balance on cancel/completion

### Phase 3

- Advanced trigger DSL
- Per-campaign relay selection
- Anti-sybil heuristics
- Optional admin review queues for high-budget campaigns

## Minimal Code Change Map

### `services/nostr/`

- extend `main.py` with campaign endpoints and worker startup
- add DB module and campaign worker modules
- replace publish-only relay connector with publish + subscribe support

### `services/wallet/`

- add internal campaign funding and payout endpoints
- add campaign-aware ledger entries
- add idempotent reserve/release semantics

### `services/common/db/metadata.py`

- add campaign-related tables

### `alembic/versions/`

- add migration for all new campaign tables and constraints

## Recommendation

Implement the feature in the Nostr service, but do not let the Nostr service spend directly from user wallets on demand. Require campaigns to be pre-funded into a campaign-controlled reserved balance first. That single decision keeps the system compatible with the current wallet model, avoids repeated 2FA prompts, and makes the payout worker deterministic and auditable.
