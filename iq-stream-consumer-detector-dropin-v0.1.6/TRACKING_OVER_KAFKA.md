# Tracking controllers/drones over time via Kafka

This pipeline publishes LoRa-ish detections to Kafka and (optionally) allows downstream
services to **correlate** detections across capture files.

## 1) Where the data is

`iq-stream-consumer` publishes one message per processed capture to:

- Topic: `KAFKA_DETECTIONS_TOPIC` (default: `iq_detections_topic`)
- Message: JSON envelope with `event_type = "detections_computed"`

The detector payload is in:

- `event["detections"]["controllers"][]`
- `event["detections"]["detections"][]`

## 2) What to use for tracking

Each controller (group) includes:

- `controller_id` — **only stable within a single file** (do not use across files)
- `fingerprint_id` — a stable-ish, quantized key intended for **exact matching** across files
- `fingerprint` — includes both unquantized medians and `fingerprint.bins` (quantized)

Recommended strategy:

1) Use `fingerprint_id` as the primary key for fast grouping.
2) Also compute a **similarity score** between fingerprints to handle drift and near-matches.
3) Gate/weight by confidence:
   - `fingerprint.chirp_r2_med` (controller level)
   - per-burst `chirp.r2` and `chirp.coverage` (detection level)

## 3) Similarity score (reference)

You can score similarity between two controller fingerprints using normalized distances.

Suggested tolerances (tune to your RF frontend / sample rate / expected drift):

- `center_tol_hz = 5_000`
- `bw_tol_hz = 5_000`
- `slope_tol_hz_per_s = 2_000_000`
- `tsym_tol_s = 50e-6`

Compute distances for available features:

- `d_center = abs(c1 - c2) / center_tol_hz`
- `d_bw = abs(bw1 - bw2) / bw_tol_hz`
- `d_slope = abs(k1 - k2) / slope_tol_hz_per_s`
- `d_tsym = abs(t1 - t2) / tsym_tol_s`

Combine (RMS):

- `d = sqrt(mean(d_i^2))`

Convert to similarity:

- `sim = 1 / (1 + d)`  (range ~0..1)

### Confidence gating

If you want to avoid false tracking when the chirp fit is weak:

- require `fingerprint.chirp_r2_med >= 0.7` (example)
- and/or require at least N detections with `chirp.r2 >= 0.7` and `chirp.coverage >= 0.6`

## 4) Minimal consumer example (Python)

Pseudo-code (outline):

```python
# consume iq_detections_topic
for event in kafka_messages:
    if event.get('event_type') != 'detections_computed':
        continue

    controllers = (event.get('detections') or {}).get('controllers') or []

    for c in controllers:
        fp_id = c.get('fingerprint_id')
        fp = c.get('fingerprint') or {}
        bins = fp.get('bins') or {}

        # exact match bucket
        track_key = fp_id

        # optional: fuzzy match against recent tracks
        # sim = fingerprint_similarity(fp, other_fp)
```

## 5) Operational notes

- The detector sees one file at a time. Tracking/correlation is intentionally downstream.
- Use a sliding time window (e.g., last 5–30 minutes) when doing fuzzy matching.
- Store both `fingerprint_id` and the raw `fingerprint` object for audit/debug.
