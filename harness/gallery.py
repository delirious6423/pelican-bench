#!/usr/bin/env python3
"""Blind pelican gallery: render every scored SVG in the sandbox, show PNGs only, labels hidden, order shuffled.

  python3 -I gallery.py --run sweep-1

Outputs (under <run>/):
  gallery/index.html          static page, no scripts, PNG <img> tags and text tiles only (safe to open)
  gallery/png/<code>.png      sandbox renders on a white 512x512 canvas
  gallery/scoring-sheet.csv   one row per code, empty rubric columns for the user (no scores are invented)
  reveal/reveal-key.csv/json  code -> model, effort, seed, cell, status (separate; not linked from the gallery)
  renders.jsonl               per-output render status (wellformed, librsvg result, blank detector, flags)
Rendering happens after inference, never during timed runs. Nothing is repaired: a non-rendering SVG is a result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render  # noqa: E402

ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
RUBRIC = ["pelican_recognizable_0_2", "bicycle_recognizable_0_2", "riding_contact_0_2", "composition_0_2",
          "overall_1_10", "notes"]


def scored_records(run_dir: Path) -> list[dict]:
    latest = {}
    for line in open(run_dir / "pelican/runs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r.get("phase") != "scored":
            continue
        if r["cell_id"] not in latest or r["attempt"] > latest[r["cell_id"]]["attempt"]:
            latest[r["cell_id"]] = r
    return sorted(latest.values(), key=lambda r: r["cell_id"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    run = ROOT / "runs" / a.run
    recs = scored_records(run)
    rng = random.Random(int(hashlib.sha256(f"gallery:{a.run}".encode()).hexdigest()[:12], 16))
    codes = rng.sample(range(100, 1000), len(recs))
    order = list(range(len(recs)))
    rng.shuffle(order)
    gdir, rdir = run / "gallery", run / "reveal"
    (gdir / "png").mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    rows, tiles = [], []
    with open(run / "renders.jsonl", "w", encoding="utf-8") as rj:
        for i in order:
            r = recs[i]
            code = f"P{codes[i]}"
            st = {"code": code, "cell_id": r["cell_id"], "svg_status": r["svg_status"]}
            if r["svg_status"] == "complete":
                svg = Path(r["svg_file"]).read_text(encoding="utf-8")
                res = render.render(svg, gdir / "png" / f"{code}.png")
                st.update(res)
                if res.get("ok"):
                    label = None
                    if (res.get("nonwhite_frac") or 0) < 0.002:
                        label = "rendered blank (nothing visible)"
                else:
                    label = {"xml": "SVG is not well-formed XML", "rsvg-load": "renderer rejected the SVG",
                             "rsvg-render": "renderer failed", "timeout": "render timed out"}.get(res.get("stage"),
                                                                                                 "render failed")
            else:
                label = {"none": "no SVG in the answer", "unclosed": "SVG incomplete (no closing tag)"}[r["svg_status"]]
                if r.get("abort") == "deadline":
                    label += " - wall-time deadline"
                elif r.get("cap_hit"):
                    label += " - token cap reached"
                elif r.get("abort"):
                    label += f" - {r['abort']}"
            st["tile_label"] = label
            rj.write(json.dumps(st) + "\n")
            png = st.get("png") if st.get("ok") else None
            if png:
                tile = f'<img src="png/{code}.png" width="256" height="256" alt="{code}">'
                if label:
                    tile += f'<div class="note">{html.escape(label)}</div>'
            else:
                tile = f'<div class="fail">{html.escape(label)}</div>'
            tiles.append(f'<figure>{tile}<figcaption>{code}</figcaption></figure>')
            rows.append({"code": code, "model_id": r["model_id"], "engine": r["engine"], "effort": r["requested_effort"],
                         "resolved_effort": r["resolved_effort"], "seed": r["seed"], "cell_id": r["cell_id"],
                         "attempt": r["attempt"], "svg_status": r["svg_status"], "render_ok": bool(st.get("ok")),
                         "render_stage": st.get("stage"), "blank": (st.get("nonwhite_frac") or 0) < 0.002 if st.get("ok") else None,
                         "unsafe_flags": ";".join(r.get("svg_unsafe") or []), "finish_reason": r["finish_reason"],
                         "abort": r.get("abort"), "completion_tokens": r.get("completion_tokens"),
                         "raw_file": r.get("raw_file"), "svg_file": r.get("svg_file")})
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Blind pelican gallery ({len(rows)} outputs)</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
<style>body{{font-family:sans-serif;background:#eee;margin:16px}} figure{{display:inline-block;margin:6px;background:#fff;
padding:6px;vertical-align:top;width:256px}} img{{display:block;border:1px solid #ccc}} figcaption{{text-align:center;
font-weight:bold;margin-top:4px}} .fail{{width:254px;height:254px;border:1px dashed #a33;display:flex;align-items:center;
justify-content:center;text-align:center;color:#a33;padding:4px;box-sizing:border-box}} .note{{color:#a33;font-size:12px}}</style>
</head><body>
<h1>Blind pelican gallery</h1>
<p>Prompt: <i>Generate an SVG of a pelican riding a bicycle</i>. {len(rows)} scored outputs (every model x low/medium/high x 5
seeds), shuffled, labels hidden. PNG renders only (librsvg, scriptless, offline sandbox, white 512x512 canvas, nothing
repaired). Score with <code>scoring-sheet.csv</code>; the key is in <code>../reveal/</code> - open it only after scoring.</p>
{''.join(tiles)}
</body></html>
"""
    (gdir / "index.html").write_text(page, encoding="utf-8")
    with open(gdir / "scoring-sheet.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code"] + RUBRIC)
        for row in sorted(rows, key=lambda x: x["code"]):
            w.writerow([row["code"]] + [""] * len(RUBRIC))
    with open(rdir / "reveal-key.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for row in sorted(rows, key=lambda x: x["code"]):
            w.writerow(row)
    (rdir / "reveal-key.json").write_text(json.dumps(sorted(rows, key=lambda x: x["code"]), indent=1))
    ok = sum(r["render_ok"] for r in rows)
    print(f"{len(rows)} outputs, {ok} rendered; gallery {gdir / 'index.html'}; key {rdir / 'reveal-key.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
