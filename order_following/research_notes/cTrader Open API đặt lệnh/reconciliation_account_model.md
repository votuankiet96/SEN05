# cTrader Open API — Reconciliation, Account Model (Netting/Hedging), and Account Info for Money Management

## What is the exact structure/usage of ProtoOAReconcileReq / ProtoOAReconcileRes, and what fields identify each position/order?

### Takeaway
`ProtoOAReconcileReq` returns a single snapshot containing **both** all currently open positions **and** all currently pending orders for an account (two separate repeated fields); each position is keyed by a broker-assigned `positionId` and each order by `orderId`, with symbol, volume, side, and label/comment carried in a shared embedded `ProtoOATradeData` structure. This is an official, documented request/response pair, verified against Spotware's own `.proto` source.

### Cited Findings
- Official description: "ProtoOAReconcileReq: Request for getting Trader's current open positions and pending orders data." — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)
- Verbatim protobuf definition (Spotware's canonical schema repo):
  ```
  message ProtoOAReconcileReq {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_RECONCILE_REQ];
    required int64 ctidTraderAccountId = 2;
    optional bool returnProtectionOrders = 3;
  }
  message ProtoOAReconcileRes {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_RECONCILE_RES];
    required int64 ctidTraderAccountId = 2;
    repeated ProtoOAPosition position = 3;
    repeated ProtoOAOrder order = 4;
  }
  ```
  — [openapi-proto-messages/OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- `ctidTraderAccountId` is documented as "Unique identifier of the trader's account. Used to match responses to trader's accounts." — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)
- `returnProtectionOrders`: "If TRUE, then current protection orders are returned separately, otherwise you can use position.stopLoss and position.takeProfit fields." — meaning by default SL/TP are just fields on the position, not separate order entries; only when this flag is set do they show up as their own `ProtoOAOrder` entries in the `order` list. — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)
- `position` field: "The list of trader's account open positions." `order` field: "The list of trader's account pending orders." — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)
- `ProtoOAPosition` verbatim fields (partial, key ones): `positionId` (int64, required) — "The unique ID of the position. Note: trader might have two positions with the same id if positions are taken from accounts from different brokers."; `tradeData` (required, embeds symbol/volume/side/label/comment); `positionStatus` (required); `swap` (int64, required); `price` (double, optional) — "VWAP price of the position based on all executions (orders) linked to the position."; `stopLoss`/`takeProfit` (double, optional); `usedMargin` (uint64, optional) — "Amount of margin used for the position in deposit currency."; `commission`, `marginRate`, `moneyDigits`, `utcLastUpdateTimestamp` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `ProtoOAOrder` verbatim fields (partial, key ones): `orderId` (int64, required) — same cross-broker-collision caveat as positionId; `tradeData` (required); `orderType` (required); `orderStatus` (required); `executionPrice` (double, optional) — "Price at which an order was executed. For order with FILLED status."; `executedVolume`; `closingOrder` (bool) — "If TRUE then the order is closing part of whole position. Must have specified positionId."; `positionId` (int64, optional) — "ID of the position linked to the order (e.g. closing order, order that increase volume of a specific position, etc.)"; `clientOrderId` (string, optional) — "Optional ClientOrderId. Max Length = 50 chars."; `limitPrice`, `stopPrice`, `stopLoss`, `takeProfit`, `timeInForce` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `ProtoOATradeData` (embedded in both `ProtoOAPosition` and `ProtoOAOrder`) verbatim: `symbolId` (int64, required); `volume` (int64, required) — "Volume in cents (e.g. 1000 in protocol means 10.00 units)."; `tradeSide` (required, BUY/SELL); `openTimestamp`; `label` (string, optional) — "Text label specified during order request."; `comment` (string, optional) — "User-specified comment."; `guaranteedStopLoss`; `measurementUnits`; `closeTimestamp` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Status enums confirming what "pending" vs "open" means: `ProtoOAPositionStatus`: `POSITION_STATUS_OPEN(1)`, `POSITION_STATUS_CLOSED(2)`, `POSITION_STATUS_CREATED(3)` ("Empty position is created for pending order"), `POSITION_STATUS_ERROR(4)`. `ProtoOAOrderStatus`: `ORDER_STATUS_ACCEPTED(1)`, `ORDER_STATUS_FILLED(2)`, `ORDER_STATUS_REJECTED(3)`, `ORDER_STATUS_EXPIRED(4)`, `ORDER_STATUS_CANCELLED(5)` — [Model messages - Open API - cTrader Help](https://help.ctrader.com/open-api/model-messages/)
- Community/official-support confirmation of intended usage: a Spotware forum moderator (amusleh) advised a developer asking "which requests were necessary" to see open orders/positions: use `ProtoOAReconcileReq` for the initial snapshot, then rely on execution events for live changes — [cTrader Forum - Open orders and positions](https://community.ctrader.com/forum/connect-api-support/37328/)
- Companion real-time event for state changes, verbatim: `ProtoOAExecutionEvent { ... required ProtoOAExecutionType executionType; optional ProtoOAPosition position; optional ProtoOAOrder order; optional ProtoOADeal deal; ... optional string errorCode; optional bool isServerEvent; }`, sent "following successful order acceptance or execution by the server," acting as the async response to `ProtoOANewOrderReq`, `ProtoOACancelOrderReq`, `ProtoOAAmendOrderReq`, `ProtoOAClosePositionReq` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto); [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)

### Inferences
- Because `ProtoOAOrder.clientOrderId` is a field on the *order* object only (not reproduced in the verbatim `ProtoOAPosition` field list found), a client-generated correlation ID set on a `ProtoOANewOrderReq` is reliable for matching the originating request to its `ProtoOAExecutionEvent`/fill, but should not be assumed to persist as a queryable field on the resulting position — `label`/`comment` (from the shared `tradeData`) are the fields that travel with the position after execution.
- Since `ProtoOAPosition.price` is explicitly documented as a VWAP "based on all executions (orders) linked to the position," a single `positionId` can represent the accumulation of more than one order/execution over time — a client cannot assume one order = one position 1:1 in all cases (this matters most under netting, see next section).

### Gaps
- The exact full field list of `ProtoOANewOrderReq` (the request used to open new trades) was not independently verbatim-verified in this research pass; only overlapping fields inferred from `ProtoOAOrder`/search summaries were confirmed. The authoritative source for this is [help.ctrader.com/open-api/messages/](https://help.ctrader.com/open-api/messages/) and [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto), which a follow-up pass should fetch directly for the `ProtoOANewOrderReq` message block specifically.

---

## Does cTrader support both netting and hedging account modes? How is this configured, and how does it affect opening a new order on a symbol with an existing position?

### Takeaway
Yes — cTrader officially supports both **Hedging** and **Netting** account types, for both demo and live accounts, but the mode is a **server/broker-level property of the trading account as a whole** (not per-symbol, and not settable via an Open API request). Under hedging, a new order always creates an independent new position (own `positionId`); under netting, a new order on a symbol that already has an open position **merges into that single existing position** (volume-weighted), rather than creating a second `positionId`.

### Cited Findings
- "cTrader supports the two industry-standard types of accounts (for both demo and live): 1. Hedging 2. Netting" — [Trading accounts - cTrader Help](https://help.ctrader.com/ctrader/trading-accounts/)
- "cTrader Open API... is supported by all trading accounts of any cTrader-affiliated brokers... cTrader allows trading with netting or hedging accounts in live or demo modes." — [Getting started - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/); corroborated by Spotware's own product announcement: [cTrader Supports Hedging and Netting Accounts - Spotware](https://www.spotware.com/news/ctrader-supports-hedging-and-netting-accounts/)
- Configuration level: "For live accounts, to change the type of a live trading account, contact your broker." Demo accounts cannot be converted between modes — a new demo account of the desired type must be created instead. This confirms the setting is broker/account-level, outside client-application control. — [Trading accounts - cTrader Help](https://help.ctrader.com/ctrader/trading-accounts/)
- The mode is exposed (read-only) to an Open API client via the account snapshot: verbatim `ProtoOATrader` field — `optional ProtoOAAccountType accountType = 15 [default = HEDGED]; // Account type: HEDGED, NETTED, etc.` with enum `ProtoOAAccountType { HEDGED = 0; // Allows multiple positions on a trading account for a symbol. NETTED = 1; // Only one position per symbol is allowed on a trading account. SPREAD_BETTING = 2; // Spread betting type account. }` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Hedging behavior (official): traders can have "both long and short (buy and sell) positions open for the same symbol at the same time" and "multiple positions of the same type... opened for the same symbol" — each trade is independent. — [Trading accounts - cTrader Help](https://help.ctrader.com/ctrader/trading-accounts/), summarized also at [ClickAlgo - cTrader Hedged vs Netted Accounts](https://clickalgo.com/ctrader-hedged-vs-netted-accounts)
- Netting behavior (official): only "one open position for the same symbol at the same time" is allowed; opposite-direction orders "sell-off each other according to the traded amount," i.e., they reduce/net against the existing position rather than opening a second one. — [Trading accounts - cTrader Help](https://help.ctrader.com/ctrader/trading-accounts/)
- Community confirmation via a live support case: a developer reported that "new orders were merging with existing positions rather than creating separate ones"; the cTrader moderator's diagnostic question was "What is your account type? Netting or hedging?" — the user confirmed their account was "Demo - Netting," which the moderator identified as the direct cause. — [cTrader Forum - How to open multiple positions for same instrument?](https://community.ctrader.com/forum/ctrader-support/25133/)
- A second, independent data point aggregated from search results states plainly: "with cTrader netting accounts, traders hold only one open position per symbol at a time, and subsequent orders are aggregated into the already existent position" (this restates the same official behavior found above; treated as corroborating, not as a distinct primary source).

### Inferences
- Because `accountType` sits on `ProtoOATrader` (obtainable via `ProtoOAReconcileReq`'s sibling `ProtoOATraderReq`/`ProtoOATraderRes`, or pushed via `ProtoOATraderUpdatedEvent` — see Q4/Q5), a client application **can and should read this field at startup** to brancy its own position-tracking logic (treat `positionId` as the unique key under HEDGED, but not rely on `positionId` uniqueness-per-intent under NETTED). No evidence was found of any per-symbol override of account type — it is one setting for the whole account.
- No field was found in any request message (`ProtoOANewOrderReq` or otherwise) that lets a client choose, per order, whether to net or hedge — the merge-vs-separate behavior is entirely a function of the pre-configured, broker-controlled `accountType`, not something togglable through the API. (This is an inference from the absence of such a field in all fetched documentation/proto excerpts, not a documented negative statement — see Gap below.)

### Gaps
- No official documentation was found that explicitly states "there is no API field to override netting/hedging per request" — this is inferred from the absence of such a field across all fetched proto/message documentation, not a directly cited negative confirmation.
- Exact partial-close/reduce semantics in netting mode (e.g., whether a smaller opposite order reduces volume in place with the same `positionId`, versus closing and reopening) were described only qualitatively ("sell-off each other according to the traded amount") — no proto-level worked example was found confirming whether `positionId` is preserved across a partial net-down.

---

## With multiple strategies each wanting an independent position on the same symbol, how does research suggest keeping them distinct under netting vs hedging?

### Takeaway
Under **hedging**, each strategy's order becomes its own position with its own server-assigned `positionId`, so `positionId` is a reliable, natively-distinct key per strategy-intent, and `label`/`comment` can additionally be attached for human-readable/strategy tagging. Under **netting**, the broker enforces a single aggregated position per symbol, so **`positionId` cannot distinguish multiple simultaneous strategy-intents on the same symbol at the API/broker level at all** — this is a hard architectural constraint of netting mode, not a labeling problem, and no source found describes an API-level workaround; the client must maintain its own internal ledger if it needs to track per-strategy "virtual" sub-positions while netting collapses them into one broker-side position.

### Cited Findings
- `ProtoOAAccountType` enum descriptions are the clearest primary-source statement of the constraint: `HEDGED` — "Allows multiple positions on a trading account for a symbol"; `NETTED` — "Only one position per symbol is allowed on a trading account." — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `label` and `comment` are real, documented fields (via `ProtoOATradeData`, "Text label specified during order request" / "User-specified comment") available on both positions and orders in the Open API — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- In cTrader's own **cTrader Algo (cBot)** environment — a different API surface than Open API, used for robots running inside the cTrader platform itself — the `Positions` collection exposes `Find(string label)`, `Find(string label, string symbolName)`, and `Find(string label, string symbolName, TradeType tradeType)` overloads, showing that Spotware's own idiomatic pattern for "which position belongs to which strategy" is filtering by `label` — [Positions - cTrader Algo](https://help.ctrader.com/ctrader-algo/references/Trading/Positions/Positions/). This is included as a design-pattern analogy, not as Open API documentation — it is a **different product/API** than the Open API this research targets, and its own reference page does not discuss netting vs. hedging or multi-strategy scenarios explicitly.
- Netting aggregation is corroborated structurally by `ProtoOAPosition.price` being defined as "VWAP price of the position based on all executions (orders) linked to the position" — i.e., the data model itself represents a netted position as the accumulation of potentially many separate order executions into one object — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- The community support interaction confirming the netting/merge constraint (cited fully in the prior section) did not proceed to discuss label-based workarounds — the thread ended once the user identified their account was in netting mode — [cTrader Forum - How to open multiple positions for same instrument?](https://community.ctrader.com/forum/ctrader-support/25133/)

### Inferences
- **Hedging mode**: `positionId` alone is sufficient and authoritative to keep strategies distinct; `label`/`comment` are a convenience layer on top (e.g., encode `strategy:timeframe` into `label` at order-send time) for human debugging, filtering in UI/logs, or reconstructing "which strategy owns this position" after a restart, since `label` is preserved on the position's `tradeData` and returned by `ProtoOAReconcileReq` on reconnect.
- **Netting mode**: because only one `ProtoOAPosition` per symbol can exist, a client that must honor multiple strategies' independent intents on the same symbol cannot represent that with distinct broker-side objects at all. The practical implications (not found explicitly stated in any source, therefore presented here strictly as this researcher's inference from the confirmed model constraints above, to be validated/decided by the engineering team) are that the client would need to do one of: (a) maintain its own internal per-strategy virtual ledger and reconcile the *sum* of intended strategy exposure against the single real netted position/label, accepting that the broker's `label` field will reflect only whichever order most recently touched the position (the exact overwrite-vs-retain behavior of `label` across a netting merge was not found documented anywhere — see Gap), (b) require strategies that need independent exposure to run on separate hedging-mode accounts, or (c) restrict the system design so only one strategy at a time is allowed to hold a position per symbol when running on a netting account.

### Gaps
- No official documentation or community report was found describing precisely what happens to the `label`/`comment` fields on a `ProtoOAPosition` when a **second** order (potentially carrying a different `label`) merges into an **existing** netted position — e.g., whether the position's label is overwritten by the newest merging order, retained from the original opening order, or something else. This is directly relevant to the user's multi-strategy scenario and should be verified empirically against a demo netting account before relying on `label` for any bookkeeping under netting.
- No source discusses FIFO-specific closing rules for netting accounts at the Open API level (a `wirexltds.com` broker page that appeared in search results promised detail on "cTrader FIFO Netting" but could not be reached — DNS resolution failed during this research pass — so this remains unverified and is likely broker-specific rather than a cTrader platform-wide rule in any case).

---

## What is the structure of ProtoOATrader — does it expose balance, equity, margin used, free margin, leverage? How current is this data, and how should a client refresh it?

### Takeaway
`ProtoOATrader` exposes **balance**, **leverage** (`leverageInCents`, `maxLeverage`), **account type**, and a margin-calculation-method flag — but it does **not** expose equity, aggregate margin-used, free margin, or margin level as ready-made fields; those must be computed client-side from balance plus per-position data and live prices. A client can pull a fresh snapshot on demand via `ProtoOATraderReq`/`ProtoOATraderRes`, and the server also proactively pushes a full updated object via `ProtoOATraderUpdatedEvent` whenever balance-affecting account data changes.

### Cited Findings
- Full verbatim `ProtoOATrader` message:
  ```
  required int64 ctidTraderAccountId = 1;
  required int64 balance = 2; // Current account balance.
  optional int64 balanceVersion = 3; // Increments each time the trader's account balance is changed.
  optional int64 managerBonus = 4;
  optional int64 ibBonus = 5;
  optional int64 nonWithdrawableBonus = 6;
  optional ProtoOAAccessRights accessRights = 7 [default = FULL_ACCESS];
  required int64 depositAssetId = 8;
  optional bool swapFree = 9;
  optional uint32 leverageInCents = 10; // e.g. leverage = 1:50 => value = 5000
  optional ProtoOATotalMarginCalculationType totalMarginCalculationType = 11; // MAX, SUM, NET
  optional uint32 maxLeverage = 12;
  optional bool frenchRisk = 13 [deprecated = true];
  optional int64 traderLogin = 14;
  optional ProtoOAAccountType accountType = 15 [default = HEDGED];
  optional string brokerName = 16;
  optional int64 registrationTimestamp = 17;
  optional bool isLimitedRisk = 18;
  optional ProtoOALimitedRiskMarginCalculationStrategy limitedRiskMarginCalculationStrategy = 19 [default = ACCORDING_TO_LEVERAGE];
  optional uint32 moneyDigits = 20; // exponent scale for balance, managerBonus, ibBonus, nonWithdrawableBonus
  optional bool fairStopOut = 21;
  optional ProtoOAStopOutStrategy stopOutStrategy = 22 [default = MOST_MARGIN_USED_FIRST];
  ```
  — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- No `equity`, `usedMargin` (account-level), `freeMargin`, or `marginLevel` field exists anywhere in the above message — confirmed by direct inspection of the verbatim proto text, and independently by aggregated search summary: "Equity is not provided directly by the API. You need to calculate it yourself using the balance and the P&L of the open positions." — [cTrader Forum - How to get Account Equity](https://community.ctrader.com/forum/connect-api-support/23513/); consistent with [Model messages - Open API - cTrader Help](https://help.ctrader.com/open-api/model-messages/)
- Per-position (not account-aggregate) margin **is** available: `ProtoOAPosition.usedMargin` (uint64, optional) — "Amount of margin used for the position in deposit currency." — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Pre-trade margin estimate is available on demand via a dedicated request: "You can use `ProtoOAExpectedMarginReq` to get the margin estimate according to leverage profiles before sending a new order request." — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/) (via aggregated search)
- On-demand refresh mechanism, verbatim:
  ```
  message ProtoOATraderReq {
    required int64 ctidTraderAccountId = 2;
  }
  message ProtoOATraderRes {
    required int64 ctidTraderAccountId = 2;
    required ProtoOATrader trader = 3;
  }
  ```
  — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- Push refresh mechanism, verbatim:
  ```
  message ProtoOATraderUpdatedEvent {
    required int64 ctidTraderAccountId = 2;
    required ProtoOATrader trader = 3;
  }
  ```
  — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- `totalMarginCalculationType` (MAX/SUM/NET) governs **how margin requirement is computed across a trader's positions/symbols** (a margining-methodology setting) — this is a distinct concept from `accountType` (HEDGED/NETTED/SPREAD_BETTING), which governs **position identity**. Both enums happen to use the word "net"/"netted," which is a likely source of confusion and is worth keeping conceptually separate. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- To compute real-time floating P&L (needed for equity): "you should use ExecutionEvent to get every trade operation on user account, and... subscribe to each position symbol so you will be able to calculate the real time tick value of each symbol for calculating P&L" — aggregated from [help.ctrader.com/open-api/](https://help.ctrader.com/open-api/) and forum guidance.

### Inferences
- "Balance" in `ProtoOATrader` is a **realized** figure (updated only on deposits/withdrawals, closed-trade P&L settlement, bonuses, etc.) — it is not the same as equity and will not reflect floating P&L of currently open positions. Given `balanceVersion` explicitly increments "each time... changed," a client can use it as a cheap dirty-check/idempotency guard when applying `ProtoOATraderUpdatedEvent` pushes out of order.
- Because per-position margin (`usedMargin`) is available and account-level `totalMarginCalculationType` tells you the aggregation rule (MAX/SUM/NET), a client that wants a total "margin used" figure should sum/aggregate `usedMargin` across all currently-open positions from its local reconciled state according to that calculation-type rule, rather than expecting a single ready-made total from the server.

### Gaps
- No official documentation was found giving the precise arithmetic definition of how `totalMarginCalculationType`'s MAX/SUM/NET modes combine per-position `usedMargin` into an account total — only that these three modes exist. A client needing an exact total-margin/free-margin/margin-level number should treat this as unverified and confirm with the broker or by empirical testing.
- No explicit statement was found on how frequently/under what exact triggers `ProtoOATraderUpdatedEvent` fires beyond "the trader's account balance is changed" (from `balanceVersion`'s description) — e.g., whether bonus changes, leverage changes, or accessRights changes also trigger it was not confirmed either way in the sources gathered.

---

## Is there a way to subscribe to real-time updates of account equity/margin, or must the client poll?

### Takeaway
There is **no server-side subscription or event that emits a ready-made "equity" or "free margin" number** — because these aren't fields the server stores. However, the client is not limited to naive polling either: **balance** changes are pushed automatically (`ProtoOATraderUpdatedEvent`), **per-position margin** changes are pushed automatically (`ProtoOAMarginChangedEvent`), and **live prices** are pushed automatically once subscribed (`ProtoOASpotEvent`) — so a client should compute equity/aggregate-margin continuously by combining these three push-based streams rather than polling `ProtoOATraderReq` on a timer.

### Cited Findings
- Verbatim event definitions confirming push (not poll) delivery for the pieces that do exist server-side:
  ```
  message ProtoOAMarginChangedEvent {
    required int64 ctidTraderAccountId = 2;
    required uint64 positionId = 3;
    required uint64 usedMargin = 4;
    optional uint32 moneyDigits = 5;
  }
  ```
  "The `ProtoOAMarginChangedEvent` is sent every time when the amount of margin allocated to a specific position is changed." — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto); [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/) (via aggregated search)
  ```
  message ProtoOATraderUpdatedEvent {
    required int64 ctidTraderAccountId = 2;
    required ProtoOATrader trader = 3;
  }
  ```
  — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- Live price subscription (needed to compute floating P&L component of equity): "To get the latest bid/ask prices, subscribe to `ProtoOASubscribeSpotsReq`. If the subscription is successful, you will receive a first `ProtoOASpotEvent` with the latest spot prices and then you will continue receiving price updates as new events are generated." Verbatim request: `message ProtoOASubscribeSpotsReq { required int64 ctidTraderAccountId = 2; repeated int64 symbolId = 3; optional bool subscribeToSpotTimestamp = 4; }` — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/); [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- Direct statement that equity requires computation, not subscription: "Equity is not provided by the API. You need to calculate it yourself using the balance and the P&L of the open positions." — [cTrader Forum - How to get Account Equity](https://community.ctrader.com/forum/connect-api-support/23513/)
- The official Open API FAQ page was checked directly and **does not** contain any entry about equity/margin subscription, confirming this isn't a documented, discoverable feature via the FAQ — [FAQ - Open API - cTrader Help](https://help.ctrader.com/open-api/faq/) (negative finding — page fetched and inspected, topic absent)

### Inferences
- The practical architecture implied by the confirmed pieces is: subscribe once to spots for every symbol with an open (or about-to-be-opened) position, keep a local position/order book updated via `ProtoOAExecutionEvent`, keep `usedMargin` per position updated via `ProtoOAMarginChangedEvent`, keep `balance` updated via `ProtoOATraderUpdatedEvent`, and **recompute** equity = balance + Σ(unrealized P&L per open position) and total margin = aggregate of per-position `usedMargin` (per the account's `totalMarginCalculationType`) on every relevant tick or event. This is event-driven/real-time in the sense that no request needs to be re-sent, but it is "poll" in the sense that the final equity/margin numbers are always locally derived, never handed to the client as a single field.

### Gaps
- No source was found confirming whether `ProtoOAMarginChangedEvent` requires any explicit subscription step beyond normal account authentication, or whether it is delivered unconditionally to every authenticated session for that account — this should be verified empirically or via direct SDK behavior before relying on it as a silent assumption.

---

## What is the recommended reconciliation pattern on startup vs. periodic re-reconciliation, and does the community report drift/inconsistency issues?

### Takeaway
The only explicit guidance found (from official cTrader forum support staff) is: call `ProtoOAReconcileReq` **once** after connecting/authenticating to get the starting snapshot, then keep state current by handling `ProtoOAExecutionEvent` for every subsequent trading operation — there is no official documentation recommending a periodic/timer-based re-reconciliation while the connection stays alive. No community reports of position/order **drift or inconsistency bugs** specifically were found in this research pass; the closest related finding is that reconnection/resync logic is explicitly left entirely to the developer, with no built-in platform guarantee, which is a reason a defensive implementation would still re-run `ProtoOAReconcileReq` after every reconnect (as opposed to on a background timer).

### Cited Findings
- Direct official-support guidance: in response to "What requests are necessary to get open orders/positions," Spotware forum moderator amusleh recommended: "For initial data retrieval: Use the reconcile request at the `ProtoOAReconcileReq` endpoint to obtain existing orders and positions. For ongoing updates: Implement the execution event mechanism to receive live, real-time changes to trading activity." — [cTrader Forum - Open orders and positions](https://community.ctrader.com/forum/connect-api-support/37328/)
- On connection-state monitoring, the same kind of official-support answer states there is no built-in reconnect/resync API: "you have to use your socket and check if you can send/receive a message or not, there is also a client disconnected proto message that you can use," and the thread explicitly does **not** provide guidance on reconnection procedures or state re-synchronization strategies following a disconnect, "leaving those implementation details to developers." — [cTrader Forum - Real-time connection status monitoring & position open/close events](https://community.ctrader.com/forum/connect-api-support/36663/)
- The same thread confirms `ExecutionEvent` is comprehensive for state changes post-reconciliation: it covers not just opens, but "take-profit adjustments, stop-loss changes, and partial position closures" — [cTrader Forum - Real-time connection status monitoring & position open/close events](https://community.ctrader.com/forum/connect-api-support/36663/)
- The official Open API FAQ page does not contain any entry addressing reconciliation cadence, drift, or periodic re-sync best practice — checked directly, topic absent. — [FAQ - Open API - cTrader Help](https://help.ctrader.com/open-api/faq/)
- A general (non-reconciliation-specific) documentation-quality complaint was found on Spotware's own proto-schema GitHub repo: a developer reported that error codes are "not clear or well documented," citing ambiguity between `ProtoOAErrorRes`/`ProtoErrorRes` and two separate error-code enums (`ProtoOAErrorCode` vs `ProtoErrorCode`), with a real example of receiving an undocumented `CANT_ROUTE_REQUEST` code during broker maintenance. This does **not** discuss position/order drift specifically, but is evidence of a broader pattern of gaps in edge-case documentation that a robust reconciliation/error-handling layer should defend against. — [GitHub Issue #30 - openapi-proto-messages](https://github.com/spotware/openapi-proto-messages/issues/30)
- A third-party (non-Spotware) Python wrapper library, `ctrader-api-client`, documents its own client-side design choice to remember each account's active subscriptions and automatically re-apply them after a reconnect, emitting its own `SubscriptionRestoreFailedEvent` if any restoration step fails and preserving the original intent so the next reconnection attempt retries — this is that specific community library's engineered behavior, **not** a native Open API server feature or official Spotware recommendation. — [ctrader-api-client on PyPI](https://pypi.org/project/ctrader-api-client/0.10.0/) (sourced from a search-result summary only; a direct fetch of this page failed during this research pass, so treat this citation as lower-confidence pending direct verification of the library's own docs/source)

### Inferences
- Synthesizing the individually-confirmed pieces (no single official page was found laying out the full sequence end-to-end), the implied startup/crash-recovery pattern is: `ProtoOAApplicationAuthReq` → `ProtoOAAccountAuthReq` → `ProtoOAReconcileReq` (full fresh snapshot — mandatory after any crash/restart since all local state is lost) → `ProtoOASubscribeSpotsReq` for every symbol now relevant → thereafter rely on `ProtoOAExecutionEvent` (+ `ProtoOAMarginChangedEvent`, `ProtoOATraderUpdatedEvent`) until the next disconnect, at which point `ProtoOAReconcileReq` should be re-run again before trusting local state. This is this researcher's synthesis, not a single cited authoritative sequence — see Gap below.
- Given that (a) no official source recommends periodic on-a-timer re-reconciliation while connected, and (b) no community bug reports of drift were found in this pass, the evidence leans toward "re-reconcile only on (re)connect, trust events while connected" as the documented/supported pattern — but this is a "no evidence found either way" situation for the periodic case, not a confirmed "periodic reconciliation is unnecessary" guarantee. A production client handling real money should treat periodic re-reconciliation (e.g., every N minutes) as a cheap defensive measure against unknown edge cases (missed events during a silent/undetected socket stall, the documented error-code ambiguities noted above, etc.) even though no source explicitly mandates it.

### Gaps
- No single official document was found that lays out the full connect → auth → reconcile → subscribe → event-loop sequence in one place; this was assembled from separate message-level docs and a forum answer, not one authoritative walkthrough. The official "Getting Started" page ([help.ctrader.com/open-api/](https://help.ctrader.com/open-api/)) references the relevant message names but was confirmed (by direct fetch) not to spell out ordering or reconnection guidance; a dedicated `/open-api/trading/` help page returned HTTP 404 during this research pass and could not be checked.
- No community forum threads or GitHub issues were found that specifically report **position/order state drift or inconsistency** between local reconciled state and broker state (e.g., "my position count was wrong after reconnect," "reconcile returned stale data," etc.). This may mean such issues are rare/undocumented publicly, or simply that they weren't surfaced by the search queries used — this should be treated as an open question rather than evidence of an absence of the problem.
- Whether `ProtoOAReconcileRes` can, in practice, return a stale snapshot (e.g., a race with an in-flight execution) is not addressed in any source found; no documented guidance exists on how to safely merge a `ProtoOAReconcileRes` snapshot with any `ProtoOAExecutionEvent`s that might arrive concurrently around the same time (e.g., via sequence numbers or timestamps) — a client should treat this as an unresolved correctness question worth testing empirically (e.g., using `utcLastUpdateTimestamp` fields defensively) rather than assuming atomicity.
