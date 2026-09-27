# cTrader Open API — ProtoOANewOrderReq Field Reference & TimeInForce Default Behavior

**Methodology note:** For this topic I did not rely only on search-engine summaries. I downloaded the two primary protobuf schema files directly from Spotware's official GitHub repo (`spotware/openapi-proto-messages`, `main` branch, fetched 2026-09-17) and read them in full (not AI-summarized). This is the single most authoritative source available and it directly resolves the critical open question. All proto excerpts below are verbatim from those files unless marked otherwise.

---

## Key Question 1: Complete field list of ProtoOANewOrderReq (type, required/optional, per orderType applicability)

### Takeaway
`ProtoOANewOrderReq` is defined in **proto2** syntax (not proto3), with 21 fields beyond the envelope `payloadType`. Only `ctidTraderAccountId`, `symbolId`, `orderType`, `tradeSide`, and `volume` are protobuf-`required`; every other field (including `timeInForce`, `limitPrice`, `stopPrice`, `stopLoss`, `takeProfit`, etc.) is protobuf-`optional`, with applicability to specific order types enforced by server-side business logic and documented only in field comments, not by the schema itself.

### Cited Findings
Verbatim field list from `OpenApiMessages.proto` (lines 82–107), fetched directly from the raw GitHub source:

```protobuf
message ProtoOANewOrderReq {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_NEW_ORDER_REQ];

    required int64 ctidTraderAccountId = 2; // The unique identifier of the trader's account in cTrader platform.
    required int64 symbolId = 3; // The unique identifier of a symbol in cTrader platform.
    required ProtoOAOrderType orderType = 4; // The type of an order - MARKET, LIMIT, STOP, MARKET_RANGE, STOP_LIMIT.
    required ProtoOATradeSide tradeSide = 5; // The trade direction - BUY or SELL.
    required int64 volume = 6; // The volume represented in 0.01 of a unit (e.g. 1000 in protocol means 10.00 units).
    optional double limitPrice = 7; // The limit price, can be specified for the LIMIT order only.
    optional double stopPrice = 8; // Stop Price, can be specified for the STOP and the STOP_LIMIT orders only.
    optional ProtoOATimeInForce timeInForce = 9 [default = GOOD_TILL_CANCEL]; // The specific order execution or expiration instruction - GOOD_TILL_DATE, GOOD_TILL_CANCEL, IMMEDIATE_OR_CANCEL, FILL_OR_KILL, MARKET_ON_OPEN.
    optional int64 expirationTimestamp = 10; // The Unix time in milliseconds of Order expiration. Should be set for the Good Till Date orders.
    optional double stopLoss = 11; // The absolute Stop Loss price (1.23456 for example). Not supported for MARKET orders.
    optional double takeProfit = 12; // The absolute Take Profit price (1.23456 for example). Unsupported for MARKET orders.
    optional string comment = 13; // User-specified comment. MaxLength = 512.
    optional double baseSlippagePrice = 14; // Base price to calculate relative slippage price for MARKET_RANGE order.
    optional int32 slippageInPoints = 15; // Slippage distance for MARKET_RANGE and STOP_LIMIT order.
    optional string label = 16; // User-specified label. MaxLength = 100.
    optional int64 positionId = 17; // Reference to the existing position if the Order is intended to modify it.
    optional string clientOrderId = 18; // Optional user-specific clientOrderId (similar to FIX ClOrderID). MaxLength = 50.
    optional int64 relativeStopLoss = 19; // Relative Stop Loss ... Specified in 1/100000 of unit of a price ... For BUY stopLoss = entryPrice - relativeStopLoss, for SELL stopLoss = entryPrice + relativeStopLoss.
    optional int64 relativeTakeProfit = 20; // Relative Take Profit ... Specified in 1/100000 of unit of a price ... For BUY takeProfit = entryPrice + relativeTakeProfit, for SELL takeProfit = entryPrice - relativeTakeProfit.
    optional bool guaranteedStopLoss = 21; // If TRUE then stopLoss is guaranteed. Required to be set to TRUE for the Limited Risk accounts (ProtoOATrader.isLimitedRisk=true).
    optional bool trailingStopLoss = 22; // If TRUE then the Stop Loss is Trailing.
    optional ProtoOAOrderTriggerMethod stopTriggerMethod = 23 [default = TRADE]; // Trigger method for the STOP or the STOP_LIMIT pending order.
}
```
— [OpenApiMessages.proto, spotware/openapi-proto-messages, GitHub](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)

- The publicly rendered docs page mirrors this field list (same names/types/MaxLength notes) but renders `timeInForce` simply as "Optional" and does **not** surface the `[default = GOOD_TILL_CANCEL]` annotation anywhere in the table — the default is only visible in the raw `.proto` source, not the rendered documentation. — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)
- The live docs page's wording for `guaranteedStopLoss` differs slightly from the GitHub `main` branch comment: docs say *"Available if GSL execution policy is not 'Disabled' for this symbol or group execution profile; Required to be set TRUE for the Limited Risk accounts when the symbol has GSL enabled,"* vs. the GitHub proto comment *"If TRUE then stopLoss is guaranteed. Required to be set to TRUE for the Limited Risk accounts (ProtoOATrader.isLimitedRisk=true)."* This indicates the live help-docs description text and the public GitHub repo comment text can drift out of sync — treat the GitHub repo as canonical for field existence/type/number, but be aware wording nuance may differ from the current production server's actual business rules. — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/); [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- Per-order-type applicability is stated only in comments, not enforced in the schema type system: `limitPrice` → "LIMIT order only"; `stopPrice` → "STOP and STOP_LIMIT orders only"; `stopLoss`/`takeProfit` → "Not supported for MARKET orders"; `baseSlippagePrice` → "MARKET_RANGE order"; `slippageInPoints` → "MARKET_RANGE and STOP_LIMIT order"; `stopTriggerMethod` → "STOP or STOP_LIMIT pending order." — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)

### Inferences
- Because this is proto2 (not proto3), field presence ("has this field been set on the wire") is distinguishable from a field being absent, and proto2 lets the schema author declare an explicit per-field default that generated code returns when the field is absent. This is architecturally different from proto3's implicit zero-value defaulting, and it is directly relevant to Key Question 4 below.
- The schema gives no machine-checkable mapping of "which timeInForce values are legal for which orderType" — that logic must live in the trading server and is not visible from the .proto file alone. Any such mapping should be treated as business-rule knowledge, not a documented contract, unless found stated explicitly (see Key Question 3).

### Gaps
- No field-level protobuf validation constraints (e.g., min/max) beyond `MaxLength` comments for strings; actual server-side enforcement of these MaxLengths was not independently verified (no forum/GitHub confirmation found of what error is thrown if `label` > 100 chars or `comment` > 512 chars).

---

## Key Question 2: All enum values of OrderType and TimeInForce exactly as defined in the current protobuf schema

### Takeaway
`ProtoOAOrderType` has 6 values (not 5 — the key-questions brief's list of MARKET/LIMIT/STOP/STOP_LIMIT/MARKET_RANGE omits `STOP_LOSS_TAKE_PROFIT`, which does exist in the enum). `ProtoOATimeInForce` has exactly the 5 values named in the brief, and enum value 1 (the lowest-numbered entry, though **not** index/value 0) is `GOOD_TILL_DATE`, not `GOOD_TILL_CANCEL`.

### Cited Findings
Verbatim from `OpenApiModelMessages.proto`:

```protobuf
/** Order type ENUM. */
enum ProtoOAOrderType {
    MARKET = 1;
    LIMIT = 2;
    STOP = 3;
    STOP_LOSS_TAKE_PROFIT = 4;
    MARKET_RANGE = 5;
    STOP_LIMIT = 6;
}

/** Order's time in force ENUM. */
enum ProtoOATimeInForce {
    GOOD_TILL_DATE = 1;
    GOOD_TILL_CANCEL = 2;
    IMMEDIATE_OR_CANCEL = 3;
    FILL_OR_KILL = 4;
    MARKET_ON_OPEN = 5;
}
```
— [OpenApiModelMessages.proto, spotware/openapi-proto-messages, GitHub](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)

- Both enums start numbering at **1**, not 0. Proto2 (unlike proto3) does not require a zero-value entry, and this schema does not define one for either enum. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `ProtoOAOrderStatus` (for completeness, since it appears throughout `ProtoOAOrder`): `ORDER_STATUS_ACCEPTED=1`, `ORDER_STATUS_FILLED=2`, `ORDER_STATUS_REJECTED=3`, `ORDER_STATUS_EXPIRED=4` ("Order expired. Might be valid for orders with partially filled volume that were expired on LP"), `ORDER_STATUS_CANCELLED=5`. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `ProtoOAExecutionType` includes `ORDER_EXPIRED = 6; // Order with GTD time in force is expired.` — this comment explicitly ties the "expired" execution event to GTD specifically (not to GTC, IOC, or FOK), which is indirect corroboration that GTC orders are not expected to auto-expire. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)

### Inferences
- Since the task brief assumed "proto3 default enum value is typically the zero-value entry" as the mechanism to identify the default — this assumption does not apply here because the schema is proto2, and proto2 enums here have no zero-value member at all. The actual mechanism is the explicit `[default = X]` annotation on the field declaration (see Key Question 4), not enum-ordinal position.
- `STOP_LOSS_TAKE_PROFIT` (value 4) is very likely a system/internally-generated order type representing the automatic closing order created when a position's SL or TP is hit (this is supported by `ProtoOAOrder.trailingStopLoss` comment: "Valid for STOP_LOSS_TAKE_PROFIT order"), rather than an order type a client is expected to submit directly via `ProtoOANewOrderReq`. This is an inference, not a directly documented statement.

### Gaps
- No explicit statement found (in schema comments, official docs, or forum posts) confirming or denying whether a client is permitted to submit `orderType = STOP_LOSS_TAKE_PROFIT` in `ProtoOANewOrderReq`. Given `ProtoOANewOrderReq`'s own field comment for `orderType` only lists "MARKET, LIMIT, STOP, MARKET_RANGE, STOP_LIMIT" (omitting `STOP_LOSS_TAKE_PROFIT`), the strong implication is that it is excluded from client-initiated new orders, but this was not independently confirmed via a validation-error report or SDK enforcement code.

---

## Key Question 3: Precise semantics of each TimeInForce value and which order types support which values

### Takeaway
The schema comments give only short generic definitions with no order-type support matrix. The clearest per-order-type mapping found comes from cTrader's **FIX API** documentation/forum (a related but distinct protocol/interface from the Open API), which should be treated as strongly suggestive but not proven-identical for the Open API.

### Cited Findings
- Schema-level description of the full set of values (all attached to the `timeInForce` field comment): "The specific order execution or expiration instruction - GOOD_TILL_DATE, GOOD_TILL_CANCEL, IMMEDIATE_OR_CANCEL, FILL_OR_KILL, MARKET_ON_OPEN." No further per-value elaboration exists in either `.proto` file. — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- From the cTrader **FIX API** forum thread "Take profit & stop loss implementation with Limit & Stop orders," a message-constructor code sample posted by user massimiliano.quarti states (FIX numeric tag values, which use the *standard FIX protocol* TimeInForce codes — 1=GTC, 3=IOC, 6=GTD — and are **not** the same numeric codes as `ProtoOATimeInForce`): *"3 = Immediate Or Cancel (IOC), it will be active only for Market Orders"*; *"1 = Good Till Cancel (GTC), it will be processed by 'Good Till Cancel' scheme; it will be active only for Limit Orders"*; *"6 = Good Till Date (GTD), it will be active only if ExpireTime is defined. GTD has a high priority, so if ExpireTime is defined, GTD will be used for the Order processing."* — [cTrader Forum - Take profit & stop loss implementation with Limit & Stop orders](https://community.ctrader.com/forum/fix-api/11964/)
- A separate forum thread ("expiration time in ms on limit/stop orders") shows a user testing a `PlaceStopOrderAsync()` call with a 500ms expiry expecting immediate cancellation-or-fill; the order was instead accepted and filled 2.5 seconds later, and the user concluded: *"From what I understand the 'expiry date' is just a time where, if the order has not been hit yet, it will be cancelled."* Spotware staff did not give a detailed technical rebuttal or confirmation in that thread — they asked for more code/details and ultimately redirected the user to their broker. — [cTrader Forum - expiration time in ms on limit/stop orders](https://community.ctrader.com/forum/cbot-support/6678/)
- General cTrader order-behavior documentation (platform-level, not confirmed to be Open-API-specific) states pending orders without an expiry "remain active until they are triggered by the price or cancelled manually," and that an order triggered shortly before its expiry "will still be sent for execution and may be filled even after the selected expiry time." — [Orders - cTrader Help](https://help.ctrader.com/ctrader/trading/orders/) (via search snippet; page not independently fetched in full, so treat this specific wording as unverified against the live page)

### Inferences
- If the FIX-API behavior generalizes to the Open API (plausible since both interfaces likely sit in front of the same underlying trade-matching/order-management engine, but **not confirmed**), the practical rule would be: **IOC is only meaningful/valid for MARKET-type execution**, **GTC is the natural default for resting LIMIT/STOP orders**, and **setting an expiration causes GTD semantics to take priority regardless of the nominal timeInForce value sent**. This would mean, for a STOP order, that omitting `timeInForce` and also omitting `expirationTimestamp` should fall back to whatever the request-level default is (see Key Question 4) — i.e., GTC-like, resting-until-cancelled behavior.
- `MARKET_ON_OPEN` is very likely intended for exchange-traded instruments with a defined market open (e.g., equities/futures on cTrader's institutional offering) and largely inapplicable to 24-hour FX/CFD symbols — this is an inference from the name alone; no source explicitly restricts it this way.

### Gaps
- **No official Open-API-specific (protobuf) documentation or forum post was found that states an explicit compatibility matrix of "OrderType × TimeInForce"** (e.g., confirming whether `FILL_OR_KILL` or `MARKET_ON_OPEN` are accepted at all for STOP/STOP_LIMIT/LIMIT orders on the Open API, or whether the server silently overrides/rejects an invalid combination). This is a genuine unresolved gap — the FIX API forum quote above is the closest analogous evidence found, and it is explicitly for a different API/protocol.
- No source clarified precise semantic distinction between `FILL_OR_KILL` and `IMMEDIATE_OR_CANCEL` beyond the standard industry meaning (FOK = fill the entire volume immediately or cancel the whole order; IOC = fill whatever quantity is immediately available and cancel/cancel the remainder) — cTrader-specific documentation of this distinction was not found; this is standard FIX/trading terminology, not a cTrader-sourced claim.

---

## Key Question 4 (CRITICAL): What does the API do if timeInForce is left unset/omitted in ProtoOANewOrderReq?

### Takeaway
**The schema itself gives a direct, authoritative answer at the protobuf-definition level: `ProtoOANewOrderReq.timeInForce` is declared as `optional ProtoOATimeInForce timeInForce = 9 [default = GOOD_TILL_CANCEL];` in proto2 syntax.** This is an explicit, named default (not an inferred proto3 zero-value), meaning that when the field is absent from the wire message, protobuf-generated getters on the receiving side return `GOOD_TILL_CANCEL`. However, **no direct empirical (forum/GitHub) report was found of someone deliberately omitting `timeInForce` entirely (as opposed to setting it explicitly) on a live/demo STOP order and reporting the observed resulting behavior** — this specific empirical test is a genuine gap.

### Cited Findings

**(a) What the schema states (primary/official source — highest confidence):**
- Verbatim, from the live `main` branch of Spotware's official proto repo, `OpenApiMessages.proto` line 92: `optional ProtoOATimeInForce timeInForce = 9 [default = GOOD_TILL_CANCEL]; // The specific order execution or expiration instruction - GOOD_TILL_DATE, GOOD_TILL_CANCEL, IMMEDIATE_OR_CANCEL, FILL_OR_KILL, MARKET_ON_OPEN.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- The file's `syntax` declaration is `syntax = "proto2";` (line 1 of both `.proto` files), confirming this is proto2, where `[default = ...]` is an explicit, first-class schema feature (distinct from proto3's implicit zero-value defaulting that the research brief hypothesized). — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto); [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- **Important internal inconsistency found:** the *response-side* `ProtoOAOrder` message (used in `ProtoOAExecutionEvent`, `ProtoOAReconcileRes`, `ProtoOAOrderListRes`, etc. — i.e., what you read back to see an order's actual state) declares its own `timeInForce` field with a **different** default: `optional ProtoOATimeInForce timeInForce = 18 [default = IMMEDIATE_OR_CANCEL]; // Order's time in force. Depends on order type.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto). This means the *request* schema's undeclared default (GOOD_TILL_CANCEL) and the *echoed-back order entity* schema's undeclared default (IMMEDIATE_OR_CANCEL) are **not the same value**. This is a real, verifiable discrepancy in the public schema, not a research artifact.
- The rendered public documentation (`help.ctrader.com/open-api/messages/`) does **not** surface either default at all — it lists `timeInForce` simply as "Optional" with no default value column/note. So a developer relying only on the docs website (not the raw `.proto` file) would have no way to learn the default from Spotware's own documentation site. — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/)

**(b) What the community has empirically observed (secondary/anecdotal):**
- The most relevant forum thread found, "TimeInForce parameter ignored when placing orders" (community.ctrader.com, OpenAPI.Net / .NET 6.0 context): user **jkurrat** reported: *"all orders are GTC orders, regardless of whether I set the TimeInForce parameter to ImmediateOrCancel, GoodTillDate."* This was for **Limit orders**, and jkurrat had explicitly *set* (not omitted) `TimeInForce` to `ImmediateOrCancel` or `GoodTillDate`. Spotware community moderator **PanagiotisChar** replied with working sample code and the remark *"Make sure your timestamp is correct, it should be in UTC time,"* demonstrating a correct `ExpirationTimestamp` set via `.ToUnixTimeMilliseconds()`. jkurrat confirmed: *"Thank you. It is working now."* — [cTrader Forum - TimeInForce parameter ignored when placing orders](https://community.ctrader.com/forum/connect-api-support/42171/)
  - **Root-cause caveat:** this thread's resolution suggests the underlying bug was a malformed/incorrect `expirationTimestamp` (not UTC, or otherwise invalid) causing the server to reject/ignore the GTD request and fall back to a GTC-like resting order — it does **not** by itself prove what happens when `timeInForce` is completely unset/absent from the message (jkurrat was always explicitly setting the field, just with a bad companion timestamp in some attempts). This thread is evidence that "malformed GTD request → falls back to GTC-like behavior" in practice, which is at least *consistent with* GOOD_TILL_CANCEL being the server's effective fallback/default — but it is not a clean test of the omitted-field scenario specifically.
- No GitHub issue was found (searched OpenApiPy, OpenAPI.Net, and general cTrader Open API GitHub repos) with a report specifically describing: "I did not set timeInForce at all on a STOP order and here is what happened on demo/live." This exact empirical test does not appear to be documented publicly anywhere found in this research pass.
- A community-maintained documentation site (`m-ahmadi.github.io/ctoa`, "cTrader OpenAPI Community Docs") exists and has a "Placing Orders" section referenced in its navigation, but the introduction/landing page itself contains no default-value or timeInForce-omission content; the specific sub-page was not reached in this pass. — [Introduction | cTrader OpenAPI Community Docs](https://m-ahmadi.github.io/ctoa/)

**(c) What remains genuinely unknown:**
- Whether the live trading server's matching/order-management engine actually implements the `[default = GOOD_TILL_CANCEL]` protobuf annotation faithfully at the business-logic layer (i.e., whether server-side code reads the field via a generated getter that honors the proto default, versus checking a "has field" flag and applying separate/different fallback logic) is **not independently confirmed** by any source found. The protobuf default is a strong, official signal of intended behavior, but protobuf wire-format defaulting is technically a code-generation/deserialization concern, and a server's business logic could in principle branch on `hasTimeInForce()` rather than trust the typed getter's default. No source discusses this distinction explicitly for cTrader's server implementation.
- No confirmation was found either way for whether an **omitted** `timeInForce` on a **STOP** order specifically behaves identically to an omitted `timeInForce` on a **LIMIT** order (i.e., whether the GOOD_TILL_CANCEL default is uniformly applied across all pending order types, or whether STOP orders have different fallback behavior tied to `ORDER_STATUS_EXPIRED`/`ORDER_EXPIRED` semantics, which the schema explicitly ties only to GTD: *"Order with GTD time in force is expired"* — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)).

### Inferences
- Combining (a) the explicit `[default = GOOD_TILL_CANCEL]` schema annotation and (b) the community forum's observation that malformed/failed GTD attempts fell back to GTC-like resting behavior, the weight of evidence points toward: **if `timeInForce` is omitted from `ProtoOANewOrderReq`, the practical, most-likely outcome is that the order behaves as GOOD_TILL_CANCEL** (rests indefinitely until filled or manually cancelled) rather than expiring or being immediately cancelled. This is the best-supported conclusion from available evidence, but it should be labeled **"strongly indicated by official schema, not independently empirically proven for the omission case specifically."**
- Because of the `ProtoOAOrder.timeInForce` default mismatch (`IMMEDIATE_OR_CANCEL`) noted above, if a project reads back an order via `ProtoOAReconcileRes`/`ProtoOAExecutionEvent`/`ProtoOAOrderListRes` and finds `order.timeInForce` unset in the deserialized object (i.e., relies on a generated getter rather than checking `hasTimeInForce()`), it could misleadingly appear as `IMMEDIATE_OR_CANCEL` even though the original request defaulted to (or explicitly requested) `GOOD_TILL_CANCEL`. Any reconciliation logic in the downstream project should explicitly check field-presence (`has_time_in_force` / `HasTimeInForce()` depending on SDK) rather than trust the bare getter value, precisely because of this cross-message default inconsistency.

### Gaps
- **No concrete, empirical, publicly-reported test of the exact scenario "timeInForce field completely omitted on a STOP order, observed on demo/live account" was found.** This is exactly the piece of evidence the task asked to prioritize finding, and despite multiple targeted search queries (forum-specific and GitHub-issue-specific), it was not located. This should be treated as an open item — if the downstream project needs certainty, the only way to close this gap conclusively is to run the empirical test directly (send a `ProtoOANewOrderReq` for a STOP order with `timeInForce` unset on a demo account and observe the returned `ProtoOAOrder.timeInForce`/expiry behavior over time), since no third party appears to have published this test's results.
- Whether Spotware's server-side implementation treats "field absent" identically to "field explicitly set to GOOD_TILL_CANCEL" was not confirmed from any changelog, release note, or engineering blog post — none were found addressing this at all.

---

## Key Question 5: GOOD_TILL_DATE requirements — is expirationTimestamp mandatory, what unit, and what happens if set without GTD?

### Takeaway
`expirationTimestamp` is Unix time in **milliseconds** (confirmed directly in the schema comment, not seconds), and the field comment says it "should be set for the Good Till Date orders" — a business-rule expectation, not a protobuf-enforced requirement (the field remains `optional` in the schema). The one concrete related-system data point found (from the FIX API, not Open API) suggests that **setting an expiration timestamp causes GTD handling to take priority over whatever TimeInForce value was nominally sent** — but this was not confirmed for the Open API specifically.

### Cited Findings
- Verbatim schema comment: `optional int64 expirationTimestamp = 10; // The Unix time in milliseconds of Order expiration. Should be set for the Good Till Date orders.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- The unit is corroborated by a forum reply from Spotware community moderator PanagiotisChar showing working C# sample code computing the value via `((DateTimeOffset)DateTime.Now.AddDays(1)).ToUnixTimeMilliseconds()`, i.e., milliseconds since epoch, and stressing *"Make sure your timestamp is correct, it should be in UTC time."* — [cTrader Forum - TimeInForce parameter ignored when placing orders](https://community.ctrader.com/forum/connect-api-support/42171/)
- `ProtoOAOrder.expirationTimestamp` (the read-back/order-entity field) has the comment: *"The Unix time in milliseconds of expiration if the order has time in force GTD."* — again explicitly milliseconds, and explicitly tied only to the GTD case. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- There is a dedicated `ProtoOAErrorCode` entry `TRADING_BAD_EXPIRATION_DATE = 130; // Invalid expiration.` in the schema, confirming the server does perform validation specifically on expiration values and can reject a request over it — though the exact trigger conditions (e.g., expiration in the past, expiration without GTD, expiration too far in the future) are not enumerated anywhere in the schema comments. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- From the FIX-API forum code sample (different protocol, cited fully under Key Question 3): *"6 = Good Till Date (GTD), it will be active only if ExpireTime is defined. GTD has a high priority, so if ExpireTime is defined, GTD will be used for the Order processing."* This directly implies (for the FIX API) that supplying an expiry date causes GTD to be applied **regardless of** the TimeInForce tag's nominal value — i.e., expirationTimestamp is not silently ignored when TimeInForce ≠ GTD; instead, in this related system, expiration presence overrides/upgrades the effective TimeInForce to GTD. — [cTrader Forum - Take profit & stop loss implementation with Limit & Stop orders](https://community.ctrader.com/forum/fix-api/11964/)
- A different forum thread shows a live behavioral quirk relevant to GTD/expiry precision: a user set a STOP order to expire 500ms in the future expecting fast rejection/cancellation, but it was instead accepted and filled 2.5 seconds after being placed — Spotware support did not give a detailed technical explanation of this discrepancy in the thread. — [cTrader Forum - expiration time in ms on limit/stop orders](https://community.ctrader.com/forum/cbot-support/6678/)

### Inferences
- `expirationTimestamp` is very likely **not enforced as mandatory at the protobuf/schema level** for `timeInForce = GOOD_TILL_DATE` (it stays `optional` in the message definition), but is likely enforced as a **business-logic requirement** server-side — i.e., sending `timeInForce = GOOD_TILL_DATE` without an `expirationTimestamp` most plausibly triggers a rejection via `TRADING_BAD_EXPIRATION_DATE` (130) or a generic validation error, though no source explicitly confirms this exact error code fires in that exact scenario.
- Given the FIX-API "GTD has high priority" finding, the most defensible working assumption for the Open API is that **setting `expirationTimestamp` while `timeInForce` is NOT `GOOD_TILL_DATE` is likely either (a) silently ignored (timestamp has no effect because the order isn't GTD) or (b) implicitly promotes the order to GTD-like expiring behavior** — but which of these two outcomes actually occurs on the Open API was **not confirmed** by any source found.

### Gaps
- No Open-API-specific source (schema comment, official doc, forum post, or GitHub issue) explicitly states what happens if `expirationTimestamp` is set while `timeInForce` is `GOOD_TILL_CANCEL`, `IMMEDIATE_OR_CANCEL`, `FILL_OR_KILL`, or `MARKET_ON_OPEN`. This is an explicit, named gap — flagged rather than guessed, per instructions.
- No source confirms whether there is a maximum allowed expiration horizon (e.g., analogous to the `2147483646000` / "19th Jan 2038" ceiling seen on several other timestamp fields in the schema, such as `toTimestamp` in deal/order list requests) that also applies to `expirationTimestamp`. The `2147483646000` ceiling is explicitly documented for unrelated history-query fields (e.g., `ProtoOADealListReq.toTimestamp`) but was not seen documented for `expirationTimestamp` specifically. — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)

---

## Key Question 6: stopLoss/takeProfit (absolute) vs relativeStopLoss/relativeTakeProfit (relative) — can both be set, what takes precedence?

### Takeaway
Both pairs of fields exist simultaneously on `ProtoOANewOrderReq` as independent optional fields with no protobuf-level mutual exclusion (e.g., no `oneof` grouping them). The schema documents their individual computation formulas precisely, but **no source found states what happens if both the absolute and relative version are set on the same request simultaneously** — this is a confirmed gap, not an assumption.

### Cited Findings
- `stopLoss`: `optional double stopLoss = 11; // The absolute Stop Loss price (1.23456 for example). Not supported for MARKET orders.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- `takeProfit`: `optional double takeProfit = 12; // The absolute Take Profit price (1.23456 for example). Unsupported for MARKET orders.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- `relativeStopLoss`: `optional int64 relativeStopLoss = 19; // Relative Stop Loss that can be specified instead of the absolute as one. Specified in 1/100000 of unit of a price. (e.g. 123000 in protocol means 1.23, 53423782 means 534.23782) For BUY stopLoss = entryPrice - relativeStopLoss, for SELL stopLoss = entryPrice + relativeStopLoss.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) — note the field comment itself explicitly says "instead of the absolute one," i.e., Spotware's own documentation frames these as alternative/mutually-substitutive ways to express the same concept, not fields meant to be combined.
- `relativeTakeProfit`: `optional int64 relativeTakeProfit = 20; // Relative Take Profit that can be specified instead of the absolute one. Specified in 1/100000 of unit of a price. (e.g. 123000 in protocol means 1.23, 53423782 means 534.23782) For BUY takeProfit = entryPrice + relativeTakeProfit, for SELL takeProfit = entryPrice - relativeTakeProfit.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- A forum search on precedence when both are set returned no explicit statement; the closest related thread ("How to exactly calculate relativeTakeProfit or relativeStopLoss") and another ("How to setup stop loss when opening a Market order") were identified but did not surface (via search snippet) any explicit precedence rule when queried. — [cTrader Forum - How to exactly calculate relativeTakeProfit or relativeStopLoss](https://community.ctrader.com/forum/connect-api-support/37262/); [cTrader Forum - How to setup stop loss when opening a Market order](https://community.ctrader.com/forum/connect-api-support/38778/)
- The relative fields are integer (`int64`) and scaled by 1/100000 of a price unit, whereas the absolute fields are `double` prices directly — meaning the two representations use entirely different numeric encodings, which is useful for a client library to distinguish "which one was actually set" (e.g., via `has_stop_loss` vs. `has_relative_stop_loss` presence checks) but does not by itself reveal precedence. — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)

### Inferences
- The phrase "instead of the absolute as one" / "instead of the absolute one" in both relative-field comments is the strongest textual signal available, and it implies Spotware's intended usage pattern is **either/or, not both** — i.e., a client should pick one mechanism per order, not populate both. This is an inference from wording, not a stated precedence rule for the both-set case.
- Given `relativeStopLoss`/`relativeTakeProfit` are computed *from the entry price* (`entryPrice - relativeStopLoss` for BUY, etc.), and the entry price for a MARKET or pending order is not fully known until execution/trigger, it is plausible the server resolves the relative fields into an absolute stop/take level at execution time — but this mechanism (and any resulting precedence over a simultaneously-set absolute value) is not documented anywhere found.

### Gaps
- **No source states which field wins if both `stopLoss` and `relativeStopLoss` (or both `takeProfit` and `relativeTakeProfit`) are set on the same `ProtoOANewOrderReq`.** No forum post, GitHub issue, or schema comment addresses the both-set case at all. This should be flagged to the project as unverified — if this scenario matters operationally, it needs direct empirical testing (send both fields on a demo account and observe the resulting `ProtoOAPosition.stopLoss`/`.takeProfit` or `ProtoOAOrder.stopLoss`/`.takeProfit` in the execution event) since no public source resolves it.
- No source discusses whether setting both simultaneously produces a validation error (e.g., some conflicting-fields error code) rather than silently picking one — the `ProtoOAErrorCode` enum has no obviously-named "conflicting SL fields" error, but there is no confirmation it wouldn't map to a generic error either.

---

## Key Question 7: Client-supplied identifier fields on the order (label / comment / clientOrderId) vs. envelope-level clientMsgId

### Takeaway
`ProtoOANewOrderReq` (and the resulting `ProtoOAOrder`/`ProtoOATradeData` entities) carry **three distinct, persistent, order-level string fields** that are separate from the transport-level `clientMsgId`: `label` (max 100 chars), `comment` (max 512 chars), and `clientOrderId` (max 50 chars, explicitly analogized to FIX `ClOrdID`). All three are readable back later via reconciliation/execution-event messages, making any of them usable for idempotency/reconciliation — `clientOrderId` is the one Spotware's own comment frames as purpose-built for that role.

### Cited Findings
- `label`: `optional string label = 16; // User-specified label. MaxLength = 100.` on the request — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto). It persists onto the order/position entity via `ProtoOATradeData.label`: `optional string label = 5; // Text label specified during order request.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto). Since `ProtoOAOrder.tradeData` and `ProtoOAPosition.tradeData` both embed `ProtoOATradeData`, the label is readable back on both the order and any resulting position.
- `comment`: `optional string comment = 13; // User-specified comment. MaxLength = 512.` on the request — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto). It likewise persists via `ProtoOATradeData.comment`: `optional string comment = 7; // User-specified comment.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto).
- `clientOrderId`: `optional string clientOrderId = 18; // Optional user-specific clientOrderId (similar to FIX ClOrderID). MaxLength = 50.` on the request — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto). It persists directly on the order entity: `optional string clientOrderId = 17; // Optional ClientOrderId. Max Length = 50 chars.` in `ProtoOAOrder` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto). Note `clientOrderId` lives directly on `ProtoOAOrder` (not inside the shared `ProtoOATradeData` sub-message the way `label`/`comment` do), meaning it is available when reading back an order (`ProtoOAOrder`) but is **not** part of `ProtoOATradeData`, so it would not automatically appear if a downstream consumer only inspects `ProtoOAPosition.tradeData` after the order is filled and only the position remains — a project doing idempotency/reconciliation against positions (not orders) should confirm whether `clientOrderId` is retrievable via the position's originating order lookup (e.g., `ProtoOAOrderDetailsReq`/`ProtoOAOrderListByPositionIdReq`) rather than assuming it rides along on the position object itself.
- This is separate from the transport-level `clientMsgId`, which the task brief correctly characterizes as envelope/message-level rather than order-level; the researched `.proto` files for `ProtoOANewOrderReq`/`ProtoOAOrder` do not define any field named `clientMsgId` — that field lives in the generic ProtoMessage envelope wrapper used by all Open API calls (not part of either file read in this research pass; not independently re-verified here since it was out of scope of the two order-specific files fetched).

### Inferences
- `clientOrderId` is the most idempotency/reconciliation-appropriate field of the three, because (a) Spotware's own comment explicitly likens it to FIX's `ClOrdID`, a field whose entire purpose in FIX-based trading systems is client-side idempotent order identification/deduplication, and (b) unlike `label`/`comment` (which are generic free-text and could plausibly be reused/duplicated across unrelated orders by a careless client), `clientOrderId`'s naming and FIX analogy strongly suggest it is intended to be unique per order attempt.
- `label` (max 100) is likely better suited to a short strategy/bot tag (many EA/cBot frameworks use `label` to mark which strategy/instance opened a position, since it's visible in the cTrader UI position list), while `comment` (max 512, much longer) is suited to free-form metadata. This is an inference from field-length design and common cTrader cBot usage patterns, not a directly documented statement of intended use.

### Gaps
- No source found confirms whether the Open API server enforces **uniqueness** of `clientOrderId` (e.g., rejecting a duplicate `clientOrderId` within some scope/time window the way exchange FIX gateways often do) or whether it is purely a passive pass-through/echo field with no dedup enforcement server-side. This materially affects whether it can be relied on for true idempotency (client-side dedup only, vs. server-enforced dedup) — this was not resolved by any source found and should be treated as unverified.
- No source found confirms whether `label`/`comment`/`clientOrderId` are queryable/filterable in `ProtoOAOrderListReq`, `ProtoOADealListReq`, or `ProtoOAReconcileReq` (i.e., can you ask the server "give me the order with clientOrderId=X" directly), or whether a client must always fetch by time range / position / order ID and then filter client-side by matching the returned `clientOrderId` string. The schema for those list-request messages (seen in `OpenApiMessages.proto`) shows filtering only by timestamps, symbol, or position/order ID — no filter-by-label/comment/clientOrderId parameter exists on any request message reviewed, which is a fairly strong signal (schema-level, not just an unconfirmed gap) that server-side lookup-by-clientOrderId is **not supported** and matching must be done client-side after listing/reconciling.

---

## Key Question 8: guaranteedStopLoss and stopTriggerMethod — what they configure and when relevant

### Takeaway
`guaranteedStopLoss` (bool) requests that the stop-loss level be guaranteed against slippage/gapping, and is mandatory-by-business-rule (not by protobuf) for accounts flagged `isLimitedRisk=true`. `stopTriggerMethod` (enum, 4 values) configures whether a STOP/STOP_LIMIT pending order (or a position's SL/TP) is triggered by the same-side or opposite-side price, with an optional "double tick" confirmation variant, defaulting to `TRADE`.

### Cited Findings
- Request field: `optional bool guaranteedStopLoss = 21; // If TRUE then stopLoss is guaranteed. Required to be set to TRUE for the Limited Risk accounts (ProtoOATrader.isLimitedRisk=true).` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- The live rendered docs page gives an updated/expanded description that reads: *"Available if GSL execution policy is not 'Disabled' for this symbol or group execution profile; Required to be set TRUE for the Limited Risk accounts when the symbol has GSL enabled."* — [Messages - Open API - cTrader Help](https://help.ctrader.com/open-api/messages/) — this is more nuanced than the GitHub comment (introduces a per-symbol/per-group "GSL execution policy" concept not mentioned in the raw `.proto` comment), reinforcing that the docs site can reflect newer server-side business rules than the repo's comment text.
- `ProtoOATrader.isLimitedRisk`: `optional bool isLimitedRisk = 18; // If TRUE then account is compliant to use specific margin calculation strategy. Such accounts are require to have guaranteed stop loss on all positions.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Related error code: `WORSE_GSL_NOT_ALLOWED = 68; // Not allowed to increase risk for Positions with Guaranteed Stop Loss.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `stopTriggerMethod` on the request: `optional ProtoOAOrderTriggerMethod stopTriggerMethod = 23 [default = TRADE]; // Trigger method for the STOP or the STOP_LIMIT pending order.` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) — note this field **does** have an explicit non-ambiguous default (`TRADE`) directly analogous in mechanism to the `timeInForce` default discussed in Key Question 4, i.e., another example proving proto2 explicit-default usage is the norm throughout this schema, not a one-off.
- Full `ProtoOAOrderTriggerMethod` enum with descriptions:
```protobuf
enum ProtoOAOrderTriggerMethod {
    TRADE = 1; // Stop Order: buy is triggered by ask, sell by bid; Stop Loss Order: for buy position is triggered by bid and for sell position by ask.
    OPPOSITE = 2; // Stop Order: buy is triggered by bid, sell by ask; Stop Loss Order: for buy position is triggered by ask and for sell position by bid.
    DOUBLE_TRADE = 3; // The same as TRADE, but trigger is checked after the second consecutive tick.
    DOUBLE_OPPOSITE = 4; // The same as OPPOSITE, but trigger is checked after the second consecutive tick.
}
```
— [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `stopTriggerMethod` is applicable in (at least) three separate contexts across the schema, each with its own field and its own `[default = TRADE]`: on the new-order request (field 23), on `ProtoOAAmendOrderReq` (field 15), on `ProtoOAOrder` (field 24, "Valid only for STOP and STOP_LIMIT orders"), and a same-concept field named `stopLossTriggerMethod` on `ProtoOAPosition` (field 14) and `ProtoOAAmendPositionSLTPReq` (field 9) governing the position's own SL/TP trigger side. — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto); [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)

### Inferences
- `guaranteedStopLoss` most likely carries a fee/cost implication, since the schema separately defines `ProtoOASymbol.gslCharge` ("Guaranteed stop loss fee") and a dedicated balance-history reason `BALANCE_WITHDRAW_GSL_CHARGE = 17; // Charge for guaranteedStopLoss.` in `ProtoOAChangeBalanceType` — meaning requesting a guaranteed SL is not purely a risk-management toggle but likely has an associated monetary cost debited from the account. — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- `TRADE` vs `OPPOSITE` essentially controls conservatism of triggering: `TRADE` triggers a stop as soon as the natural execution-side price touches the level (faster/more standard), while `OPPOSITE` requires the less favorable side of the spread to touch the level (more conservative, avoiding some spread-driven false triggers); `DOUBLE_*` variants add a second-tick confirmation to reduce single-tick/spike-driven false triggers. This is a straightforward reading of the enum comments, not additional outside information.

### Gaps
- No source found quantifies the actual `gslCharge` fee magnitude or how/when it's charged (upfront vs. on top of the guaranteed fill), nor confirms whether `guaranteedStopLoss=true` combined with `relativeStopLoss` (vs. absolute `stopLoss`) is supported identically.
- No source found describing symbol-level or account-level restrictions on which `stopTriggerMethod` values are actually selectable per broker (the docs mention a broker-configurable "GSL execution policy" for guaranteedStopLoss, and it's plausible similar broker-level restrictions exist for trigger method, but this wasn't confirmed).

---

## Key Question 9: Common validation errors for malformed new-order requests

### Takeaway
Trading-related validation errors are enumerated in the shared `ProtoOAErrorCode` enum (numeric range roughly 117–136, plus a few earlier general-purpose codes like 67/68/69), delivered back to the client either as a request-level `ProtoOAErrorRes.errorCode` or (more commonly for trading actions) as a `ProtoOAOrderErrorEvent` carrying `errorCode` (string) + optional `orderId`/`positionId`/`description`.

### Cited Findings
- The event specifically for order-request failures: 
```protobuf
message ProtoOAOrderErrorEvent {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_ORDER_ERROR_EVENT];
    required int64 ctidTraderAccountId = 5;
    required string errorCode = 2; // The name of the ProtoErrorCode or the other custom ErrorCodes (e.g. ProtoCHErrorCode).
    optional int64 orderId = 3;
    optional int64 positionId = 6;
    optional string description = 7; // The error description.
}
```
— [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto). Note `errorCode` is typed as `string` (the enum name, e.g. `"TRADING_BAD_STOPS"`), not the numeric enum value directly, and the comment explicitly allows for broker-specific custom codes beyond the standard enum ("e.g. ProtoCHErrorCode") — meaning a robust client should not assume the observed error code is always a member of the documented `ProtoOAErrorCode` enum.
- Trading-relevant entries from `ProtoOAErrorCode` (verbatim comments), directly relevant to malformed new-order requests:
  - `TRADING_BAD_VOLUME = 125; // Invalid volume.`
  - `TRADING_BAD_STOPS = 126; // Invalid stop price.`
  - `TRADING_BAD_PRICES = 127; // Invalid price (e.g. negative).`
  - `TRADING_BAD_STAKE = 128; // Invalid stake volume (e.g. negative).`
  - `PROTECTION_IS_TOO_CLOSE_TO_MARKET = 129; // Invalid protection prices.` (i.e., SL/TP too close to current market price, presumably relative to `ProtoOASymbol.slDistance`/`tpDistance`/`gslDistance` minimums)
  - `TRADING_BAD_EXPIRATION_DATE = 130; // Invalid expiration.`
  - `PENDING_EXECUTION = 131; // Unable to apply changes as position has an order under execution.`
  - `TRADING_DISABLED = 132; // Trading is blocked for the symbol.`
  - `TRADING_NOT_ALLOWED = 133; // Trading account is in read only mode.`
  - `UNABLE_TO_CANCEL_ORDER = 134; // Unable to cancel order.`
  - `UNABLE_TO_AMEND_ORDER = 135; // Unable to amend order.`
  - `SHORT_SELLING_NOT_ALLOWED = 136; // Short selling is not allowed.`
  - `NO_QUOTES = 117; // Trading cannot be done as not quotes are available. Applicable for Book B.`
  - `NOT_ENOUGH_MONEY = 118; // Not enough funds to allocate margin.`
  - `MAX_EXPOSURE_REACHED = 119; // Max exposure limit is reached for a {trader, symbol, side}.`
  - `POSITION_NOT_FOUND = 120`, `ORDER_NOT_FOUND = 121`, `POSITION_NOT_OPEN = 122`, `POSITION_LOCKED = 123`
  - `TOO_MANY_POSITIONS = 124; // Trading account reached its limit for max number of open positions and orders.`
  - `WORSE_GSL_NOT_ALLOWED = 68; // Not allowed to increase risk for Positions with Guaranteed Stop Loss.`
  - `SYMBOL_HAS_HOLIDAY = 69; // Trading disabled because symbol has holiday.`
  — all verbatim from [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Separately, a forum thread title ("Order price has more digits than symbol allows. Allowed 3 digits") corroborates that price-precision validation errors are a real, reported occurrence, consistent with `TRADING_BAD_PRICES`/`TRADING_BAD_STOPS` style validation, though the thread's full body/resolution was not fetched in this pass. — [cTrader Forum - Order price has more digits than symbol allows](https://community.ctrader.com/forum/connect-api-support/37007/)

### Inferences
- `PROTECTION_IS_TOO_CLOSE_TO_MARKET` (129) is almost certainly the error a client would hit if `stopLoss`/`takeProfit`/`relativeStopLoss`/`relativeTakeProfit` violate the symbol's configured `slDistance`/`tpDistance`/`gslDistance` minimums (all defined on `ProtoOASymbol`), since those are the only schema-defined "minimum distance" concepts that map naturally to this error's description.
- Given `errorCode` is a free-form string field (not a strongly-typed enum on the wire) and explicitly documented to допускать non-`ProtoOAErrorCode` values, a production integration should treat error-code handling defensively (e.g., fallback/unknown-code handling path), rather than assuming exhaustive enum coverage.

### Gaps
- No source found gives a definitive, complete mapping from "which malformed field → which exact error code" beyond what's inferable from the error-name/description text itself (e.g., no source explicitly confirms that omitting `expirationTimestamp` on a GTD order produces `TRADING_BAD_EXPIRATION_DATE` specifically, as opposed to a more generic rejection).
- No source found describes the exact HTTP/response-level shape difference (if any) between a "hard" validation rejection returned synchronously (if that even happens for this async, event-driven protocol) versus the `ProtoOAOrderErrorEvent` being the sole failure-signaling channel. Given the schema, it appears `ProtoOAOrderErrorEvent` (and generic `ProtoOAErrorRes` for connection/auth-level errors) are the only failure-signaling messages defined for trading requests, but this wasn't independently confirmed by a source describing overall request/response flow end-to-end.

---

## Overall summary of source hierarchy used

1. **Official schema (highest confidence, primary source):** `OpenApiMessages.proto` and `OpenApiModelMessages.proto`, fetched directly (raw file content, not AI-summarized) from `https://raw.githubusercontent.com/spotware/openapi-proto-messages/main/`, `main` branch, as of 2026-09-17.
2. **Official rendered docs (secondary, sometimes more current wording than repo comments but omits proto-level defaults):** `help.ctrader.com/open-api/messages/` and `help.ctrader.com/open-api/model-messages/`.
3. **Community empirical evidence (anecdotal, contextualized above per-claim):** `community.ctrader.com` forum threads — most relevant were the "TimeInForce parameter ignored" thread (Open API/.NET), the "Take profit & stop loss implementation" thread (FIX API, not Open API — flagged wherever cited), and the "expiration time in ms on limit/stop orders" thread (cBot/cAlgo context).
4. **Unreached/inconclusive:** the community docs site `m-ahmadi.github.io/ctoa` has a "Placing Orders" page that was not successfully retrieved with content in this pass; a follow-up researcher could try fetching `https://m-ahmadi.github.io/ctoa/placing-order` directly if further corroboration is wanted.
