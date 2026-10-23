"""Tests for the pelican harness: SVG extraction rule, static flags, metrics correlation, and the Runner request path
against a fake Strata server (SSE + /metrics). No model, loopback only:  python3 -I test_harness.py
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pelican as pel  # noqa: E402

GOOD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


class ExtractTests(unittest.TestCase):
    def test_complete_in_fence(self):
        r = pel.extract_svg(f"Here:\n```svg\n{GOOD}\n```\nEnjoy")
        self.assertEqual((r["status"], r["svg"]), ("complete", GOOD))

    def test_first_of_two(self):
        two = GOOD + "\n" + GOOD.replace("rect", "circle")
        r = pel.extract_svg(two)
        self.assertEqual(r["svg"], GOOD)
        self.assertEqual(r["n_svg_starts"], 2)

    def test_nested_svg_balanced(self):
        s = '<svg xmlns="http://www.w3.org/2000/svg"><svg x="1"><rect/></svg><circle r="1"/></svg>'
        self.assertEqual(pel.extract_svg("x " + s + " y")["svg"], s)

    def test_unclosed_is_not_repaired(self):
        r = pel.extract_svg('text <svg xmlns="http://www.w3.org/2000/svg"><rect')
        self.assertEqual(r["status"], "unclosed")
        self.assertTrue(r["svg"].endswith("<rect"))

    def test_none_and_lookalikes(self):
        self.assertEqual(pel.extract_svg("no image, but <svgfoo> and svg text")["status"], "none")
        self.assertEqual(pel.extract_svg("")["status"], "none")

    def test_case_and_self_closing(self):
        self.assertEqual(pel.extract_svg('<SVG width="1"/> tail')["svg"], '<SVG width="1"/>')


class FlagTests(unittest.TestCase):
    def test_flags(self):
        bad = ('<svg xmlns="http://www.w3.org/2000/svg"><script>x</script><rect onclick="a()"/>'
               '<image href="http://e.com/a.png"/><use href="#ok"/><foreignObject/>'
               '<image href="data:image/png;base64,AAAA"/><style>@import url(http://x/y.css);</style></svg>')
        f = pel.svg_flags(bad)
        for k in ("script", "event_handler", "foreign_object", "data_uri", "css_import", "external_ref"):
            self.assertIn(k, f)
        self.assertIn("http://e.com/a.png", f["external_ref"])
        self.assertNotIn("#ok", f["external_ref"])
        self.assertTrue(f["has_xmlns_svg"])
        clean = pel.svg_flags(GOOD)
        self.assertFalse([k for k in pel.UNSAFE_KEYS if k in clean])

    def test_pelican_body(self):
        b = pel.build_body("m", "high", 33, 32768)
        self.assertEqual(b["messages"], [{"role": "user", "content": "Generate an SVG of a pelican riding a bicycle"}])
        self.assertEqual((b["reasoning_effort"], b["reasoning_budget_tokens"], b["seed"], b["max_tokens"]),
                         ("high", 0, 33, 32768))
        self.assertEqual((b["temperature"], b["top_p"], b["top_k"]), (1.0, 0.95, 20))


class CorrelateTests(unittest.TestCase):
    def test_match_and_mismatch(self):
        met = {"requests": [{"time": 100.5, "output_tokens": 40, "decode_tok_s": 50.0},
                            {"time": 90.0, "output_tokens": 40}]}
        c = pel.correlate_history(met, 100.0, 120.0, {"completion_tokens": 40})
        self.assertEqual(c["match"], "time+tokens")
        self.assertEqual(c["entry"]["decode_tok_s"], 50.0)
        c = pel.correlate_history(met, 100.0, 120.0, {"completion_tokens": 41})
        self.assertIsNone(c["match"])
        c = pel.correlate_history(met, 200.0, 220.0, {"completion_tokens": 40})
        self.assertIsNone(c["match"])


class FakeStrata(BaseHTTPRequestHandler):
    history = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._json({"data": [{"id": "fake-model"}]})
        if self.path.startswith("/metrics"):
            return self._json({"live": {"state": "idle"}, "requests": list(reversed(FakeStrata.history)), "engine": {}})
        self._json({})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        t = time.time()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def ev(delta, finish=None, extra=None):
            d = {"id": "chatcmpl-x", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **(extra or {})}
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()
        ev({"role": "assistant", "content": ""})
        ev({"reasoning_content": "Let me draw. <svg> in thought must not count"})
        time.sleep(0.05)
        ev({"content": "Here you go:\n```svg\n" + GOOD + "\n```"})
        n = 12
        ev({}, "stop", {"usage": {"prompt_tokens": 20, "completion_tokens": n, "total_tokens": 32,
                                  "prompt_tokens_details": {"cached_tokens": 0}},
                        "timings": {"predicted_per_second": 44.4, "prompt_ms": 30.0, "predicted_ms": 270.0,
                                    "prompt_per_second": 666.0, "cache_n": 0}})
        self.wfile.write(b"data: [DONE]\n\n")
        FakeStrata.history.append({"time": t, "output_tokens": n, "decode_tok_s": 44.0, "engine_generated": n})


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeStrata)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        import serverctl as sc
        cls.sc = sc
        cls.saved = (sc.BASE_URL, sc.PORT, sc.unit_active, sc.unit_pids, sc.MODELS)
        sc.PORT = cls.srv.server_port
        sc.BASE_URL = f"http://127.0.0.1:{sc.PORT}/v1"
        sc.unit_active = lambda: "active"
        sc.unit_pids = lambda: set()
        sc.MODELS = {"fake-model": {"engine": "official"}}

    @classmethod
    def tearDownClass(cls):
        sc = cls.sc
        sc.BASE_URL, sc.PORT, sc.unit_active, sc.unit_pids, sc.MODELS = cls.saved
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_request_record_and_quick_eval(self):
        import sweep
        import monitor
        saved_apps = monitor.gpu_compute_apps
        monitor.gpu_compute_apps = lambda: []       # the real GPU may be busy with a real run; not this test's concern
        self.addCleanup(setattr, monitor, "gpu_compute_apps", saved_apps)
        with tempfile.TemporaryDirectory() as td:
            sweep.ROOT = Path(td)
            a = sweep.main.__globals__["argparse"].Namespace(
                mode="sweep", run="t", models=None, deadline=30, idle_timeout=10, qe_budget=1536, qe_max_tokens=2560, effort_pilot_models="x",
                qe_deadline=30, effort_probes=False, retry_blocked=False, note="")
            r = sweep.Runner(a)
            try:
                rec = r.request("fake-model", "scored", "high", 22, "fake-model-high-s22", 30)
                self.assertEqual(rec["resolved_effort"], "xhigh")
                self.assertEqual(rec["svg_status"], "complete")
                self.assertEqual(rec["metrics_match"], "time+tokens")
                self.assertEqual(rec["engine_decode_tok_s"], 44.4)
                self.assertEqual(rec["history_decode_tok_s"], 44.0)
                self.assertEqual(rec["completion_tokens"], 12)
                self.assertIsNotNone(rec["ttft_s"])
                self.assertLessEqual(rec["ttft_s"], rec["t_first_answer_s"])
                self.assertEqual(rec["budget_sent"], 0)
                self.assertGreater(rec["resources"]["n_samples"], -1)
                svg = Path(rec["svg_file"]).read_text()
                self.assertEqual(svg, GOOD)
                raw = json.loads(Path(rec["raw_file"]).read_text())
                self.assertIn("in thought", raw["reasoning"])
                r.quick_eval("fake-model", only="recall-01,arith-mul-0", deadline=30)
                out = Path(td) / "runs/t/quick-eval/fake-model"
                recs = [json.loads(l) for l in open(out / "runs.jsonl")]
                self.assertEqual(len(recs), 2)
                self.assertTrue(all(x["request"]["reasoning_budget_tokens"] == 1536 for x in recs))
                self.assertTrue(all(x["metrics_match"] == "time+tokens" for x in recs))
                self.assertTrue((out / "summary.json").exists())
            finally:
                r.mon.stop()


class RenderTests(unittest.TestCase):
    FILTER_TEXT = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="620" viewBox="0 0 900 620"><defs>'
        '<filter id="g" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="18"/></filter>'
        '<filter id="n"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" stitchTiles="stitch"/>'
        '<feColorMatrix type="saturate" values="0"/></filter></defs><rect width="900" height="620" fill="#cde"/>'
        '<circle cx="450" cy="310" r="260" fill="#fd8" filter="url(#g)"/>'
        '<rect width="900" height="620" filter="url(#n)" opacity=".15"/>'
        '<text x="44" y="566" font-family="Avenir Next, Segoe UI, Helvetica Neue, sans-serif" font-size="14">PELICAN'
        '</text></svg>')

    def test_text_plus_filters_renders(self):
        # regression: under a 1.5 GiB RLIMIT_AS the loader died ("zbus i/o error") on exactly this combination
        import render
        with tempfile.TemporaryDirectory() as td:
            r = render.render(self.FILTER_TEXT, Path(td) / "t.png")
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r.get("render_version"), "v2-as4096")
            self.assertGreater(r["nonwhite_frac"], 0.3)

    def test_external_image_not_fetched(self):
        import render
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" '
               'height="200"><image href="file:///etc/hostname" width="200" height="200"/>'
               '<image xlink:href="https://example.com/a.png" width="200" height="200"/></svg>')
        with tempfile.TemporaryDirectory() as td:
            r = render.render(svg, Path(td) / "e.png")
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["nonwhite_frac"], 0.0)   # nothing external drawn on the white canvas


class GateTests(unittest.TestCase):
    def test_foreign_gpu_pauses_then_resumes(self):
        import sweep
        import monitor
        import serverctl as sc
        calls = {"n": 0}

        def apps():
            calls["n"] += 1
            return [{"pid": 999999, "name": "other-job", "mib": 4000}] if calls["n"] == 1 else []
        saved = (monitor.gpu_compute_apps, sc.unit_pids)
        monitor.gpu_compute_apps, sc.unit_pids = apps, (lambda: set())
        try:
            r = sweep.Runner.__new__(sweep.Runner)
            r.mon = type("M", (), {"check_kernel": lambda self: [], "storage_alarm": [], "hot": lambda self: False,
                                   "last_gpu": {}})()
            r.events = open("/dev/null", "w")
            g = r.gate()
            self.assertGreater(g["paused_foreign_s"], 5)
            self.assertEqual(g["contention"], [])
        finally:
            monitor.gpu_compute_apps, sc.unit_pids = saved


if __name__ == "__main__":
    unittest.main()
