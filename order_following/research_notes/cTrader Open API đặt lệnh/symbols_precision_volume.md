# cTrader Open API — Symbol Data, Price Precision, and Volume Units

## What is the exact request/response structure of ProtoOASymbolsListReq / ProtoOASymbolByIdReq / ProtoOALightSymbol / ProtoOASymbol?

### Takeaway
All four messages are officially defined in Spotware's public `.proto` source files (the canonical reference for the Open API wire format). `ProtoOASymbolsListReq/Res` returns a lightweight symbol list (`ProtoOALightSymbol`, one per symbol) for a trading account; `ProtoOASymbolByIdReq/Res` takes one or more symbolIds and returns the heavyweight `ProtoOASymbol` entity (full trading specification) for each.

### Cited Findings
- Verbatim from `OpenApiMessages.proto` (retrieved directly from the raw GitHub file):
```protobuf
/** Request for a list of symbols available for a trading account. Symbol entries are returned with the limited set of fields. */
message ProtoOASymbolsListReq {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_SYMBOLS_LIST_REQ];
    required int64 ctidTraderAccountId = 2; // Unique identifier of the trader's account.
    optional bool includeArchivedSymbols = 3 [default = false]; // Whether to include old archived symbols into response.
}

/** Response to the ProtoOASymbolsListReq request. */
message ProtoOASymbolsListRes {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_SYMBOLS_LIST_RES];
    required int64 ctidTraderAccountId = 2;
    repeated ProtoOALightSymbol symbol = 3; // The list of symbols.
    repeated ProtoOAArchivedSymbol archivedSymbol = 4; // The list of archived symbols.
}

/** Request for getting a full symbol entity. */
message ProtoOASymbolByIdReq {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_SYMBOL_BY_ID_REQ];
    required int64 ctidTraderAccountId = 2;
    repeated int64 symbolId = 3; // Unique identifier of the symbol in cTrader platform.
}

/** Response to the ProtoOASymbolByIdReq request. */
message ProtoOASymbolByIdRes {
    optional ProtoOAPayloadType payloadType = 1 [default = PROTO_OA_SYMBOL_BY_ID_RES];
    required int64 ctidTraderAccountId = 2;
    repeated ProtoOASymbol symbol = 3; // Symbol entity with the full set of fields.
    repeated ProtoOAArchivedSymbol archivedSymbol = 4;
}
```
  — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (lines 201-232 as retrieved 2026-09-17)
- Verbatim from `OpenApiModelMessages.proto`:
```protobuf
/** Lightweight symbol entity. */
message ProtoOALightSymbol {
    required int64 symbolId = 1; // The unique identifier of the symbol in specific server environment within cTrader platform. Different brokers might have different IDs.
    optional string symbolName = 2; // Name of the symbol (e.g. EUR/USD).
    optional bool enabled = 3; // If TRUE then symbol is visible for traders.
    optional int64 baseAssetId = 4;
    optional int64 quoteAssetId = 5;
    optional int64 symbolCategoryId = 6;
    optional string description = 7;
    optional double sortingNumber = 8; // The number used for sorting Symbols in the UI (lowest number should appear at the top).
}

/** Trading symbol entity. */
message ProtoOASymbol {
    required int64 symbolId = 1;
    required int32 digits = 2; // Number of price digits to be displayed.
    required int32 pipPosition = 3; // Pip position on digits.
    optional bool enableShortSelling = 4;
    optional bool guaranteedStopLoss = 5;
    optional ProtoOADayOfWeek swapRollover3Days = 6 [default = MONDAY];
    optional double swapLong = 7;
    optional double swapShort = 8;
    optional int64 maxVolume = 9;   // Maximum allowed volume in cents for an order with a symbol.
    optional int64 minVolume = 10;  // Minimum allowed volume in cents for an order with a symbol.
    optional int64 stepVolume = 11; // Step of the volume in cents for an order.
    optional uint64 maxExposure = 12;
    repeated ProtoOAInterval schedule = 13; // Symbol trading interval, specified in seconds starting from SUNDAY 00:00 in specified time zone.
    optional int64 commission = 14 [deprecated = true];
    optional ProtoOACommissionType commissionType = 15 [default = USD_PER_MILLION_USD];
    optional uint32 slDistance = 16;
    optional uint32 tpDistance = 17;
    optional uint32 gslDistance = 18;
    optional int64 gslCharge = 19;
    optional ProtoOASymbolDistanceType distanceSetIn = 20 [default = SYMBOL_DISTANCE_IN_POINTS];
    optional int64 minCommission = 21 [deprecated = true];
    optional ProtoOAMinCommissionType minCommissionType = 22 [default = CURRENCY];
    optional string minCommissionAsset = 23 [default = "USD"];
    optional int64 rolloverCommission = 24;
    optional int32 skipRolloverDays = 25;
    optional string scheduleTimeZone = 26; // Time zone for the symbol trading intervals.
    optional ProtoOATradingMode tradingMode = 27 [default = ENABLED];
    optional ProtoOADayOfWeek rolloverCommission3Days = 28 [default = MONDAY];
    optional ProtoOASwapCalculationType swapCalculationType = 29 [default = PIPS];
    optional int64 lotSize = 30; // Lot size of the Symbol (in cents).
    optional int64 preciseTradingCommissionRate = 31;
    optional int64 preciseMinCommission = 32;
    repeated ProtoOAHoliday holiday = 33; // List of holidays for this symbol specified by broker.
    optional int32 pnlConversionFeeRate = 34;
    optional int64 leverageId = 35;
    optional int32 swapPeriod = 36;
    optional int32 swapTime = 37;
    optional int32 skipSWAPPeriods = 38;
    optional bool chargeSwapAtWeekends = 39;
    optional string measurementUnits = 40;
}
```
  — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 114-167 as retrieved 2026-09-17)
- The official help-centre page mirrors these same field descriptions for `ProtoOASymbol`/`ProtoOALightSymbol` (digits, pipPosition, maxVolume/minVolume/stepVolume "in cents", lotSize "in cents", schedule/scheduleTimeZone) — [Model messages - Open API - cTrader Help](https://help.ctrader.com/open-api/model-messages/)
- `ProtoOASymbolsListReq` explicitly documents that it returns symbols "with the limited set of fields," which is why a second round-trip via `ProtoOASymbolByIdReq` is required to get `digits`, `pipPosition`, volume limits, schedule, etc. — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto)
- Official walkthrough doc for symbol/market data does not itself demonstrate the name→ID list call; it focuses on trend bars, tick data, spot subscriptions, and depth quotes — [Attain symbol data - Open API - cTrader Help](https://help.ctrader.com/open-api/symbol-data/)

### Inferences
- The correct client workflow is: (1) call `ProtoOASymbolsListReq` once per account to get the full `ProtoOALightSymbol` list (cheap, includes `symbolName`); (2) resolve the desired human-readable name(s) to `symbolId`(s) client-side; (3) call `ProtoOASymbolByIdReq` with those `symbolId`s to get the full `ProtoOASymbol` records needed for price/volume formatting and order placement. This two-step pattern is necessary because the light list omits `digits`, `pipPosition`, `lotSize`, `minVolume`/`maxVolume`/`stepVolume`, and `schedule`.

### Gaps
- No official sample code was found (in the fetched pages) that specifically demonstrates the name-to-ID lookup step; only the generic OpenApiPy repository was pointed to by Spotware staff without an inline example — [cTrader Forum - how to get symbolid?](https://community.ctrader.com/forum/connect-api-support/43750/)

---

## How should a client map a human-readable symbol name to symbolId — is there a name field in the light symbol list, and are symbol names guaranteed unique/stable per account?

### Takeaway
`ProtoOALightSymbol.symbolName` (field 2, e.g. "EUR/USD") is the documented name field returned by `ProtoOASymbolsListReq`, and this is the field to match against a plain-text symbol name to resolve `symbolId`. However, no official Spotware documentation found in this research explicitly states or guarantees that `symbolName` values are unique or stable within an account's symbol list — this must be treated as an assumption, not a documented guarantee.

### Cited Findings
- `ProtoOALightSymbol` field: `optional string symbolName = 2; // Name of the symbol (e.g. EUR/USD).` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (line 160)
- `symbolId` is explicitly documented as scoped to "specific server environment within cTrader platform. Different brokers might have different IDs" — i.e., the same real-world instrument (e.g. EURUSD) will have a *different* numeric `symbolId` on different brokers/servers, so IDs must always be resolved per-account and never hardcoded across brokers — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (line 159); confirmed again in Web search summary of the same file: "different brokers might have different IDs" — [openapi-proto-messages/OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto)
- Spotware staff (Panagiotis Charalampous) responding to "how to get all symbolid via python?" only pointed to the generic OpenApiPy example repo and did not address name-uniqueness — [cTrader Forum - how to get symbolid?](https://community.ctrader.com/forum/connect-api-support/43750/)
- `ProtoOAArchivedSymbol` (returned alongside the light/full symbol lists when `includeArchivedSymbols=true`) also carries a `required string name = 2;` field separate from the live symbol's `symbolName`, confirming naming is handled per-symbol-record rather than through some global registry — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 169-174)

### Inferences
- Because `symbolId` values are broker/server-specific and not portable, and because a plain-text alias like "US30" or "GOLD" is a broker-defined convention (brokers name index/metal CFDs differently — e.g. "GOLD", "XAUUSD", "XAU/USD" all refer to the same instrument at different brokers), a robust client should: fetch `ProtoOASymbolsListReq` for the specific `ctidTraderAccountId` being used, build a name→id map from the returned `symbolName` values for that account, and re-resolve this map per account/session rather than caching it globally or across brokers.
- Because no uniqueness constraint is documented, defensive client code should treat a name lookup that returns more than one match as an ambiguous/error condition requiring disambiguation (e.g., by `symbolCategoryId` or `description`), rather than silently picking the first match.

### Gaps
- No official documentation or forum thread was found that explicitly confirms (or denies) that `symbolName` is guaranteed unique within one account's symbol list. This should be treated as an unverified assumption in any implementation — flagged here as a gap rather than asserted as fact.
- No official guidance was found on symbol-name stability over time (i.e., whether a broker can rename a symbol while keeping the same `symbolId`, or vice versa).

---

## What do digits and pipPosition mean exactly, and how are they used together to round a price to a valid tick?

### Takeaway
`digits` is the total number of decimal places used to display/represent the symbol's price, and `pipPosition` is the decimal position (counting from the left after the decimal point) that represents one "pip" of that symbol; the smallest valid price increment (tick size) is `10^-digits`, while one pip is `10^-pipPosition`, and any price sent to the API must be rounded to a multiple of `10^-digits`.

### Cited Findings
- `required int32 digits = 2; // Number of price digits to be displayed.` and `required int32 pipPosition = 3; // Pip position on digits.` — both fields are `required` on `ProtoOASymbol` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 116-117)
- Pip size formula and tick size formula, from official/derived documentation: "the pip size can be calculated as `1 / Math.Pow(10, symbol.PipPosition)`, and tick size as `1 / Math.Pow(10, symbol.Digits)`" — [Symbol rate conversion - Open API - cTrader Help](https://help.ctrader.com/open-api/symbol-rate-conversion/)
- Real numeric case from an official Spotware-staff-confirmed forum example: for EURUSD-like FX symbols, `digits = 5` and `pipPosition = 4` is the typical convention (5 decimal digits displayed, with the 4th decimal place being the pip) — summarized from [ProtoOASymbol cTrader Open API digits pipPosition search results], cross-referenced with [Symbol rate conversion - Open API](https://help.ctrader.com/open-api/symbol-rate-conversion/)
- Officially confirmed rejection behavior when a price does not match `digits`: Spotware staff member **Amusleh** stated on the forum: "The decimal places of your stop price must match with symbol digits. You can get the symbol digits from ProtoOASymbol." — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- The exact server error text reported by a user hitting this validation: `"Order price = 131.07400024414062 has more digits than symbol allows. Allowed 3 digits"`, and separately `"Relative stop loss has invalid precision"` — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- Related fields also expressed relative to a fixed sub-unit scale rather than `digits` directly: `relativeStopLoss`/`relativeTakeProfit` in `ProtoOANewOrderReq` are "Specified in 1/100000 of unit of a price. (e.g. 123000 in protocol means 1.23, 53423782 means 534.23782)" — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (lines 102-103)
- The official "Attain symbol data" walkthrough confirms the same 1/100000 relative-price convention is used for trend-bar data too, and explicitly instructs developers to divide by 100,000 and then round to the symbol's `digits` when converting relative trend-bar prices back to absolute prices — [Attain symbol data - Open API - cTrader Help](https://help.ctrader.com/open-api/symbol-data/)

### Inferences
- Practical rounding formula to compute a valid tick-aligned price for an absolute price field (`limitPrice`, `stopPrice`, `stopLoss`, `takeProfit`) is:
  `validPrice = round(rawPrice, digits)` (round to `digits` decimal places), i.e. `validPrice = Math.Round(rawPrice * 10^digits) / 10^digits`.
- For relative price fields (`relativeStopLoss`, `relativeTakeProfit`), because the wire format always uses a fixed 1/100000 scale regardless of `digits`, the safe approach demonstrated by the forum troubleshooting is to **round the price-space value to `digits` decimal places first, then multiply by 100000 and truncate/cast to int64** — rounding after multiplying by 100000 can produce a value with more effective precision than the symbol allows and gets rejected. This ordering requirement ("round(pipsInPrice, digits) * 100000 works, but round(pipsInPrice * 100000, digits) fails") was the specific bug/fix reported in the forum thread — [cTrader Forum - Order price has more digits than symbol allows](https://community.ctrader.com/forum/connect-api-support/37007/)
- `pipPosition` is used for pip-denominated UI/analytics (e.g., computing stop-loss distance "in pips," or converting a pip value to a price offset), while `digits` alone governs what price values the trading engine will actually accept.

### Gaps
- No official source was found giving a single canonical worked example row (symbolName, digits, pipPosition, computed pip size, computed tick size) straight from Spotware documentation; the "digits=5, pipPosition=4" EURUSD convention is a reasonable, widely-repeated inference from secondary/aggregated sources rather than a directly quoted official table.

---

## What is the exact unit and scaling factor of the `volume` field in ProtoOANewOrderReq, and how does it relate to lotSize, minVolume, maxVolume, stepVolume?

### Takeaway
`volume` in `ProtoOANewOrderReq` (and in `ProtoOAAmendOrderReq`/`ProtoOAClosePositionReq`) is an `int64` expressed in **hundredths of a unit** ("1000 in protocol means 10.00 units"), and `lotSize`, `minVolume`, `maxVolume`, `stepVolume` on `ProtoOASymbol` are documented as being "in cents" — i.e. the **same** hundredths-scale integer representation — so they are directly comparable to the `volume` field without any additional unit conversion between them. The verified, broker-agnostic formula is: **`volume_to_send = round(numberOfLots × symbol.lotSize)`**, where `symbol.lotSize` is used exactly as returned by `ProtoOASymbolByIdRes` (no pre-division needed) because both sides of the multiplication share the same ×100 scale.

### Cited Findings
- Verbatim proto comment (primary source): `required int64 volume = 6; // The volume represented in 0.01 of a unit (e.g. 1000 in protocol means 10.00 units).` on `ProtoOANewOrderReq` — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (line 89)
- Same 0.01-unit convention repeated for the amend/close paths: `optional int64 volume = 4; // Volume, represented in 0.01 of a unit (e.g. 1000 in protocol means 10.00 units).` (`ProtoOAAmendOrderReq`) and `required int64 volume = 4; // Volume to close, represented in 0.01 of a unit (e.g. 1000 in protocol means 10.00 units).` (`ProtoOAClosePositionReq`) — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (lines 138, 171)
- `ProtoOASymbol` field comments (primary source): `optional int64 maxVolume = 9; // Maximum allowed volume in cents for an order with a symbol.`; `optional int64 minVolume = 10; // Minimum allowed volume in cents for an order with a symbol.`; `optional int64 stepVolume = 11; // Step of the volume in cents for an order.`; `optional int64 lotSize = 30; // Lot size of the Symbol (in cents).` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 123-125, 144)
- **Officially confirmed worked example** from a Spotware staff member (amusleh) resolving a user's confusion, for the **US30 symbol (symbolId 1132) on the TopFX demo server**: `digits = 2`, `minVolume = 1` (cents), `stepVolume = 1` (cents), `lotSize = 100` (cents). Staff explanation: *"Open API returns Symbol min/max/step volumes in cents, you have to divide it by 100 to get the volume in symbol quote asset unit."* This resolves to a real minimum trade size of `1 / 100 = 0.01` contracts, which the staff member confirmed matches the broker's actual 0.01 minimum on that demo account — [cTrader Forum - real minimum trade volume calculation](https://community.ctrader.com/forum/connect-api-support/38065/)
- A second, independently-reported numeric example (FX symbol, exact instrument not stated) from a different forum thread: user's own math converts `lotSize = 10,000,000` (raw/cents) to `100,000` real units by dividing by 100 — consistent with a standard 100,000-unit FX lot — [cTrader Forum - Lot size, min trade quantity and max trade quantity calculation with ProtoOASymbol](https://community.ctrader.com/forum/connect-api-support/43672/)
- The same "divide raw integer by 100 to get the real-world value" convention is used elsewhere in the API too, reinforcing that ×100 fixed-point scaling is a systemic pattern (not unique to volume): the official market-data walkthrough instructs dividing `ProtoOADepthQuote.size` by 100, and dividing relative trend-bar prices by 100,000 then rounding to `digits` — [Attain symbol data - Open API - cTrader Help](https://help.ctrader.com/open-api/symbol-data/); `ProtoOADepthQuote` field: `required uint64 size = 3; // Quote size in cents.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (line 614)

### Worked Numeric Example (verified formula)
Using the officially-confirmed US30 data point above (`lotSize = 100`, `minVolume = 1`, `stepVolume = 1`, `digits = 2`):

- **Formula:** `volume_field_value = numberOfLots × symbol.lotSize` (raw, no division — both quantities are already on the identical ×100 integer scale, so the 100s cancel).
- To trade **1.00 lot/contract of US30**: `volume_field_value = 1.00 × 100 = 100` → send `volume = 100` in `ProtoOANewOrderReq`.
- To trade the broker's **minimum of 0.01 lots**: `volume_field_value = 0.01 × 100 = 1` → send `volume = 1`, which exactly equals the reported `minVolume = 1` — confirming internal consistency of the formula against the officially-verified `minVolume` field.
- Equivalent EURUSD-style example (from the independently reported `lotSize = 10,000,000` case, i.e. a broker whose 1.00 lot = 100,000 base-currency units): to trade **0.50 lots**, `volume_field_value = 0.50 × 10,000,000 = 5,000,000`; to trade the typical **0.01-lot micro minimum**, `volume_field_value = 0.01 × 10,000,000 = 100,000`.
- To convert the other direction (validate a raw `volume` against the symbol's limits, or display it to a user): `realUnits = raw_value / 100` applies uniformly to `volume`, `minVolume`, `maxVolume`, `stepVolume`, and `lotSize` — so `numberOfLots = raw_volume / symbol.lotSize` (again, raw/raw, no separate ÷100 step needed since it cancels).
- Validation before sending an order should check: `minVolume ≤ volume_field_value ≤ maxVolume` AND `(volume_field_value − minVolume) % stepVolume == 0` (all three comparison values used directly in their raw/"cents" form, matching `volume_field_value`'s own raw scale).

### Inferences
- Client code should never need to hardcode "divide by 100" separately when going from lots to the order's `volume` field, because multiplying `numberOfLots` by the raw (un-divided) `lotSize` field value already produces a correctly-scaled integer. The ÷100 conversion is only needed when converting a raw API integer into a *human-readable* real-world quantity (e.g. for display, or for arithmetic against real currency amounts), not when preparing the `volume` field for a request.
- Because `lotSize`, `minVolume`, `maxVolume`, and `stepVolume` are all optional fields on `ProtoOASymbol` (not required), a defensive client should treat their absence as "use broker/platform default" or fail closed rather than assuming a value.

### Gaps
- No single official Spotware document was found that states the "N lots × lotSize = volume" formula explicitly in one place; this formula is derived here by combining the primary-source proto comments (both `volume` and `lotSize`/`minVolume`/etc. use the same ×100/"cents" scale) with the staff-confirmed US30 numeric example. Treat the *individual* facts as officially documented, but the combined formula itself as a well-supported inference cross-validated against two independent real numeric examples (US30 official; EURUSD-style community-reported).

---

## Are there known community reports of confusion or bugs around volume units, and how were they resolved?

### Takeaway
Yes — volume/lot-size unit confusion is a recurring, multi-thread topic on the official cTrader community forum, generally stemming from the "cents"/×100 fixed-point scaling being non-obvious and from relative price/volume fields using different scale factors (100 vs 100,000) depending on the field. Resolution in the one thread with a captured official reply was: divide raw min/max/step/lotSize values by 100 to get real-world units, as directly confirmed by Spotware staff.

### Cited Findings
- **Resolved with official staff reply:** "real minimum trade volume calculation" — user was confused that a broker's live account and demo account seemed to disagree on US30's minimum volume; Spotware staff (amusleh) clarified the /100 scaling and confirmed the true minimum was 0.01 contracts, and the discrepancy traced back to comparing a demo account against a live account with different contract specifications (not an API bug) — [cTrader Forum - real minimum trade volume calculation](https://community.ctrader.com/forum/connect-api-support/38065/)
- **Reported but no official resolution captured in this research:** "Lot size, min trade quantity and max trade quantity calculation with ProtoOASymbol" — user shows working math for `lotSize` (10,000,000 ÷ 100 = 100,000) but flags the analogous division for `minVolume`/`maxVolume` (100,000 ÷ 100 = 1,000) as apparently "incorrect" per their expectations; the fetched excerpt of this thread did not contain a follow-up staff reply resolving the discrepancy — [cTrader Forum - Lot size, min trade quantity and max trade quantity calculation with ProtoOASymbol](https://community.ctrader.com/forum/connect-api-support/43672/)
- **Reported but no official resolution captured in this research:** "Lot Size/Volume Calculation Issue Using Open API" — developer reported that a 200-pip stop-loss calculation produced `relativeStopLoss = 2,000,000` for BTCUSD but only `2,000` for EURUSD using the same formula, i.e., the relative-price/volume scaling behaved inconsistently across asset classes in their implementation; the fetched excerpt did not contain a staff resolution — [cTrader Forum - Lot Size/Volume Calculation Issue Using Open API](https://community.ctrader.com/forum/connect-api-support/40740/)
- Multiple other forum threads with titles indicating the same class of recurring confusion exist (titles only, not fetched in depth in this research): "Lots vs Volume vs Quantity guidance please," "Calculating Volume," "How to calculate proper lot size?," "How to calculate Volume/ Lot Size exactly," "Calculate Volume/Lot Size," "Lots vs Units," "How to calculate trading volume, based on stop loss" — [cTrader Forum search results, community.ctrader.com/forum]

### Inferences
- The BTCUSD-vs-EURUSD relative-stop-loss discrepancy in the 40740 thread is plausibly explained by `digits`/`pipPosition` differing between crypto and FX symbols (e.g., BTCUSD often has fewer/different decimal places than EURUSD), which — combined with the fixed 1/100000 relative-price scale documented for `relativeStopLoss`/`relativeTakeProfit` — would produce very different-looking raw integers for what is conceptually "200 pips" on each symbol. This is a plausible inference, not a confirmed root cause, since no official reply was captured for that thread.
- The recurring pattern across threads suggests the ×100 "cents" scaling for volume-related fields is a known ergonomic pain point in the Open API that the official documentation does not sufficiently call out inline (the FAQ page has no entry on this topic).

### Gaps
- Full official resolutions for threads 43672 and 40740 were not visible in the fetched excerpts (the fetch tool may have truncated later replies); a deeper manual read of the full forum threads (with pagination/login) would be needed to confirm whether Spotware staff ultimately replied.
- No GitHub Issues (as opposed to forum posts) specifically about volume-unit confusion were located in this research; the community discussion found was exclusively on the `community.ctrader.com` forum rather than GitHub.

---

## How is a symbol's trading session / market hours schedule exposed via the API, and can a client query "is this symbol tradable right now"?

### Takeaway
`ProtoOASymbol` exposes trading hours as a repeated list of `ProtoOAInterval` (`schedule`) plus a `scheduleTimeZone` string and a separate `holiday` list (`ProtoOAHoliday`); combined with the `tradingMode` enum, these fields let a client compute tradability client-side. The Open API does **not** appear to expose a single dedicated RPC/boolean field that directly answers "is this symbol tradable right now" — that determination must be computed by the client from `schedule` + `scheduleTimeZone` + `holiday` + `tradingMode`, and re-fetched via `ProtoOASymbolByIdReq` whenever a `ProtoOASymbolChangedEvent` push notification arrives.

### Cited Findings
- `repeated ProtoOAInterval schedule = 13; // Symbol trading interval, specified in seconds starting from SUNDAY 00:00 in specified time zone.` and `optional string scheduleTimeZone = 26; // Time zone for the symbol trading intervals.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 127, 140)
- `ProtoOAInterval` definition: `/** Symbol trading session entity. */ message ProtoOAInterval { required uint32 startSecond = 3; // Interval start, specified in seconds starting from SUNDAY 00:00 in specified time zone (inclusive to the interval). required uint32 endSecond = 4; // Interval end, specified in seconds starting from SUNDAY 00:00 in specified time zone (exclusive from the interval). }` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 196-199)
- `ProtoOAHoliday` definition (per-symbol holiday calendar, separate from the weekly `schedule`): `required int64 holidayId`; `required string name`; `optional string description`; `required string scheduleTimeZone`; `required int64 holidayDate; // Amount of days from 1st Jan 1970, multiply it by 86400000 to get Unix time in milliseconds.`; `required bool isRecurring; // If TRUE, then the holiday happens each year.`; `optional int32 startSecond`; `optional int32 endSecond // Amount of seconds from 00:00:00 of the holiday day when holiday actually starts/finishes.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 693-702)
- `ProtoOASymbol.holiday` field: `repeated ProtoOAHoliday holiday = 33; // List of holidays for this symbol specified by broker.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (line 147)
- `tradingMode` field and its enum: `optional ProtoOATradingMode tradingMode = 27 [default = ENABLED]; // Rules for trading with the symbol.` with values `ENABLED = 0; DISABLED_WITHOUT_PENDINGS_EXECUTION = 1; DISABLED_WITH_PENDINGS_EXECUTION = 2; CLOSE_ONLY_MODE = 3;` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 141, 222-227)
- Push notification for symbol-level changes: `/** Event that is sent when the symbol is changed on the Server side. */ message ProtoOASymbolChangedEvent { ... required int64 ctidTraderAccountId = 2; repeated int64 symbolId = 3; }` — this event only signals *that* a symbol changed (by ID); it carries no payload describing what changed, so the client must re-issue `ProtoOASymbolByIdReq` to see the new state (e.g. updated `schedule`, `tradingMode`, or `holiday`) — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (lines 251-257)
- Related trading-blocked error codes surfaced only at order-submission time (not as a pre-check API): `SYMBOL_HAS_HOLIDAY = 69; // Trading disabled because symbol has holiday.` and `TRADING_DISABLED = 132; // Trading is blocked for the symbol.`, both part of `ProtoOAErrorCode` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 655, 680)
- A community forum question, "How to determine the Market is Open for a forex symbol," reported that `Symbol.MarketHours.IsOpened()` returned true when the market was actually closed; however this method belongs to the **cTrader Algo (cBot/Automate) API**, a different (higher-level, in-platform) API from the protobuf-based Open API covered by this research — no official Spotware reply was captured in the fetched thread — [cTrader Forum - How to determine the Market is Open for a forex symbol](https://community.ctrader.com/forum/ctrader-support/47033/)
- For comparison, the separate cTrader Algo (cBot) API does expose a purpose-built `MarketHours` object for this exact question, per its own reference docs — [MarketHours - cTrader Algo](https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/MarketHours/) — confirming this convenience method exists only on the Algo side, not confirmed to exist on the Open API side.

### Inferences
- To answer "is this symbol tradable right now" using only the Open API, a client must: (1) fetch `ProtoOASymbol.schedule` (list of weekly `[startSecond, endSecond)` windows relative to Sunday 00:00 in `scheduleTimeZone`), (2) convert "now" into seconds-since-Sunday-00:00 in that same time zone, (3) check whether that value falls inside any interval, (4) separately check `holiday` entries for the current date (accounting for `isRecurring`), and (5) check that `tradingMode` is `ENABLED` (or an acceptable partially-restricted mode for the intended action, e.g. `CLOSE_ONLY_MODE` still allows closing). Only if all of these pass should the client treat the symbol as tradable; otherwise it should expect a `TRADING_DISABLED` or `SYMBOL_HAS_HOLIDAY` rejection from the server if it attempts to trade anyway.
- Subscribing to `ProtoOASymbolChangedEvent` and re-fetching via `ProtoOASymbolByIdReq` on receipt is the documented mechanism to keep a locally-cached tradability computation up to date, since there is no dedicated "tradability changed" push payload — only a generic "this symbol changed, go re-fetch it" signal.

### Gaps
- No official Spotware documentation or forum post was found explicitly confirming whether the Open API has (or lacks) a single convenience call equivalent to the Algo API's `Symbol.MarketHours.IsOpened()`. Based on the absence of any such request/response message in the proto files retrieved (`OpenApiMessages.proto` was fully scanned for symbol-status/market-open-related message names with no match beyond `ProtoOASymbolChangedEvent`), this research concludes such a convenience call likely does not exist in the Open API, but this is an absence-of-evidence inference rather than an explicit documented statement that it doesn't exist.
- No worked example was found showing the seconds-since-Sunday-00:00 tradability computation end-to-end in official sample code.

---

## What happens if an order is placed with a price that doesn't align to the symbol's tick size / digits — does the server auto-round or reject?

### Takeaway
The server **rejects** (does not auto-round) prices whose decimal precision doesn't match the symbol's `digits`, returning a validation error naming the exact allowed digit count; this is directly confirmed by official Spotware staff on the community forum, with an exact reproduced error message.

### Cited Findings
- Reported exact server error text: `"Order price = 131.07400024414062 has more digits than symbol allows. Allowed 3 digits"` — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- A related precision error was also reported for relative stop-loss values: `"Relative stop loss has invalid precision"` — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- **Official confirmation from Spotware staff (Amusleh):** "The decimal places of your stop price must match with symbol digits. You can get the symbol digits from ProtoOASymbol." — i.e., the client is responsible for pre-rounding; the server validates and rejects rather than silently correcting — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- The user's own confirmed fix, as reported in the thread: round the price to `digits` decimal places *before* converting to the protocol's relative integer representation — rounding *after* multiplying by 100,000 (the relative-price scale factor) could yield a value implying more precision than `digits` allows and gets rejected; rounding first and then multiplying avoids the error — [cTrader Forum - Order price has more digits than symbol allows. Allowed 3 digits](https://community.ctrader.com/forum/connect-api-support/37007/)
- Corresponding formal error codes exist in the `ProtoOAErrorCode` enum for this class of validation failure, returned presumably via `ProtoOAExecutionEvent.errorCode` or a `ProtoOAErrorRes`: `TRADING_BAD_PRICES = 127; // Invalid price (e.g. negative).`, `TRADING_BAD_STOPS = 126; // Invalid stop price.`, and `TRADING_BAD_VOLUME = 125; // Invalid volume.` — [OpenApiModelMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiModelMessages.proto) (lines 673-675)
- `ProtoOAExecutionEvent` (the event acting as the async response to `ProtoOANewOrderReq`) carries `optional string errorCode = 9; // The name of the ProtoErrorCode or the other custom ErrorCodes.` confirming that rejections are surfaced asynchronously via this errorCode field rather than the order being silently modified — [OpenApiMessages.proto](https://github.com/spotware/openapi-proto-messages/blob/main/OpenApiMessages.proto) (line 120)

### Inferences
- Client implementations must pre-round every absolute price field (`limitPrice`, `stopPrice`, `stopLoss`, `takeProfit`) to exactly `symbol.digits` decimal places before sending `ProtoOANewOrderReq`/`ProtoOAAmendOrderReq`/`ProtoOAAmendPositionSLTPReq`; relying on the server to snap the price to the nearest valid tick is not safe and will produce a rejected order/execution event instead.
- The same precision discipline applies to volume: given `TRADING_BAD_VOLUME` exists as a distinct error code from the price-related ones, a `volume` value that isn't a multiple of `stepVolume` (or is outside `[minVolume, maxVolume]`) is analogously expected to be rejected rather than auto-adjusted, by symmetry with the confirmed price behavior — though no forum thread with an explicit reproduction of a `stepVolume` misalignment rejection was found in this research (see Gaps).

### Gaps
- No forum thread or official doc was found with a directly-reproduced example of a `TRADING_BAD_VOLUME` rejection specifically caused by a `stepVolume` misalignment (as opposed to being outside min/max) — the volume-rejection behavior is inferred by analogy to the confirmed price/digits rejection behavior and the existence of the dedicated error code, not independently confirmed with its own worked example.
