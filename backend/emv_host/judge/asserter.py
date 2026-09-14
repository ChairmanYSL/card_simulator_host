"""Result assertion engine: dotted key paths + structured matchers.

A verdict is PASS when every ``pass``-severity rule matches; WARN when all
``pass`` rules match but at least one ``warn`` rule does not; FAIL otherwise.
The detail list explains every rule so the UI can render actual-vs-expected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..cases.models import MatchRule

_MISSING = object()


@dataclass
class RuleResult:
    rule: MatchRule
    ok: bool
    actual: Any
    message: str


@dataclass
class Verdict:
    status: str  # PASS | WARN | FAIL
    rules: list[RuleResult] = field(default_factory=list)

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.ok]

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rules": [
                {
                    "path": r.rule.path,
                    "op": r.rule.op,
                    "expect": r.rule.expect,
                    "severity": r.rule.severity,
                    "ok": r.ok,
                    "actual": r.actual,
                    "message": r.message,
                }
                for r in self.rules
            ],
        }


def get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted key path like ``tags.gen_ac.9F26``.  Returns
    ``_MISSING`` when absent (distinguishes an actual None value)."""
    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def match_rule(rule: MatchRule, result: dict[str, Any]) -> RuleResult:
    actual = get_path(result, rule.path)
    op = rule.op

    if op == "present":
        ok = actual is not _MISSING
        return RuleResult(rule, ok, None if actual is _MISSING else actual,
                          f"{rule.path} present" if ok else f"{rule.path} missing")
    if op == "absent":
        ok = actual is _MISSING
        return RuleResult(rule, ok, None if actual is _MISSING else actual,
                          f"{rule.path} absent" if ok else f"{rule.path} unexpectedly present")

    if actual is _MISSING:
        return RuleResult(rule, False, None,
                          f"{rule.path} missing (expected {rule.expect!r} via {op})")

    value = _stringify(actual)
    expect = rule.expect

    if op == "eq":
        ok = value == _stringify(expect)
    elif op == "neq":
        ok = value != _stringify(expect)
    elif op == "contains":
        ok = _stringify(expect) in value
    elif op == "prefix":
        ok = value.startswith(_stringify(expect))
    elif op == "regex":
        try:
            ok = re.search(str(expect), value) is not None
        except re.error:
            ok = False
    elif op == "any_of":
        ok = value in {_stringify(item) for item in expect}
    elif op == "range":
        try:
            numeric = float(value)
            lo, hi = float(expect[0]), float(expect[1])
            ok = lo <= numeric <= hi
        except (TypeError, ValueError, IndexError):
            ok = False
    else:  # pragma: no cover - pydantic blocks unknown ops
        ok = False

    message = f"{rule.path} {op} {expect!r}: actual={value!r}"
    return RuleResult(rule, ok, actual, message)


def assert_result(result: dict[str, Any], rules: list[MatchRule]) -> Verdict:
    verdict = Verdict(status="PASS")
    failed_pass = False
    failed_warn = False
    for rule in rules:
        rr = match_rule(rule, result)
        verdict.rules.append(rr)
        if not rr.ok:
            if rule.severity == "warn":
                failed_warn = True
            else:
                failed_pass = True
    if failed_pass:
        verdict.status = "FAIL"
    elif failed_warn:
        verdict.status = "WARN"
    return verdict
