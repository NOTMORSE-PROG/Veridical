"""Execute unchanged default tests; report retained invalid oracle separately.

The default pytest result remains a failure. Acceptance checks the source-correct
controls and refuses every other failure, absent gate or changed frozen artifact.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/data/backend_acceptance_v1.json"


def classify(
    xml_path: Path, pytest_exit: int, manifest: dict, root: Path, *, max_skips=None
) -> dict:
    problems = []
    default_result = (
        "PASS" if pytest_exit == 0 else "FAIL" if pytest_exit == 1 else "ERROR"
    )
    if pytest_exit != 1:
        problems.append(
            f"Expected retained default failure (exit 1); got exit {pytest_exit}."
        )
    for name, expected in manifest["frozen_artifact_sha256"].items():
        path = root / name
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            problems.append(f"Frozen source changed or missing: {name}")
    try:
        document = ET.parse(xml_path)
    except (ET.ParseError, OSError):
        return {
            "accepted": False,
            "default_pytest_exit": pytest_exit,
            "default_result": default_result,
            "problems": problems + ["JUnit missing or invalid."],
        }
    element = document.getroot()
    suites = list(element) if element.tag == "testsuites" else [element]
    if element.tag not in {"testsuite", "testsuites"} or not suites:
        problems.append("Invalid JUnit root or no test suite.")
    for suite in suites:
        if suite.tag != "testsuite" or any(
            child.tag not in {"testcase", "properties", "system-out", "system-err"}
            for child in suite
        ):
            problems.append("Unsupported JUnit suite structure.")
        suite_cases = list(suite.iter("testcase"))
        for case in suite_cases:
            outcomes = [
                child for child in case if child.tag in {"failure", "error", "skipped"}
            ]
            if len(outcomes) > 1 or not case.get("name") or not case.get("classname"):
                problems.append("Invalid or conflicting testcase outcomes.")
        counts = {
            "tests": len(suite_cases),
            "failures": sum(len(c.findall("failure")) for c in suite_cases),
            "errors": sum(len(c.findall("error")) for c in suite_cases),
            "skipped": sum(len(c.findall("skipped")) for c in suite_cases),
        }
        for key, expected in counts.items():
            if suite.get(key) != str(expected):
                problems.append(f"JUnit {key} count missing or inconsistent.")
    if element.tag == "testsuites":
        for key in ("tests", "failures", "errors", "skipped"):
            if key in element.attrib:
                try:
                    expected = sum(int(suite.get(key, "-1")) for suite in suites)
                except ValueError:
                    expected = -1
                if element.get(key) != str(expected):
                    problems.append(f"JUnit root {key} count inconsistent.")
    cases = {}
    skipped = []
    failed = []
    for case in document.iter("testcase"):
        module = case.get("classname", "").replace(".", "/") + ".py"
        node = module + "::" + case.get("name", "")
        cases.setdefault(node, []).append(case)
        skip = case.find("skipped")
        if skip is not None:
            skipped.append(
                {
                    "selector": node,
                    "type": skip.get("type"),
                    "reason": skip.get("message", skip.text or ""),
                }
            )
        failure, error = case.find("failure"), case.find("error")
        if failure is not None or error is not None:
            failed.append(node)
            if node != manifest["retained_diagnostic"] or error is not None:
                problems.append(f"Unexpected failure/error: {node}")
            elif (
                "assert flags == []" not in (failure.text or "")
                or manifest["diagnostic_assertion_location"]
                not in (failure.text or "").replace("\\", "/")
                or "Left contains one more item" not in failure.get("message", "")
            ):
                problems.append("Retained diagnostic failed for a different reason.")
    for node in manifest["gating_selectors"]:
        matches = cases.get(node, [])
        if len(matches) != 1 or any(
            matches[0].find(kind) is not None
            for kind in ("failure", "error", "skipped")
        ):
            problems.append(
                f"Required control absent, duplicated or nonpassing: {node}"
            )
    diagnostic = cases.get(manifest["retained_diagnostic"], [])
    if len(diagnostic) != 1 or diagnostic[0].find("failure") is None:
        problems.append(
            "Original default failing diagnostic absent, duplicated or no longer failed."
        )
    if len(failed) != 1:
        problems.append(f"Expected one retained failure; observed {len(failed)}.")
    skip_limit = manifest["max_skips_default"] if max_skips is None else max_skips
    actual_skips = sum(item["type"] != "pytest.xfail" for item in skipped)
    if actual_skips > skip_limit:
        problems.append(
            f"Incomplete verification: {actual_skips} skips exceed {skip_limit}."
        )
    return {
        "accepted": not problems,
        "default_pytest_exit": pytest_exit,
        "default_result": default_result,
        "test_cases": sum(map(len, cases.values())),
        "failed_selectors": failed,
        "skipped_or_xfailed": skipped,
        "skip_limit": skip_limit,
        "source_correct_controls": len(manifest["gating_selectors"]),
        "scope": manifest["scope"],
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    xml = args.output_dir.resolve() / "default-pytest.xml"
    if xml.exists() or (args.output_dir / "acceptance.json").exists():
        parser.error("Prior receipts preserved; select a fresh output directory.")
    # No deselection, test rewriting, xfail or skip injection.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", f"--junitxml={xml}"],
        cwd=ROOT / "backend",
        check=False,
    )
    manifest = json.loads(MANIFEST.read_text())
    skip_limit = int(
        os.environ.get("VERIDICAL_MAX_SKIPS", manifest["max_skips_default"])
    )
    report = classify(xml, result.returncode, manifest, ROOT, max_skips=skip_limit)
    (args.output_dir / "acceptance.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))
    print(
        "Default pytest: "
        + report["default_result"]
        + "; backend acceptance: "
        + ("PASS WITH RETAINED FAILED DIAGNOSTIC" if report["accepted"] else "FAIL")
    )
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
