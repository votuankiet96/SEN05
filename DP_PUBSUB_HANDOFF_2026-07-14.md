# DP Program Handoff: Candle Snapshot Pub/Sub

Date: 2026-07-14

Owner receiving this handoff: DP Program team

Purpose: provide the official Redis Pub/Sub lane that runs in parallel with the existing Redis Stream lane.

## Current Durable Flow

DP Program keeps publishing candle snapshots to Redis with:

```text
Redis db0
SET  dp:candle_snapshot:latest:{SYMBOL}:{TIMEFRAME}
XADD dp:candle_snapshot:events
```

This Stream flow remains durable and unchanged.

## Pub/Sub Flow

After DP writes the latest snapshot state and the durable stream event, DP publishes a lightweight metadata message to:

```text
dp:pubsub:candle_snapshot:events
```

Pub/Sub does not carry the 500 candles. It only tells OG which `state_key` changed.

Correct order:

```text
1. SET latest snapshot key
2. XADD durable Redis Stream event
3. PUBLISH lightweight Pub/Sub metadata
```

## Required Message

```json
{
  "schema_version": 1,
  "event_type": "snapshot_updated",
  "symbol_id": 123,
  "tv_symbol": "HK50",
  "tf_code": "H4",
  "bar_time": "2026-07-14T04:00:00",
  "state_key": "dp:candle_snapshot:latest:HK50:H4",
  "snapshot_version": "HK50:H4:2026-07-14T04:00:00",
  "bars_count": 500,
  "published_at_utc": "2026-07-14T04:05:01Z"
}
```

Redis Pub/Sub channels are not database-scoped. The channel name is the namespace.

## OG Usage

OG Live Pub/Sub mechanism will:

```text
SUBSCRIBE dp:pubsub:candle_snapshot:events
GET message.state_key from Redis db0
Validate snapshot_version and latest_bar_time
Run configured strategy
Publish generated Pub/Sub-mechanism signals to Redis db2
```

The Stream mechanism publishes generated Stream-mechanism signals to Redis db1.

## Safety

If Pub/Sub publish fails, DP should log the issue but must not rollback snapshot state, Stream event, DB writes, or live_fetching.
