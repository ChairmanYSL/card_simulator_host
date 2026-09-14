"""Assertion engine tests: path lookup + every matcher + severity logic."""

from emv_host.cases.models import MatchRule
from emv_host.judge.asserter import assert_result, get_path

RESULT = {
    "resp_code": "00",
    "outcome": "approved",
    "online": "offline",
    "amount": "100.00",
    "tags": {
        "gen_ac": {"9F26": "AABBCCDD11223344", "9F10": "000000000001"},
        "gpo": {"82": "5800"},
    },
}


def rule(path, op, expect=None, severity="pass"):
    return MatchRule(path=path, op=op, expect=expect, severity=severity)


def test_get_path():
    assert get_path(RESULT, "resp_code") == "00"
    assert get_path(RESULT, "tags.gen_ac.9F26") == "AABBCCDD11223344"
    assert get_path(RESULT, "missing") is not None
    assert get_path(RESULT, "missing") is get_path(RESULT, "nope")  # same sentinel
    from emv_host.judge.asserter import _MISSING
    assert get_path(RESULT, "nope") is _MISSING


def test_eq_neq_contains_prefix():
    assert assert_result(RESULT, [rule("resp_code", "eq", "00")]).status == "PASS"
    assert assert_result(RESULT, [rule("resp_code", "neq", "05")]).status == "PASS"
    assert assert_result(RESULT, [rule("outcome", "contains", "prov")]).status == "PASS"
    assert assert_result(RESULT, [rule("online", "prefix", "off")]).status == "PASS"
    assert assert_result(RESULT, [rule("resp_code", "eq", "05")]).status == "FAIL"


def test_any_of_and_regex_and_range():
    assert assert_result(RESULT, [rule("resp_code", "any_of", ["00", "01"])]).status == "PASS"
    assert assert_result(RESULT, [rule("resp_code", "regex", "^0[01]$")]).status == "PASS"
    assert assert_result(RESULT, [rule("amount", "range", ["0", "1000"])]).status == "PASS"
    assert assert_result(RESULT, [rule("amount", "range", ["1000", "2000"])]).status == "FAIL"


def test_present_absent():
    assert assert_result(RESULT, [rule("tags.gen_ac.9F26", "present")]).status == "PASS"
    assert assert_result(RESULT, [rule("tags.gpo.82", "present")]).status == "PASS"
    assert assert_result(RESULT, [rule("tags.gen_ac.9F27", "absent")]).status == "PASS"
    assert assert_result(RESULT, [rule("tags.gpo.82", "absent")]).status == "FAIL"


def test_severity_logic():
    verdict = assert_result(RESULT, [
        rule("resp_code", "eq", "00"),
        rule("tags.gen_ac.9F10", "prefix", "9999", severity="warn"),
    ])
    assert verdict.status == "WARN"
    verdict = assert_result(RESULT, [
        rule("resp_code", "eq", "00"),
        rule("tags.gen_ac.9F10", "prefix", "9999", severity="warn"),
        rule("outcome", "eq", "declined"),
    ])
    assert verdict.status == "FAIL"


def test_missing_key_is_failure_not_exception():
    verdict = assert_result(RESULT, [rule("no.such.key", "eq", "x")])
    assert verdict.status == "FAIL"
    assert verdict.rules[0].actual is None


def test_verdict_public_shape():
    verdict = assert_result(RESULT, [rule("resp_code", "eq", "00")])
    public = verdict.public()
    assert public["status"] == "PASS"
    assert public["rules"][0]["path"] == "resp_code"
    assert public["rules"][0]["actual"] == "00"
