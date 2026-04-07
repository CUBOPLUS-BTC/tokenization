# Frontend Specification

## 1. Technology Stack

| Layer            | Technology                                        |
| :--------------- | :------------------------------------------------ |
| Framework        | React 18 + TypeScript 5                           |
| Styling          | Tailwind CSS 4 + Shadcn                  |
| State Management | Zustand (global) + React Query (server state)     |
| Routing          | React Router v6                                   |
| Build Tool       | Vite 5                                            |
| Charts           | Recharts or Lightweight Charts (TradingView)      |
| WebSocket        | Native WebSocket with reconnect wrapper            |
| PWA              | Workbox for service worker + offline support       |
| Testing          | Vitest + React Testing Library + Playwright (e2e) |

## 2. Design System

### 2.1 Visual Identity

- **Color Palette**: Dark theme primary (Bitcoin/finance feel)
  - Background: `#0f172a` (slate-900)
  - Surface: `#1e293b` (slate-800)
  - Primary: `#f7931a` (Bitcoin orange)
  - Accent: `#22c55e` (green-500 for positive), `#ef4444` (red-500 for negative)
  - Text: `#f8fafc` (slate-50), `#94a3b8` (slate-400 for secondary)
- **Typography**: Inter (sans-serif), JetBrains Mono (monospace for addresses/hashes)
- **Border Radius**: `8px` standard, `12px` for cards
- **Spacing Scale**: Tailwind default (4px base unit)

### 2.2 Responsive Breakpoints

| Breakpoint | Width     | Target                |
| :--------- | :-------- | :-------------------- |
| `sm`       | ≥ 640px   | Large phones          |
| `md`       | ≥ 768px   | Tablets               |
| `lg`       | ≥ 1024px  | Small desktops        |
| `xl`       | ≥ 1280px  | Standard desktops     |

Mobile-first design. The app functions as a PWA on mobile devices.

## 3. Page Structure & Routes

```
/                           → Landing / Marketing page (public)
/auth/login                 → Login form
/auth/register              → Registration form
/dashboard                  → User dashboard (requires auth)
/wallet                     → Wallet overview
/wallet/deposit             → Deposit (Lightning invoice / on-chain address)
/wallet/withdraw            → Withdraw form
/wallet/history             → Transaction history
/assets                     → Browse tokenized assets (public)
/assets/:id                 → Asset detail page
/assets/submit              → Submit asset for tokenization (seller)
/marketplace                → Order book & trading interface
/marketplace/:tokenId       → Token trading view
/education                  → Course catalog (public)
/education/:courseId         → Course detail / learning interface
/admin                      → Admin dashboard (admin only)
/admin/users                → User management
/admin/disputes             → Dispute resolution
/admin/treasury             → Treasury management
/settings                   → User settings, 2FA, Nostr identity
```

## 4. Page Specifications

### 4.1 Dashboard (`/dashboard`)

Primary hub after login. Displays portfolio summary and recent activity.

**Layout:**
```
┌──────────────────────────────────────────────────┐
│  Header / Nav Bar                                │
├──────────────────────────────────────────────────┤
│  ┌────────────────┐  ┌────────────────────────┐  │
│  │ Total Balance  │  │ Balance Breakdown      │  │
│  │ 1,150,000 sats │  │ On-chain: 500,000     │  │
│  │ ≈ $XXX.XX      │  │ Lightning: 150,000    │  │
│  │                │  │ Tokens: 500,000       │  │
│  └────────────────┘  └────────────────────────┘  │
├──────────────────────────────────────────────────┤
│  Token Portfolio (table)                         │
│  ┌──────────┬────────┬──────────┬──────────────┐ │
│  │ Asset    │ Units  │ Value    │ Change (24h) │ │
│  │ DOB      │ 50     │ 500,000  │ +2.3%        │ │
│  └──────────┴────────┴──────────┴──────────────┘ │
├──────────────────────────────────────────────────┤
│  Recent Activity (last 5 transactions)           │
│  Open Orders (active buy/sell orders)            │
└──────────────────────────────────────────────────┘
```

**Components**: `BalanceCard`, `BalanceBreakdown`, `PortfolioTable`, `ActivityFeed`, `OpenOrdersList`

### 4.2 Wallet (`/wallet`)

**Sections:**
1. **Balance Overview** — sats (on-chain + Lightning) with fiat estimate
2. **Quick Actions** — Deposit, Withdraw, Send payment buttons
3. **Token Balances** — List of owned token types and quantities
4. **Transaction History** — Filterable, paginated list with type icons

**Components**: `BalanceDisplay`, `QuickActionBar`, `TokenBalanceList`, `TransactionTable`, `TransactionFilter`

### 4.3 Asset Detail (`/assets/:id`)

**Sections:**
1. **Header** — Asset name, category badge, status badge
2. **AI Analysis Card** — Score gauge (0-100), risk level, projected ROI, summary
3. **Token Info** (if tokenized) — Supply, unit price, market cap
4. **Documents** — Links to supporting documentation
5. **Trade Button** — Routes to marketplace for this token

**Components**: `AssetHeader`, `AIScoreGauge`, `AIAnalysisPanel`, `TokenInfoCard`, `DocumentList`

### 4.4 Marketplace / Trading View (`/marketplace/:tokenId`)

**Layout:**
```
┌──────────────────────────────────────────────────┐
│  Token Header: Name, Price, 24h Change, Volume   │
├────────────────────────┬─────────────────────────┤
│  Price Chart           │  Order Book             │
│  (candlestick/line)    │  ┌──────┬──────┐       │
│                        │  │ Bids │ Asks │       │
│                        │  │ ...  │ ...  │       │
│                        │  └──────┴──────┘       │
├────────────────────────┼─────────────────────────┤
│  Order Form            │  Recent Trades          │
│  [Buy] [Sell]          │  (time & sales)         │
│  Quantity: ___         │                         │
│  Price: ___            │                         │
│  [Place Order]         │                         │
├────────────────────────┴─────────────────────────┤
│  My Open Orders (for this token)                 │
└──────────────────────────────────────────────────┘
```

**Real-time Updates**: WebSocket subscription for price feed and order book changes.

**Components**: `PriceChart`, `OrderBookPanel`, `OrderForm`, `RecentTradesList`, `MyOrdersTable`

### 4.5 Asset Submission (`/assets/submit`)

**Multi-step Form (Wizard):**

| Step | Content                                                   |
| :--- | :-------------------------------------------------------- |
| 1    | **Basic Info**: Name, description, category dropdown       |
| 2    | **Valuation**: Estimated value (sats), supporting documents upload |
| 3    | **Review**: Summary of all inputs before submission        |
| 4    | **Confirmation**: Asset submitted, pending AI evaluation   |

**Validation**: Client-side with Zod schemas mirroring API validations.

### 4.6 Education Portal (`/education`)

**Sections:**
1. **Course Catalog** — Grid of course cards with category/difficulty filters
2. **My Courses** — Enrolled courses with progress bars (auth required)
3. **Treasury Impact** — Public counter showing total funds allocated to education

**Components**: `CourseGrid`, `CourseCard`, `ProgressBar`, `TreasuryCounter`, `CategoryFilter`

### 4.7 Admin Dashboard (`/admin`)

**Tabs:**
- **Overview** — Platform metrics (users, trades, volume, treasury balance)
- **Users** — Searchable user table with role management
- **Disputes** — Active disputes with resolution workflow
- **Treasury** — Full ledger view with disbursement actions
- **Assets** — Pending assets requiring manual review

## 5. Component Library

### 5.1 Core Components

| Component          | Description                                          |
| :----------------- | :--------------------------------------------------- |
| `Button`           | Primary, secondary, danger variants. Loading state.  |
| `Input`            | Text, number, password. With label, error, helper.   |
| `Select`           | Dropdown with search. Single and multi-select.       |
| `Modal`            | Overlay dialog with close, confirm, cancel.          |
| `Card`             | Elevated surface with header, body, footer slots.    |
| `Badge`            | Status badges (pending, approved, rejected, etc.)    |
| `Table`            | Sortable, paginated, with row actions.               |
| `Toast`            | Success, error, info notifications. Auto-dismiss.    |
| `Skeleton`         | Loading placeholders matching content layout.        |
| `CopyButton`       | Copies text (addresses, hashes) to clipboard.        |

### 5.2 Domain Components

| Component              | Description                                      |
| :--------------------- | :----------------------------------------------- |
| `SatoshiAmount`        | Formats sats with optional fiat conversion       |
| `BitcoinAddress`       | Truncated display with copy + QR code            |
| `LightningInvoice`     | QR code + copy for BOLT11 invoices               |
| `EscrowStatusTracker`  | Step indicator: Created → Funded → Released      |
| `AIScoreGauge`         | Circular gauge (0-100) with color gradient       |
| `OrderBookDepthChart`  | Visual depth chart for bid/ask spread            |

## 6. State Management

### 6.1 Zustand Stores

| Store            | Scope                                                 |
| :--------------- | :---------------------------------------------------- |
| `authStore`      | User session, JWT tokens, 2FA state                   |
| `walletStore`    | Balances, active deposit/withdraw flows                |
| `tradeStore`     | Active order form state, selected token                |
| `notificationStore` | Toast queue, WebSocket notification buffer          |

### 6.2 React Query Keys

```
["wallet"]                    → GET /wallet
["wallet", "transactions"]    → GET /wallet/transactions
["assets", { status, category }] → GET /assets
["asset", assetId]            → GET /assets/:id
["orderbook", tokenId]        → GET /orderbook/:id
["orders", { tokenId, status }]  → GET /orders
["trades", tokenId]           → GET /trades
["courses", { category }]     → GET /courses
["treasury", "summary"]       → GET /treasury/summary
```

Cache invalidation triggers on WebSocket events (order filled, escrow funded, etc.).

## 7. Security (Frontend)

- JWT stored in `httpOnly` cookie (not localStorage) to prevent XSS theft.
- CSRF token required for state-changing requests.
- All user inputs sanitized (DOMPurify) before rendering.
- Content Security Policy headers enforced.
- Sensitive operations (withdraw, trade) trigger 2FA modal.
- Auto-logout after 15 minutes of inactivity.
