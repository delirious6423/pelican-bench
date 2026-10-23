#!/bin/bash
# Compact status of a running sweep: progress, last events, health. Read-only.
R=${PELICAN_ROOT:-pelican-data}/runs/${1:-sweep-1}
echo "units: sweep=$(systemctl --user is-active strata-pelican-sweep) serve=$(systemctl --user is-active strata-pelican-serve)  nvme=$(cat ${PELICAN_NVME_STATE:-/sys/class/nvme/nvme0/state})  $(date +%H:%M:%S)"
python3 -I - "$R" <<'EOF'
import json, sys, collections
from pathlib import Path
R = Path(sys.argv[1])
recs = [json.loads(l) for l in open(R / "pelican/runs.jsonl")] if (R / "pelican/runs.jsonl").exists() else []
sc = [r for r in recs if r["phase"] == "scored"]
by = collections.defaultdict(list)
for r in sc:
    by[r["model_id"]].append(r)
print(f"scored {len(sc)}/120 | " + " ".join(f"{m.split('-')[0]}{m.split('-')[1] if len(m.split('-'))>1 else ''}:{len(v)}" for m, v in by.items()))
bad = [r for r in sc if r.get("abort") or r.get("cap_hit") or r.get("svg_status") != "complete"]
print(f"non-complete/cap/abort: {len(bad)} " + "; ".join(f"{r['cell_id']}:{r['svg_status']}/{r['finish_reason']}/{r.get('abort')}" for r in bad[-4:]))
qe = sorted(p.parent.name for p in (R / "quick-eval").glob("*/summary.json")) if (R / "quick-eval").exists() else []
print("quick-eval done:", qe)
st = json.loads((R / "state.json").read_text()) if (R / "state.json").exists() else {}
print("models:", {m: v.get("status") for m, v in st.get("models", {}).items()})
EOF
tail -2 "$R/events.log" | cut -c1-220
free -g | awk 'NR==2{printf "RAM avail %sG ",$7} NR==3{printf "swap used %sG\n",$3}'
nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.sm,memory.used --format=csv,noheader
[ -f "$R/STORAGE-ALARM.txt" ] && cat "$R/STORAGE-ALARM.txt"; true
