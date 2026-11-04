"""Known-bad acceptance receipts must fail closed."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from run_backend_acceptance import classify


class AcceptanceControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "frozen.py"
        self.source.write_text("original")
        self.manifest = dict(
            schema_version="backend-acceptance-v1",
            max_skips_default=5,
            frozen_artifact_sha256={
                "frozen.py": hashlib.sha256(b"original").hexdigest()
            },
            gating_selectors=["tests/gates.py::positive"],
            retained_diagnostic="tests/old.py::invalid_oracle",
            diagnostic_assertion_location="tests/old.py:246: AssertionError",
            scope="controls",
        )
        self.xml = self.root / "result.xml"
        self.suite = ET.Element("testsuite")
        self.gate = ET.SubElement(
            self.suite, "testcase", classname="tests.gates", name="positive"
        )
        self.old = ET.SubElement(
            self.suite, "testcase", classname="tests.old", name="invalid_oracle"
        )
        self.failure = ET.SubElement(
            self.old, "failure", message="Left contains one more item"
        )
        self.failure.text = "> assert flags == []\ntests/old.py:246: AssertionError"

    def verdict(self, exit_code=1):
        self.suite.set("tests", str(len(self.suite)))
        for attribute, tag in (
            ("failures", "failure"),
            ("errors", "error"),
            ("skipped", "skipped"),
        ):
            self.suite.set(attribute, str(sum(len(c.findall(tag)) for c in self.suite)))
        ET.ElementTree(self.suite).write(self.xml)
        return classify(self.xml, exit_code, self.manifest, self.root)["accepted"]

    def test_known_retained_failure_remains_failed_but_acceptance_passes(self):
        self.assertTrue(self.verdict())

    def test_unrelated_failure_is_rejected(self):
        ET.SubElement(self.gate, "failure").text = "new bug"
        self.assertFalse(self.verdict())

    def test_diagnostic_error_or_different_assertion_is_rejected(self):
        self.failure.text = "tests/old.py:200: AssertionError"
        self.assertFalse(self.verdict())

    def test_conflicting_diagnostic_outcomes_are_rejected(self):
        for tag in ("failure", "error", "skipped"):
            extra = ET.SubElement(self.old, tag)
            self.assertFalse(self.verdict())
            self.old.remove(extra)

    def test_invalid_root_and_inconsistent_aggregate_are_rejected(self):
        self.assertTrue(self.verdict())
        for attribute in ("tests", "errors", "failures", "skipped"):
            self.suite.set(attribute, "999")
            ET.ElementTree(self.suite).write(self.xml)
            self.assertFalse(
                classify(self.xml, 1, self.manifest, self.root)["accepted"]
            )
            self.verdict()
        self.suite.tag = "malformed_receipt"
        self.assertFalse(self.verdict())

    def test_existing_receipt_cannot_authorize_a_new_run(self):
        from unittest.mock import patch
        from run_backend_acceptance import main

        (self.root / "default-pytest.xml").write_text("prior receipt")
        with (
            patch("sys.argv", ["runner", "--output-dir", str(self.root)]),
            patch("run_backend_acceptance.subprocess.run") as run,
        ):
            with self.assertRaises(SystemExit):
                main()
            run.assert_not_called()

    def test_missing_duplicate_and_skipped_controls_are_rejected(self):
        for state in ("missing", "duplicate", "skip"):
            with self.subTest(state=state):
                saved = list(self.suite)
                if state == "missing":
                    self.suite.remove(self.gate)
                elif state == "duplicate":
                    self.suite.append(self.gate)
                else:
                    ET.SubElement(self.gate, "skipped")
                self.assertFalse(self.verdict())
                self.suite[:] = saved
                self.gate[:] = []

    def test_changed_frozen_source_is_rejected(self):
        self.source.write_text("changed")
        self.assertFalse(self.verdict())

    def migrate(self):
        parent = dict(self.manifest, schema_version="backend-acceptance-v1")
        other = self.root / "unchanged.txt"
        other.write_bytes(b"independent oracle")
        parent["frozen_artifact_sha256"] = dict(parent["frozen_artifact_sha256"])
        parent["frozen_artifact_sha256"][other.name] = hashlib.sha256(
            other.read_bytes()
        ).hexdigest()
        parent_path = self.root / "v1.json"
        parent_path.write_text(json.dumps(parent))
        archive = self.root / "original.py.txt"
        archive.write_bytes(self.source.read_bytes())
        old_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.source.write_bytes(b"new contract with original oracles preserved")
        new_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
        self.manifest = dict(parent, schema_version="backend-acceptance-v2")
        self.manifest["frozen_artifact_sha256"] = {
            **parent["frozen_artifact_sha256"],
            self.source.name: new_hash,
            archive.name: old_hash,
            parent_path.name: parent_hash,
        }
        self.manifest["transition"] = {
            "parent_manifest": parent_path.name,
            "parent_manifest_sha256": parent_hash,
            "test_path": self.source.name,
            "prior_test_archive": archive.name,
            "prior_test_sha256": old_hash,
            "current_test_sha256": new_hash,
            "reason": "Reviewed conservative coverage contract migration",
        }
        return parent_path, archive

    def test_versioned_transition_retains_prior_authority(self):
        self.migrate()
        self.assertTrue(self.verdict())

    def test_changed_parent_or_archive_cannot_be_authorized_by_v2(self):
        parent, archive = self.migrate()
        for path in (parent, archive):
            saved = path.read_bytes()
            path.write_bytes(b"changed")
            self.assertFalse(self.verdict())
            path.write_bytes(saved)

    def test_transition_cannot_expand_diagnostics_gates_or_skips(self):
        self.migrate()
        for key, value in (
            ("max_skips_default", 99),
            ("gating_selectors", []),
            ("retained_diagnostic", "tests/other.py::new_failure"),
        ):
            saved = self.manifest[key]
            self.manifest[key] = value
            self.assertFalse(self.verdict())
            self.manifest[key] = saved

    def test_transition_cannot_drop_an_unchanged_oracle(self):
        self.migrate()
        del self.manifest["frozen_artifact_sha256"]["unchanged.txt"]
        self.assertFalse(self.verdict())

    def test_missing_transition_or_reason_is_rejected(self):
        self.migrate()
        saved = self.manifest.pop("transition")
        self.assertFalse(self.verdict())
        self.manifest["transition"] = saved
        saved["reason"] = ""
        self.assertFalse(self.verdict())

    def declaration_transition(self):
        self.migrate()
        parent = dict(self.manifest)
        parent_path = self.root / "v2.json"
        parent_path.write_text(json.dumps(parent))
        parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
        module = self.root / "backend/tests/declared.py"
        module.parent.mkdir(parents=True)
        module.write_text("source declared controls")
        old_selector = parent["gating_selectors"][0]
        new_selector = "tests/declared.py::positive"
        added = ["tests/declared.py::unknown", "tests/declared.py::continuous"]
        prior_rule = {
            "selector": parent["retained_diagnostic"],
            "trace_contains": [
                "assert flags == []",
                parent["diagnostic_assertion_location"],
            ],
            "message_contains": ["Left contains one more item"],
        }
        rule = {
            "selector": old_selector,
            "trace_contains": [
                "assert integer_warning",
                "tests/gates.py:10: AssertionError",
            ],
            "message_contains": ["assert False"],
        }
        self.manifest = {
            "schema_version": "backend-acceptance-v3",
            "max_skips_default": parent["max_skips_default"],
            "scope": "proposed source applicability controls",
            "frozen_artifact_sha256": {
                **parent["frozen_artifact_sha256"],
                parent_path.name: parent_hash,
                "backend/tests/declared.py": hashlib.sha256(
                    module.read_bytes()
                ).hexdigest(),
            },
            "gating_selectors": [new_selector, *added],
            "retained_diagnostics": [prior_rule, rule],
            "transition": {
                "parent_manifest": parent_path.name,
                "parent_manifest_sha256": parent_hash,
                "replacements": [
                    {
                        "prior_selector": old_selector,
                        "replacement_selector": new_selector,
                        "diagnostic": rule,
                        "reason": "Original input does not declare integer response granularity.",
                    }
                ],
                "additional_controls": added,
                "reason": "Require source applicability without suppressing positive controls.",
            },
        }
        failure = ET.SubElement(self.gate, "failure", message="assert False")
        failure.text = "assert integer_warning\ntests/gates.py:10: AssertionError"
        self.declared = []
        for name in ("positive", "unknown", "continuous"):
            self.declared.append(
                ET.SubElement(
                    self.suite, "testcase", classname="tests.declared", name=name
                )
            )
        return parent_path, failure, module

    def test_declaration_transition_keeps_actual_diagnostics_failed(self):
        self.declaration_transition()
        self.assertTrue(self.verdict())
        self.assertEqual(len(list(self.suite.iter("failure"))), 2)

    def test_default_cli_runs_unchanged_collection_with_the_approved_registry(self):
        import io
        import os
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from run_backend_acceptance import main

        self.declaration_transition()
        self.assertTrue(self.verdict())
        manifest = self.root / "tools/data/backend_acceptance_v3.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(self.manifest))
        output = self.root / "fresh-run"

        def run_default(command, *, cwd, check):
            self.assertEqual(command[:4], [sys.executable, "-m", "pytest", "-v"])
            self.assertEqual(
                command[4:], [f"--junitxml={output / 'default-pytest.xml'}"]
            )
            self.assertEqual(cwd, self.root / "backend")
            self.assertFalse(check)
            (output / "default-pytest.xml").write_bytes(self.xml.read_bytes())
            return SimpleNamespace(returncode=1)

        with (
            patch("run_backend_acceptance.ROOT", self.root),
            patch("sys.argv", ["runner", "--output-dir", str(output)]),
            patch.dict(os.environ, {"VERIDICAL_MAX_SKIPS": "5"}),
            patch("run_backend_acceptance.subprocess.run", side_effect=run_default),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(main(), 0)
        report = json.loads((output / "acceptance.json").read_text())
        self.assertEqual(report["registry_version"], "backend-acceptance-v3")
        self.assertEqual(report["default_result"], "FAIL")
        self.assertEqual(len(report["failed_selectors"]), 2)
        self.assertTrue(report["accepted"])

    def test_declaration_transition_rejects_wrong_failure_reason_or_error(self):
        _, failure, _ = self.declaration_transition()
        failure.text = "An unrelated assertion"
        self.assertFalse(self.verdict())
        failure.text = "assert integer_warning\ntests/gates.py:10: AssertionError"
        failure.set("message", "unrelated message")
        self.assertFalse(self.verdict())
        failure.tag = "error"
        self.assertFalse(self.verdict())

    def test_declaration_transition_rejects_extra_failure_and_missing_diagnostic(self):
        self.declaration_transition()
        extra = ET.SubElement(self.declared[0], "failure", message="new defect")
        self.assertFalse(self.verdict())
        self.declared[0].remove(extra)
        self.suite.remove(self.gate)
        self.assertFalse(self.verdict())

    def test_declaration_transition_requires_every_positive_and_negative_control(self):
        self.declaration_transition()
        for case in self.declared:
            for state in ("missing", "duplicate", "skip", "error", "failure"):
                with self.subTest(case=case.get("name"), state=state):
                    saved = list(self.suite)
                    if state == "missing":
                        self.suite.remove(case)
                    elif state == "duplicate":
                        self.suite.append(case)
                    else:
                        ET.SubElement(case, "skipped" if state == "skip" else state)
                    self.assertFalse(self.verdict())
                    self.suite[:] = saved
                    case[:] = []

    def test_declaration_transition_rejects_source_and_parent_mutations(self):
        parent, _, module = self.declaration_transition()
        for path in (parent, module, self.root / "original.py.txt"):
            saved = path.read_bytes()
            path.write_bytes(b"changed")
            self.assertFalse(self.verdict())
            path.write_bytes(saved)

    def test_declaration_transition_cannot_drop_gates_or_expand_skips(self):
        self.declaration_transition()
        for key, value in (
            ("gating_selectors", self.manifest["gating_selectors"][:-1]),
            ("max_skips_default", 99),
            ("retained_diagnostics", self.manifest["retained_diagnostics"][:-1]),
        ):
            saved = self.manifest[key]
            self.manifest[key] = value
            self.assertFalse(self.verdict())
            self.manifest[key] = saved

    def test_declaration_transition_rejects_malformed_failure_identity(self):
        self.declaration_transition()
        for value in ([], "trace", [""], [None]):
            saved = self.manifest["retained_diagnostics"][1]["trace_contains"]
            self.manifest["retained_diagnostics"][1]["trace_contains"] = value
            self.assertFalse(self.verdict())
            self.manifest["retained_diagnostics"][1]["trace_contains"] = saved

    def test_skip_floor_cannot_be_hidden_by_expected_failure(self):
        for index in range(6):
            case = ET.SubElement(
                self.suite, "testcase", classname="tests.other", name=f"skipped_{index}"
            )
            ET.SubElement(
                case, "skipped", type="pytest.skip", message="required DB missing"
            )
        self.assertFalse(self.verdict())
        for case in list(self.suite)[2:]:
            case.find("skipped").set("type", "pytest.xfail")
        self.assertTrue(self.verdict())

    def test_absent_original_failure_is_rejected(self):
        self.old.remove(self.failure)
        self.assertFalse(self.verdict())

    def test_bad_pytest_exit_is_rejected(self):
        for code in (0, 2, 3, 4, 5):
            self.assertFalse(self.verdict(code))

    def test_missing_and_truncated_junit_are_rejected(self):
        for content in (None, "<testsuite>"):
            if content is not None:
                self.xml.write_text(content)
            self.assertFalse(
                classify(self.xml, 1, self.manifest, self.root)["accepted"]
            )


if __name__ == "__main__":
    unittest.main()
