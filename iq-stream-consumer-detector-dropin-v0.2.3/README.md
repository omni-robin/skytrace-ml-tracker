# iq-stream-consumer detector drop-in (no source)

This package contains:
- a drop-in `iq-stream-consumer.py` (Kafka consume unchanged, optional detector + publish)
- optional `ngva_adapter.py` (bonus)
- prebuilt detector binaries (Linux amd64 + arm64) under `bin/`

Detector output schema (current): `edgehub-lora-detector:v2.0`

Note: This drop-in intentionally keeps implementation details out of the docs. It’s meant to be “install + run” for field techs.

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

# Advanced: optional detector tuning
# You may pass additional detector flags if asked (internal guidance).
DETECTOR_ARGS=""

# Optional: use a named preset (applies ONLY when explicitly set)
# If you were given a preset name, set it here. Otherwise leave it unset.
DETECTOR_PRESET=

# Bonus off by default
ENABLE_NGVA_ADAPTER=false
```

Expected result:
- for each `files_uploaded` event, a new message is published to `iq_detections_topic`
  with `event_type=detections_computed` and a `detections` payload.

Output notes (detector JSON):
- The output includes an `output_schema` field for downstream compatibility checks.
- The output includes a per-controller `fingerprint_id` intended for cross-file correlation.

## UI diagnostics (optional)

This drop-in includes optional UI diagnostics for internal troubleshooting.
Field use does not require this.

## Tracking controllers/drones across files (Kafka)

The detector emits a **controller fingerprint** intended for cross-file correlation.

- Use `detections.controllers[].fingerprint_id` for correlation across files.
- Do **not** use `controller_id` for cross-file tracking; it is only stable within one output.

## Debugging

If you need deeper debugging, ask for internal guidance.

## Performance notes

- Always run the detector binary built in **release** (already provided in this package).
- Keep Kafka messages small: this pipeline sends references to files, not the IQ payload.
- If performance is insufficient, ask for internal guidance.

## Bonus: NGVA adapter (optional)

Enable:

```bash
ENABLE_NGVA_ADAPTER=true
NGVA_ADAPTER_KIND=stdout   # or udp_json / http_json
```

This is a thin bridge/placeholder only (NOT a full STANAG 4754 implementation).
