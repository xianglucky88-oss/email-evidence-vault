import json
import unittest
from pathlib import Path
from email_evidence_vault.pipeline import run
from email_evidence_vault.parser import load_records
from email_evidence_vault.engine import calculate

def fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "input.json").read_text())

class TestCore(unittest.TestCase):
    def test_pipeline_and_modules(self):
        payload = fixture()
        rows = load_records(payload)
        self.assertTrue(rows)
        data = calculate(rows, payload)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)
        result = run(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], 'email-evidence-vault')

        assert result["threads"]["m1"] == ["m1", "m2"] and result["attachment_count"] == 1
