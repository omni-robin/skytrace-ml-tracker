# TASK.md — skytrace-ml-tracker (Next-gen LoRa controller separation)

## Why this exists
SkytraceRT_poc proved we can get decent coverage with an occupancy-style model, but **merged bands are unacceptable**.

This project is the next-gen approach aimed at:
- separating **multiple LoRa controllers** in a contested RF scene
- hitting **edge tolerance ±0.4% of GT bandwidth** per controller
- being **deployable on Orin** (small, fast models)
- being **easy to retrain** on new battle data

Constraints / environment:
- Each IQ capture is short (~4–8 ms) then the antenna **retunes** to another wide band.
- Temporal accumulation is allowed, but observations are **gappy** and **non-contiguous in frequency**.
- Metadata GT controllers are 100% LoRa, but the RF scene may include non-LoRa trash (LTE/3G/etc).

## Success criteria (hard gates)
For evaluation on labeled captures:
1) **No merges:** each GT controller band must match a distinct predicted controller band (1:1 matching)
2) **Edge accuracy:** for each matched band:
   - `|pred_lower - gt_lower| / gt_bw <= 0.004`
   - `|pred_upper - gt_upper| / gt_bw <= 0.004`
3) **High recall:** GT coverage and controller recall are prioritized over precision initially

## Proposed pipeline (hybrid ML + tracking)
### Stage A — ML burst / controller proposal (per capture)
Input: short IQ window (or spectrogram)
Output: a set of candidate LoRa emissions with:
- center_hz
- bandwidth_hz
- chirp_slope_hz_per_s (or equivalent)
- symbol_duration_s / SF proxy
- confidence + LoRa-likeness score

Model direction (keep small):
- tiny CNN over log-magnitude spectrogram (or 1D conv over log-PSD)
- heads for regression (center/bw/slope) + classification (LoRa-likeness)

### Stage B — Multi-target association / tracking across gappy revisits
- maintain tracks keyed by (center, slope, bw, sym_dur)
- gated association + track persistence across time gaps

### Stage C — Edge refinement (DSP or learned local refinement)
- once a track exists, refine edges in a narrow band around it
- optional learned edge refiner (small MLP) using local high-res PSD slice

## Work plan (start here)
### 0) Repo skeleton
- [ ] Create minimal python package + scripts folder
- [ ] Add a single entrypoint script to run end-to-end on a folder of SigMF

### 1) Build the evaluator (before models)
- [ ] Implement band matching (Hungarian or greedy IoU) with strict non-merge
- [ ] Implement the ±0.4% edge metrics
- [ ] Produce a report JSON + summary table

### 2) Baseline feature extractor (DSP) — for labels + sanity
- [ ] Implement simple LoRa-ish event extractor (even crude) to generate training targets / proposals
- [ ] Verify it separates controllers better than occupancy regions

### 3) ML v0 model: spectrogram → proposals
- [ ] Define training data format: per-capture spectrogram + GT controller bands (+ optional slope if available)
- [ ] Train tiny model to predict multiple controllers (set prediction or dense heatmaps)
- [ ] Evaluate against gates

### 4) Tracking + refinement
- [ ] Implement association with gap tolerance
- [ ] Add edge refinement and re-evaluate

## Experiments (log here)
- (empty)

## Notes
- Prefer models that are exportable to ONNX/TensorRT.
- Keep training simple and fast: few epochs, few million params, strong augmentations.
