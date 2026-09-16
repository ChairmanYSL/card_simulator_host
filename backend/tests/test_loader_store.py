"""Library loader + store tests using a tiny generated library (v2 layout:
profiles embedded in cases, brand.json card_template + pos_params)."""

import json

from emv_host.cases import load_library
from emv_host.cases.validation import luhn_ok
from emv_host.store.run_store import RunStore


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


BRAND = {
    "brand": "visa",
    "display_name": "Visa",
    "entry_modes": ["contactless", "contact"],
    "suite_policy": {"on_failure": "stop", "retry_count": 1},
    "card_template": {
        "id": "visa_card_template",
        "name": "Visa default",
        "network": "VISA",
        "aids": [{"aid": "A0000000031010", "label": "VISA", "priority": 0}],
        "pan": "4111111111111111",
        "applicationExpirationDate": "281231",
        "track2EquivalentData": "343131313131313131313131313131313D32383132323031",
        "aip": "5800",
        "afl": "08010100",
        "records": [{"sfi": 1, "record": 1,
                     "fields": ["track2", "pan", "expiryDate", "cvmList"]}],
        "acBehavior": "offline_approved",
    },
    "pos_params": [{"tag": "9F02", "name": "amount", "value": "100.00"}],
}

PROFILE = {
    # canonical v1.1 CardProfile schema (camelCase); id intentionally omitted
    # -> the loader binds it to the case_id
    "name": "Visa offline approved",
    "network": "VISA",
    "isActive": True,
    "aids": [{"aid": "A0000000031010", "label": "VISA", "priority": 0}],
    "pan": "4111111111111111",
    "applicationExpirationDate": "281231",
    "cardholderName": "TEST CARD",
    "track2EquivalentData": "343131313131313131313131313131313D32383132323031",
    "aip": "5800",
    "afl": "08010100",
    "records": [{"sfi": 1, "record": 1,
                 "fields": ["track2", "pan", "expiryDate", "cvmList"]}],
    "acBehavior": "offline_approved",
}

CASE = {
    "case_id": "VIS-CL-001",
    "title": "contactless offline approved",
    "summary": "tap card, expect offline approval",
    "brand": "visa",
    "entry_mode": "contactless",
    "profile": dict(PROFILE),
    "transaction": {
        "operator_instructions": {
            "amount": "100.00", "currency": "CNY", "entry_mode": "contactless",
            "pin_hint": "none", "notes": "tap the phone on the reader",
        },
        "expected_values": {"amount": "100.00", "currency": "156",
                            "entry_mode": "contactless"},
        "pos_params": [{"tag": "9F02", "name": "amount", "value": "100.00"}],
    },
    "preconditions": ["phone NFC enabled"],
    "test_plan": [
        {"step": 1, "action": "push profile", "expect": "android emulating"},
        {"step": 2, "action": "operator taps phone", "expect": "approved"},
    ],
    "timeout_seconds": 120,
    "retry_on_fail": 0,
    "expected_results": [
        {"path": "resp_code", "op": "eq", "expect": "00"},
        {"path": "outcome", "op": "eq", "expect": "approved"},
        {"path": "tags.gen_ac.9F26", "op": "present"},
    ],
}


def make_library(tmp_path):
    root = tmp_path / "cases"
    _write(root / "visa" / "brand.json", BRAND)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", CASE)
    _write(root / "visa" / "contact" / "cases" / "VIS-C-001.json",
           {**CASE, "case_id": "VIS-C-001", "entry_mode": "contact"})
    return root


def test_luhn():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_load_library(tmp_path):
    library = load_library(make_library(tmp_path))
    assert list(library.brands) == ["visa"]
    assert library.all_case_ids("visa", "contactless") == ["VIS-CL-001"]
    assert library.all_case_ids("visa", "contact") == ["VIS-C-001"]
    tree = library.tree()
    assert tree[0]["brand"] == "visa"
    assert tree[0]["case_count"] == 2
    assert not any(i.severity == "error" for i in library.issues)
    # brand defaults are indexed
    brand_def = library.brands["visa"]
    assert brand_def.card_template is not None
    assert brand_def.card_template.id == "visa_card_template"
    assert [p.tag for p in brand_def.pos_params] == ["9F02"]


def test_profile_bound_to_case_id(tmp_path):
    root = make_library(tmp_path)
    library = load_library(root)
    case = library.get_case("visa", "contactless", "VIS-CL-001")
    # profile.id was omitted in the file -> auto-filled with case_id
    assert case.profile.id == "VIS-CL-001"


def test_profile_id_mismatch_warns(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["profile"]["id"] = "someone-else"
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any("profile.id" in i.message and i.severity == "warning"
               for i in library.issues)
    # case still loads (warning only)
    assert library.get_case("visa", "contactless", "VIS-CL-001") is not None


def test_missing_profile_is_error(tmp_path):
    root = make_library(tmp_path)
    bad = dict(CASE)
    del bad["profile"]
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any(i.severity == "error" and "内嵌" in i.message
               for i in library.issues)
    assert library.brand_cases("visa", "contactless") == {}


def test_luhn_violation_caught(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["profile"]["pan"] = "4111111111111112"
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any("Luhn" in i.message for i in library.issues)


def test_invalid_ac_behavior_warns_not_errors(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["profile"]["acBehavior"] = "sideways"
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert not any(i.severity == "error" for i in library.issues)
    assert any("acBehavior" in i.message and i.severity == "warning"
               for i in library.issues)


def test_expiry_yyymmdd_checked(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["profile"]["applicationExpirationDate"] = "2812"
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any("applicationExpirationDate" in i.message for i in library.issues)


def test_embedded_keys_validated(tmp_path):
    root = make_library(tmp_path)
    good = json.loads(json.dumps(CASE))
    good["keys"] = {"symmetric_keys": [{"id": "mk1", "key_data": "QUJDREVGR0hJSktM"}]}
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", good)
    library = load_library(root)
    case = library.get_case("visa", "contactless", "VIS-CL-001")
    assert case is not None and case.keys is not None
    assert case.keys.symmetric_keys[0]["id"] == "mk1"
    assert not any(i.severity == "error" for i in library.issues)

    # bad base64 -> load-time error issue on the case file
    bad = json.loads(json.dumps(good))
    bad["keys"] = {"symmetric_keys": [{"id": "mk1", "key_data": "!!!not-base64!!!"}]}
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any("base64" in i.message for i in library.issues)


def test_pos_params_in_case(tmp_path):
    root = make_library(tmp_path)
    library = load_library(root)
    case = library.get_case("visa", "contactless", "VIS-CL-001")
    assert [p.tag for p in case.transaction.pos_params] == ["9F02"]
    detail = library.public_case("visa", "contactless", "VIS-CL-001")
    assert detail["pos_params"][0]["tag"] == "9F02"
    assert detail["profile"]["id"] == "VIS-CL-001"
    assert detail["profile"]["hasKeys"] is False


def test_duplicate_pos_tag_warns(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["transaction"]["pos_params"] = [
        {"tag": "9F02", "name": "a", "value": "1"},
        {"tag": "9F02", "name": "b", "value": "2"},
    ]
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any("重复" in i.message and i.severity == "warning"
               for i in library.issues)


def test_invalid_pos_tag_rejects_case(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(CASE))
    bad["transaction"]["pos_params"] = [{"tag": "8F02"}]  # bad first byte
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
    library = load_library(root)
    assert any(i.severity == "error" and "invalid case" in i.message
               for i in library.issues)
    assert library.brand_cases("visa", "contactless") == {}


def test_run_store_round_trip(tmp_path):
    store = RunStore(tmp_path / "runs")
    run = store.new_run("visa", "contactless", ["VIS-CL-001"], "full")
    run_id = run["run_id"]
    store.write_checkpoint(run, "VIS-CL-001", "att-1", "upcoming")
    assert store.read_checkpoint(run_id)["next_case_id"] == "VIS-CL-001"
    store.write_case_result(run, "VIS-CL-001", {
        "case_id": "VIS-CL-001", "status": "PASS", "attempts": 1,
        "verdict": "PASS", "finished_at": 123,
    })
    run["status"] = "FINISHED"
    store.update_run(run)
    history = store.history()
    assert history[0]["run_id"] == run_id
    assert history[0]["passed"] == 1
    assert store.get_case_result(run_id, "VIS-CL-001")["status"] == "PASS"


def test_trace_round_trip(tmp_path):
    store = RunStore(tmp_path / "runs")
    run = store.new_run("visa", "contactless", ["VIS-CL-001"], "single")
    run_id = run["run_id"]
    store.write_trace(run, "VIS-CL-001", "att-1",
                      {"at": 1, "message": {"event": "apdu", "seq": 1}})
    store.write_trace(run, "VIS-CL-001", "att-1",
                      {"at": 2, "message": {"event": "apdu", "seq": 2}})
    lines = store.read_trace(run_id, "VIS-CL-001")
    assert len(lines) == 2
    assert lines[0]["attempt_id"] == "att-1"
    assert store.read_trace(run_id, "GHOST") == []


def test_recover_interrupted_run(tmp_path):
    store = RunStore(tmp_path / "runs")
    run = store.new_run("visa", "contactless", ["VIS-CL-001"], "full")
    run["status"] = "RUNNING"
    store.update_run(run)
    store.write_checkpoint(run, "VIS-CL-001", "att-1", "waiting_result")
    recovered = store.recover_interrupted_runs()
    assert run["run_id"] in recovered
    updated = store.get_run(run["run_id"])
    assert updated["status"] == "INTERRUPTED"
    assert updated["results"]["VIS-CL-001"]["status"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# group (总案例/子案例) loading
# ---------------------------------------------------------------------------

GROUP = {
    "group_id": "OPBD-001",
    "title": "PPSE directory selection",
    "purpose": "ensure PPSE directory selection",
    "test_conditions": "card answers SELECT PPSE with FCI and 9000",
    "brand": "visa",
    "entry_mode": "contactless",
    "test_plan": [{"step": 1, "action": "preprocess", "expect": "ok"}],
    "pass_criteria": ["case 01 approves"],
}

SUB_CASE = {
    "case_id": "OPBD-001-01",
    "title": "normal FCI",
    "brand": "visa",
    "entry_mode": "contactless",
    "profile": dict(PROFILE, ppseMode="auto"),
    "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
    "expected_results": [{"path": "resp_code", "op": "eq", "expect": "00"}],
}


def make_group_library(tmp_path):
    root = make_library(tmp_path)
    group_dir = root / "visa" / "contactless" / "cases" / "OPBD-001"
    _write(group_dir / "group.json", GROUP)
    _write(group_dir / "OPBD-001-01.json", SUB_CASE)
    return root


def test_group_loaded_with_children(tmp_path):
    library = load_library(make_group_library(tmp_path))
    group = library.get_group("visa", "contactless", "OPBD-001")
    assert group is not None and group.purpose.startswith("ensure")
    children = library.group_child_ids("visa", "contactless", "OPBD-001")
    assert children == ["OPBD-001-01"]
    child = library.get_case("visa", "contactless", "OPBD-001-01")
    assert child is not None and child.group_id == "OPBD-001"
    assert child.profile.id == "OPBD-001-01"  # bound to the sub-case
    assert "OPBD-001-01" in library.all_case_ids("visa", "contactless")

    tree_mode = [s for s in library.tree() if s["brand"] == "visa"][0]["entry_modes"][0]
    assert tree_mode["standalone"] == [{"case_id": "VIS-CL-001", "title": "contactless offline approved"}]
    assert tree_mode["groups"][0]["group_id"] == "OPBD-001"
    assert tree_mode["groups"][0]["children"][0]["case_id"] == "OPBD-001-01"
    # full-leaf "cases" keeps both kinds (used by select-all / full runs)
    ids = {c["case_id"] for c in tree_mode["cases"]}
    assert ids == {"VIS-CL-001", "OPBD-001-01"}


def test_group_missing_group_json_warns(tmp_path):
    root = make_library(tmp_path)
    group_dir = root / "visa" / "contactless" / "cases" / "NOPE"
    _write(group_dir / "X-01.json", SUB_CASE)  # no group.json
    library = load_library(root)
    assert any("group.json" in i.message and i.severity == "warning"
               for i in library.issues)


def test_group_id_mismatch_warns(tmp_path):
    root = make_library(tmp_path)
    group_dir = root / "visa" / "contactless" / "cases" / "OPBD-001"
    bad = dict(GROUP, group_id="OTHER")
    _write(group_dir / "group.json", bad)
    library = load_library(root)
    assert any("differs" in i.message for i in library.issues)


# ---------------------------------------------------------------------------
# PPSE behavior overrides (v1.1 N11) — embedded profile validation
# ---------------------------------------------------------------------------

def test_ppse_fields_validated(tmp_path):
    root = make_library(tmp_path)

    def load_case(mutator):
        bad = json.loads(json.dumps(CASE))
        mutator(bad["profile"])
        _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", bad)
        return load_library(root)

    lib = load_case(lambda p: p.update(ppseMode="sideways"))
    assert any("ppseMode" in i.message for i in lib.issues)

    lib = load_case(lambda p: p.update(ppseSw="900"))
    assert any("ppseSw" in i.message for i in lib.issues)

    lib = load_case(lambda p: p.update(ppseData="XYZ"))
    assert any("ppseData" in i.message for i in lib.issues)

    # TLV with a declared inner length bigger than actual data (5006 + 4 bytes)
    lib = load_case(lambda p: p.update(
        ppseMode="custom",
        ppseData="6F1F840E325041592E5359532E4444463031A50DBF0C0B6109500656495341870101"))
    assert any("TLV" in i.message for i in lib.issues)


def test_ppse_valid_template_passes(tmp_path):
    from emv_host.cases.validation import ppse_tlv_ok
    template = ("6F20840E325041592E5359532E4444463031A50EBF0C0B6109"
                "500456495341870101")
    assert ppse_tlv_ok(template) == (True, "")


# ---------------------------------------------------------------------------
# v1.1 N12: noResponseCommands (silence behavior)
# ---------------------------------------------------------------------------

def test_no_response_commands_known_pass(tmp_path):
    root = make_library(tmp_path)
    case = json.loads(json.dumps(CASE))
    case["profile"]["noResponseCommands"] = [
        "GPO", {"command": "READ_RECORD", "times": 1}]
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", case)
    library = load_library(root)
    assert not any(i.severity == "error" for i in library.issues)
    assert not any("noResponseCommands" in i.message for i in library.issues)
    tc = library.get_case("visa", "contactless", "VIS-CL-001")
    assert tc.profile.noResponseCommands[0] == "GPO"
    assert tc.profile.noResponseCommands[1].command == "READ_RECORD"
    assert tc.profile.noResponseCommands[1].times == 1


def test_no_response_commands_unknown_warns(tmp_path):
    root = make_library(tmp_path)
    case = json.loads(json.dumps(CASE))
    case["profile"]["noResponseCommands"] = ["GPO", "FOOBAR"]
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", case)
    library = load_library(root)
    warns = [i for i in library.issues if "FOOBAR" in i.message]
    assert any(i.severity == "warning" for i in warns)
    # case still loads (warning only)
    assert library.get_case("visa", "contactless", "VIS-CL-001") is not None


def test_no_response_commands_duplicate_warns(tmp_path):
    from emv_host.cases import CardProfile
    from emv_host.cases.validation import validate_profile
    profile = CardProfile.model_validate({
        "id": "X", "pan": "4111111111111111",
        "noResponseCommands": ["GPO", {"command": "GPO"}]})
    report = validate_profile(profile)
    assert any("重复" in i.message and i.severity == "warning"
               for i in report.issues)


# ---------------------------------------------------------------------------
# v1.2 D1: loader must never crash, and duplicate ids are errors
# ---------------------------------------------------------------------------

def _errors(library):
    return [i for i in library.issues if i.severity == "error"]


def test_malformed_json_does_not_abort_the_scan(tmp_path):
    """A truncated/invalid case file used to raise out of load_library()."""
    root = make_library(tmp_path)
    cases = root / "visa" / "contactless" / "cases"
    (cases / "BROKEN-001.json").write_text("{ not json", encoding="utf-8")
    _write(cases / "VIS-CL-001.json", CASE)

    library = load_library(root)
    assert library.get_case("visa", "contactless", "VIS-CL-001") is not None
    broken = [i for i in _errors(library) if "BROKEN-001" in i.path]
    assert broken, "the bad file must be reported, not swallowed"


def test_undecodable_utf8_case_is_reported_not_raised(tmp_path):
    root = make_library(tmp_path)
    cases = root / "visa" / "contactless" / "cases"
    (cases / "BADENC-001.json").write_bytes(b"\xff\xfe\x00bad")
    library = load_library(root)
    assert any("BADENC-001" in i.path for i in _errors(library))


def test_duplicate_case_id_rejected_and_first_kept(tmp_path):
    root = make_library(tmp_path)
    cases = root / "visa" / "contactless" / "cases"
    _write(cases / "VIS-CL-001.json", CASE)
    group = cases / "OPBD-001"
    group.mkdir()
    _write(group / "group.json", {
        "group_id": "OPBD-001", "title": "g", "brand": "visa",
        "entry_mode": "contactless",
        "test_plan": [{"step": 1, "action": "a", "expect": "e"}]})
    duplicate = json.loads(json.dumps(CASE))
    duplicate["title"] = "duplicate inside the group"
    _write(group / "VIS-CL-001.json", duplicate)

    library = load_library(root)
    assert any("VIS-CL-001" in i.message and "重复" in i.message
               for i in _errors(library))
    kept = library.get_case("visa", "contactless", "VIS-CL-001")
    assert kept is not None and kept.title == CASE["title"]


def test_case_id_colliding_with_group_id_is_an_error(tmp_path):
    root = make_library(tmp_path)
    cases = root / "visa" / "contactless" / "cases"
    _write(cases / "VIS-CL-001.json", CASE)
    # a case whose id equals a 总案例 group_id
    collide = json.loads(json.dumps(CASE))
    collide["case_id"] = "OPBD-001"
    _write(cases / "OPBD-001.json", collide)
    group = cases / "OPBD-001"
    group.mkdir()
    _write(group / "group.json", {
        "group_id": "OPBD-001", "title": "g", "brand": "visa",
        "entry_mode": "contactless",
        "test_plan": [{"step": 1, "action": "a", "expect": "e"}]})

    library = load_library(root)
    assert any("互斥" in i.message for i in _errors(library)), \
        [i.message for i in _errors(library)]
    assert library.get_group("visa", "contactless", "OPBD-001") is not None
    assert library.get_case("visa", "contactless", "OPBD-001") is None


def test_duplicate_group_id_rejected(tmp_path):
    root = make_library(tmp_path)
    cases = root / "visa" / "contactless" / "cases"
    _write(cases / "VIS-CL-001.json", CASE)
    for name in ("OPBD-001", "OPBD-001-copy"):
        d = cases / name
        d.mkdir()
        _write(d / "group.json", {
            "group_id": "OPBD-001", "title": f"g {name}", "brand": "visa",
            "entry_mode": "contactless",
            "test_plan": [{"step": 1, "action": "a", "expect": "e"}]})

    library = load_library(root)
    assert any("group_id 'OPBD-001' 重复" in i.message for i in _errors(library))
    assert library.get_group("visa", "contactless", "OPBD-001").title == \
        "g OPBD-001"


def test_duplicate_brand_directory_rejected(tmp_path):
    root = make_library(tmp_path)
    other = root / "visa-again"
    other.mkdir()
    _write(other / "brand.json", {**BRAND, "display_name": "Visa clone"})

    library = load_library(root)
    assert any("重复定义" in i.message for i in _errors(library))
    assert library.brands["visa"].display_name == BRAND["display_name"]


def test_invalid_brand_json_reported_not_raised(tmp_path):
    """A broken brand.json drops that brand (with an error) but must not stop
    the scan of the remaining brands."""
    root = make_library(tmp_path)
    other = root / "mastercard"
    other.mkdir()
    _write(other / "brand.json", {
        "brand": "mastercard", "display_name": "Mastercard",
        "entry_modes": ["contactless"]})
    (root / "visa" / "brand.json").write_text("{oops", encoding="utf-8")

    library = load_library(root)
    assert "visa" not in library.brands
    assert "mastercard" in library.brands
    assert any("brand.json" in i.path and i.severity == "error"
               for i in library.issues)


# ---------------------------------------------------------------------------
# v1.2 T-D0: local (gitignored) kernel_params.json
# ---------------------------------------------------------------------------

_TERM = "9F6604360000809F1A0201569F7A0101"
_MODULUS = "AB" * 128
_RID, _INDEX, _EXPIRY = "A000000003", "01", "20301231"
_CHECKSUM = __import__("hashlib").sha1(
    bytes.fromhex(_RID + _INDEX + _MODULUS + "03")).hexdigest().upper()

KERNEL_PARAMS = {
    "aids": [{"aid": "A0000000031010",
              "fields": [{"tag": "DF01", "value": "01"}]}],
    "caps": [{"rid": _RID, "key_index": _INDEX, "expiry": _EXPIRY,
              "modulus": _MODULUS, "exponent": "03",
              "checksum": _CHECKSUM}],
    "term": _TERM,
}


def test_local_kernel_params_are_loaded_only_when_opted_in(tmp_path):
    """C3: the legacy in-repo kernel_params.json is read only on explicit opt-in.

    Default-off is deliberate — the file carries CAPK moduli (key material), so
    leaving the workspace must be the only way it is normally consumed.
    """
    root = make_library(tmp_path)
    _write(root / "visa" / "kernel_params.json", KERNEL_PARAMS)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", CASE)

    # default: not read at all
    default_library = load_library(root)
    assert default_library.brands["visa"].kernel_params is None
    assert not _errors(default_library)

    # explicit opt-in: read and validated
    library = load_library(root, use_legacy_kernel_params=True)
    params = library.brands["visa"].kernel_params
    assert params is not None
    assert len(params.aids) == 1 and len(params.caps) == 1
    assert params.term == _TERM
    assert not _errors(library)


def test_absent_kernel_params_leave_brand_default_none(tmp_path):
    root = make_library(tmp_path)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", CASE)
    library = load_library(root)
    assert library.brands["visa"].kernel_params is None
    assert not _errors(library)


def test_broken_kernel_params_reported_and_brand_still_loads(tmp_path):
    root = make_library(tmp_path)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", CASE)
    (root / "visa" / "kernel_params.json").write_text("{broken",
                                                      encoding="utf-8")
    library = load_library(root, use_legacy_kernel_params=True)
    assert library.brands["visa"].kernel_params is None
    assert any("kernel_params.json" in i.path and i.severity == "error"
               for i in library.issues)
    assert library.get_case("visa", "contactless", "VIS-CL-001") is not None


def test_invalid_capk_checksum_in_local_file_is_an_error(tmp_path):
    root = make_library(tmp_path)
    bad = json.loads(json.dumps(KERNEL_PARAMS))
    bad["caps"][0]["checksum"] = "00" * 20
    _write(root / "visa" / "kernel_params.json", bad)
    library = load_library(root, use_legacy_kernel_params=True)
    assert any("DF03" in i.message and i.severity == "error"
               for i in library.issues)


def test_case_level_kernel_params_are_validated(tmp_path):
    root = make_library(tmp_path)
    case = json.loads(json.dumps(CASE))
    case["kernel_params"] = {"aids": [{"aid": "A0000000031010"}],
                             "term": _TERM}
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", case)
    library = load_library(root)
    loaded = library.get_case("visa", "contactless", "VIS-CL-001")
    assert loaded.kernel_params is not None
    assert not _errors(library)


# ---------------------------------------------------------------------------
# v1.2 T11: atomic writes
# ---------------------------------------------------------------------------

def test_atomic_write_leaves_no_tmp_and_reads_back(tmp_path):
    from emv_host.store.run_store import _atomic_write_json

    target = tmp_path / "day" / "run.json"
    _atomic_write_json(target, {"a": 1})
    _atomic_write_json(target, {"a": 2, "note": "中文"})
    assert json.loads(target.read_text(encoding="utf-8")) == \
        {"a": 2, "note": "中文"}
    assert not list(tmp_path.rglob("*.tmp"))


def test_atomic_write_cleans_tmp_on_failure(tmp_path):
    from emv_host.store.run_store import _atomic_write_json

    target = tmp_path / "bad.json"
    try:
        _atomic_write_json(target, {"k": object()})  # not serializable
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected TypeError")
    assert not target.exists()
    assert not (tmp_path / "bad.json.tmp").exists()


def test_case_failure_reason_reaches_run_index(tmp_path):
    store = RunStore(tmp_path)
    run = store.new_run("visa", "contactless", ["VIS-CL-001"], "single")
    store.write_case_result(run, "VIS-CL-001", {
        "case_id": "VIS-CL-001", "status": "FAIL", "attempts": 1,
        "failure_reason": "result_timeout"})
    assert store.get_run(run["run_id"])["results"]["VIS-CL-001"][
        "failure_reason"] == "result_timeout"
    assert not list(tmp_path.rglob("*.tmp"))
