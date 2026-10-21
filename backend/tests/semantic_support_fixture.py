"""Explicit supportive test double for tests of grading/voting mechanics.

These cases script support; adversarial and integration behavior is exercised
separately in test_semantic_support_gate. They are not analytical labels.
"""

import json


def supported_response(prompt):
    task = json.loads(prompt.split("--- TASK JSON ---\n", 1)[1])
    return {
        "items": [
            {
                "index": item["index"],
                "status": "supported",
                "reason": "Scripted support control.",
                "requirements": [
                    {
                        "key": obligation["key"],
                        "status": "supported",
                        "evidence_quotes": item["candidate_quotes"],
                    }
                    for obligation in item["required_obligations"]
                ],
            }
            for item in task["items"]
        ]
    }
