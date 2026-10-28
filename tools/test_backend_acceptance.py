"""Known-bad acceptance receipts must fail closed."""

import hashlib
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
