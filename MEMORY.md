# MEMORY.md - Long-Term Memory

## Projects

### SkytraceRT_poc
- New PoC repo: https://github.com/omni-robin/SkytraceRT_poc
- Uses SigMF IQ captures with ground truth controller bands embedded in meta at `global.annotations.custom.rc_configuration.rcs[]`.
- Pipeline (best so far): raw IQ → FFT/log-PSD feature vector → tiny MLP occupancy model → postprocess to controller bands (absolute Hz).
- Postprocess improved with smoothing + hysteresis + sub-bin edge refinement.
- Local datasets pulled from GCS `yolo-oscilion/Yolo_Oscilion/unprocessed/capture_data_config_info/` into workspace subset folders (subset20/subset100).

### skytrace-utils (`stu`)
- Added `stu sigmf quicklook` for fast spectrogram previews: renders a colored terminal waterfall and can optionally write a PNG.
- Added pipeline metric `sigmf-quicklook-png` for `stu pipe artifacts|detections`, writing PNGs to `--out-dir`.
- Added quicklook scaling controls:
  - `stu sigmf quicklook --db-min/--db-max` and `--autoscale p5,p99`.
  - pipeline equivalents: `--ql-db-min/--ql-db-max` and `--ql-autoscale`.
- When publishing to an out-topic:
  - quicklook metric emits `event_type: quicklook_generated` (others remain `metrics_computed`).
  - quicklook JSON result includes `png_path`, `db_min/db_max`, and `autoscale_pct`.

### edgehub-lora-detector
- Rust CLI in workspace that detects LoRa-ish bursts in SigMF captures and emits UI-friendly JSON.
- Output schema includes per-controller/group fingerprints:
  - `fingerprint.center_hz`, `bandwidth_hz`, `chirp_slope_hz_per_s`, `symbol_duration_s`, plus quantized `fingerprint.bins.*` and a stable-ish `fingerprint_id` string.
- README recommends downstream similarity scoring using normalized distances over these features, with confidence gating using chirp R² / coverage.
- TECH_KIT.md documents Kafka integration with `iq-stream-consumer` using a `detections_computed` envelope and a multi-stage Dockerfile.
