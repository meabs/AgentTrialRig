"""What the agent says it verified, set against what the verify command found.

With `claim = true` the prompt asks the agent to end with one line:

    VERIFICATION: {"ran": [...], "result": "passed|failed|not-run", "could_not_verify": [...], "missing": [...]}

The claim is the agent's own account, so it is a lead, not evidence. It becomes useful next to the measured result:
an agent that says "passed" when the independent check failed has a false_pass, and the `missing` and
`could_not_verify` notes say what would have made checking easier (a test command, a fixture, a documented step).
This compares the agent's report with the independent check; it does not measure whether the report was honest
about the commands the agent actually ran.
"""

from __future__ import annotations

import json
import re

INSTRUCTION = (
    "When you finish, end your final message with one line starting with VERIFICATION: followed by a JSON object with "
    'these keys: "ran" (the commands you ran to check your work), "result" ("passed", "failed" or "not-run"), '
    '"could_not_verify" (anything you could not check) and "missing" (anything that would have made checking easier, '
    "such as a test command, a fixture or a documented step). Be literal: say not-run if you did not run the tests."
)

RESULTS = ("passed", "failed", "not-run")

# Old run.json files used overclaim/underclaim; map them when loading.
_LEGACY_MATCH = {"overclaim": "false_pass", "underclaim": "false_fail"}

MATCHES = ("agree", "false_pass", "false_fail", "none")


def extract(answer: str) -> dict | None:
    """The last well-formed VERIFICATION line in the answer, or None."""
    found = None
    for m in re.finditer(r"VERIFICATION:\s*(\{.*\})", answer or ""):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("result") in RESULTS:
            found = {"ran": _strs(d.get("ran")), "result": d["result"], "could_not_verify": _strs(d.get("could_not_verify")), "missing": _strs(d.get("missing"))}
    return found


def _strs(v) -> list[str]:
    return [str(x).strip()[:200] for x in v if str(x).strip()] if isinstance(v, list) else []


def compare(claim: dict | None, verified: bool | None) -> str | None:
    """agree, false_pass (said passed, check failed), false_fail (said failed or not-run, check passed), or none."""
    if verified is None:
        return None
    if claim is None:
        return "none"
    says_passed = claim["result"] == "passed"
    if says_passed and not verified:
        return "false_pass"
    if not says_passed and verified:
        return "false_fail"
    return "agree"


def normalize_match(label: str | None) -> str | None:
    """Map legacy overclaim/underclaim labels to false_pass/false_fail; leave others unchanged."""
    if label is None:
        return None
    return _LEGACY_MATCH.get(label, label)
