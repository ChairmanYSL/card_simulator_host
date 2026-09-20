"""v2 CTEL2 ``transaction.act`` validation (``validate_act``) and the add-case
API round trip for ``act``.

Semantics encoded here come from the frozen v2 documents:

* ``act.signalData=[{id,value}]`` replaces v1.2 ``transaction.pos_params``;
  ``value`` is always a hex string, ``null`` = do not send the tag, ``""`` =
  length 00; the tag table ends with a free "私有 tag" slot —
  docs/protocol.md:514-542 (§8.1), docs/protocol-v2-design.md:94-113 (§5);
* the host reads only ``act`` when building the wire frame —
  ``runner/executor.py:798-799``;
* ``ActParam`` normalises id/value on parse (strip + upper + space removal) —
  ``cases/models.py:442-465``;
* a case that has migrated therefore carries no legacy ``pos_params`` at all,
  but ``validate_pos_params`` stays in place for legacy data —
  docs/protocol.md:541-542, ``cases/models.py:471-476``.

Nothing here asserts on ``transaction.pos_params`` content of a migrated case
beyond "it raises no issue", so the assertions cannot mask a regression by
weakening an old one.
"""

import json

import pytest
from fastapi.testclient import TestClient

from emv_host.cases import load_library
from emv_host.cases.models import ActParam
from emv_host.cases.validation import validate_act
from emv_host.store.run_store import RunStore
from emv_host.web import AppContext, EventBus, create_app

OWNER = "VIS-CL-901"


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# validate_act unit checks
# ---------------------------------------------------------------------------

def test_good_act_passes():
    """Documented ACT tags, the host's DF8101 convention, a private tag and the
    two legal empty shapes must all pass without a single issue."""
    params = [
        ActParam(id="9f02", value="000000001500"),   # normalised to 9F02/upper
        ActParam(id="5F2A", value="0978"),
        ActParam(id="5F36", value="02"),
        ActParam(id="9C", value="00"),
        ActParam(id="DF8101", value="01"),           # docs/protocol.md:539
        ActParam(id="DF8112", value="9F02069F0306"),  # 私有 tag, allowed (532)
        ActParam(id="9F53", value=None),             # null = tag not sent
        ActParam(id="9F7C", value=""),               # "" = length 00
    ]
    report = validate_act(params, OWNER)
    assert report.issues == []
    # the fixture is not vacuous: normalisation really happened
    assert params[0].id == "9F02"
    assert params[0].value == "000000001500"


def test_empty_act_passes():
    assert validate_act([], OWNER).issues == []


def test_duplicate_id_warns_only():
    report = validate_act(
        [ActParam(id="9F02", value="000000001500"),
         ActParam(id="9f02", value="000000000001")],  # same id after upper()
        OWNER)
    assert report.errors == []
    duplicates = [i for i in report.warnings if "重复" in i.message]
    assert len(duplicates) == 1
    assert "9F02" in duplicates[0].message
    assert duplicates[0].profile_id == OWNER


def test_odd_length_value_errors():
    report = validate_act([ActParam(id="9A", value="24041")], OWNER)
    assert len(report.issues) == 1
    assert report.errors[0].severity == "error"
    assert "偶数长度" in report.errors[0].message


def test_non_hex_value_errors():
    report = validate_act([ActParam(id="9F02", value="123g")], OWNER)
    assert len(report.issues) == 1
    assert report.errors[0].severity == "error"
    assert "不是 hex" in report.errors[0].message


def test_lowercase_value_that_normalises_is_clean_but_raw_lowercase_warns():
    """The model uppercases on parse (models.py:462-465), so a lowercase author
    never reaches the wire — and must not be flagged.  A value that is still
    lowercase *after* normalisation can only come from an unvalidated
    construction; that one is flagged (warning, never error)."""
    parsed = ActParam(id="9f02", value="0000000015a0")
    assert parsed.value == "0000000015A0"          # normalised
    assert validate_act([parsed], OWNER).issues == []

    unvalidated = ActParam.model_construct(id="9F02", value="0000000015a0")
    report = validate_act([unvalidated], OWNER)
    assert report.errors == []
    assert len(report.warnings) == 1
    assert "大写" in report.warnings[0].message


def test_implausible_id_warns_but_never_errors():
    """A non-tag id is advisory only: ACT allows private tags, so the host must
    not block an unfamiliar one (docs/protocol.md:532)."""
    report = validate_act([ActParam(id="Amount", value="01")], OWNER)
    assert report.errors == []
    assert len(report.warnings) == 1
    assert "NOTSUPPORTED" in report.warnings[0].message


def test_bad_multibyte_tag_shape_warns():
    """8F02 is hex but not a 2-byte EMV tag (first byte low 5 bits != 0x1F) —
    the same shape rule the PosParam model enforces (models.py:56-59)."""
    report = validate_act([ActParam(id="8F02", value="01")], OWNER)
    assert report.errors == []
    assert len(report.warnings) == 1
    assert "8F02" in report.warnings[0].message


def test_documented_ctel2_signal_id_is_not_flagged():
    """The id check is deliberately conservative: an id the protocol catalogue
    defines (test_info/out signalData ids, design:32-44) is never called
    implausible."""
    report = validate_act([ActParam(id="ResponseCode", value="01")], OWNER)
    assert report.issues == []


# ---------------------------------------------------------------------------
# loader wiring
# ---------------------------------------------------------------------------

BRAND = {
    "brand": "visa",
    "display_name": "Visa",
    "entry_modes": ["contactless"],
    "suite_policy": {"on_failure": "stop", "retry_count": 0},
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
        "records": [{"sfi": 1, "record": 1, "fields": ["track2", "pan", "expiryDate"]}],
        "acBehavior": "offline_approved",
    },
    # legacy brand template: no `act`, must not be copied into a new case
    "pos_params": [{"tag": "9F02", "name": "amount", "value": "100.00"}],
}

PROFILE = {
    "name": "Visa offline",
    "network": "VISA",
    "aids": [{"aid": "A0000000031010", "label": "VISA", "priority": 0}],
    "pan": "4111111111111111",
    "applicationExpirationDate": "281231",
    "track2EquivalentData": "343131313131313131313131313131313D32383132323031",
    "aip": "5800",
    "afl": "08010100",
    "records": [{"sfi": 1, "record": 1, "fields": ["track2", "pan", "expiryDate"]}],
    "acBehavior": "offline_approved",
}


def _case(case_id: str, transaction: dict) -> dict:
    return {
        "case_id": case_id,
        "title": case_id,
        "brand": "visa",
        "entry_mode": "contactless",
        "profile": dict(PROFILE),
        "transaction": transaction,
        "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
        "expected_results": [{"path": "resp_code", "op": "eq", "expect": "00"}],
    }


def test_loader_checks_act_and_keeps_legacy_pos_params_checked(tmp_path):
    root = tmp_path / "cases"
    _write(root / "visa" / "brand.json", BRAND)
    cases_dir = root / "visa" / "contactless" / "cases"
    # migrated case: act only -> no pos_params noise at all
    _write(cases_dir / "VIS-CL-900.json",
           _case("VIS-CL-900", {"act": [{"id": "9F02", "value": "000000001500"}]}))
    # bad act: odd-length value + duplicate id
    _write(cases_dir / "VIS-CL-901.json",
           _case("VIS-CL-901", {"act": [{"id": "9F02", "value": "000000001500"},
                                        {"id": "9f02", "value": "123"}]}))
    # legacy case: act plus repeated legacy pos_params tag
    _write(cases_dir / "VIS-CL-902.json",
           _case("VIS-CL-902", {
               "act": [{"id": "9F02", "value": "01"}],
               "pos_params": [{"tag": "9F02"}, {"tag": "9F02"}]}))

    library = load_library(root)
    issues = [(i.severity, i.message) for i in library.issues if "VIS-CL-" in i.message]

    # migrated case: neither act nor pos_params issues
    assert [i for i in issues if "VIS-CL-900" in i[1]] == []
    # act problems are reported against the case
    assert any(s == "error" and "VIS-CL-901" in m and "偶数长度" in m
               for s, m in issues)
    assert any(s == "warning" and "VIS-CL-901" in m and "重复" in m
               for s, m in issues)
    # legacy pos_params is still checked (warning) alongside act
    assert any(s == "warning" and "VIS-CL-902" in m and "pos_params" in m
               for s, m in issues)


# ---------------------------------------------------------------------------
# API create / validate path
# ---------------------------------------------------------------------------

NEW_CASE = {
    "case_id": "VIS-CL-950",
    "title": "act round trip",
    "brand": "visa",
    "entry_mode": "contactless",
    "profile": dict(PROFILE),
    "transaction": {
        "operator_instructions": {"amount": "100.00", "currency": "CNY",
                                  "entry_mode": "contactless"},
        # authored lowercase on purpose: the API must normalise to uppercase
        "act": [{"id": "9f02", "value": "000000001500"},
                {"id": "5F2A", "value": "0978"},
                {"id": "9F7C", "value": ""}],
    },
    "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
    "expected_results": [{"path": "resp_code", "op": "eq", "expect": "00"}],
}


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "cases"
    _write(root / "visa" / "brand.json", BRAND)
    library = load_library(root)
    context = AppContext(library=library, executor=None,
                         store=RunStore(tmp_path / "runs"),
                         bus=EventBus(), static_dir="")
    return TestClient(create_app(context)), root


def test_api_rejects_bad_act_hex(client):
    api, _ = client
    payload = json.loads(json.dumps(NEW_CASE))
    payload["transaction"]["act"].append({"id": "9F03", "value": "123"})
    response = api.post("/api/cases/validate", json=payload)
    body = response.json()
    assert body["ok"] is False
    assert any(m.startswith("act: ") and "偶数长度" in m for m in body["errors"])


def test_api_act_round_trip_and_persisted_file(client):
    api, root = client
    response = api.post("/api/cases", json=NEW_CASE)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    expected = [{"id": "9F02", "value": "000000001500"},
                {"id": "5F2A", "value": "0978"},
                {"id": "9F7C", "value": ""}]

    saved = json.loads((root / "visa" / "contactless" / "cases"
                        / "VIS-CL-950.json").read_text(encoding="utf-8"))
    assert saved["transaction"]["act"] == expected
    # migrated case: the frontend sent no pos_params, so the persisted list is empty
    assert saved["transaction"]["pos_params"] == []

    detail = api.get("/api/cases/visa/contactless/VIS-CL-950")
    assert detail.status_code == 200
    assert detail.json()["act"] == expected
