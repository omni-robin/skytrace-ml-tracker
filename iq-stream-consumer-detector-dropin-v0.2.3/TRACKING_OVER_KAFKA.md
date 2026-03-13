# Tracking controllers/drones over time via Kafka

This pipeline publishes detections to Kafka and allows downstream services to correlate
activity across capture files.

## Where the data is

`iq-stream-consumer` publishes one message per processed capture to:

- Topic: `KAFKA_DETECTIONS_TOPIC` (default: `iq_detections_topic`)
- Message: JSON envelope with `event_type = "detections_computed"`

The detector payload is in:

- `event["detections"]["controllers"][]`
- `event["detections"]["detections"][]`

## What to use for tracking

Each controller includes:

- `controller_id` — only stable within a single file (do not use across files)
- `fingerprint_id` — a stable-ish key intended for correlation across files

Recommended strategy:

1) Use `fingerprint_id` as the primary key for grouping.
2) If you need drift-tolerant matching, follow internal guidance for fuzzy matching.

## Operational notes

- The detector processes one file at a time. Tracking/correlation is downstream.
- Use a sliding time window when doing correlation.
