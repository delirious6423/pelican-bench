#!/usr/bin/env python3
"""Mobile grading page for the blind pelican gallery, served on your own LAN or tailnet.

  python3 -I grade.py --run sweep-1 --host <tailnet-or-lan-ip> --port 18094

Serves only: the page, sandbox-rendered PNGs (gallery/png/<code>.png) and a scores API. Model names, efforts and the
reveal key are never read or sent. The page is our own static HTML + small inline JS; no model output is embedded
except the PNG renders. Scores autosave to <run>/grade/scores-latest.json (+ timestamped copies) and
gallery/scoring-sheet-filled.csv, in the same columns as scoring-sheet.csv.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("PELICAN_ROOT", "pelican-data")).resolve()
SUBS = [("pelican_recognizable_0_2", "Pelican recognizable"), ("bicycle_recognizable_0_2", "Bicycle recognizable"),
        ("riding_contact_0_2", "Riding / contact"), ("composition_0_2", "Composition")]
COLUMNS = ["code"] + [k for k, _ in SUBS] + ["overall_1_10", "notes"]
CODE_RE = re.compile(r"^P\d{3}$")
MAX_BODY = 512 << 10


def items(run_dir: Path) -> list[dict]:
    out = []
    for line in open(run_dir / "renders.jsonl", encoding="utf-8"):      # gallery order (already shuffled)
        r = json.loads(line)
        out.append({"code": r["code"], "png": bool(r.get("ok") and r.get("png")), "label": r.get("tile_label") or ""})
    return out


def validate(scores: dict, codes: set[str]) -> dict:
    """Keep only known codes and in-range values; anything else is dropped, never guessed."""
    clean = {}
    for code, s in scores.items():
        if code not in codes or not isinstance(s, dict):
            continue
        row = {}
        for k, _ in SUBS:
            if s.get(k) in (0, 1, 2):
                row[k] = s[k]
        if isinstance(s.get("overall_1_10"), int) and 1 <= s["overall_1_10"] <= 10:
            row["overall_1_10"] = s["overall_1_10"]
        if isinstance(s.get("notes"), str) and s["notes"].strip():
            row["notes"] = s["notes"].strip()[:500]
        if row:
            clean[code] = row
    return clean


_SAVE_LOCK = threading.Lock()


def save(run_dir: Path, scores: dict, order: list[str]) -> str:
    """Write timestamped + latest JSON and the CSV as one serialized transaction; returns the stamp."""
    with _SAVE_LOCK:
        return _save(run_dir, scores, order)


def _save(run_dir: Path, scores: dict, order: list[str]) -> str:
    gdir = run_dir / "grade"
    gdir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S.%f")
    blob = json.dumps({"saved": stamp, "scores": scores}, indent=1, ensure_ascii=False)
    (gdir / f"scores-{stamp}.json").write_text(blob)
    tmp = gdir / "scores-latest.json.tmp"
    tmp.write_text(blob)
    tmp.replace(gdir / "scores-latest.json")
    with open(run_dir / "gallery/scoring-sheet-filled.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for code in order:
            s = scores.get(code, {})
            w.writerow([code] + [s.get(k, "") for k in COLUMNS[1:]])
    return stamp


def page(its: list[dict], gid: str, token: str) -> str:
    cards = []
    for i, it in enumerate(its, 1):
        c = it["code"]
        img = (f'<a href="png/{c}.png" target="_blank"><img src="png/{c}.png" loading="lazy" alt="{c}"></a>'
               if it["png"] else f'<div class="tile">{html.escape(it["label"] or "no image")}</div>')
        subs = "".join(f'<div class="row"><span>{lab}</span><div class="seg" data-k="{k}">'
                       + "".join(f'<button data-v="{v}">{v}</button>' for v in (0, 1, 2)) + "</div></div>"
                       for k, lab in SUBS)
        overall = "".join(f'<button data-v="{v}">{v}</button>' for v in range(1, 11))
        cards.append(f'''<section class="card" id="{c}" data-code="{c}"><div class="hd"><b>{c}</b><span>{i}/{len(its)}</span>
<span class="st"></span></div>{img}<div class="lab">Overall (1-10)</div><div class="seg ov" data-k="overall_1_10">{overall}</div>
<details><summary>Details (optional)</summary>{subs}<textarea data-k="notes" rows="2" placeholder="notes"></textarea></details></section>''')
    return TEMPLATE.replace("__GID__", gid).replace("__TOKEN__", token).replace("__N__", str(len(its))).replace("__CARDS__", "\n".join(cards))


TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pelican grading (blind)</title>
<style>
body{margin:0;font:16px -apple-system,system-ui,sans-serif;background:#f2f2f4;color:#111}
header{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid #ddd;padding:8px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
header b{flex:1}#sync{font-size:13px;color:#666}
.card{background:#fff;margin:12px;border-radius:12px;padding:10px;box-shadow:0 1px 3px #0002}
.card.done{outline:3px solid #34c759}.hd{display:flex;gap:8px;align-items:center;margin-bottom:6px}.hd b{flex:1}
.hd span{color:#666;font-size:13px}img{width:100%;height:auto;aspect-ratio:1;border:1px solid #eee;border-radius:8px;background:#fff}
.tile{aspect-ratio:1;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px;border:1px dashed #aaa;border-radius:8px;color:#a00}
.lab{margin:8px 0 4px;font-weight:600}.seg{display:flex;gap:4px}.seg button{flex:1;min-height:44px;border:1px solid #ccc;border-radius:8px;background:#fafafa;font-size:16px}
.seg button.on{background:#007aff;color:#fff;border-color:#007aff}.row{display:flex;align-items:center;gap:8px;margin:6px 0}.row span{width:42%;font-size:14px}.row .seg{flex:1}
details{margin-top:8px}summary{color:#007aff;padding:6px 0}textarea{width:100%;box-sizing:border-box;font:inherit;margin-top:6px}
button.act{min-height:36px;border:1px solid #007aff;background:#fff;color:#007aff;border-radius:8px;padding:0 10px}
</style></head><body>
<header><b>Blind pelicans: <span id="cnt">0</span>/__N__ graded</b><button class="act" id="next">Next ungraded</button>
<button class="act" id="copy">Copy CSV</button><span id="sync">loading...</span></header>
<p style="margin:12px;color:#555;font-size:14px">Prompt: "Generate an SVG of a pelican riding a bicycle". Order is shuffled; models are hidden.
Tap a number to grade (tap again to clear). Grades autosave to the PC and this phone. Tap an image to enlarge.</p>
__CARDS__
<script>
const KEY="pelican-grades-__GID__",TOKEN="__TOKEN__";let S={},D={},V=0,ready=false,busy=false,again=false;let timer=null;
// D: codes edited on this phone and not yet confirmed by the PC (code -> edit version). V only ever grows, so a
// late response can never acknowledge a newer edit. One save in flight at a time keeps the PC copy in order.
function keep(){localStorage.setItem(KEY,JSON.stringify(S));localStorage.setItem(KEY+"-pending",JSON.stringify(D));
localStorage.setItem(KEY+"-v",String(V));}
const sync=t=>document.getElementById("sync").textContent=t;
function render(){let n=0;document.querySelectorAll(".card").forEach(c=>{const s=S[c.dataset.code]||{};
c.querySelectorAll(".seg").forEach(g=>g.querySelectorAll("button").forEach(b=>b.classList.toggle("on",s[g.dataset.k]===+b.dataset.v)));
const t=c.querySelector("textarea");if(document.activeElement!==t)t.value=s.notes||"";
const done=Number.isInteger(s.overall_1_10);c.classList.toggle("done",done);c.querySelector(".st").textContent=done?"\u2713":"";if(done)n++;});
document.getElementById("cnt").textContent=n;}
function push(code){if(code)D[code]=++V;keep();clearTimeout(timer);sync("saving...");timer=setTimeout(send,600);}
function send(){if(busy){again=true;return}busy=true;again=false;const sent={...D};fetch("api/scores",{method:"POST",headers:{"Content-Type":"application/json","X-Grade-Token":TOKEN},body:JSON.stringify({scores:S})})
.then(r=>{if(r.status===403)throw "restart";if(!r.ok)throw "err";return r.json()})
.then(j=>{for(const c in sent)if(D[c]===sent[c])delete D[c];keep();sync("saved to PC "+j.saved.slice(9,15)+" ("+j.n+")")})
.catch(e=>sync(e==="restart"?"PC server restarted: reload this page (edits kept on phone)":"PC unreachable; kept on phone"))
.finally(()=>{busy=false;if(again)send()});}
document.addEventListener("click",e=>{const b=e.target.closest(".seg button");if(!b||!ready)return;const card=b.closest(".card"),k=b.parentNode.dataset.k,v=+b.dataset.v;
const s=S[card.dataset.code]||(S[card.dataset.code]={});if(s[k]===v)delete s[k];else s[k]=v;render();push(card.dataset.code);});
document.addEventListener("input",e=>{if(e.target.tagName!=="TEXTAREA"||!ready)return;const code=e.target.closest(".card").dataset.code;
const s=S[code]||(S[code]={});s.notes=e.target.value;push(code);});
document.getElementById("next").onclick=()=>{const c=[...document.querySelectorAll(".card")].find(c=>!c.classList.contains("done"));if(c)c.scrollIntoView({behavior:"smooth"});};
document.getElementById("copy").onclick=()=>{const cols=["pelican_recognizable_0_2","bicycle_recognizable_0_2","riding_contact_0_2","composition_0_2","overall_1_10","notes"];
const rows=["code,"+cols.join(",")];document.querySelectorAll(".card").forEach(c=>{const s=S[c.dataset.code]||{};
rows.push([c.dataset.code,...cols.map(k=>k==="notes"?JSON.stringify(s[k]||""):(s[k]??""))].join(","));});
navigator.clipboard.writeText(rows.join("\n")).then(()=>alert("CSV copied"),()=>prompt("Copy:",rows.join("\n")));};
try{S=JSON.parse(localStorage.getItem(KEY)||"{}");D=JSON.parse(localStorage.getItem(KEY+"-pending")||"{}");
V=+localStorage.getItem(KEY+"-v")||0}catch(e){S={};D={};V=0}
// edits are ignored until this first sync finishes, so a slow load cannot overwrite them
// PC copy wins, except codes with unconfirmed phone edits (including cleared grades), which are re-sent
fetch("api/scores").then(r=>r.json()).then(j=>{const P=j.scores||{};for(const c in D){if(S[c]&&Object.keys(S[c]).length)P[c]=S[c];else delete P[c]}
S=P;keep();ready=true;render();sync(j.saved?"PC copy "+j.saved.slice(9,15):"connected");if(Object.keys(D).length)push();})
.catch(()=>{ready=true;render();sync("PC unreachable; saving on phone")});
render();
</script></body></html>'''


def make_handler(run_dir: Path):
    its = items(run_dir)
    order = [i["code"] for i in its]
    codes = set(order)
    gid = hashlib.sha256((run_dir / "renders.jsonl").read_bytes()).hexdigest()[:16]   # phone storage per gallery
    token = secrets.token_hex(16)                 # per-process; a cross-site page cannot read it or send the header
    body_page = page(its, gid, token).encode("utf-8")
    png_dir = run_dir / "gallery/png"

    class H(BaseHTTPRequestHandler):
        def send(self, code, ctype, data, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self.send(200, "text/html; charset=utf-8", body_page)
            m = re.fullmatch(r"/png/(P\d{3})\.png", path)
            if m and m.group(1) in codes and (png_dir / f"{m.group(1)}.png").is_file():
                return self.send(200, "image/png", (png_dir / f"{m.group(1)}.png").read_bytes())
            if path == "/api/scores":
                f = run_dir / "grade/scores-latest.json"
                return self.send(200, "application/json", f.read_bytes() if f.exists() else b'{"scores": {}}')
            self.send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path != "/api/scores":
                return self.send(404, "text/plain", b"not found")
            if (self.headers.get("X-Grade-Token") != token
                    or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"):
                return self.send(403, "text/plain", b"forbidden")
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BODY:
                return self.send(413, "text/plain", b"bad size")
            try:
                scores = validate(json.loads(self.rfile.read(n)).get("scores", {}), codes)
            except (ValueError, AttributeError):
                return self.send(400, "text/plain", b"bad json")
            saved = save(run_dir, scores, order)
            self.send(200, "application/json", json.dumps({"saved": saved, "n": len(scores)}).encode())

        def log_message(self, fmt, *a):            # keep a short access log, no bodies
            print(f"{self.address_string()} {fmt % a}", flush=True)

    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="sweep-1")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=18094)
    a = ap.parse_args(argv)
    run_dir = ROOT / "runs" / a.run
    ThreadingHTTPServer((a.host, a.port), make_handler(run_dir)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
