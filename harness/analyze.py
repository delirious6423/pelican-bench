#!/usr/bin/env python3
"""Join blind grades with timings and truncation, one row per scored cell, and print the RESULTS tables.

  python3 -I analyze.py --run sweep-1 --export ../docs/data/sweep-1-cells.csv   # needs the run directory
  python3 -I analyze.py --csv ../docs/data/sweep-1-cells.csv                    # tables from the published CSV

Truncated = the request ended on the token ceiling (finish_reason "length") or the wall deadline. A truncated cell
keeps the grade it got (usually low: no closed SVG), and is counted in its own column, never hidden in the mean.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
EFFORTS = ("low", "medium", "high")
LABELS = {"gyro-m-tq2_0": "Gyro-M TQ2_0", "gyro-s-tq1_0": "Gyro-S TQ1_0", "gguf-iq3_s": "GGUF IQ3_S",
          "gguf-iq3_xxs": "GGUF IQ3_XXS", "td-exl3-2.68-h6-ng8": "turboderp EXL3 2.68bpw h6",
          "td-exl3-2.18-h6-ng8": "turboderp EXL3 2.18bpw h6", "sc-exl3-3.05-h8": "EXL3-SC 3.05bpw h8",
          "sc-exl3-2.05-h8": "EXL3-SC 2.05bpw h8"}
COLS = ["model_id", "engine", "effort", "seed", "code", "grade", "wall_s", "t_first_answer_s", "completion_tokens",
        "max_tokens", "deadline_s", "finish_reason", "abort", "truncated", "svg_status"]


def export(run: Path) -> list[dict]:
    latest = {}
    for line in (run / "pelican/runs.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("phase") == "scored" and r["attempt"] >= latest.get(r["cell_id"], {}).get("attempt", 0):
            latest[r["cell_id"]] = r
    with (run / "reveal/reveal-key.csv").open(encoding="utf-8", newline="") as f:
        key = {r["cell_id"]: r["code"] for r in csv.DictReader(f)}
    scores = json.loads((run / "grade/scores-latest.json").read_text())["scores"]
    rows = []
    for cid, r in sorted(latest.items()):
        code = key[cid]
        rows.append({"model_id": r["model_id"], "engine": r["engine"], "effort": r["requested_effort"],
                     "seed": r["seed"], "code": code, "grade": scores.get(code, {}).get("overall_1_10"),
                     "wall_s": round(r["wall_s"], 1) if r.get("wall_s") is not None else None,
                     "t_first_answer_s": round(r["t_first_answer_s"], 1) if r.get("t_first_answer_s") is not None else None,
                     "completion_tokens": r.get("completion_tokens"), "max_tokens": r["max_tokens"],
                     "deadline_s": r["deadline_s"], "finish_reason": r.get("finish_reason"), "abort": r.get("abort") or "",
                     "truncated": int(bool(r.get("cap_hit")) or r.get("abort") == "deadline"),
                     "svg_status": r.get("svg_status")})
    return rows


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("seed", "grade", "completion_tokens", "max_tokens", "truncated"):
            r[k] = int(r[k]) if r[k] not in ("", None) else None
        for k in ("wall_s", "t_first_answer_s", "deadline_s"):
            r[k] = float(r[k]) if r[k] not in ("", None) else None
    return rows


def mmss(s):
    if s is None:
        return "-"
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def tables(rows: list[dict]) -> str:
    by = defaultdict(list)
    for r in rows:
        by[(r["model_id"], r["effort"])].append(r)
    models = sorted({r["model_id"] for r in rows},
                    key=lambda m: -statistics.mean(x["grade"] for x in by[(m, "high")] if x["grade"] is not None))
    seeds = sorted({r["seed"] for r in rows})
    ceiling = {r["max_tokens"] for r in rows}
    deadline = {r["deadline_s"] for r in rows}
    L = [f"Each cell: blind grade (1-10) / wall time (m:ss). **T** = truncated (token ceiling "
         f"{'/'.join(str(c) for c in sorted(ceiling))} or {'/'.join(f'{d:.0f}' for d in sorted(deadline))} s deadline). "
         "Models sorted by high-effort mean.", ""]
    for e in ("high", "medium", "low"):
        L += [f"### {e} effort", "",
              "| model | mean | " + " | ".join(f"seed {s}" for s in seeds) + " | median time | truncated |",
              "|---|---|" + "---|" * len(seeds) + "---|---|"]
        for m in models:
            cell = {r["seed"]: r for r in by[(m, e)]}
            grades = [r["grade"] for r in cell.values() if r["grade"] is not None]
            seed_cols = []
            for s in seeds:
                r = cell.get(s)
                seed_cols.append("-" if r is None else
                                 f"{r['grade'] if r['grade'] is not None else '?'} / {mmss(r['wall_s'])}"
                                 + (" **T**" if r["truncated"] else ""))
            n_tr = sum(r["truncated"] for r in cell.values())
            L.append(f"| {LABELS.get(m, m)} | {statistics.mean(grades):.1f} | " + " | ".join(seed_cols) +
                     f" | {mmss(statistics.median(r['wall_s'] for r in cell.values()))} | {n_tr}/{len(cell)} |")
        L.append("")
    L += ["### Token headroom", "",
          f"Most tokens one run used (reasoning + answer) against the ceiling of "
          f"{'/'.join(str(c) for c in sorted(ceiling))}.", "",
          "| model | " + " | ".join(EFFORTS) + " |", "|---|" + "---|" * len(EFFORTS)]
    for m in models:
        L.append(f"| {LABELS.get(m, m)} | " + " | ".join(
            str(max(r["completion_tokens"] for r in by[(m, e)])) for e in EFFORTS) + " |")
    tr = [r for r in rows if r["truncated"]]
    L += ["", f"Total: {len(tr)}/{len(rows)} truncated"
          + (" (" + ", ".join(f"{LABELS.get(r['model_id'], r['model_id'])} {r['effort']} seed {r['seed']}: "
                              f"{r['finish_reason'] or r['abort']} at {r['completion_tokens']} tokens, {mmss(r['wall_s'])}"
                              for r in tr) + ")" if tr else "") + "."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", help="run name under $PELICAN_ROOT/runs")
    src.add_argument("--csv", type=Path, help="a cells CSV written by --export")
    ap.add_argument("--export", type=Path, help="with --run: write the per-cell CSV here")
    a = ap.parse_args(argv)
    if a.run:
        rows = export(ROOT / "runs" / a.run)
        if a.export:
            a.export.parent.mkdir(parents=True, exist_ok=True)
            with open(a.export, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLS)
                w.writeheader()
                w.writerows(rows)
            rows = load_csv(a.export)                 # print from the file, so the tables match what is published
    else:
        rows = load_csv(a.csv)
    sys.stdout.write(tables(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
