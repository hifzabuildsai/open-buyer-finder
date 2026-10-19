import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from buyerfind.core import SourceError, collect, load_profile
from buyerfind.report import metrics

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "examples/fourgate.json"
FIXTURE = ROOT / "examples/demo-responses.json"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run"

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "buyerfind", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=10)

    def demo(self, fixture=FIXTURE):
        result = self.cli("demo", PROFILE, "--fixture", fixture, "--since", "2026-10-01",
                          "--out", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.out / "run.json").read_text())

    def review(self, row):
        return {"id": row["id"], "source_url": row["url"], "decision": "potential_fit",
                "reason": "The source describes a staging write checked manually.",
                "evidence_quote": "We check every update by hand in staging.",
                "unknowns": ["Budget and decision authority", "Current need and compatibility"],
                "next_question": "How do you currently check this write?"}

    def save_reviews(self, reviews):
        path = Path(self.tmp.name) / "review.json"
        path.write_text(json.dumps(reviews))
        return path

    def test_demo_cli_generates_honest_reports(self):
        run = self.demo()
        self.assertEqual(run["mode"], "synthetic_demo")
        self.assertEqual(len(run["items"]), 2)
        self.assertTrue(all(x["decision"] == "unreviewed" for x in run["items"]))
        html = (self.out / "report.html").read_text()
        self.assertIn("not confirmed buyers", html)
        self.assertIn("synthetic_demo", html)
        self.assertIn("Not measured", html)
        self.assertTrue((self.out / "candidates.csv").exists())

    def test_review_citation_and_founder_feedback_flow(self):
        run = self.demo()
        row = next(x for x in run["items"] if x["author"] == "demo-workflow-owner")
        result = self.cli("review", self.out, self.save_reviews([self.review(row)]),
                          "--reviewer", "coding-agent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("potential_fit", (self.out / "report.html").read_text())
        self.assertIn("potential_fit", (self.out / "candidates.csv").read_text())
        result = self.cli("feedback", self.out, "--id", row["id"], "--verdict", "relevant")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli("summary", self.out)
        self.assertEqual(json.loads(result.stdout)["relevance_rate"], 1.0)
        # Relabel, don't inflate the denominator with duplicate decisions.
        self.cli("feedback", self.out, "--id", row["id"], "--verdict", "unclear")
        result = self.cli("summary", self.out)
        self.assertIsNone(json.loads(result.stdout)["relevance_rate"])

    def test_fabricated_quote_or_url_cannot_modify_run(self):
        run = self.demo()
        row = next(x for x in run["items"] if x["author"] == "demo-workflow-owner")
        before = (self.out / "run.json").read_bytes()
        for key, value in [("evidence_quote", "I have a budget of $10,000"),
                           ("source_url", "https://example.com/not-the-source"),
                           ("decision", "confirmed_buyer")]:
            with self.subTest(key=key):
                review = self.review(row)
                review[key] = value
                result = self.cli("review", self.out, self.save_reviews([review]), "--reviewer", "human")
                self.assertEqual(result.returncode, 1)
                self.assertEqual((self.out / "run.json").read_bytes(), before)

    def test_existing_output_is_preserved(self):
        self.demo()
        before = (self.out / "run.json").read_bytes()
        result = self.cli("demo", PROFILE, "--fixture", FIXTURE, "--out", self.out)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.out / "run.json").read_bytes(), before)

    def test_partial_source_failure_returns_nonzero_and_keeps_evidence(self):
        fixture = json.loads(FIXTURE.read_text())
        fixture["hn"][0] = {"fixture_error": "HTTP 429; quota exhausted"}
        path = Path(self.tmp.name) / "partial.json"
        path.write_text(json.dumps(fixture))
        result = self.cli("search", PROFILE, "--fixture", path, "--since", "2026-10-01", "--out", self.out)
        self.assertEqual(result.returncode, 2, result.stderr)
        run = json.loads((self.out / "run.json").read_text())
        self.assertEqual(len(run["items"]), 2)
        self.assertFalse(run["complete"])
        self.assertIn("Incomplete research", (self.out / "report.html").read_text())

    def test_retrieval_deduplicates_and_excludes_old_closed_and_prs(self):
        fixture = json.loads(FIXTURE.read_text())
        row = fixture["github"][0]["items"][0]
        duplicate = copy.deepcopy(row)
        fixture["github"][1]["items"].append(duplicate)
        for number, change in [(3, {"created_at": "2020-01-01T00:00:00Z"}),
                               (4, {"state": "closed"}), (5, {"pull_request": {}})]:
            other = copy.deepcopy(row)
            other.update(change, html_url=f"https://github.com/example/demo/issues/{number}")
            fixture["github"][0]["items"].append(other)
        run = collect(load_profile(PROFILE), "2026-10-01", fixture=fixture)
        self.assertEqual(len(run["items"]), 2)
        found = next(x for x in run["items"] if x["author"] == "demo-workflow-owner")
        self.assertEqual(len(found["queries"]), 2)

    def test_untrusted_html_and_csv_formulas_are_inert(self):
        fixture = json.loads(FIXTURE.read_text())
        row = fixture["github"][0]["items"][0]
        row["title"] = '=HYPERLINK("https://bad.invalid")<script>alert(1)</script>'
        row["body"] = '<img src=x onerror="alert(2)">'
        path = Path(self.tmp.name) / "hostile.json"
        path.write_text(json.dumps(fixture))
        self.demo(path)
        html = (self.out / "report.html").read_text()
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img ", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("'=HYPERLINK", (self.out / "candidates.csv").read_text())

    def test_public_scope_and_token_are_only_sent_to_github(self):
        profile = load_profile(PROFILE)
        calls = []

        def fetch(url, **kwargs):
            calls.append((url, kwargs))
            return {"items": [], "hits": []}

        from unittest.mock import patch
        with patch.dict(os.environ, {"GITHUB_TOKEN": "unit-test-secret"}):
            run = collect(profile, "2026-10-01", fetch=fetch)
        self.assertEqual(len(calls), 4)
        for url, kwargs in calls:
            if "api.github.com" in url:
                self.assertIn("is%3Apublic", url)
                self.assertEqual(kwargs["token"], "unit-test-secret")
            else:
                self.assertNotIn("token", kwargs)
        self.assertNotIn("unit-test-secret", json.dumps(run))

    def test_malformed_api_is_not_empty_success(self):
        run = collect(load_profile(PROFILE), "2026-10-01", fetch=lambda *a, **k: {"surprise": []})
        self.assertFalse(run["complete"])
        self.assertTrue(all(x["status"] == "error" for x in run["requests"]))

    def test_unknowns_and_no_feedback_are_not_zero_precision(self):
        self.assertIsNone(metrics({"feedback": []})["relevance_rate"])
        self.assertIsNone(metrics({"feedback": [{"verdict": "unclear"}]})["relevance_rate"])


if __name__ == "__main__":
    unittest.main()
