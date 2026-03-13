import json, glob, os, subprocess, shlex

CAPS = [
  "iq-capture-1667919724678940790",
  "iq-capture-1667921356665449380",
  "rec102",
]
BASE = "tmp_result_016/result-0.1.6"
BIN = "edgehub-lora-detector/target/release/edgehub-lora-detector"

PRESETS = {
  "default": "",
  "high_sr_sf5": "--fft-size 1024 --hop 128 --threshold-db 8 --min-area 20 --min-height-px 2 --min-width-px 1 --min-burst-snr-db 0 --group-min-pulse-count 1 --group-min-avg-snr-db 0 --emit-non-lora",
  "high_overlap_strict": "--fft-size 2048 --hop 256 --threshold-db 8 --min-area 20 --min-height-px 2 --min-width-px 1 --min-burst-snr-db 10 --group-min-pulse-count 1 --group-min-avg-snr-db 10",
  "rapid": "--mode rapid --fft-size 8192 --rapid-windows 32 --rapid-baseline-quantile 0.2 --threshold-db 8",
}

def load_expected(path):
  j=json.load(open(path))
  ctrls=j.get('controllers',[])
  exp_ctrl=len(ctrls)
  exp_det=sum(len(c.get('detections',[])) for c in ctrls)
  return exp_ctrl, exp_det

def load_inf(path):
  j=json.load(open(path))
  ctrls=j.get('controllers',[])
  inf_ctrl=len(ctrls)
  # controller-level detections summary if present
  inf_det=0
  for c in ctrls:
    ds=c.get('detections') or []
    inf_det += len(ds)
  # fallback to top-level detections
  if inf_det==0:
    inf_det = len(j.get('detections',[]))
  return inf_ctrl, inf_det

print("capture\tpreset\texp_ctrl\texp_det\tinf_ctrl\tinf_det\tcmd")
for cap in CAPS:
  meta=f"{BASE}/{cap}.sigmf-meta"
  data=f"{BASE}/{cap}.sigmf-data"
  exp=f"{BASE}/{cap}-expected.json"
  exp_ctrl, exp_det = load_expected(exp)
  for pname, args in PRESETS.items():
    out=f"tmp_result_016/inf_{cap}_{pname}.json"
    cmd=[BIN, "--meta", meta, "--data", data]
    if args:
      cmd += shlex.split(args)
    cmd += ["--out", out]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    inf_ctrl, inf_det = load_inf(out)
    print(f"{cap}\t{pname}\t{exp_ctrl}\t{exp_det}\t{inf_ctrl}\t{inf_det}\t{' '.join(cmd)}")
