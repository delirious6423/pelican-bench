#!/usr/bin/env python3
"""Pure offline tests: data reads and test-owned temporary files only; no server, GPU or renderer."""
import gzip
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish


class UnitTests(unittest.TestCase):
    def test_rank_ties(self):
        self.assertEqual(publish.ranks([4, 1, 4, 2]), [3.5, 1, 3.5, 2])

    def test_spearman_controls(self):
        self.assertAlmostEqual(publish.spearman([1, 2, 3], [1, 2, 3]), 1)
        self.assertAlmostEqual(publish.spearman([1, 2, 3], [3, 2, 1]), -1)
        self.assertAlmostEqual(publish.spearman([1, 1, 2], [1, 2, 3]), 3 ** .5 / 2)
        self.assertIsNone(publish.spearman([1, 1, 1], [1, 2, 3]))
        with self.assertRaises(ValueError):
            publish.spearman([1, 2], [1])

    def test_redaction_is_recursive_and_non_mutating(self):
        source = {"path": "/mnt/private/model/revision.bin", "pids": [123],
                  "nested": [{"inode": [1, 2], "answer": "42", "cwd": "/home/test-user/code"}],
                  "completion_tokens": 42}
        prefixes = (("/mnt/private", "${ARTIFACTS}"), ("/home/test-user", "${HOME}"))
        with patch.object(publish, "PREFIXES", prefixes):
            result = publish.scrub(source)
            self.assertEqual(result, {"path": "${ARTIFACTS}/model/revision.bin",
                                     "nested": [{"answer": "42", "cwd": "${HOME}/code"}], "completion_tokens": 42})
            self.assertEqual(publish.scrub(result), result)
            self.assertEqual(publish.scrub("scan /mnt/private /home/test-user"), "scan ${ARTIFACTS} ${HOME}")
        self.assertIn("pids", source)

    def test_private_metadata_and_absolute_time_are_removed(self):
        source = {"host": "192.0.2.10", "port": 1234, "cmd": ["/private/bin"], "saved": "private-date",
                  "env": {"PASSWORD": "not-a-real-password", "STRATA_EXL3_DENSE": "q8", "TMPDIR": "/tmp/private"},
                  "metrics_entry": {"id": "opaque-private-id", "time": 1700000012.5},
                  "t_wall_start": 1700000012.0, "wall_s": 3.5,
                  "sha256_status": "verified 2026-01-02T03:04:05+08:00"}
        with patch.object(publish, "TIME_ORIGIN", 1700000000.0):
            result = publish.scrub(source)
        self.assertEqual(result, {"env": {"STRATA_EXL3_DENSE": "q8"}, "metrics_entry": {"time": 12.5},
                                 "t_wall_start": 12.0, "wall_s": 3.5, "sha256_status": "verified [timestamp omitted]"})

    def test_privacy_gate_rejects_synthetic_leaks_in_text_and_gzip(self):
        leaks = ["/home/test-user/private", "/mnt/private", "C:\\\\Users\\\\test-user", "192.0.2.10",
                 "00:11:22:33:44:55", "person@example.invalid", "2026-01-02T03:04:05Z", '"start_wall": 1700000000.0',
                 '"unit_pids": [123]', '"password": "fake-test-credential"', "Bearer fake-test-credential"]
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "data").mkdir()
            for compressed in (False, True):
                path = docs / "data" / ("test.gz" if compressed else "test.txt")
                for leak in leaks:
                    path.write_bytes(gzip.compress(leak.encode()) if compressed else leak.encode())
                    with self.subTest(leak=leak, compressed=compressed), self.assertRaises(ValueError):
                        publish.privacy_check(docs)
                path.unlink()

    def test_privacy_gate_rejects_png_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            target = docs / "images/sweep-1/test.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\n" + (0).to_bytes(4, "big") + b"tEXt" + b"0000")
            with self.assertRaisesRegex(ValueError, "ancillary metadata"):
                publish.privacy_check(docs)

    def test_rejected_export_does_not_touch_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir()
            (docs / "keep.txt").write_text("unchanged")
            def leaking_export(source, staging):
                publish.write_text(staging / "data/leak.txt", "/home/test-user/private")
            with patch.object(publish, "export", side_effect=leaking_export):
                with self.assertRaisesRegex(ValueError, "Privacy gate"):
                    publish.stage_export(Path(tmp) / "source", docs)
            self.assertEqual((docs / "keep.txt").read_text(), "unchanged")
            self.assertFalse((docs / "data").exists())

    def test_safe_ids(self):
        self.assertEqual(publish.safe_id("sc-exl3-2.05-h8-high-s11"), "sc-exl3-2.05-h8-high-s11")
        for value in ("../escape", "..", "/absolute", "a/b", "a\\b", "<script>", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publish.safe_id(value)

    def test_duplicates_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate code"):
            publish.unique([{"code": "J1"}, {"code": "J1"}], "code")

    def test_jsonl_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.jsonl"
            publish.write_jsonl(path, [{"answer": "繁體中文\nSVG"}, {"value": None}])
            self.assertEqual(publish.read_jsonl(path), [{"answer": "繁體中文\nSVG"}, {"value": None}])


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.judges = publish.validate(publish.DOCS)

    def test_full_evidence_and_generated_reports(self):
        for name, content in publish.generated(publish.DOCS).items():
            self.assertEqual((publish.DOCS / name).read_text(), content, name)
        self.assertEqual((publish.DOCS / "data/SHA256SUMS").read_text(), publish.checksums(publish.DOCS))

    def test_failure_denominators_and_conditional_score(self):
        rows = [r for r in self.rows if r["model_id"] == "gguf-iq3_s" and r["effort"] == "high"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(r["grade"] for r in rows) / 5, 6)
        complete = [r for r in rows if r["render_ok"]]
        self.assertEqual(len(complete), 4)
        self.assertEqual(sum(r["grade"] for r in complete) / 4, 7.25)
        failed = next(r for r in rows if r["cap_hit"])
        self.assertEqual((failed["code"], failed["seed"], failed["completion_tokens"]), ("P231", 33, 49152))
        for ratings in self.judges.values():
            self.assertNotIn("P231", ratings)
        table = publish.judge_tables(self.rows, self.judges)
        self.assertIn("| GGUF IQ3_S | 4 | 7.25 |", table)

    def test_numeric_totals_and_exception_to_effort_claim(self):
        self.assertEqual(sum(r["completion_tokens"] for r in self.rows), 1270280)
        self.assertEqual(sum(r["completion_tokens"] > 32768 for r in self.rows), 6)
        iq = [r for r in self.rows if r["model_id"] == "gguf-iq3_xxs"]
        self.assertEqual(sum(r["grade"] for r in iq if r["effort"] == "medium"), 17)
        self.assertEqual(sum(r["grade"] for r in iq if r["effort"] == "high"), 15)

    def test_blind_page_has_no_grades_or_model_labels(self):
        page = publish.gallery(self.rows, True)
        for r in self.rows:
            self.assertNotIn(r["model_id"], page)
        self.assertNotIn("/10", page)
        self.assertEqual(page.count("<img "), 119)
        self.assertEqual(page.count("## P"), 120)

    def test_chart_and_rating_page_cover_every_observation(self):
        chart = ET.fromstring(publish.scatter(self.rows))
        ns = {"s": "http://www.w3.org/2000/svg"}
        self.assertEqual(len(chart.findall(".//s:title", ns)), 121)
        self.assertFalse(chart.findall(".//s:script", ns))
        page = publish.ai_ratings(self.rows, self.judges)
        self.assertEqual(page.count("<img "), 119)
        for name in publish.JUDGES:
            self.assertEqual(page.count(f"| {name} |"), 119)

    def test_local_document_links(self):
        repo = publish.DOCS.parent
        paths = list(publish.DOCS.glob("*.md")) + [repo / "README.md", repo / "CHANGELOG.md", publish.DOCS / "data/README.md"]
        for p in paths:
            links = re.findall(r"\]\(([^)]+)\)|src=\"([^\"]+)\"", p.read_text())
            for markdown, image in links:
                link = markdown or image
                if re.match(r"[a-z]+://", link) or link.startswith("#"):
                    continue
                target = p.parent / link.split("#")[0]
                self.assertTrue(target.exists(), f"{p.name}: missing {link}")

    def test_public_privacy_gate_and_rebased_telemetry(self):
        publish.privacy_check(publish.DOCS)
        data = gzip.decompress((publish.DOCS / "data/sweep-1/resources.jsonl.gz").read_bytes()).decode()
        first = json.loads(data.splitlines()[0])
        self.assertLess(first["t"], 10)
        self.assertEqual(first["cpu_busy_s"], 0)
        self.assertTrue(all(v == 0 for v in first["vm"].values()))
        self.assertTrue(all(v == 0 for counters in first["disk"].values() for v in counters.values()))

    def test_missing_or_duplicate_judge_is_rejected(self):
        original = publish.read_jsonl
        judge_path = publish.DOCS / "data/sweep-1/judges/astra.jsonl"
        for mode in ("missing", "duplicate"):
            def modified(path):
                rows = original(path)
                if path == judge_path:
                    return rows[:-1] if mode == "missing" else rows + [rows[0]]
                return rows
            with self.subTest(mode=mode), patch.object(publish, "read_jsonl", side_effect=modified):
                with self.assertRaises(ValueError):
                    publish.validate(publish.DOCS)

    def test_csv_mismatch_and_missing_grade_are_rejected(self):
        for field, value in (("grade", None), ("wall_s", -1), ("seed", 99), ("resolved_effort", "high")):
            rows = [dict(r) for r in self.rows]
            rows[0][field] = value
            with self.subTest(field=field), patch.object(publish, "load", return_value=rows):
                with self.assertRaises(ValueError):
                    publish.validate(publish.DOCS)

    def test_all_raw_records_and_images_accounted_for(self):
        dest = publish.DOCS / "data/sweep-1"
        records = publish.read_jsonl(dest / "runs.jsonl")
        for r in records:
            path = dest / "raw" / f"{r['cell_id']}-a{r['attempt']}.json"
            raw = json.loads(path.read_text())
            self.assertEqual((raw["cell_id"], raw["attempt"], raw["phase"]), (r["cell_id"], r["attempt"], r["phase"]))
            self.assertEqual(raw["completion_tokens"], r["completion_tokens"])
        self.assertEqual(len(list((dest / "raw").glob("*.json"))), 136)
        self.assertEqual(len(list((publish.DOCS / "images/sweep-1").glob("*.png"))), 119)


if __name__ == "__main__":
    unittest.main()
