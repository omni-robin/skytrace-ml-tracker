# iq-stream-consumer detector drop-in (no source)

This package contains:
- a drop-in `iq-stream-consumer.py` (Kafka consume unchanged, optional detector + publish)
- optional `ngva_adapter.py` (bonus)
- prebuilt detector binaries (Linux amd64 + arm64) under `bin/`

## Install (folder name assumed: `iq-stream-consumer/`)

1) Copy these files into your existing `iq-stream-consumer/` folder:
- `iq-stream-consumer.py` (overwrite existing)
- `ngva_adapter.py` (optional)

2) Put ONE detector binary into the container image (recommended) or mount it in.
Choose based on container CPU arch:
- amd64: `bin/edgehub-lora-detector-linux-amd64`
- arm64: `bin/edgehub-lora-detector-linux-arm64`

Place it at:
- `/usr/local/bin/edgehub-lora-detector`

and ensure it is executable.

3) Ensure your compose mounts the SigMF directory to `/shared_data` (matches Kafka paths):

```yaml
volumes:
  - /host/path/shared_data:/shared_data
```

## Usage (main test: Kafka → detections topic)

Set env vars:

```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
KAFKA_TOPIC=iq_stream_topic
GROUP_ID=local-analysis-group

# Enable detector
ENABLE_DETECTOR=true
DETECTOR_CMD=edgehub-lora-detector
KAFKA_DETECTIONS_TOPIC=iq_detections_topic

# Bonus off by default
ENABLE_NGVA_ADAPTER=false
```

Expected result:
- for each `files_uploaded` event, a new message is published to `iq_detections_topic`
  with `event_type=detections_computed` and a `detections` payload.

Output notes (detector JSON):
- `freq_bounds_hz.center` is always `(lower+upper)/2` and `freq_bounds_hz.bandwidth` is `(upper-lower)`.
- `fingerprint.bandwidth_est_hz` is a chirp-derived BW estimate (often closer to configured BW than the energy span).
- `fingerprint.sf_est` is an estimated LoRa spreading factor derived from BW*Tsym.

## Dechirp demo ("chirp collapses to tone")

This drop-in includes a **Dechirp demo** tab in the NiceGUI UI.

- It renders **side-by-side** waterfalls:
  - shifted-to-center (raw chirps)
  - shifted + dechirped (chirps collapse to a narrow tone)
- It also plots the **peak-frequency track**, which should flatten after dechirp.

Notes:
- Requires `ENABLE_DETECTOR=true` so we have chirp slope estimates.
- Uses the SigMF paths from Kafka events (`/shared_data/...`) or you can paste local
  `meta_file_path`/`data_file_path` and click **Run detector on paths**.

## Tracking controllers/drones across files (Kafka)

The detector emits a **controller fingerprint** intended for cross-file correlation.

- Use `detections.controllers[].fingerprint_id` for exact matching.
- For drift/near-matches, compute a similarity score using the quantized
  `detections.controllers[].fingerprint.bins` (see `TRACKING_OVER_KAFKA.md`).
- Do **not** use `controller_id` for cross-file tracking; it is only stable within one output.

## Examples-only debug: expected controller ranges

If (and only if) the SigMF meta contains the example-format expected controller bands under:
`global.annotations.custom.rc_configuration.rcs[]`, you can ask the detector to emit a debug report:

```bash
edgehub-lora-detector --emit-expected-report --expected-min-overlap-ratio 0.2 --meta ... --data ...
```

This is intended for lab/examples. Field metadata will not contain these ranges.

## Performance notes

- Always run the detector binary built in **release** (already provided in this package).
- Keep Kafka messages small: this pipeline sends references to files, not the IQ payload.
- If you need faster processing, reduce STFT work (future): fewer FFT windows / sparse STFT.

## Bonus: NGVA adapter (optional)

Enable:

```bash
ENABLE_NGVA_ADAPTER=true
NGVA_ADAPTER_KIND=stdout   # or udp_json / http_json
```

This is a thin bridge/placeholder only (NOT a full STANAG 4754 implementation).
