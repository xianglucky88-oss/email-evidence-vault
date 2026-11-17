import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
AUDIT = ROOT / "tools" / "index_audit.py"
CASES = ROOT / "docs" / "samples" / "cases"
BASELINE = ROOT / "docs" / "samples" / "baseline" / "v0.1.0"


def load_audit():
    spec = importlib.util.spec_from_file_location("index_audit", AUDIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_audit(audit, argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return audit.main(argv)


class TestFrozenBaseline(unittest.TestCase):
    """The documented samples and the v0.1.0 baseline must not drift."""

    def setUp(self):
        self.audit = load_audit()

    def test_manifest_schema(self):
        manifest = json.loads((BASELINE / "manifest.json").read_text())
        self.assertEqual(manifest["schema"], self.audit.MANIFEST_SCHEMA)
        self.assertEqual(manifest["baseline"], "v0.1.0")

    def test_check_reports_no_drift(self):
        code = run_audit(
            self.audit,
            [
                "check",
                "--cases",
                str(CASES),
                "--baseline",
                str(BASELINE),
                "--json",
            ],
        )
        self.assertEqual(code, 0)

    def test_every_case_has_baseline_and_manifest_entry(self):
        manifest = json.loads((BASELINE / "manifest.json").read_text())
        case_ids = {path.stem for path in CASES.glob("*.json")}
        self.assertEqual(case_ids, set(manifest["cases"]))
        self.assertEqual(
            case_ids,
            {
                p.name[: -len(".expected.json")]
                for p in BASELINE.glob("*.expected.json")
            },
        )

    def test_manifest_is_reproducible(self):
        rebuilt = self.audit.build_manifest(CASES, "v0.1.0")
        stored = json.loads((BASELINE / "manifest.json").read_text())
        self.assertEqual(self.audit.canonical(rebuilt), self.audit.canonical(stored))

    def test_rules_fingerprint_is_pinned(self):
        stored = json.loads((BASELINE / "manifest.json").read_text())
        self.assertRegex(stored["rules_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(stored["rules_sha256"], self.audit.rule_fingerprint())


class TestRunSidecar(unittest.TestCase):
    def setUp(self):
        self.audit = load_audit()

    def test_run_writes_sidecar_and_compare_reads_it(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            before = Path(directory) / "before"
            after = Path(directory) / "after"
            for target in (before, after):
                code = run_audit(
                    self.audit,
                    ["run", "--cases", str(CASES), "--out", str(target)],
                )
                self.assertEqual(code, 0)
                sidecar = json.loads((target / "run-meta.json").read_text())
                self.assertEqual(sidecar["schema"], self.audit.RUN_SCHEMA)
                self.assertEqual(sidecar["rules_sha256"], self.audit.rule_fingerprint())

            code = run_audit(
                self.audit,
                ["compare", "--before", str(before), "--after", str(after)],
            )
            self.assertEqual(code, 0)

            # A silent rule change (identical outputs) must still fail.
            sidecar_path = after / "run-meta.json"
            sidecar = json.loads(sidecar_path.read_text())
            sidecar["rules_sha256"] = "0" * 64
            sidecar_path.write_text(json.dumps(sidecar))
            code = run_audit(
                self.audit,
                ["compare", "--before", str(before), "--after", str(after)],
            )
            self.assertEqual(code, 1)

    def test_run_refuses_to_overwrite_without_force(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out"
            self.assertEqual(
                run_audit(
                    self.audit,
                    ["run", "--cases", str(CASES), "--out", str(target)],
                ),
                0,
            )
            self.assertEqual(
                run_audit(
                    self.audit,
                    ["run", "--cases", str(CASES), "--out", str(target)],
                ),
                2,
            )
            self.assertEqual(
                run_audit(
                    self.audit,
                    [
                        "run",
                        "--cases",
                        str(CASES),
                        "--out",
                        str(target),
                        "--force",
                    ],
                ),
                0,
            )


class TestClassification(unittest.TestCase):
    """The audit harness must name the dimension a rule change touches."""

    def setUp(self):
        self.audit = load_audit()

    def _compare(self, case_id, mutate):
        case_path = CASES / f"{case_id}.json"
        payload = json.loads(case_path.read_text())
        before = self.audit.run_pipeline(payload)
        after = mutate(json.loads(json.dumps(before)))
        return self.audit.compare_case(payload, case_path, before, after)

    def test_thread_change_is_not_hash_change(self):
        def mutate(result):
            result["threads"] = {"m1": ["m1", "m2", "m3", "m4"]}
            return result

        report = self._compare("s01-missing-message-id", mutate)
        self.assertEqual(report["classification"], ["THREAD"])
        self.assertFalse(report["attachments"]["changed"])
        self.assertEqual(report["threads"]["removed_roots"], ["m4", "missing-message-id"])

    def test_unchanged_result_is_identical(self):
        report = self._compare("s03-attachment-addressing", lambda result: result)
        self.assertEqual(report["classification"], ["IDENTICAL"])
        self.assertTrue(report["attachments"]["count_consistent_before"])

    def test_hold_change_is_hold_class(self):
        def mutate(result):
            result["held_messages"] = []
            return result

        report = self._compare("s01-missing-message-id", mutate)
        self.assertEqual(report["classification"], ["HOLD"])
        self.assertFalse(report["threads"]["changed"])

    def test_metadata_only_change_is_envelope_class(self):
        def mutate(result):
            result["schema_version"] = "1"
            return result

        report = self._compare("s03-attachment-addressing", mutate)
        self.assertEqual(report["classification"], ["ENVELOPE"])
        self.assertFalse(report["threads"]["changed"])
        self.assertFalse(report["attachments"]["changed"])

    def test_attachment_count_change_is_hash_class(self):
        def mutate(result):
            result["attachment_count"] = 1
            return result

        report = self._compare("s03-attachment-addressing", mutate)
        self.assertEqual(report["classification"], ["HASH"])


class TestAttachmentOracle(unittest.TestCase):
    def setUp(self):
        self.audit = load_audit()

    def test_oracle_matches_engine_for_all_cases(self):
        for case_path in sorted(CASES.glob("*.json")):
            payload = json.loads(case_path.read_text())
            result = self.audit.run_pipeline(payload)
            self.assertEqual(
                result["attachment_count"],
                len(set(self.audit.attachment_digests(payload))),
                case_path.name,
            )

    def test_filename_does_not_participate(self):
        payload = {
            "messages": [
                {
                    "id": "a",
                    "attachments": [
                        {"name": "one.txt", "content": "same"},
                        {"name": "two.txt", "content": "same"},
                    ],
                }
            ]
        }
        self.assertEqual(len(self.audit.attachment_digests(payload)), 2)
        self.assertEqual(len(set(self.audit.attachment_digests(payload))), 1)

    def test_content_is_not_unicode_normalized(self):
        composed = {"messages": [{"id": "a", "attachments": [{"content": "é"}]}]}
        decomposed = {"messages": [{"id": "a", "attachments": [{"content": "é"}]}]}
        self.assertNotEqual(
            self.audit.attachment_digests(composed),
            self.audit.attachment_digests(decomposed),
        )

    def test_non_string_content_is_rejected(self):
        payload = {"messages": [{"id": "a", "attachments": [{"content": 1}]}]}
        with self.assertRaises(self.audit.AuditError):
            self.audit.attachment_digests(payload)

    def test_compare_identical_directory_sets(self):
        code = run_audit(
            self.audit,
            ["compare", "--before", str(BASELINE), "--after", str(BASELINE)],
        )
        self.assertEqual(code, 0)

    def test_compare_rejects_mismatched_case_sets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "only-one.json"
            target.write_text("{}\n")
            code = run_audit(
                self.audit,
                ["compare", "--before", str(BASELINE), "--after", directory],
            )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
