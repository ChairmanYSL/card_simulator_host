"""Web API tests for the add-case feature (validation, persistence, hot reload).

v2: profiles are embedded in each case JSON (bound to the case); brand.json
provides card_template + pos_params for the add-case page.
"""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from emv_host.cases import load_library
from emv_host.store.run_store import RunStore
from emv_host.web import AppContext, EventBus, create_app

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
    "pos_params": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
    ],
}

EMBEDDED_PROFILE = {
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

EXISTING_CASE = {
    "case_id": "VIS-CL-001",
    "title": "existing",
    "brand": "visa",
    "entry_mode": "contactless",
    "profile": dict(EMBEDDED_PROFILE),
    "transaction": {
        "operator_instructions": {"amount": "100.00", "currency": "CNY",
                                  "entry_mode": "contactless"},
        "expected_values": {"amount": "100.00", "currency": "156",
                            "entry_mode": "contactless"},
        "pos_params": [{"tag": "9F02", "name": "amount", "value": "100.00"}],
    },
    "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
    "expected_results": [{"path": "resp_code", "op": "eq", "expect": "00"}],
}

NEW_CASE = {
    "case_id": "VIS-CL-900",
    "title": "web created case",
    "summary": "created through the add-case page",
    "brand": "visa",
    "entry_mode": "contactless",
    "profile": dict(EMBEDDED_PROFILE),
    "transaction": {
        "operator_instructions": {"amount": "50.00", "currency": "CNY",
                                  "entry_mode": "contactless",
                                  "pin_hint": "none", "notes": "tap it"},
        "expected_values": {"amount": "50.00", "currency": "156",
                            "entry_mode": "contactless"},
        "pos_params": [{"tag": "9F02", "name": "amount", "value": "50.00"},
                       {"tag": "5F2A", "name": "currency", "value": "156"}],
    },
    "preconditions": ["nfc on"],
    "test_plan": [
        {"step": 1, "action": "push config", "expect": "emulating"},
        {"step": 2, "action": "tap", "expect": "approved"},
    ],
    "timeout_seconds": 120,
    "retry_on_fail": 0,
    "expected_results": [
        {"path": "resp_code", "op": "eq", "expect": "00"},
        {"path": "tags.gen_ac.9F26", "op": "present"},
        {"path": "amount", "op": "eq", "expect": "50.00"},
    ],
}


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "cases"
    _write(root / "visa" / "brand.json", BRAND)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", EXISTING_CASE)
    library = load_library(root)
    store = RunStore(tmp_path / "runs")
    bus = EventBus()
    context = AppContext(library=library, executor=None, store=store,
                         bus=bus, static_dir="")
    return TestClient(create_app(context)), root


def test_brand_template(client):
    api, _ = client
    response = api.get("/api/brand/visa/template")
    assert response.status_code == 200
    body = response.json()
    assert body["card_template"]["id"] == "visa_card_template"
    assert [p["tag"] for p in body["pos_params"]] == ["9F02", "5F2A"]


def test_brand_template_unknown_brand(client):
    api, _ = client
    assert api.get("/api/brand/amex/template").status_code == 404


def test_validate_ok(client):
    api, _ = client
    response = api.post("/api/cases/validate", json=NEW_CASE)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": []}


def test_create_case_and_hot_reload(client):
    api, root = client
    response = api.post("/api/cases", json=NEW_CASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["case_id"] == "VIS-CL-900"
    saved = root / "visa" / "contactless" / "cases" / "VIS-CL-900.json"
    assert saved.is_file()

    # hot reload: the new case is immediately visible in the tree and API
    tree = api.get("/api/tree").json()["brands"]
    visa = next(s for s in tree if s["brand"] == "visa")
    assert visa["case_count"] == 2
    assert any(c["case_id"] == "VIS-CL-900" for c in visa["entry_modes"][0]["cases"])
    detail = api.get("/api/cases/visa/contactless/VIS-CL-900")
    assert detail.status_code == 200
    body = detail.json()
    assert body["title"] == "web created case"
    # embedded profile bound to the case: id auto-filled with case_id
    assert body["profile"]["id"] == "VIS-CL-900"
    assert body["profile"]["acBehavior"] == "offline_approved"
    assert [p["tag"] for p in body["pos_params"]] == ["9F02", "5F2A"]


def test_duplicate_case_rejected(client):
    api, _ = client
    response = api.post("/api/cases", json=EXISTING_CASE)
    assert response.status_code == 400  # validate-first design, clear message
    assert any("已存在" in m for m in response.json()["detail"])


def test_unknown_brand_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE, brand="amex")
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400
    assert any("卡组" in m for m in response.json()["detail"])


def test_missing_profile_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE)
    del payload["profile"]
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400
    assert any("内嵌" in m for m in response.json()["detail"])


def test_bad_embedded_profile_rejected(client):
    api, _ = client
    payload = json.loads(json.dumps(NEW_CASE))
    payload["profile"]["pan"] = "4111111111111112"  # fails Luhn
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("Luhn" in m for m in response.json()["errors"])


def test_bad_pos_tag_rejected(client):
    api, _ = client
    payload = json.loads(json.dumps(NEW_CASE))
    payload["transaction"]["pos_params"].append({"tag": "8F02", "name": "x"})
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("0x1F" in m or "0x1f" in m.lower() or "tag" in m
               for m in response.json()["errors"])


def test_contact_mode_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE, entry_mode="contact")
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400
    assert any("contactless" in m for m in response.json()["detail"])


def test_unsafe_case_id_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE, case_id="../evil")
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400  # pydantic pattern violation


def test_invalid_op_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE)
    payload["expected_results"] = [{"path": "a", "op": "explode", "expect": "x"}]
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400


def test_empty_expected_results_rejected(client):
    api, _ = client
    payload = dict(NEW_CASE, expected_results=[])
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 400
    assert any("expected_results" in m for m in response.json()["detail"])


def test_non_object_payload_rejected(client):
    api, _ = client
    response = api.post("/api/cases", json=[1, 2, 3])
    assert response.status_code == 400
    assert any("JSON 对象" in m for m in response.json()["detail"])


def test_validate_reports_errors(client):
    api, _ = client
    payload = dict(NEW_CASE, brand="nope", entry_mode="contact",
                   expected_results=[])
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert len(response.json()["errors"]) >= 3


def test_tree_exposes_groups_and_run_rejects_group_id(tmp_path):
    """总案例 group shows up in the tree; running a group id is rejected with
    a hint (groups are not executable, only their 子案例 are)."""
    root = tmp_path / "gcases"
    _write(root / "visa" / "brand.json", BRAND)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json", EXISTING_CASE)
    group_dir = root / "visa" / "contactless" / "cases" / "OPBD-001"
    _write(group_dir / "group.json", {
        "group_id": "OPBD-001", "title": "PPSE group",
        "purpose": "p", "brand": "visa", "entry_mode": "contactless",
        "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
        "pass_criteria": ["c1"]})
    _write(group_dir / "OPBD-001-01.json", {
        "case_id": "OPBD-001-01", "title": "sub", "brand": "visa",
        "entry_mode": "contactless",
        "profile": dict(EMBEDDED_PROFILE, ppseMode="custom"),
        "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
        "expected_results": [{"path": "resp_code", "op": "eq", "expect": "00"}]})

    library = load_library(root)
    store = RunStore(tmp_path / "runs")
    context = AppContext(library=library, executor=None, store=store,
                         bus=EventBus(), static_dir="")
    api = TestClient(create_app(context))

    tree = api.get("/api/tree").json()["brands"]
    mode = next(s for s in tree if s["brand"] == "visa")["entry_modes"][0]
    assert mode["groups"][0]["group_id"] == "OPBD-001"
    assert {c["case_id"] for c in mode["standalone"]} == {"VIS-CL-001"}

    # a group id is not a runnable target
    response = api.post("/api/run", json={
        "brand": "visa", "mode": "contactless", "kind": "selected",
        "case_ids": ["OPBD-001"]})
    assert response.status_code == 400
    assert "子案例" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 总案例 (group) creation via the web UI
# ---------------------------------------------------------------------------

NEW_GROUP = {
    "group_id": "OPBD-900",
    "title": "PPSE group created by UI",
    "purpose": "test purpose",
    "test_conditions": "some conditions",
    "brand": "visa",
    "entry_mode": "contactless",
    "test_plan": [{"step": 1, "action": "tap", "expect": "ok"}],
    "pass_criteria": ["criterion 1"],
}


def test_validate_group_ok(client):
    api, _ = client
    response = api.post("/api/groups/validate", json=NEW_GROUP)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": []}


def test_validate_group_errors(client):
    api, _ = client
    bad = dict(NEW_GROUP, test_plan=[], entry_mode="contact")
    response = api.post("/api/groups/validate", json=bad)
    body = response.json()
    assert body["ok"] is False
    assert any("test_plan" in m for m in body["errors"])
    assert any("contactless" in m for m in body["errors"])


def test_create_group_and_hot_reload(client):
    api, root = client
    response = api.post("/api/groups", json=NEW_GROUP)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    path = root / "visa" / "contactless" / "cases" / "OPBD-900" / "group.json"
    assert path.is_file()

    tree = api.get("/api/tree").json()["brands"]
    mode = next(s for s in tree if s["brand"] == "visa")["entry_modes"][0]
    assert any(g["group_id"] == "OPBD-900" for g in mode["groups"])


def test_create_duplicate_group_rejected(client):
    api, _ = client
    api.post("/api/groups", json=NEW_GROUP)
    response = api.post("/api/groups", json=NEW_GROUP)
    assert response.status_code == 400
    assert any("已存在" in m for m in response.json()["detail"])


def test_group_id_collides_with_case_rejected(client):
    api, _ = client
    payload = dict(NEW_GROUP, group_id="VIS-CL-001")  # existing case id
    response = api.post("/api/groups/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("冲突" in m for m in response.json()["errors"])


def test_create_sub_case_into_group_dir(tmp_path):
    """A case created with group_id lands inside the group directory and is
    bound by the loader (tree children + group_id on the case)."""
    root = tmp_path / "gcases"
    _write(root / "visa" / "brand.json", BRAND)
    group_dir = root / "visa" / "contactless" / "cases" / "OPBD-900"
    _write(group_dir / "group.json", {
        "group_id": "OPBD-900", "title": "g", "brand": "visa",
        "entry_mode": "contactless",
        "test_plan": [{"step": 1, "action": "a", "expect": "b"}]})
    library = load_library(root)
    store = RunStore(tmp_path / "runs")
    context = AppContext(library=library, executor=None, store=store,
                         bus=EventBus(), static_dir="")
    api = TestClient(create_app(context))

    sub = dict(NEW_CASE, case_id="OPBD-900-01", group_id="OPBD-900")
    response = api.post("/api/cases", json=sub)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["group_id"] == "OPBD-900"
    saved = group_dir / "OPBD-900-01.json"
    assert saved.is_file()
    # the persisted file must NOT carry group_id (binding from the directory)
    assert "group_id" not in json.loads(saved.read_text(encoding="utf-8"))

    tree = api.get("/api/tree").json()["brands"]
    mode = next(s for s in tree if s["brand"] == "visa")["entry_modes"][0]
    group = next(g for g in mode["groups"] if g["group_id"] == "OPBD-900")
    assert "OPBD-900-01" in {c["case_id"] for c in group["children"]}
    assert {c["case_id"] for c in mode["standalone"]} == set()

    detail = api.get("/api/cases/visa/contactless/OPBD-900-01")
    assert detail.status_code == 200
    assert detail.json()["profile"]["id"] == "OPBD-900-01"


def test_sub_case_unknown_group_rejected(client):
    api, _ = client
    sub = dict(NEW_CASE, case_id="OPBD-999-01", group_id="OPBD-999")
    response = api.post("/api/cases", json=sub)
    assert response.status_code == 400
    assert any("总案例" in m and "不存在" in m for m in response.json()["detail"])


def test_case_id_collides_with_group_rejected(client):
    api, _ = client
    api.post("/api/groups", json=NEW_GROUP)
    payload = dict(NEW_CASE, case_id="OPBD-900")
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("总案例" in m for m in response.json()["errors"])


# ---------------------------------------------------------------------------
# v1.2: kernel_params validation, write-path guards, WS since parsing
# ---------------------------------------------------------------------------

_RID, _INDEX, _EXPIRY = "A000000003", "01", "20301231"
_MODULUS = "AB" * 128
_CHECKSUM = hashlib.sha1(
    bytes.fromhex(_RID + _INDEX + _MODULUS + "03")).hexdigest().upper()
_TERM = "9F6604360000809F1A0201569F7A0101"

VALID_KERNEL_PARAMS = {
    "aids": [{"aid": "A0000000031010",
              "fields": [{"tag": "DF01", "value": "01"}]}],
    "caps": [{"rid": _RID, "key_index": _INDEX, "expiry": _EXPIRY,
              "modulus": _MODULUS, "exponent": "03", "checksum": _CHECKSUM}],
    "term": _TERM,
}


def test_kernel_params_accepted_and_persisted(client):
    api, root = client
    payload = dict(NEW_CASE, kernel_params=VALID_KERNEL_PARAMS)
    response = api.post("/api/cases", json=payload)
    assert response.status_code == 200, response.text
    saved = json.loads(
        (root / "visa" / "contactless" / "cases"
         / f"{NEW_CASE['case_id']}.json").read_text(encoding="utf-8"))
    assert saved["kernel_params"]["term"] == _TERM
    assert saved["kernel_params"]["aids"][0]["aid"] == "A0000000031010"


def test_bad_kernel_params_checksum_rejected(client):
    api, _ = client
    payload = json.loads(json.dumps(NEW_CASE))
    payload["kernel_params"] = json.loads(json.dumps(VALID_KERNEL_PARAMS))
    payload["kernel_params"]["caps"][0]["checksum"] = "00" * 20
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("DF03" in m for m in response.json()["errors"])


def test_bad_kernel_params_expiry_rejected(client):
    api, _ = client
    payload = json.loads(json.dumps(NEW_CASE))
    payload["kernel_params"] = json.loads(json.dumps(VALID_KERNEL_PARAMS))
    # the 银联 A000000333/08 anomaly must never be accepted
    payload["kernel_params"]["caps"][0]["expiry"] = "PHWWIPQI"
    response = api.post("/api/cases/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("DF05" in m for m in response.json()["errors"])


def test_group_payload_cannot_carry_kernel_params(client):
    api, _ = client
    payload = dict(NEW_GROUP, kernel_params=VALID_KERNEL_PARAMS)
    response = api.post("/api/groups/validate", json=payload)
    assert response.json()["ok"] is False
    assert any("kernel_params" in m for m in response.json()["errors"])


def test_write_path_guard_refuses_escape(tmp_path):
    """The resolved write path must stay inside the case library (v1.2)."""
    from fastapi import HTTPException

    from emv_host.web.api import _resolve_inside

    root = tmp_path / "cases"
    root.mkdir()
    inside = _resolve_inside(root, "visa", "contactless", "cases", "X.json",
                             label="案例路径")
    assert inside.parent == root / "visa" / "contactless" / "cases"

    with pytest.raises(HTTPException) as excinfo:
        _resolve_inside(root, "..", "..", "escaped.json", label="案例路径")
    assert excinfo.value.status_code == 400
    assert "用例库之外" in excinfo.value.detail


def test_websocket_since_garbage_is_tolerated(tmp_path):
    """?since=abc must not kill the socket before the snapshot is sent."""
    class _StubExecutor:
        """The WS snapshot needs only these two readers."""

        busy = False

        def snapshot(self):
            return {"devices": {}, "session": None}

    root = tmp_path / "wscases"
    _write(root / "visa" / "brand.json", BRAND)
    _write(root / "visa" / "contactless" / "cases" / "VIS-CL-001.json",
           EXISTING_CASE)
    library = load_library(root)
    context = AppContext(library=library, executor=_StubExecutor(),
                         store=RunStore(tmp_path / "runs"),
                         bus=EventBus(), static_dir="")
    api = TestClient(create_app(context))

    with api.websocket_connect("/ws?since=abc") as ws:
        first = ws.receive_json()
        assert first["kind"] == "snapshot"
        assert first["seq"] == 0


def test_run_request_accepts_group_kind(client):
    """v1.2 T17: kind=group expands a 总案例's 子案例 (validation only here —
    the executor is a stub or None in these tests, so only 4xx paths run)."""
    api, _ = client
    # unknown group -> 404 (proof the branch is reached, not the generic 400)
    response = api.post("/api/run", json={
        "brand": "visa", "mode": "contactless", "kind": "group",
        "group_id": "NOPE-001"})
    assert response.status_code == 404
    # missing group_id -> 400
    response = api.post("/api/run", json={
        "brand": "visa", "mode": "contactless", "kind": "group"})
    assert response.status_code == 400
    assert "group_id" in response.json()["detail"]
