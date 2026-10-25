#!/usr/bin/env python3
"""Offline sweep-1 evidence export and deterministic Markdown reports. Never imports the runner.

python3 -I harness/publish.py --source /big/disk/Strata-pelican
python3 -I harness/publish.py --check       # verify the checked-in evidence and reports

This is deliberately an exporter for this recorded experiment, not a general publication service.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import hashlib
import html
import io
import json
import re
import socket
import statistics as st
import sys
import shutil
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze

DOCS = Path(__file__).resolve().parents[1] / "docs"
JUDGES = ("astra", "sol", "luna", "sonnet", "haiku")
SUBS = ("pelican_recognizable_0_2", "bicycle_recognizable_0_2", "riding_contact_0_2", "composition_0_2")
# Set only while exporting; private machine roots/timestamps never become checked-in constants.
PREFIXES = ()
TIME_ORIGIN = 0.0
PRIVATE_KEYS = {"pids", "listener_pids", "unit_pids", "pid", "inode", "chunk_id", "host", "port",
                "base_url", "unit_name", "config_path", "log", "log_offset", "load_log", "launch",
                "scan", "cmd", "argv", "ts", "started", "generated", "saved"}
TIME_KEYS = {"t", "t0", "t1", "time", "t_wall_start", "t_wall_end", "start_wall"}
PUBLIC_ENV = {"CUDA_VISIBLE_DEVICES", "STRATA_EXL3_DENSE", "STRATA_ROUTE_TAIL_SKIP", "STRATA_EXL3_MISS_SHARE",
              "STRATA_ROUTE_RESIDENT", "STRATA_HC_REQ8"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def scrub(value):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k in PRIVATE_KEYS:
                continue
            if k == "env":
                result[k] = {name: scrub(setting) for name, setting in v.items() if name in PUBLIC_ENV}
            elif k == "metrics_entry" and isinstance(v, dict):
                result[k] = scrub({name: setting for name, setting in v.items() if name != "id"})
            elif k in TIME_KEYS and isinstance(v, (int, float)) and v > 1_000_000_000:
                require(TIME_ORIGIN > 0, "Timestamp needs a private export origin")
                result[k] = round(v - TIME_ORIGIN, 6)
            else:
                result[k] = scrub(v)
        return result
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        for old, new in PREFIXES:
            value = value.replace(old, new)
        value = re.sub(r"\b20\d{2}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?",
                       "[timestamp omitted]", value)
    return value


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, value):
    write_text(path, json.dumps(scrub(value), ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path, rows):
    write_text(path, "".join(json.dumps(scrub(r), ensure_ascii=False) + "\n" for r in rows))


def unique(rows, field):
    result = {r[field]: r for r in rows}
    require(len(result) == len(rows), f"Duplicate {field}")
    return result


def safe_id(value):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None, f"Unsafe artifact ID: {value!r}")
    return value


def export(source, docs):
    global PREFIXES, TIME_ORIGIN
    PREFIXES = ((str(source), "${EXPERIMENT}"), (str(source.parent), "${ARTIFACTS}"), (str(Path.home()), "${HOME}"))
    run = source / "runs/sweep-1"
    TIME_ORIGIN = datetime.datetime.fromisoformat(read_jsonl(run / "manifest.jsonl")[0]["started"]).timestamp()
    dest = docs / "data/sweep-1"
    records = read_jsonl(run / "pelican/runs.jsonl")
    scored = unique([r for r in records if r["phase"] == "scored"], "cell_id")
    renders = unique(read_jsonl(run / "renders.jsonl"), "cell_id")
    with (run / "reveal/reveal-key.csv").open(encoding="utf-8", newline="") as f:
        reveal = list(csv.DictReader(f))
    require(len(unique(reveal, "cell_id")) == len(unique(reveal, "code")) == 120, "Reveal coverage")
    require(set(scored) == set(renders) == {r["cell_id"] for r in reveal}, "Run/render/reveal mismatch")
    scores = json.loads((run / "grade/scores-latest.json").read_text())["scores"]
    require(set(scores) == {r["code"] for r in reveal}, "Grade coverage")
    rows = analyze.export(run)
    for row in rows:
        cid = f"{row['model_id']}-{row['effort']}-s{row['seed']}"
        r, render = scored[cid], renders[cid]
        key = next(k for k in reveal if k["cell_id"] == cid)
        require(int(key["attempt"]) == r["attempt"], "Grade is for a different attempt")
        stem = f"{safe_id(cid)}-a{int(r['attempt'])}"
        row.update(cell_id=cid, attempt=r["attempt"], resolved_effort=r["resolved_effort"],
                   wall_s=r["wall_s"], t_first_answer_s=r["t_first_answer_s"],
                   ttft_s=r["ttft_s"], engine_decode_tok_s=r["engine_decode_tok_s"],
                   cap_hit=int(r["cap_hit"]), error=r["error"] or "",
                   render_ok=int(bool(render.get("ok"))), render_stage=render.get("stage", "not_rendered"),
                   raw=f"sweep-1/raw/{stem}.json",
                   svg=f"sweep-1/svg/{stem}.svg.txt" if r["svg_status"] == "complete" else "",
                   image=f"../images/sweep-1/{safe_id(row['code'])}.png" if render.get("ok") else "")
        if row["svg"]:
            src = run / "pelican/svg" / r["model_id"] / f"{stem}.svg"
            write_text(docs / "data" / row["svg"], src.read_text(encoding="utf-8"))
        if row["image"]:
            target = docs / "data" / row["image"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((run / "gallery/png" / f"{row['code']}.png").read_bytes())
    fields = analyze.COLS + [k for k in rows[0] if k not in analyze.COLS]
    with (docs / "data/sweep-1-cells.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    for r in records:
        raw_path = Path(r["raw_file"]).resolve()
        require(raw_path.is_relative_to(run.resolve()), "Raw path escapes run")
        raw = json.loads(raw_path.read_text())
        require(raw["cell_id"] == r["cell_id"] and raw["attempt"] == r["attempt"], "Raw identity mismatch")
        write_json(dest / "raw" / f"{safe_id(r['cell_id'])}-a{int(r['attempt'])}.json", raw)
    for name, original in [("runs.jsonl", "pelican/runs.jsonl"), ("renders.jsonl", "renders.jsonl"),
                           ("per-run-metrics.jsonl", "report/per_run.jsonl"), ("loads.jsonl", "loads.jsonl")]:
        write_jsonl(dest / name, read_jsonl(run / original))
    write_json(dest / "manifest.json", read_jsonl(run / "manifest.jsonl")[0])
    write_json(dest / "human-scores.json", json.loads((run / "grade/scores-latest.json").read_text()))
    write_json(dest / "reveal.json", reveal)
    write_json(dest / "judge-map.json", json.loads((run / "judge-key/judge-code-map.json").read_text()))
    catalogue = json.loads((source / "catalogue/catalogue.json").read_text())
    write_json(dest / "catalogue.json", {"checkpoints": [r for r in catalogue["checkpoints"]
                                                        if r["id"] in {x["model_id"] for x in rows}]})
    for folder in ("provenance", "configs"):
        for p in sorted((run / folder if folder == "provenance" else source / folder).glob("*.json")):
            if folder == "configs":
                provenance = json.loads((run / "provenance" / p.name).read_text())
                require(hashlib.sha256(p.read_bytes()).hexdigest() == provenance["config_sha256"],
                        f"Config changed since inference: {p.name}")
            write_json(dest / folder / p.name, json.loads(p.read_text()))
    for p in sorted((source / "evidence").glob("prompt-render-*.json")):
        write_json(dest / "prompt-render" / p.name, json.loads(p.read_text()))
    for judge in JUDGES:
        write_jsonl(dest / "judges" / f"{judge}.jsonl", read_jsonl(source / f"judge-blind/out/{judge}.jsonl"))
        write_text(dest / "judges" / f"order-{judge}.txt", (source / f"judge-blind/order-{judge}.txt").read_text())
    write_text(dest / "judges/prompt.md", scrub((source / "judge-blind/PROMPT.md").read_text()))
    for label, folder in [("quick-eval", run / "quick-eval"),
                          ("effort-probes", source / "runs/probes-1/effort-probes")]:
        combined = []
        for p in sorted(folder.glob("*/runs.jsonl")):
            for r in read_jsonl(p):
                combined.append(dict(r, model_id=p.parent.name))
        write_jsonl(dest / f"{label}.jsonl", combined)
    snapshot = source / "harness-snapshots/e12de63b64d2/strata-quick-eval/items.jsonl"
    write_text(dest / "quick-eval-items.jsonl", snapshot.read_text())
    # Telemetry is retained, but process identifiers and local paths are not public identifiers.
    telemetry = read_jsonl(run / "resources.jsonl")
    baseline = json.loads(json.dumps(telemetry[0]))
    for sample in telemetry:
        # Publish changes during the experiment, not the machine's cumulative pre-run activity.
        for device, counters in sample["disk"].items():
            for key in counters:
                counters[key] -= baseline["disk"][device][key]
        for key in sample["vm"]:
            sample["vm"][key] -= baseline["vm"][key]
        sample["cpu_busy_s"] -= baseline["cpu_busy_s"]
    payload = "".join(json.dumps(scrub(r), separators=(",", ":")) + "\n" for r in telemetry).encode()
    # GzipFile fixes both timestamp and OS header for deterministic output across Python versions.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", filename="", mtime=0) as gz:
        gz.write(payload)
    (dest / "resources.jsonl.gz").write_bytes(buf.getvalue())


def load(docs):
    rows = analyze.load_csv(docs / "data/sweep-1-cells.csv")
    for r in rows:
        for k in ("attempt", "cap_hit", "render_ok"):
            r[k] = int(r[k])
    return rows


def validate(docs):
    rows = load(docs)
    require(len(unique(rows, "cell_id")) == len(unique(rows, "code")) == 120, "120 unique scored outcomes required")
    require(set(Counter((r["model_id"], r["effort"]) for r in rows).values()) == {5}, "Five seeds per group")
    require(len({r["model_id"] for r in rows}) == 8, "Eight models required")
    for model in {r["model_id"] for r in rows}:
        for effort in analyze.EFFORTS:
            require({r["seed"] for r in rows if r["model_id"] == model and r["effort"] == effort}
                    == {11, 22, 33, 44, 55}, "Seed matrix mismatch")
    dest = docs / "data/sweep-1"
    all_runs = read_jsonl(dest / "runs.jsonl")
    require(Counter(r["phase"] for r in all_runs) == {"scored": 120, "warmup": 8, "compat": 8}, "Phase counts")
    require(len({(r["cell_id"], r["attempt"]) for r in all_runs}) == 136, "Duplicate run attempts")
    scores = json.loads((dest / "human-scores.json").read_text())["scores"]
    require(set(scores) == {r["code"] for r in rows}, "Human grade IDs")
    for r in rows:
        require(type(r["grade"]) is int and 1 <= r["grade"] <= 10, "Invalid human grade")
        require(r["grade"] == scores[r["code"]]["overall_1_10"], "Score mismatch")
        require(r["max_tokens"] == 49152 and r["deadline_s"] == 2700, "Limit mismatch")
        require(r["resolved_effort"] == ("xhigh" if r["effort"] == "high" else r["effort"]), "Effort mapping")
        for field in ("raw", "image", "svg"):
            if r[field]:
                artifact = (docs / "data" / r[field]).resolve()
                require(artifact.is_relative_to(docs.resolve()) and artifact.is_file(), f"Missing/unsafe {field}")
        raw = json.loads((docs / "data" / r["raw"]).read_text())
        for field in ("cell_id", "seed", "wall_s", "completion_tokens", "finish_reason", "svg_status"):
            require(raw[field] == r[field], f"Raw/CSV mismatch: {field}")
        require(bool(r["cap_hit"]) == bool(raw["cap_hit"]), "Cap mismatch")
        require(bool(r["truncated"]) == (raw["cap_hit"] or raw["abort"] == "deadline"), "Truncation mismatch")
        require(bool(r["image"]) == bool(r["render_ok"]), "Image/render mismatch")
    require(sum(r["cap_hit"] for r in rows) == 1 and sum(r["render_ok"] for r in rows) == 119, "Failure totals")
    mapping_rows = json.loads((dest / "judge-map.json").read_text())
    mapping = {k: v["gallery_code"] for k, v in unique(mapping_rows, "judge_code").items()}
    require(len(set(mapping.values())) == 119 and set(mapping.values()) == {r["code"] for r in rows if r["render_ok"]}, "Judge coverage")
    judges = {}
    for judge in JUDGES:
        ratings = unique(read_jsonl(dest / "judges" / f"{judge}.jsonl"), "code")
        require(set(ratings) == set(mapping), f"Missing/extra {judge} image")
        order = (dest / "judges" / f"order-{judge}.txt").read_text().splitlines()
        require(len(order) == 119 and set(order) == set(mapping), "Judge order mismatch")
        for rating in ratings.values():
            require(type(rating["overall_1_10"]) is int and 1 <= rating["overall_1_10"] <= 10, "Judge score range")
            require(all(type(rating[k]) is int and 0 <= rating[k] <= 2 for k in SUBS), "Judge rubric range")
            require(type(rating["text_in_image"]) is bool and isinstance(rating["reason"], str), "Judge metadata")
        judges[judge] = {mapping[k]: v for k, v in ratings.items()}
    for label, count, passed in (("quick-eval", 480, 469), ("effort-probes", 120, 120)):
        records = read_jsonl(dest / f"{label}.jsonl")
        require(len(records) == count and sum(r["passed"] for r in records) == passed, f"{label} totals")
        require(len({(r['model_id'], r['id'], r['effort'], r['seed']) for r in records}) == count, "Duplicate evaluation item")
    return rows, judges


def ranks(values):
    return [1 + sum(y < x for y in values) + (sum(y == x for y in values) - 1) / 2 for x in values]


def spearman(a, b):
    require(len(a) == len(b) and len(a) > 1, "Correlation needs matched observations")
    x, y = ranks(a), ranks(b)
    mx, my = st.mean(x), st.mean(y)
    den = (sum((v - mx) ** 2 for v in x) * sum((v - my) ** 2 for v in y)) ** .5
    return sum((v - mx) * (w - my) for v, w in zip(x, y)) / den if den else None


def judge_tables(rows, judges):
    human = {r["code"]: r for r in rows}
    common = sorted(next(iter(judges.values())))
    h = [human[c]["grade"] for c in common]
    lines = ["# AI judge results", "", "Generated by `harness/publish.py`; [interpretation and rubric](JUDGING.md).",
             "All comparisons below use the same **119 completed images**. The incomplete output has no AI rating.", "",
             "## Agreement with the human", "", f"Human mean on these 119 images: **{st.mean(h):.2f}** (all 120 outcomes: **4.50**).", "",
             "| Judge label | n | Mean | Mean absolute difference | Within 1 point | Spearman |", "|---|---:|---:|---:|---:|---:|"]
    for name, ratings in judges.items():
        g = [ratings[c]["overall_1_10"] for c in common]
        lines.append(f"| {name} | {len(g)} | {st.mean(g):.2f} | {st.mean(abs(a-b) for a,b in zip(h,g)):.2f} | "
                     f"{sum(abs(a-b)<=1 for a,b in zip(h,g))}/{len(g)} | {spearman(h,g):.3f} |")
    lines += ["", "Spearman uses average ranks for ties. These are descriptive point estimates, not a test of which judge is best.",
              "", "## High effort, completed images only", "", "IQ3_S has n=4 here; every other model has n=5. Do not compare its conditional mean to an all-outcome mean.", "",
              "| Model | n | Human | " + " | ".join(JUDGES) + " |", "|---|---:|---:|" + "---:|" * len(JUDGES)]
    for model in sorted({r["model_id"] for r in rows}):
        codes = [c for c in common if human[c]["model_id"] == model and human[c]["effort"] == "high"]
        means = [st.mean(judges[j][c]["overall_1_10"] for c in codes) for j in JUDGES]
        lines.append(f"| {analyze.LABELS[model]} | {len(codes)} | {st.mean(human[c]['grade'] for c in codes):.2f} | " + " | ".join(f"{m:.2f}" for m in means) + " |")
    lines += ["", "## Scores by effort", "", "| Effort | n | Human | " + " | ".join(JUDGES) + " |", "|---|---:|---:|" + "---:|" * len(JUDGES)]
    for effort in analyze.EFFORTS:
        codes = [c for c in common if human[c]["effort"] == effort]
        means = [st.mean(judges[j][c]["overall_1_10"] for c in codes) for j in JUDGES]
        lines.append(f"| {effort} | {len(codes)} | {st.mean(human[c]['grade'] for c in codes):.2f} | " + " | ".join(f"{m:.2f}" for m in means) + " |")
    return "\n".join(lines) + "\n"


def ai_ratings(rows, judges):
    lines = ["# Every AI rating", "", "[Comparison tables](AI-RESULTS.md) · [Rubric and provenance](JUDGING.md)", "",
             "Original judgments, not normalized. P/B/R/C = pelican / bicycle / riding contact / composition (each 0–2).",
             "The human grade is overall 1–10; no human rubric sub-scores were recorded. P231 was not AI-rated.", ""]
    for r in sorted(rows, key=lambda r: r["code"]):
        if not r["render_ok"]:
            continue
        lines += [f"## {r['code']}", "", f"**{r['cell_id']}** · Human **{r['grade']}/10** · {r['wall_s']:.1f} s", "",
                  f'<img src="images/sweep-1/{r["code"]}.png" width="256" alt="Drawing {r["code"]}">', "",
                  "| Judge | Overall | P/B/R/C | Text reported | Original explanation |", "|---|---:|---|---|---|"]
        for name, ratings in judges.items():
            rating = ratings[r["code"]]
            reason = html.escape(rating["reason"]).replace("|", "&#124;").replace("\n", " ")
            subs = "/".join(str(rating[k]) for k in SUBS)
            lines.append(f"| {name} | {rating['overall_1_10']} | {subs} | {str(rating['text_in_image']).lower()} | {reason} |")
        lines.append("")
    return "\n".join(lines)


def ai_rating_pages(rows, judges):
    # Bound images/tables per page for GitHub's mobile viewer; keep old index anchors.
    rated = sorted((r for r in rows if r["render_ok"]), key=lambda r: r["code"])
    chunks = [rated[i:i + 10] for i in range(0, len(rated), 10)]
    names = [f"AI-RATINGS-{chunk[0]['code']}-{chunk[-1]['code']}.md" for chunk in chunks]
    index = ["# Every AI rating", "", "[Comparison tables](AI-RESULTS.md) · [Rubric and provenance](JUDGING.md)", "",
             "All 595 original judgments on 119 images, split into pages of at most 10 drawings for mobile viewing.",
             "P231 was not AI-rated. Choose a range or an individual drawing below.", ""]
    pages = {}
    for i, (name, chunk) in enumerate(zip(names, chunks)):
        label = f"{chunk[0]['code']}–{chunk[-1]['code']}"
        index.append(f"- [{label}]({name})")
        nav = ["[All AI ratings](AI-RATINGS.md)"]
        if i:
            nav.append(f"[Previous]({names[i - 1]})")
        if i + 1 < len(names):
            nav.append(f"[Next]({names[i + 1]})")
        navigation = " · ".join(nav)
        body = ai_ratings(chunk, judges).replace("# Every AI rating", f"# AI ratings: {label}", 1)
        pages[name] = navigation + "\n\n" + body + "\n" + navigation + "\n"
    for name, chunk in zip(names, chunks):
        for r in chunk:
            index += ["", f"## {r['code']}", "",
                      f"[View drawing and all five judgments]({name}#{r['code'].lower()})"]
    pages["AI-RATINGS.md"] = "\n".join(index) + "\n"
    return pages


def scatter(rows):
    # This chart is generated from numeric measurements, never from model-authored SVG.
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" role="img" aria-labelledby="title desc">',
             '<title id="title">Human score versus request wall time, all 120 outcomes</title>',
             '<desc id="desc">Each point is one generation. Low effort is a circle, medium a square, high a triangle. The token-cap failure is a red cross. Points may overlap. Full values are in the CSV and seed tables.</desc>',
             '<rect width="960" height="560" fill="white"/>',
             '<g font-family="Geist Mono, monospace" font-size="16" fill="#181818">',
             '<text x="64" y="32">All 120 outcomes: human score versus wall time</text>']
    for score in range(1, 11):
        y = 440 - (score - 1) * 40
        parts += [f'<path d="M64 {y}H928" stroke="#dddddd"/>', f'<text x="32" y="{y+4}">{score}</text>']
    for seconds in range(0, 1201, 200):
        x = 64 + seconds * .72
        parts += [f'<text x="{x}" y="472" text-anchor="middle">{seconds}</text>']
    parts += ['<text x="400" y="504">Request wall time (seconds)</text>']
    colors = {"low": "#0f766e", "medium": "#b45309", "high": "#4338ca"}
    for r in rows:
        x, y = 64 + r["wall_s"] * .72, 440 - (r["grade"] - 1) * 40
        title = html.escape(f"{r['cell_id']}: {r['grade']}/10, {r['wall_s']:.1f} s" + (", token cap" if r['cap_hit'] else ""))
        parts += [f'<g fill="{colors[r["effort"]]}" fill-opacity="0.65"><title>{title}</title>']
        if r["cap_hit"]:
            parts += [f'<path d="M{x-6} {y-6}l12 12m0 -12l-12 12" stroke="#b91c1c" stroke-width="3"/>']
        elif r["effort"] == "low":
            parts += [f'<circle cx="{x}" cy="{y}" r="5"/>']
        elif r["effort"] == "medium":
            parts += [f'<rect x="{x-5}" y="{y-5}" width="10" height="10"/>']
        else:
            parts += [f'<path d="M{x} {y-6}l6 11h-12z"/>']
        parts += ['</g>']
    parts += ['<text x="64" y="544">Circle: low · Square: medium · Triangle: high · Red cross: token cap</text>', '</g></svg>']
    return "\n".join(parts) + "\n"


def gallery(rows, blind=False):
    title = "Blind gallery" if blind else "All 120 outcomes"
    lines = [f"# {title}", "", "Existing PNGs only; no generated SVG is executed.", ""]
    if blind:
        lines += ["Model identities and scores are hidden on this page, not access-controlled. Avoid the results/data pages until grading is finished.",
                  "Order is by opaque gallery code, not by model or score. The original score-object key order is preserved in the human score file; it is not a timestamped rating history.", ""]
    else:
        lines += ["[All seed results](SEEDS.md) · [Data dictionary](data/README.md) · [Blind view](BLIND-GALLERY.md)", ""]
    ordered = sorted(rows, key=lambda r: r["code"] if blind else (r["model_id"], r["effort"], r["seed"]))
    for r in ordered:
        lines += [f"## {r['code']}" if blind else f"## {r['cell_id']} ({r['code']})", ""]
        if r["image"]:
            lines += [f'<img src="images/sweep-1/{r["code"]}.png" width="256" alt="Drawing {r["code"]}">', ""]
        else:
            lines += ["**No complete SVG: token ceiling reached.**", ""]
        if not blind:
            lines += [f"Human **{r['grade']}/10** · {r['wall_s']:.1f} s · {r['completion_tokens']:,} tokens · "
                      f"finish `{r['finish_reason']}` · SVG `{r['svg_status']}` · render `{r['render_stage']}`.", "",
                      f"[Raw response](data/{r['raw']})" + (f" · [SVG source (text)](data/{r['svg']})" if r['svg'] else "")
                      + (f" · [Five AI judgments](AI-RATINGS.md#{r['code'].lower()})" if r['render_ok'] else " · AI: not rated"), ""]
    return "\n".join(lines)


def eval_tables(docs):
    lines = ["# Existing non-pelican evaluations", "", "Generated from the saved item-level records; no additional inference.", "",
             "Quick-eval: medium effort, seed 11, reasoning budget 1536, completion cap 2560. One draw per item.",
             "", "| Model | Passed | Arithmetic | Format | Recall | Repetition | Cap hits | Errors/aborts |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    records = read_jsonl(docs / "data/sweep-1/quick-eval.jsonl")
    for model in sorted({r["model_id"] for r in records}):
        xs = [r for r in records if r["model_id"] == model]
        cols = []
        for category in (None, "arith", "format", "recall", "repeat"):
            subset = [r for r in xs if category is None or r["category"] == category]
            cols.append(f"{sum(r['passed'] for r in subset)}/{len(subset)}")
        lines.append(f"| {analyze.LABELS[model]} | " + " | ".join(cols) + f" | {sum(r['cap_hit'] for r in xs)} | {sum(bool(r.get('error') or r.get('abort')) for r in xs)} |")
    lines += ["", "These near-saturated scores do not reproduce the human drawing-quality ranking. They are not evidence of equal general capability.",
              "", "## Failed items", "", "| Model | Item | Expected/scoring detail |", "|---|---|---|"]
    for r in records:
        if not r["passed"]:
            detail = html.escape(str(r["detail"])).replace("|", "&#124;").replace("\n", " ")
            lines.append(f"| {r['model_id']} | {r['id']} | {detail} |")
    lines += ["", "[Full requests, answers and scoring details](data/sweep-1/quick-eval.jsonl).", "",
              "## Effort probes", "", "Five short items × three efforts × eight models = **120/120 passed**. Seed 11, reasoning budget 0, completion cap 49152.",
              "These items were too easy to distinguish the tested settings. Do not interpret their perfect scores as broad reasoning equivalence.",
              "", "[All probe responses](data/sweep-1/effort-probes.jsonl) · [Archived item definitions](data/sweep-1/quick-eval-items.jsonl)", ""]
    return "\n".join(lines)


def generated(docs):
    rows, judges = validate(docs)
    return {"SEEDS.md": "# Every seed: score, time and token headroom\n\n[Methods](METHODS.md) · [CSV](data/sweep-1-cells.csv) · [Gallery](GALLERY.md)\n\n" + analyze.tables(rows),
            "AI-RESULTS.md": judge_tables(rows, judges), **ai_rating_pages(rows, judges),
            "images/score-time.svg": scatter(rows), "GALLERY.md": gallery(rows),
            "BLIND-GALLERY.md": gallery(rows, True), "QUICK-EVAL.md": eval_tables(docs)}


def privacy_check(docs):
    """Fail closed on common private data in every text/gzip artifact and on PNG ancillary metadata."""
    patterns = {
        "local absolute path": r"/(?:home|mnt|Users|root|tmp|run/user)(?:/|\b)",
        "Windows user path": r"[A-Za-z]:[\\\\/]+Users[\\\\/]",
        "IPv4 address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "MAC address": r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b",
        "email address": r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "bearer credential": r"(?i)Bearer\s+[A-Za-z0-9_.~+/=-]{12,}",
        "credential assignment": r'''(?i)(?:api[_-]?key|access[_-]?token|authorization|password|secret)["']?\s*[:=]\s*["'][^"']{8,}''',
        "absolute timestamp": r"\b20\d{2}-\d\d-\d\dT\d\d:\d\d:\d\d",
        "Unix timestamp": r'"(?:' + "|".join(sorted(TIME_KEYS)) + r')"\s*:\s*\d{10}(?:\D|$)',
        "host identity": re.escape(socket.gethostname()),
    }
    for path in sorted((docs / "data").rglob("*")):
        if not path.is_file():
            continue
        content = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text(encoding="utf-8")
        for name, pattern in patterns.items():
            require(not re.search(pattern, content), f"Privacy gate: {name} in {path.relative_to(docs)}")
        for key in PRIVATE_KEYS:
            require(not re.search(r'"' + re.escape(key) + r'"\s*:', content), f"Private field {key} in {path.name}")
    for path in (docs / "images").rglob("*.png"):
        data = path.read_bytes()
        require(data[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG: {path.name}")
        offset = 8
        while offset < len(data):
            size = int.from_bytes(data[offset:offset+4], "big")
            tag = data[offset+4:offset+8]
            require(tag in (b"IHDR", b"IDAT", b"IEND"), f"PNG ancillary metadata: {path.name}")
            offset += size + 12
        require(offset == len(data), f"PNG trailing data: {path.name}")


def checksums(docs):
    paths = sorted(p for p in (docs / "data").rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    paths += sorted((docs / "images/sweep-1").glob("*.png"))
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(docs).as_posix()}\n" for p in paths)


def stage_export(source, docs):
    # A rejected export must not leave new sensitive artifacts in a Git working tree.
    with tempfile.TemporaryDirectory(prefix="pelican-export-") as tmp:
        staging = Path(tmp) / "docs"
        if docs.exists():
            shutil.copytree(docs, staging)
        (staging / "data").mkdir(parents=True, exist_ok=True)
        export(source, staging)
        privacy_check(staging)
        generated(staging)  # validate joins/artifacts before promoting any files
        shutil.copytree(staging, docs, dirs_exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="existing Strata-pelican root; export saved evidence only")
    parser.add_argument("--docs", type=Path, default=DOCS)
    parser.add_argument("--check", action="store_true", help="read-only validation, including generated pages/checksums")
    args = parser.parse_args()
    require(not (args.source and args.check), "--source and --check are mutually exclusive")
    if args.source:
        stage_export(args.source.resolve(), args.docs)
    privacy_check(args.docs)
    pages = generated(args.docs)
    pages["data/SHA256SUMS"] = checksums(args.docs)
    for name, content in pages.items():
        path = args.docs / name
        if args.check:
            require(path.read_text(encoding="utf-8") == content, f"Stale generated file: {name}")
        else:
            write_text(path, content)
    print("PASS: privacy gate; 120 scored outcomes, 119 images, 595 AI ratings, 480 quick-eval and 120 probe records; reports/checksums match")


if __name__ == "__main__":
    main()
