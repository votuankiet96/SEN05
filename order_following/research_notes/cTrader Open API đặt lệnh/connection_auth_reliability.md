# cTrader Open API — Kiến trúc kết nối, giao thức, xác thực OAuth2, và độ tin cậy vận hành

> Scope note: All primary documentation cited below was fetched live from help.ctrader.com, connect.spotware.com/GitHub, and community.ctrader.com in September 2026. One documented URL (`connect.spotware.com/docs/api-reference/oauth-services-description`) returned HTTP 404 at fetch time — flagged inline where relevant since the task explicitly named it as a suggested source. Where a fact could only be confirmed through a search-engine synthesis rather than a direct page fetch, this is noted explicitly so the report writer can weigh confidence accordingly.

## What transport options does cTrader Open API offer (TCP+Protobuf vs JSON over WebSocket)? What are the tradeoffs and which is recommended for a production trading bot?

### Takeaway
cTrader Open API supports two serialization formats (Protobuf and JSON) over two socket types (raw TCP and WebSocket), but the combinations are fixed by port: Protobuf is TCP-only on one port, while JSON can run over either TCP or WebSocket on a different port. No official page explicitly declares one combination "recommended," but all official SDKs (Python, .NET, Java) and the performance-oriented framing in official/community material point to TCP+Protobuf as the production choice.

### Cited Findings
- "You can connect to a cTrader Open API proxy using either the TCP protocol or the WebSocket protocol. The TCP client connection must use SSL, otherwise you will not be able to connect or interact with the API." — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/)
- "Operating with Protobuf always requires a connection to port 5035 (and only this port). Operating with JSON always requires a connection with port 5036 (and only this port)." — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/)
- "Communication is done by sending and receiving either JSON objects or Google Protocol Buffers (Protobufs), which are language-neutral means of data serialisation and deserialisation. When working with JSON, you can use either a TCP connection or a WebSocket connection." — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/) (via search synthesis; corroborated by dedicated pages [Protobuf and JSON](https://help.ctrader.com/open-api/protocol-buffers-json/) and [Sending/Receiving JSON](https://help.ctrader.com/open-api/sending-receiving-json/), titles confirmed via search but not independently re-fetched)
- Any language with a TCP/TLS client and Protobuf or JSON support can integrate (Python, C#, JavaScript/TypeScript, Java, Go, C++ all have example implementations). — [cTrader Open API](https://openapi.ctrader.com/) / [Getting started - Open API](https://help.ctrader.com/open-api/) (search synthesis)
- Official example/SDK repos (OpenApiPy, OpenAPI.Net, ctrader-open-api-v2-java-example) are built around the Protobuf/TCP transport. — [spotware/ctrader-open-api-v2-java-example](https://github.com/spotware/ctrader-open-api-v2-java-example); [Authentication - OpenApiPy](https://spotware.github.io/OpenApiPy/authentication/)
- Community/aggregator framing: "The API's use of Protobuf offers much higher performance in data exchange transfers than other solutions, making it suitable for stable 24/7 operations." — [cTrader API Guide: Python & OpenAPI Integration](https://ctraderacademy.com/ctrader-api/) (third-party, not Spotware-authored; treat as community-level opinion, not official guidance)

### Inferences
- Because Protobuf is fixed to a dedicated port (5035) and is the format every official SDK defaults to, and because binary Protobuf messages are smaller/faster to parse than JSON, TCP+Protobuf is the de facto production choice for a 24/7 order-placement bot; JSON/WebSocket appears aimed at browser-based or lightweight integrations where a Protobuf toolchain is inconvenient.
- No Spotware page makes an explicit "use X for production" recommendation — this is an inference from architecture and SDK defaults, not a documented statement.

### Gaps
- No official Spotware page was found that explicitly compares TCP+Protobuf vs JSON+WebSocket tradeoffs (latency, throughput, message-size overhead) or issues a formal recommendation for production trading bots. This should be treated as an inferred best practice, not a documented one.

## What are the exact demo and live server hostnames/ports for each transport?

### Takeaway
Demo and live each have one hostname with two fixed ports: 5035 for Protobuf, 5036 for JSON (over either TCP or WebSocket). The two environments are fully isolated — a connection to one cannot authenticate accounts belonging to the other.

### Cited Findings
- Live environment: `live.ctraderapi.com:5035` (Protobuf), `live.ctraderapi.com:5036` (JSON). Demo environment: `demo.ctraderapi.com:5035` (Protobuf), `demo.ctraderapi.com:5036` (JSON). — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/)
- "Operating with Protobuf always requires a connection to port 5035 (and only this port). Operating with JSON always requires a connection with port 5036 (and only this port)." — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/)
- Both ports accept TCP and WebSocket connections interchangeably (i.e., port choice is determined by serialization format, not socket type). — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/)
- Demo and live environments are completely isolated; a single connection cannot serve both simultaneously. Connections to live endpoints cannot authenticate demo accounts, and vice versa; separate connections must be maintained per environment. — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/); corroborated at [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/): "Use separate connections for demo and live accounts; each supports unlimited accounts of that type."

### Inferences
- A production bot that trades both demo (for shadow/testing) and live accounts concurrently must run two independent socket connections (and typically two separate credential/token sets), not one connection with a mode flag.

### Gaps
- None significant — hostnames/ports were confirmed directly from the current official page.

## Is TLS/SSL mandatory? What are the certificate requirements?

### Takeaway
TLS/SSL is mandatory for the TCP transport ("must use SSL, otherwise you will not be able to connect"); however, Spotware's documentation does not publish explicit certificate-pinning, CA-trust, or cipher-suite requirements — official SDKs simply perform standard hostname-verified TLS handshakes.

### Cited Findings
- "The TCP client connection must use SSL, otherwise you will not be able to connect or interact with the API." — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/)
- SSL/TLS is handled automatically by the official SDKs; the reference C# implementation performs a standard handshake via `SslStream.AuthenticateAsClientAsync(Host)`, i.e., standard certificate verification against the connecting hostname rather than any custom pinning logic. — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/) (SDK behavior as summarized from the page; underlying SDK is OpenAPI.Net)

### Inferences
- Since no custom certificate/CA guidance is published, a standard OS/language TLS trust store (verifying against a public CA-issued cert for `demo.ctraderapi.com` / `live.ctraderapi.com`) should be sufficient — there is no evidence of self-signed certificates or a requirement to pin a specific certificate.

### Gaps
- No official page enumerates TLS version requirements (e.g., minimum TLS 1.2), supported cipher suites, or certificate pinning guidance. This appears to simply not be documented publicly, rather than being an oversight in this research — treat as a genuine documentation gap.

## What is the ProtoHeartbeatEvent mechanism — required interval, what happens if missed, client vs server-initiated?

### Takeaway
ProtoHeartbeatEvent is a bidirectional keep-alive message with no payload; Spotware's official operational guidance says to send one at least every 10 seconds, while the protobuf schema's own comment frames the requirement as "whenever no other message has been sent for longer than 30 seconds." Missing heartbeats leads to disconnection for inactivity; the mechanism is usable by both client and server.

### Cited Findings
- Schema definition (fetched directly from the canonical proto source): `ProtoHeartbeatEvent` is a lightweight keep-alive message whose only field is `payloadType` (defaults to `HEARTBEAT_EVENT`); comment text: "Open API client can send this message when he needs to keep the connection open for a period without other messages longer than 30 seconds." — [OpenApiCommonMessages.proto, spotware/openapi-proto-messages](https://raw.githubusercontent.com/spotware/openapi-proto-messages/main/OpenApiCommonMessages.proto)
- "ProtoHeartbeatEvent... is sent from Open API proxy and can be used as criteria that connection is healthy when no other messages are sent by cTrader platform. Open API clients can send this message when they need to keep the connection open for a period without other messages longer than 30 seconds." — [Common messages - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/common-messages/) (search synthesis) — this explicitly confirms the event is sent **from the proxy (server) as well as from the client**, i.e., it is bidirectional/either-side-initiated.
- Official operational guidance (stricter than the 30s schema comment): "keep sending a heartbeat event every 10 seconds" to maintain the connection. — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/)
- FAQ repeats the same 10-second guidance: "Make sure that you send a heartbeat to the server at least once every 10 seconds," and states that applications disconnect after prolonged inactivity. — [FAQ - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/faq/)

### Inferences
- There is a real (if minor) inconsistency between the proto schema's comment ("longer than 30 seconds") and the operational how-to guidance ("every 10 seconds"). The 10-second figure is the safer, currently-published operational recommendation and should be treated as the practical requirement for a 24/7 bot; the 30-second figure in the schema comment likely represents the outer bound before the server considers a connection stale/inactive and drops it, so 10s gives roughly a 3x safety margin.
- Because the server can also emit heartbeats absent other traffic, an application can use *inbound* heartbeat receipt as a secondary liveness signal (in addition to sending its own), but should not rely on it exclusively since the documentation frames client-side sending as the primary responsibility.

### Gaps
- No official source explicitly states the exact server-side timeout threshold after which a connection missing heartbeats is forcibly closed (only "prolonged inactivity" is mentioned, not a number of seconds). Community forum threads titled "[ProtoHeartbeatEvent](https://community.ctrader.com/forum/connect-api-support/38894/)", "[Heartbeat](https://community.ctrader.com/forum/connect-api-support/42264/)", and "[Heatbeats not comming when sending your own heartbeats](https://community.ctrader.com/forum/connect-api-support/36909/)" exist and appear directly relevant to this exact ambiguity, but were surfaced only as search-result titles in this pass and not fetched for full content — flagged here as an unexplored lead rather than a sourced claim.

## What is the full OAuth2 flow to obtain an access token via connect.spotware.com (authorization code flow), including required scopes/redirect URI setup?

### Takeaway
The flow is a standard OAuth2 authorization-code exchange: register an app and redirect URI on `connect.spotware.com/apps`, send the user to an authorize URL with `client_id`/`redirect_uri`/`scope`, receive a short-lived authorization code at the redirect URI, then exchange it for an access + refresh token at `openapi.ctrader.com/apps/token`. One important discrepancy surfaced: the currently-live official help page names the authorize host as `id.ctrader.com`, not `connect.spotware.com` as the task brief assumed — this is flagged explicitly below.

### Cited Findings
- App registration/management: "Open cTrader Open API portal. Log in using your cTrader ID. After logging in, open the applications page. Select the Add new app button." Applications enter a "submitted" status pending Spotware review. — [Register an application / App and account authentication - Open API](https://help.ctrader.com/open-api/api-application/) (this URL is the current live location; `connect.spotware.com/docs/open_api_2/getting_started_v2` 301-redirects here as of this research)
- Redirect URI setup: done on the applications page; "you can always change the redirect URIs assigned to your application, remove them entirely or add new ones." The default redirect URI "is only for the playground environment... you cannot use it in your code" for real integrations. — [help.ctrader.com/open-api/api-application/](https://help.ctrader.com/open-api/api-application/)
- **Authorization URL (directly confirmed from the current official page):** `https://id.ctrader.com/my/settings/openapi/grantingaccess/?client_id={clientId}&redirect_uri={your_redirectURI}&scope={scope}&product=web`. Required params: `client_id` (app identifier), `redirect_uri` (must match a URI registered on the app), `scope`; optional `product=web`. — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/)
- **Scopes:** `accounts` = view-only (account information/statistics; no trading operations possible); `trading` = full access to account info/statistics plus all permitted trading operations. — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/)
- **Token exchange endpoint:** `https://openapi.ctrader.com/apps/token`, method GET, with `grant_type` = `authorization_code` (or `refresh_token` for renewal), plus `code`, `client_id`, `client_secret` (and, per a separate search-engine synthesis of the same flow, `redirect_uri`). — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/); redirect_uri parameter corroborated only via search synthesis, not directly re-quoted from the fetched page text
- Token response fields: `accessToken`, `tokenType`, `expiresIn`, `refreshToken`. — search synthesis citing [App and account authentication](https://help.ctrader.com/open-api/account-authentication/)
- Authorization code lifetime: "issued for an individual cTID with an expiration period of one minute" and must be exchanged quickly for an access token. — search synthesis, attributed to Spotware OAuth documentation (exact fetched page unconfirmed; see Gaps)
- The OpenApiPy Python SDK wraps this same flow: `auth.getAuthUri()` generates a URL pointing to `https://connect.spotware.com/apps/auth`, the user authorizes, is redirected back with a code in the query string, and `auth.getToken("auth_code")` performs the exchange. — [Authentication - OpenApiPy](https://spotware.github.io/OpenApiPy/authentication/)
- **Discrepancy flagged:** the SDK-level doc (OpenApiPy) names `connect.spotware.com/apps/auth` as the authorize entry point, while the current `help.ctrader.com/open-api/account-authentication/` page's authorize URL uses host `id.ctrader.com`. These are plausibly the same flow at different hops (an app-facing entry point that redirects to the cTID login/consent host) or the help-center page reflects a more recent rename; the fetched account-authentication page content explicitly stated "The documentation does not mention connect.spotware.com as an authentication endpoint" when queried directly. The task brief's premise ("via connect.spotware.com") should therefore be treated as SDK-convention-accurate but not an exact match to the currently published raw authorize URL on help.ctrader.com.
- The suggested source `https://connect.spotware.com/docs/api-reference/oauth-services-description` returned **HTTP 404** at fetch time in this research session. — direct WebFetch result, September 2026

### Inferences
- For a production bot, the practical implementation path is: (1) register the app and a real (non-playground) redirect URI at the apps portal under `connect.spotware.com/apps`; (2) use `scope=trading` since order placement requires full trading permissions, not the read-only `accounts` scope; (3) perform the redirect/callback once per account (or once per cTID, then discover accounts) to obtain a refresh token that can be renewed indefinitely thereafter, so the interactive browser step should not need to repeat in normal 24/7 operation.
- Because the authorization code is very short-lived (~1 minute per the search-synthesized figure), the code-exchange step must be automated immediately upon redirect callback (e.g., a local HTTP listener), not something a human relays manually after delay.

### Gaps
- Could not directly fetch/confirm the connect.spotware.com-hosted OAuth reference doc named in the task brief (404). It's unclear whether this page was deprecated/moved, or whether "connect.spotware.com" is now purely the app-management portal while the actual OAuth mechanics live under help.ctrader.com and id.ctrader.com. The report writer should flag this as a likely documentation-restructuring/version drift rather than assume the brief's URL is simply wrong.
- The exact 1-minute authorization-code expiry and the precise full parameter list of the token endpoint (whether `redirect_uri` is strictly required) were not independently re-confirmed via a second direct fetch; treat with moderate (not full) confidence.

## What is the distinction and message sequence between ProtoOAApplicationAuthReq (clientId/clientSecret) and ProtoOAAccountAuthReq (per-account access token)?

### Takeaway
`ProtoOAApplicationAuthReq` authenticates the *application* once per connection using `clientId`/`clientSecret`; only after that succeeds can the client send one `ProtoOAAccountAuthReq` per trading account (using `ctidTraderAccountId` + the user's OAuth `accessToken`) to unlock trading/data requests for that specific account. Sending any other request before app auth completes, or before the relevant account auth completes, produces an error.

### Cited Findings
- Required sequence: (1) `ProtoOAApplicationAuthReq` — authenticates the application itself, fields `clientId` and `clientSecret` ("the unique Client ID provided during the registration" and its corresponding secret); (2) optionally `ProtoOAGetAccountListByAccessTokenReq` — retrieves the accounts a given OAuth `accessToken` is authorized for; (3) `ProtoOAAccountAuthReq` — authenticates a specific trading account, fields `ctidTraderAccountId` and `accessToken`. — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/); message field descriptions corroborated at [Messages - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/messages/)
- "After a connection is established, you should pass the app authorisation flow... If you send any messages before your application is authorised, you will receive an error." — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/)
- "You must send a ProtoOAAccountAuthReq before sending any other request for an account, and after receiving the ProtoOAAccountAuthRes you can start sending other types of requests for that account." — search synthesis of [Messages](https://help.ctrader.com/open-api/messages/) / [App and account authentication](https://help.ctrader.com/open-api/account-authentication/)
- Per-account scoping of session termination: "An event is sent when a session to a specific trader's account is terminated by the server but the existing connections with the other trader's accounts are maintained" — i.e., account auth state is tracked independently per `ctidTraderAccountId` on the same socket. — search synthesis referencing official message docs
- If a cTID user creates additional trading accounts *after* the OAuth consent screen was completed, those new accounts are not automatically authorized inside the application; the user must go through the authorization flow again to grant access to the new account(s). — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/) (search synthesis)

### Inferences
- `ProtoOAApplicationAuthReq` is a connection-level, one-time step (per socket) — it identifies *which app* is talking to the API, independent of any end user. `ProtoOAAccountAuthReq` is a session-level, repeatable step — it must be issued once per `ctidTraderAccountId` the bot intends to trade on, and can be repeated on the same connection for additional accounts without re-doing app auth.
- A robust bot's connection state machine should therefore be: connect → app-auth → (per account) account-auth → trade, and should track authorization state per-account so that one account's token invalidation/disconnection doesn't require tearing down the whole socket.

### Gaps
- None major for the core sequence; see the Token Expiry section below for what happens when a specific account's underlying access token expires after `ProtoOAAccountAuthReq` has already succeeded.

## What is the access token expiry duration, and what's the refresh token flow? What happens to an open connection when the token expires mid-session?

### Takeaway
Access tokens are long-lived (~2,628,000 seconds, i.e., ~30 days) while refresh tokens never expire on a timer but are single-use (each refresh call issues a new refresh token that invalidates the old one). When a token expires mid-session, the affected account's requests start failing with an auth-related `ProtoOAErrorRes`/invalidation event, but this appears to be scoped to that account rather than killing the whole TCP connection.

### Cited Findings
- "Access tokens expire after approximately 30 days; refresh tokens have no expiration and enable token renewal via either HTTP requests or the `ProtoOARefreshTokenReq` message." — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/) (search synthesis)
- More precise figure: "The expiration period of an access token is 2,628,000 seconds (approximately 30 days). The refresh token does not have an expiration period." — search synthesis citing [App and account authentication](https://help.ctrader.com/open-api/account-authentication/) and a community thread, [Refresh token in Open API Sample](https://community.ctrader.com/forum/connect-api-support/24269/)
- FAQ: "The refresh token is valid forever until you use it to refresh an access token or if you re-authorise your cTrader ID and trading accounts." — [FAQ - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/faq/) — i.e., the refresh token is effectively single-use: using it produces a new access+refresh token pair and retires the old refresh token.
- "You can refresh an access token before or after its expiry" via the App and account authentication guide. — [FAQ - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/faq/)
- Refresh mechanics: refresh over HTTP by calling the token endpoint with `grant_type=refresh_token`, or in-band on the same socket via `ProtoOARefreshTokenReq` → `ProtoOARefreshTokenRes` (returns new access + refresh tokens). — [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/) (search synthesis); OpenApiPy SDK equivalent: `auth.refreshToken("refresh_token")`, and "the refresh token has no expiry, but you can only use it once." — [Authentication - OpenApiPy](https://spotware.github.io/OpenApiPy/authentication/)
- Mid-session expiry error surface: an expired token produces an `OA_AUTH_TOKEN_EXPIRED`-style error delivered via `ProtoOAErrorRes`; a token invalidation is separately signaled via `ProtoOAAccountsTokenInvalidatedEvent`. — search synthesis; the existence of `ProtoOAAccountsTokenInvalidatedEvent` was independently corroborated by a direct fetch of [Messages - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/messages/), which states the API uses "token invalidation handled through ProtoOAAccountsTokenInvalidatedEvent" — however, the exact string `OA_AUTH_TOKEN_EXPIRED` itself was not independently verified against the raw `ProtoOAErrorCode` enum in this research pass (see Gaps).
- Per the account-auth message-sequence findings above, session/account-auth termination events are scoped per `ctidTraderAccountId`, with other accounts' authorizations on the same connection unaffected. — search synthesis of official message docs (see previous section)

### Inferences
- Because invalidation is delivered as an event/error tied to a specific account rather than the socket, a production bot most likely does **not** need to reconnect the whole TCP session when one account's token expires — it should catch the invalidation event/error for that `ctidTraderAccountId`, run the refresh flow (HTTP or in-band `ProtoOARefreshTokenReq`), and re-send `ProtoOAAccountAuthReq` for that account with the new access token, leaving other accounts and the socket itself untouched.
- Given refresh tokens are single-use and access tokens last ~30 days, a 24/7 bot should proactively refresh (e.g., on a schedule well inside the 30-day window, such as weekly) rather than waiting for expiry, both to avoid any mid-session error-handling edge cases and because "you can refresh... before or after its expiry" is explicitly sanctioned.
- Persisting the *current* refresh token (and overwriting it after every refresh, since old ones are invalidated on use) is a hard operational requirement — losing track of the latest refresh token would force a full interactive re-authorization.

### Gaps
- The exact, verbatim `ProtoOAErrorCode` value returned for an expired access token was not confirmed from the raw enum source directly (only a plausible name, `OA_AUTH_TOKEN_EXPIRED`, surfaced via search synthesis). The report writer should treat the exact string as unconfirmed/best-guess.
- Whether the underlying TCP connection is ever proactively closed by the server as a side effect of token expiry (versus purely returning account-scoped errors while keeping the socket alive) was not explicitly documented anywhere found in this research — this is inferred, not confirmed.

## Can one physical connection multiplex multiple trading accounts, or is one connection required per account?

### Takeaway
Yes — a single connection can multiplex an unlimited number of trading accounts within the same environment (all-demo or all-live) by sending one `ProtoOAAccountAuthReq` per account after a single app-auth step; but demo and live accounts can never share one connection, so a bot spanning both needs exactly two connections.

### Cited Findings
- "You should create at most two connections: one for demo accounts and one for live accounts, with each connection supporting an unlimited number of accounts of a certain type." — search synthesis of official docs
- "Your application must send a separate ProtoOAAccountAuthReq for each account you want to access... after which the user should be authenticated under that specific account." — search synthesis of [Messages](https://help.ctrader.com/open-api/messages/) / [App and account authentication](https://help.ctrader.com/open-api/account-authentication/)
- "Use separate connections for demo and live accounts; each supports unlimited accounts of that type." — [Establish a connection - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/connection/)
- Rate-limit confirmation implies multiplexing is officially expected behavior: a Spotware-affiliated forum contributor (PanagiotisChar, described as representing Aieden Technologies, a cTrader support provider) stated the 50 req/s and 5 req/s limits "are per connection, no matter how many users are authorized through it," confirming multiple accounts/users sharing one connection is a normal, supported pattern that the rate limiter is explicitly designed around. — [cTrader Forum - Can anyone please explain limits on how many times you can perform certain requests to the cTrader backend?](https://community.ctrader.com/forum/connect-api-support/41177/)

### Inferences
- The architecture strongly favors connection pooling by environment rather than by account: a multi-account trading bot should maintain exactly one live socket (with N account-auths on it) and, if needed, one demo socket — not N separate sockets. This also matters directly for the rate-limit question below, since all accounts on one connection *share* that connection's 50 req/s budget.

### Gaps
- No documented upper bound on how many accounts a single connection can realistically multiplex (the docs say "unlimited," but this is likely aspirational/architectural rather than load-tested guidance) — practical ceilings (e.g., driven by the shared 50 req/s budget) are left to the integrator to reason about.

## What rate limits are documented officially (requests/sec per connection, per account) vs only reported by the community? What are common throttling error codes?

### Takeaway
Spotware officially documents two per-connection ceilings — 50 requests/sec for non-historical data and 5 requests/sec for historical data — and these are explicitly *per connection*, shared across every account authorized on it, not per account. The `REQUEST_FREQUENCY_EXCEEDED` error code (delivered in a `ProtoOAErrorRes`) was confirmed via community discussion that cross-references the same official numbers, giving reasonably high confidence despite not being fetched from an error-code enum directly.

### Cited Findings
- **Official:** "You can perform a maximum of 50 requests per second per connection for any non-historical data requests. You can perform a maximum of 5 requests per second per connection for any historical data requests." — [Getting started - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/) (directly fetched and confirmed)
- **Official, scope clarification:** these limits apply "per connection, no matter how many users are authorized through it" — i.e., they are not per-account limits; adding more accounts to one connection does not multiply the budget. — [cTrader Forum thread, quoting/paraphrasing a support-provider representative](https://community.ctrader.com/forum/connect-api-support/41177/)
- **Error code (community-confirmed, cross-referencing official numbers):** `errorCode: REQUEST_FREQUENCY_EXCEEDED`, description "You have reached the rate limit of requests," delivered with `payloadType: PROTO_OA_ERROR_RES`. — [cTrader Forum - Can anyone please explain limits...](https://community.ctrader.com/forum/connect-api-support/41177/)
- **FAQ, generic framing (no HTTP status/number given):** the 429-style "too many requests in a given period" wording appears in the FAQ purely as a description, without the FAQ page itself stating an HTTP status code or a `Retry-After` header. — [FAQ - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/faq/) (directly fetched; note this page did **not** confirm the HTTP 429/`Retry-After` mechanism — see Gaps)
- Community forum threads specifically discussing this topic (titles only, not deep-fetched in this pass): [Maximum Order Requests per second/hour/day on FIX API](https://community.ctrader.com/forum/fix-api/40973/) (note: this is the *FIX* API, a separate product from Open API, so its numbers should not be assumed to apply to Open API); [What is the maximum concurrent request for each app connected to demo.ctraderapi.com?](https://community.ctrader.com/forum/connect-api-support/21640/).

### Inferences
- Because limits are per-connection and shared across all multiplexed accounts (per the "Multiplexing" section above), a bot managing many accounts on one connection must implement its own client-side request queue/scheduler to stay under 50 req/s aggregate (5 req/s for historical/bar-data pulls) rather than assuming each account gets its own budget.
- The `REQUEST_FREQUENCY_EXCEEDED` code should be treated as the primary, reasonably-confirmed throttling signal to special-case in error handling (e.g., trigger backoff), even though it was corroborated via a forum thread rather than an official error-code table.

### Gaps
- The claim that exceeding limits also produces an HTTP `429 Too Many Requests` with a `Retry-After` header could not be pinned to a specific fetched official page in this research pass — it surfaced only in an early, broad search-engine synthesis and was **not** reproduced when the FAQ page itself was directly fetched. Since cTrader Open API's trading protocol is a persistent socket (not a REST request/response API), an HTTP 429 semantics may actually apply only to REST-style endpoints (e.g., the OAuth token endpoint) rather than to the streaming protocol's `ProtoOAErrorRes`/`REQUEST_FREQUENCY_EXCEEDED` path. This should be treated as **unconfirmed** and flagged to the report writer rather than stated as fact.
- No official, exhaustive list of all throttling-related error codes (e.g., is there a distinct code for historical-data throttling vs general throttling?) was found.
- No official per-account (as opposed to per-connection) limits were found documented anywhere — all confirmed numbers are per-connection.

## What reconnection/backoff strategies does Spotware recommend or does the community commonly implement?

### Takeaway
No official Spotware page was found that prescribes a specific reconnection or backoff algorithm; the only officially documented reliability guidance is the heartbeat requirement itself (send every 10s to avoid inactivity disconnects). Community evidence is limited to bug-report-style forum threads about unexpected disconnects, with heartbeat misconfiguration cited as a real-world root cause.

### Cited Findings
- Official guidance is limited to heartbeat cadence and noting that "applications disconnect after prolonged inactivity" and that the API "may be unavailable during scheduled weekend maintenance and upgrades" — implying any production bot must handle planned weekend outages as a normal, expected disconnection event, not just failures. — [FAQ - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/faq/)
- Community forum threads document real disconnection scenarios: "[Connection to the other side was lost in a non-clean fashion: Connection lost](https://community.ctrader.com/forum/connect-api-support/43751/)"; "[Websocket no longer connecting](https://community.ctrader.com/forum/connect-api-support/42283/)" (WebSocket connections that stop connecting after previously working); a FIX API (not Open API) thread, "[FIX API crash: An existing connection was forcibly closed by the remote host](https://community.ctrader.com/forum/fix-api/14614/)" — included for context only, since FIX API is a different product.
- "Reconfiguring heartbeat code has been reported to fix disconnection problems" in community troubleshooting. — search synthesis of forum discussions (titles above); exact thread not individually re-fetched for verbatim quotes
- Community troubleshooting pattern for auth-related connection failures: testing credentials against the official Python or C# SDKs (OpenApiPy / OpenAPI.Net) is recommended to isolate whether a disconnect is caused by bad credentials/handshake versus a transport-layer issue. — search synthesis referencing forum thread "[Issues with Auth or Credentials](https://community.ctrader.com/forum/connect-api-support/46693/)"

### Inferences
- In the absence of an official backoff spec, the safe default for a 24/7 bot is the general industry-standard pattern — exponential backoff with jitter on reconnect attempts, capped at a reasonable ceiling (e.g., 30–60s), combined with resuming the full auth sequence (app-auth → per-account account-auth) after every reconnect, since the protocol offers no documented "session resume" capability. This is a general engineering inference, not something Spotware documents.
- Because scheduled weekend maintenance is officially acknowledged as an availability gap, a bot's reconnection logic should distinguish (via logging/alerting, not necessarily different code paths) between "unexpected disconnect requiring alerting" and "expected weekend-maintenance window," to avoid false-positive incident alerts every week.

### Gaps
- No Spotware-authored page recommending a specific backoff algorithm, maximum retry count, or reconnect-interval was found. This appears to be a genuine gap in official documentation rather than a search miss, since the "Establish a connection" and FAQ pages — the two most relevant official pages — were both directly fetched and neither mentions backoff.
- The specific forum threads about disconnects (linked above) were identified only by title via search results and not individually fetched for full root-cause detail in this pass; a follow-up pass could fetch these directly for more granular, citable detail if deeper community-reported failure modes are needed.

## What operational differences exist between demo and live accounts that matter for testing (e.g., different servers, different limits, different symbol behavior)?

### Takeaway
The only clearly documented differences are infrastructural: separate hostnames and mandatory separate connections, with Spotware explicitly recommending development/testing on demo before switching to live. No official source documents different rate limits, different symbol sets, or different order-execution behavior between demo and live at the Open API layer — these are governed by broker/liquidity-provider configuration rather than by the API itself.

### Cited Findings
- Separate hostnames/ports: `demo.ctraderapi.com` vs `live.ctraderapi.com`, both on 5035 (Protobuf)/5036 (JSON). — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/)
- "If you connect to a live endpoint, you cannot use demo accounts in your application, and vice versa. If your application needs to operate on behalf of demo and live accounts simultaneously, you would need to establish and maintain two separate connections." — [Proxies and endpoints - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/proxies-endpoints/) (search synthesis)
- Explicit official recommendation: "We recommend using demo accounts for development and testing, and then switching to live" after verifying the integration functions correctly. — [Getting started - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/) (directly fetched)
- "A demo account is a simulated trading environment provided for practice and testing, mirroring live market conditions but using virtual funds instead of real money, while a live account is a real-money trading account that connects to live servers, where all trades are executed with actual funds." — [Trading accounts - cTrader Help](https://help.ctrader.com/ctrader/trading-accounts/) (search synthesis; general cTrader platform doc, not Open-API-specific)
- Symbol availability/specifications (spreads, contract sizes, trading hours, etc.) are determined by each broker's own setup and liquidity providers, a factor that applies identically to demo and live in terms of *mechanism*, though a broker could in principle configure its demo server differently from live. No Open-API-specific documentation was found asserting the two are guaranteed identical or listing specific known differences. — search synthesis, no single authoritative page

### Inferences
- For 24/7 order-placement testing, the practical implication is: demo is safe for validating the full connection/auth/order lifecycle end-to-end (including reconnection and heartbeat handling) with zero financial risk, but a bot's demo-validated behavior around symbol specifics (spread, slippage, fill behavior, and possibly rate-limit headroom under real load) should not be assumed identical to live, since these are broker-configured rather than API-guaranteed constants. This is a reasonable operational caution, not a documented fact.
- Since rate limits are stated generically ("per connection") without a demo/live distinction anywhere found, it's likely — but not confirmed — that the same 50/5 req/s ceiling applies to both environments equally.

### Gaps
- No official source was found that directly confirms or denies whether rate limits, symbol lists, or execution behavior differ between demo and live at the Open API level — this entire question is thinly documented. A dedicated forum thread exists ("[demo account vs live account](https://community.ctrader.com/forum/ctrader-support/43507/)") but was only surfaced by title, not fetched, in this pass.
- No information was found on whether demo servers have different/looser maintenance windows than live, or any other scheduling asymmetry relevant to 24/7 testing continuity.

---

## Source Reliability Summary (for the report writer)

**High confidence (directly fetched from current official pages, and/or corroborated by a primary GitHub proto source):**
- Hostnames/ports (demo/live × Protobuf/JSON) — [proxies-endpoints](https://help.ctrader.com/open-api/proxies-endpoints/)
- TLS mandatory for TCP — [connection](https://help.ctrader.com/open-api/connection/)
- 50 req/s (non-historical) / 5 req/s (historical) per-connection limits — [Getting started](https://help.ctrader.com/open-api/) (official) + [forum corroboration](https://community.ctrader.com/forum/connect-api-support/41177/)
- Heartbeat: send every 10s (official operational guidance); `ProtoHeartbeatEvent` schema and its "30 seconds" framing (primary proto source: [OpenApiCommonMessages.proto](https://raw.githubusercontent.com/spotware/openapi-proto-messages/main/OpenApiCommonMessages.proto))
- OAuth authorize URL (`id.ctrader.com/.../grantingaccess/`), scopes (`accounts` vs `trading`), token endpoint (`openapi.ctrader.com/apps/token`) — [account-authentication](https://help.ctrader.com/open-api/account-authentication/), directly fetched twice
- App-auth → account-auth message sequence and per-account multiplexing on one connection — [account-authentication](https://help.ctrader.com/open-api/account-authentication/) / [connection](https://help.ctrader.com/open-api/connection/)
- Refresh token never timer-expires but is single-use; access token ~30 days — [FAQ](https://help.ctrader.com/open-api/faq/) + search-synthesis of account-authentication page

**Medium confidence (search-engine synthesis of official pages, not independently re-verified verbatim, but internally consistent across multiple searches):**
- Exact access-token lifetime in seconds (2,628,000)
- Authorization-code lifetime (1 minute)
- `OA_AUTH_TOKEN_EXPIRED` as the exact error-code string for expired tokens
- `ProtoOAAccountsTokenInvalidatedEvent` as the invalidation notification (partially corroborated directly via the Messages page)

**Known documentation gap, evidenced directly:** a maintainer-unaddressed GitHub issue confirms Spotware's own error-code system is ambiguous even to integrators: error-code fields are plain strings (not enums) in the wire format, two parallel enums (`ProtoOAErrorCode` and `ProtoErrorCode`) can seemingly appear in either error message type, and it is unclear when `ProtoErrorRes` (vs `ProtoOAErrorRes`) should be expected on the socket. — [Error codes and error messages are not clear or well documented · Issue #30 · spotware/openapi-proto-messages](https://github.com/spotware/openapi-proto-messages/issues/30) (open, unanswered as of fetch time)

**Confirmed dead/moved link from the task brief:** `https://connect.spotware.com/docs/api-reference/oauth-services-description` → HTTP 404 at fetch time. `https://connect.spotware.com/docs/open_api_2/getting_started_v2` → HTTP 301 redirect to `https://help.ctrader.com/open-api/api-application/`. This suggests Spotware has migrated/restructured its docs away from the `connect.spotware.com/docs/...` path toward `help.ctrader.com/open-api/...`, which the report writer should treat as the current canonical documentation root rather than `connect.spotware.com`.
