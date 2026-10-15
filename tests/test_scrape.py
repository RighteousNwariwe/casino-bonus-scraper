"""Offline tests. No network and no API key needed: run `python -m unittest`.

The HTML fixture is a hand-made imitation of the BetProof comparison table,
so these tests check the parsing and validation logic, not the live site.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scrape  # noqa: E402

FIXTURE = """
<table>
  <thead><tr><th>Casino</th><th>Bonus</th><th>Wagering</th><th>Min Deposit</th>
  <th>Clearable?</th><th>Licence</th></tr></thead>
  <tbody>
    <tr><td><a href="/casino-reviews/hollywoodbets/">Hollywoodbets</a> <a href="/go/x/">Play</a></td>
        <td>R25 sign-up + 50 free spins</td><td>1x sports / 10x spins</td><td>R10</td><td>Yes</td><td>WCGRB</td></tr>
    <tr><td><a href="/casino-reviews/playabets/">PlayaBets</a></td>
        <td>100% up to R3,000</td><td>6x</td><td>R10</td><td>Yes</td><td>MER</td></tr>
    <tr><td><a href="/casino-reviews/springbok-casino/">Springbok Casino</a></td>
        <td>R250 no-deposit + up to R11,500 (3 deposits)</td><td>30x</td><td>R25</td><td>No</td><td>Offshore</td></tr>
  </tbody>
</table>
"""


class FakeModel:
    """Stands in for the model call and returns canned answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        return self.answers.pop(0)


GOOD = {
    "welcome_bonus": "100% up to R3,000",
    "wagering_multiplier": 6,
    "wagering_note": "Applies to the deposit match.",
    "min_deposit_amount": 10,
    "min_deposit_currency": "ZAR",
}


class ParseTests(unittest.TestCase):
    def test_parses_all_rows(self):
        rows = scrape.parse_table(FIXTURE)
        self.assertEqual([r["name"] for r in rows], ["Hollywoodbets", "PlayaBets", "Springbok Casino"])
        self.assertEqual(rows[0]["wagering_text"], "1x sports / 10x spins")
        self.assertEqual(rows[1]["review_url"], "https://betproof.co.za/casino-reviews/playabets/")

    def test_missing_casino_raises(self):
        with self.assertRaises(scrape.ParseError):
            scrape.pick_targets(scrape.parse_table(FIXTURE), ["Not A Casino"])

    def test_no_table_raises(self):
        with self.assertRaises(scrape.ParseError):
            scrape.parse_table("<p>nothing here</p>")


class ValidationTests(unittest.TestCase):
    ROW = {"wagering_text": "6x", "min_deposit_text": "R10"}

    def test_accepts_matching_numbers(self):
        scrape.validate_extraction(GOOD, self.ROW)

    def test_rejects_invented_wagering(self):
        with self.assertRaises(ValueError):
            scrape.validate_extraction({**GOOD, "wagering_multiplier": 35}, self.ROW)

    def test_handles_thousands_separator(self):
        self.assertIn(11500.0, scrape.numbers_in("up to R11,500"))


class ExtractionTests(unittest.TestCase):
    ROW = {"name": "PlayaBets", "bonus_text": "100% up to R3,000",
           "wagering_text": "6x", "min_deposit_text": "R10"}

    def test_retries_after_bad_answer(self):
        model = FakeModel([{**GOOD, "wagering_multiplier": 35}, GOOD])
        scrape.time.sleep = lambda s: None  # keep the test fast
        result = scrape.extract_with_llm(model, self.ROW)
        self.assertEqual(result["wagering_multiplier"], 6)
        self.assertEqual(model.calls, 2)

    def test_does_not_retry_client_errors(self):
        response = scrape.requests.Response()
        response.status_code = 403
        response._content = b"bad key"

        def call(prompt):
            raise scrape.requests.HTTPError(response=response)

        with self.assertRaises(RuntimeError):
            scrape.extract_with_llm(call, self.ROW)


class OutputTests(unittest.TestCase):
    def test_writes_valid_json(self):
        row = scrape.parse_table(FIXTURE)[1]
        record = scrape.build_record(row, GOOD)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out" / "casinos.json"
            scrape.write_output([record], path)
            data = json.loads(path.read_text())
        self.assertEqual(data["casinos"][0]["min_deposit"], {"amount": 10, "currency": "ZAR"})
        self.assertEqual(data["casinos"][0]["source_url"], scrape.SOURCE_URL)


if __name__ == "__main__":
    unittest.main()
