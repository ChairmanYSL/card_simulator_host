"""Generate (or regenerate) the demo case library under cases/.

Usage:  python tools/gen_cases.py [cases_root]

v2 layout: profiles are EMBEDDED in each executable case JSON (bound to the
case, id auto = case_id); scheme.json carries a ``card_template`` (default card
for the add-case page) and a ``pos_params`` template (scheme-specific POS-side
transaction parameters auto-added per case).  There is no shared profiles/
directory any more.

Safety: this tool NEVER deletes whole scheme directories.  Each scheme keeps a
``.gen_cases_manifest.json`` listing the files the generator owns; a rerun only
overwrites those files (and drops previously generated ones that are no longer
part of the demo).  User-created cases (e.g. added via the web UI) are left
untouched — but note a case added through the UI with a case_id equal to one of
the demo ids WOULD be overwritten by design.

PANs are generated Luhn-valid and unique per case.  Demo data is synthetic;
online cases assume a test acquirer host / whitelist (see scheme.json
environment_assumptions).  Values inside pos_params are informational strings
that the POS app interprets by tag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# -- manifest bookkeeping (non-destructive regeneration) ----------------------
_gen_root: Path | None = None
_gen_files: list[str] = []


def _track(path: Path) -> None:
    if _gen_root is not None:
        try:
            rel = path.relative_to(_gen_root).as_posix()
        except ValueError:
            return
        if rel not in _gen_files:
            _gen_files.append(rel)


def _read_manifest(scheme_root: Path) -> list[str]:
    manifest = scheme_root / ".gen_cases_manifest.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return list(data.get("files", []))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


def _clean_previous_generated(scheme_root: Path) -> None:
    """Delete only files this generator created before (per its manifest).

    Every entry is resolved and must stay inside ``scheme_root``: a hand-edited
    or corrupted manifest containing ``../`` would otherwise let a regeneration
    delete files outside the case library.
    """
    root = scheme_root.resolve()
    for rel in _read_manifest(scheme_root):
        target = (scheme_root / rel).resolve()
        if target != root and root not in target.parents:
            print(f"  ! manifest entry outside the scheme dir, skipped: {rel}")
            continue
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            pass
    # prune now-empty sub-directories (best effort, bottom-up)
    for rel in reversed(sorted(_read_manifest(scheme_root))):
        parent = (scheme_root / Path(rel).parent).resolve()
        if parent != root and root not in parent.parents:
            continue
        try:
            parent.rmdir()  # only removes when empty
        except OSError:
            pass


def _write_manifest(scheme_root: Path) -> None:
    manifest = scheme_root / ".gen_cases_manifest.json"
    manifest.write_text(json.dumps({"files": sorted(_gen_files)},
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def luhn_digit(prefix: str, length: int = 16) -> str:
    body = prefix + "0" * (length - 1 - len(prefix))
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        # the body's rightmost digit sits at position 1 of the full number
        # (the check digit takes position 0), so double even indices here
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - total % 10) % 10
    return body + str(check)


def write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    _track(path)


SCHEMES = {
    "visa": {
        "display_name": "Visa",
        "aid": "A0000000031010",
        "label": "VISA CREDIT",
        "pan_prefix": "476134",
        "holder": "VISA TEST CARD",
    },
    "mastercard": {
        "display_name": "Mastercard",
        "aid": "A0000000041010",
        "label": "MASTERCARD",
        "pan_prefix": "541275",
        "holder": "MC TEST CARD",
    },
    "unionpay": {
        "display_name": "UnionPay",
        "aid": "A000000333010101",
        "label": "UNIONPAY",
        "pan_prefix": "622202",
        "holder": "UP TEST CARD",
    },
    # ---- 新增演示卡组（占位数据，可用网页 AddCase 改成真实值）----
    "jcb": {
        "display_name": "JCB",
        "aid": "A0000000651010",
        "label": "JCB CREDIT",
        "pan_prefix": "352800",
        "holder": "JCB TEST CARD",
    },
    "amex": {
        "display_name": "American Express",
        "aid": "A00000002501",
        "label": "AMEX",
        "pan_prefix": "340000",
        "holder": "AMEX TEST CARD",
    },
    "discover": {
        "display_name": "Discover",
        "aid": "A0000003241010",
        "label": "DISCOVER",
        "pan_prefix": "601100",
        "holder": "DISCOVER TEST CARD",
    },
    # PURE（Gemalto，PPSE=2PAY.SYS.DDF01，规范 PURE Contactless Reader v2.1.8）
    "pure": {
        "display_name": "PURE",
        "aid": None,  # 多个支付应用 AID，见 aids_list
        "label": "PURE",
        "pan_prefix": "628070",
        "holder": "PURE TEST CARD",
        "aids_list": [
            "D9999999991010", "D9999999992020", "D666666666",
            "A0000002282010", "A0000002802010", "A0000007231010",
        ],
        # 发卡方示例：PAN 与 tag57 track2 保持一致（用户提供）
        "fixed_pan": "6280706258024090",
        "fixed_track2": "6280706258024090D280460110889160",
        # PURE kernel identifier，PPSE 目录项 9F2A（透传供安卓侧使用）
        "kernel_id": "2D",
    },
}

# scheme -> POS transaction parameter template (POS-side, tag-keyed;
# per-scheme requirements differ: add/remove tags as needed)
POS_PARAMS = {
    "visa": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9C", "name": "transaction_type", "value": "00"},
    ],
    "mastercard": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9F1A", "name": "terminal_country", "value": "156"},
    ],
    "unionpay": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9F33", "name": "terminal_capabilities", "value": "E0F0C8"},
    ],
    # 新增演示卡组共用基础模板
    "jcb": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9C", "name": "transaction_type", "value": "00"},
    ],
    "amex": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9C", "name": "transaction_type", "value": "00"},
    ],
    "discover": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9C", "name": "transaction_type", "value": "00"},
    ],
    "pure": [
        {"tag": "9F02", "name": "amount", "value": "100.00"},
        {"tag": "5F2A", "name": "currency", "value": "156"},
        {"tag": "9C", "name": "transaction_type", "value": "00"},
    ],
}

CASE_SPECS = [
    {
        "suffix": "001", "title": "offline approved (TC)",
        "behavior": "offline_approved",
        "expect": [
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": "offline"},
            {"path": "tags.gen_ac.9F26", "op": "present"},
        ],
    },
    {
        "suffix": "002", "title": "offline declined (AAC)",
        "behavior": "offline_declined",
        "expect": [
            {"path": "resp_code", "op": "eq", "expect": "05"},
            {"path": "outcome", "op": "eq", "expect": "declined"},
            {"path": "online", "op": "eq", "expect": "offline"},
        ],
    },
    {
        "suffix": "003", "title": "online approved (ARQC/ARPC, test host)",
        "behavior": "online_request",
        "expect": [
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": "online"},
        ],
    },
    {
        "suffix": "004", "title": "transaction data tags present",
        "behavior": "offline_approved",
        "expect": [
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "tags.gen_ac.9F26", "op": "present"},
            {"path": "tags.gen_ac.9F10", "op": "present"},
            {"path": "tags.gen_ac.5F2A", "op": "eq", "expect": "0156"},
            {"path": "amount", "op": "eq", "expect": "100.00"},
        ],
    },
    {
        "suffix": "005", "title": "DEMO expected-failure (declined card, expect approve)",
        "behavior": "offline_declined",
        "expect": [
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "outcome", "op": "eq", "expect": "approved"},
        ],
    },
]

MODES = {
    "contactless": {
        "notes": "tap the phone on the POS reader when it shows the amount",
        "pin_hint": "none",
    },
}


def embedded_profile(scheme: dict, brand: str, case_id: str, title: str,
                     ac_behavior: str, pan_key: int,
                     extra: dict | None = None) -> dict:
    """One case-bound CardProfile (id = case_id, unique PAN per case)."""
    network_name = {"visa": "VISA", "mastercard": "MASTERCARD",
                    "unionpay": "UNIONPAY"}.get(brand, brand.upper())
    # multi-AID schemes (PURE) vs single-AID demo schemes
    aids_list = scheme.get("aids_list") or [scheme.get("aid")]
    aids = [{"aid": aid, "label": scheme["label"], "priority": i}
            for i, aid in enumerate(aids_list) if aid]
    pan = scheme.get("fixed_pan") or luhn_digit(f"{scheme['pan_prefix']}{pan_key:02d}")
    track2 = scheme.get("fixed_track2") or f"{pan}=2812201".encode("ascii").hex()
    profile = {
        "id": case_id,  # bound: profile id == case id
        "name": f"{scheme['display_name']} {title}",
        "network": network_name,
        "isActive": True,
        "aids": aids,
        "pan": pan,
        "panSequenceNumber": 1,
        "applicationExpirationDate": "281231",
        "cardholderName": scheme["holder"],
        "track2EquivalentData": track2,
        "applicationLabel": scheme["label"],
        "applicationVersionNumber": "0001",
        "aip": "5800",
        "afl": "08010100",
        "issuerCountryCode": "0156",
        "records": [
            {"sfi": 1, "record": 1,
             "fields": ["track2", "pan", "cardholderName", "expiryDate",
                        "issuerCountryCode", "cvmList"]},
        ],
        # no symmetricKeyId -> app falls back to the fake cryptogram
        # (8x0xFA, ac_crypto:"fake") per v1.1 O4/N8
        "acBehavior": ac_behavior,
    }
    if scheme.get("kernel_id"):
        # PURE: PPSE 目录项 9F2A Kernel Identifier（透传字段供安卓侧使用）
        profile["kernelId"] = scheme["kernel_id"]
    if extra:
        profile.update(extra)
    return profile


def card_template(scheme: dict, brand: str) -> dict:
    """scheme.json card_template: default card cloned by the add-case page."""
    return embedded_profile(scheme, brand, f"{brand}_card_template",
                            "默认卡片模板", "offline_approved", pan_key=0,
                            extra={"name": f"{scheme['display_name']} 默认卡片"})


def gen_case(scheme_key: str, scheme: dict, mode: str, spec: dict,
             brand: str, case_prefix: str, pos_params: list[dict]) -> dict:
    case_id = f"{case_prefix}-CL-{spec['suffix']}"
    pin = MODES[mode]["pin_hint"]
    return {
        "case_id": case_id,
        "title": f"{scheme['display_name']} {mode} {spec['title']}",
        "summary": f"{mode} transaction with ac_behavior={spec['behavior']}",
        "scheme": scheme_key,
        "entry_mode": mode,
        "profile": embedded_profile(scheme, brand, case_id, spec["title"],
                                    spec["behavior"], int(spec["suffix"])),
        "transaction": {
            "operator_instructions": {
                "amount": "100.00",
                "currency": "CNY",
                "entry_mode": mode,
                "pin_hint": pin,
                "notes": MODES[mode]["notes"],
            },
            "expected_values": {
                "amount": "100.00",
                "currency": "156",
                "entry_mode": mode,
            },
            "pos_params": [dict(p) for p in pos_params],
            "kernel_params": None,
        },
        "preconditions": [
            "phone NFC enabled and card simulator app in foreground",
            "POS in sale mode, amount entered manually by operator",
        ],
        "test_plan": [
            {"step": 1, "action": "host pushes card profile and starts emulation",
             "expect": "android reports emulating"},
            {"step": 2, "action": "host pushes POS transaction parameters (tags)",
             "expect": "POS applies them before the operator finishes the transaction"},
            {"step": 3, "action": "operator enters 100.00 on the POS and completes "
                                 "the transaction with the simulated card",
             "expect": "POS app reports transaction_result"},
            {"step": 4, "action": "host asserts expected_results",
             "expect": "case judged PASS/FAIL/WARN"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": spec["expect"],
    }


def main(root: str) -> None:
    root = Path(root)
    full_demo_schemes = {"visa", "mastercard", "unionpay"}  # 原有完整演示（5 用例+分组）
    prefix_map = {"visa": "VISA", "mastercard": "MC", "unionpay": "UPI",
                  "jcb": "JCB", "amex": "AMEX", "discover": "DISC",
                  "pure": "PURE"}
    for scheme_key, scheme in SCHEMES.items():
        brand = scheme_key
        case_prefix = prefix_map[scheme_key]
        pos_params = POS_PARAMS[scheme_key]

        # non-destructive regeneration: only remove files we generated before
        scheme_root = root / scheme_key
        scheme_root.mkdir(parents=True, exist_ok=True)
        global _gen_root, _gen_files
        _gen_root = scheme_root
        _gen_files = []
        _clean_previous_generated(scheme_root)

        write(scheme_root / "scheme.json", {
            "scheme": scheme_key,
            "display_name": scheme["display_name"],
            "description": f"{scheme['display_name']} demo case library",
            "entry_modes": ["contactless"],
            "suite_policy": {"on_failure": "stop", "retry_count": 1},
            "card_template": card_template(scheme, brand),
            "pos_params": pos_params,
            "environment_assumptions":
                "online cases require a test acquirer host / card whitelist; "
                "the card answers ARQC with a dummy cryptogram, so a real "
                "production host would decline online transactions",
        })

        if scheme_key in full_demo_schemes:
            for mode in ("contactless",):
                for spec in CASE_SPECS:
                    case = gen_case(scheme_key, scheme, mode, spec, brand,
                                    case_prefix, pos_params)
                    write(scheme_root / mode / "cases" / f"{case['case_id']}.json",
                          case)
        else:
            # 新增卡组（JCB/AMEX/DISCOVER/PURE）：骨架 + 1 个冒烟用例
            _gen_smoke_case(scheme_root, scheme, brand, case_prefix, pos_params)

        # PPSE 分组演示：总案例 OPBD-001 + 4 个子案例（各内嵌 PPSE 变体档案）
        if scheme_key == "visa":
            _gen_ppse_group(scheme_root, scheme, brand, pos_params)

        # v1.1 N12: 指令不响应（静默）演示 —— UnionPay 卡组
        if scheme_key == "unionpay":
            _gen_no_response_cases(scheme_root, scheme, brand, pos_params)
            # UnionPay QPB.I.010.00: 交易不能联机完成（分组演示）
            _gen_qpb_i_010_group(scheme_root, scheme, brand, pos_params)
            # UnionPay QPB.B.002.00: 状态检查请求（分组演示）
            _gen_qpb_b_002_group(scheme_root, scheme, brand, pos_params)
            # UnionPay QPB.D.001.00: 使用 PPSE 目录选择方式（总案例 + 4 子案例）
            _gen_qpb_d_001_group(scheme_root, scheme, brand, pos_params)

        _write_manifest(scheme_root)
        _gen_root = None
        _gen_files = []

    print(f"case library generated under {root}")


def _gen_smoke_case(scheme_root: Path, scheme: dict, brand: str,
                    case_prefix: str, pos_params: list[dict]) -> None:
    """新增卡组（JCB/AMEX/DISCOVER/PURE）：仅生成 1 个冒烟用例（offline approved）。

    卡片数据说明（JCB/AMEX/Discover 为演示占位，PURE 使用发卡方提供的
    AID 列表 + 示例 PAN/track2 + 9F2A kernel id）。"""
    mode = "contactless"
    case_id = f"{case_prefix}-CL-001"
    placeholder_note = ("（AID/PAN 为演示占位数据，可在网页 AddCase 中改为真实值）"
                        if not scheme.get("aids_list")
                        else "（PURE：使用规范确认的 AID/PAN/track2/kernelId）")
    write(scheme_root / mode / "cases" / f"{case_id}.json", {
        "case_id": case_id,
        "title": f"{scheme['display_name']} contactless offline approved (TC) 冒烟用例",
        "summary": f"{mode} smoke case with ac_behavior=offline_approved {placeholder_note}",
        "scheme": brand,
        "entry_mode": mode,
        "profile": embedded_profile(scheme, brand, case_id,
                                    "offline approved (TC)", "offline_approved",
                                    pan_key=1),
        "transaction": {
            "operator_instructions": {
                "amount": "100.00", "currency": "CNY",
                "entry_mode": mode, "pin_hint": "none",
                "notes": MODES[mode]["notes"],
            },
            "expected_values": {
                "amount": "100.00", "currency": "156", "entry_mode": mode,
            },
            "pos_params": [dict(p) for p in pos_params],
            "kernel_params": None,
        },
        "preconditions": [
            "phone NFC enabled and card simulator app in foreground",
            "POS in sale mode, amount entered manually by operator",
        ],
        "test_plan": [
            {"step": 1, "action": "host pushes card profile and starts emulation",
             "expect": "android reports emulating"},
            {"step": 2, "action": "operator enters 100.00 on the POS and completes "
                                 "the transaction with the simulated card",
             "expect": "POS app reports transaction_result"},
            {"step": 3, "action": "host asserts expected_results",
             "expect": "case judged PASS/FAIL/WARN"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": CASE_SPECS[0]["expect"],
    })


def _gen_no_response_cases(scheme_root: Path, scheme: dict, brand: str,
                           pos_params: list[dict]) -> None:
    """UnionPay 演示：GPO 不响应（每次静默 / 仅首次 N 次静默）。"""
    cases_dir = scheme_root / "contactless" / "cases"

    def write_case(case_id: str, title: str, behavior: str, no_resp,
                   rules: list[dict]) -> None:
        mode = "contactless"
        write(cases_dir / f"{case_id}.json", {
            "case_id": case_id,
            "title": f"{scheme['display_name']} contactless {title}",
            "summary": f"noResponseCommands={behavior}（档案随案例内嵌）",
            "scheme": brand,
            "entry_mode": mode,
            "profile": embedded_profile(scheme, brand, case_id, title,
                                        "offline_approved",
                                        int(case_id.rsplit("-", 1)[1]),
                                        extra={"noResponseCommands": no_resp}),
            "transaction": {
                "operator_instructions": {
                    "amount": "100.00", "currency": "CNY",
                    "entry_mode": mode, "pin_hint": "none",
                    "notes": "将手机贴到 POS 读卡器；观察 POS 对 GPO 无响应的处理",
                },
                "expected_values": {"amount": "100.00", "currency": "156",
                                    "entry_mode": mode},
                "pos_params": [dict(p) for p in pos_params],
                "kernel_params": None,
            },
            "preconditions": [
                "手机 NFC 已开启，模拟 app 保持前台",
                "真实交互需支持 APDU 静默的安卓模拟器 + 真实 POS（mock 不回放静默）",
            ],
            "test_plan": [
                {"step": 1, "action": "主机下发含 noResponseCommands 的档案并启动模拟",
                 "expect": "安卓上报 emulating 并记录静默指令"},
                {"step": 2, "action": "操作员在 POS 发起交易，终端执行到 GPO",
                 "expect": behavior},
            ],
            "timeout_seconds": 180,
            "retry_on_fail": 0,
            "expected_results": rules,
        })

    # UPI-CL-006: 每次 GPO 都静默 -> 终端等不到响应，交易中止/走异常分支
    write_case("UPI-CL-006",
               "GPO 不响应（每次静默）",
               "卡片对 GPO 无响应（每次），终端超时中止交易",
               ["GPO"],
               [{"path": "outcome", "op": "eq", "expect": "declined"},
                {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}])
    # UPI-CL-007: 仅首次 GPO 静默，之后正常响应 -> 终端重试后交易批准
    write_case("UPI-CL-007",
               "GPO 首次不响应后恢复正常（times=1）",
               "首次 GPO 无响应，重试后卡片正常应答，交易批准",
               [{"command": "GPO", "times": 1}],
               [{"path": "resp_code", "op": "eq", "expect": "00"},
                {"path": "outcome", "op": "eq", "expect": "approved"}])


def _gen_qpb_i_010_group(scheme_root: Path, scheme: dict, brand: str,
                         pos_params: list[dict]) -> None:
    """UnionPay QPB.I.010.00「交易不能联机完成」分组演示（QPB-I-010）。

    语义映射（对照银联测试计划 QPB.I.010.00）：
    * 卡片 GPO 响应须含发卡行认证数据 tag 9F10（profile.issuerApplicationData），
      第5字节第6-5位为 '10' —— 9F10 样例值由用例提供方给定，原样透传
      （值取自真实回放/规范样例，位序按银联规范，真机联调以 POS 回放核对）；
    * '联机交易不能完成（主机没有响应）'由 POS 侧环境构造（联机通道不可用），
      host 只能断言 POS 回传的 transaction_result；
    * profile.acBehavior=online_request：卡片应答 ARQC，促使 POS 发起联机授权，
      联机失败后读写器应拒绝交易；
    * 通过标准映射（用户约定）：outcome=declined 且 resp_code ∈ {05,91,16,80}。
    """
    cases_dir = scheme_root / "contactless" / "cases"
    group_dir = cases_dir / "QPB-I-010"

    # 9F10（issuerApplicationData）样例：22 10 90 42 03 24 00 00 00 00 00 00
    # 00 23 33 10 FF FF 00 00 00 07 41 94 00 FF（26 字节，用例提供方给定）
    iad_9f10 = ("22109042032400000000000000"
                "233310FFFF00000007419400FF")

    write(group_dir / "group.json", {
        "group_id": "QPB-I-010",
        "title": "QPB.I.010.00 交易不能联机完成",
        "summary": "联机交易不能完成（如主机没有响应）时，读写器应拒绝交易并提示商户和持卡人交易被拒绝",
        "purpose": "在交易不能联机完成时，确保读写器拒绝交易并提示商户和持卡人交易被拒绝",
        "test_conditions": "读写器支持拒绝交易；读写器具有联机能力；联机交易不能完成（例如：主机没有响应）",
        "scheme": brand,
        "entry_mode": "contactless",
        "test_plan": [
            {"step": 1, "action": "交易预处理和探测卡片成功完成",
             "expect": "进入 PPSE 选择流程"},
            {"step": 2, "action": "使用 PPSE 方式选择应用",
             "expect": "应用选择成功"},
            {"step": 3, "action": "执行初始化应用处理：卡片从读写器接收到 GPO 命令",
             "expect": "卡片返回 GPO 响应"},
            {"step": 4, "action": "卡片返回 GPO 响应，含发卡行认证数据（标签 9F10，"
                                 "第5字节第6-5位为 '10'）",
             "expect": "读写器按卡片返回的数据继续交易"},
            {"step": 5, "action": "联机交易不能完成（发卡行/主机没有返回响应）",
             "expect": "读写器拒绝交易并提示商户和持卡人交易被拒绝"},
        ],
        "pass_criteria": [
            "读写器应拒绝交易，读写器应提示商户和持卡人交易被拒绝。",
            "本 host 断言映射（用户约定）：POS transaction_result 的 "
            "outcome=declined，resp_code ∈ {05, 91, 16, 80}。",
        ],
    })

    # 子案例 QPB-I-010-01：唯一可执行流程（后续同系列流程可补 -02…）
    case_id = "QPB-I-010-01"
    write(group_dir / f"{case_id}.json", {
        "case_id": case_id,
        "title": "交易不能联机完成（读写器拒绝交易）",
        "summary": "GPO 响应含 9F10（第5字节第6-5位='10'）；联机主机无响应；"
                   "读写器应拒绝交易（档案随案例内嵌）",
        "scheme": brand,
        "entry_mode": "contactless",
        "profile": embedded_profile(scheme, brand, case_id,
                                    "交易不能联机完成（拒绝交易）",
                                    "online_request", 8,
                                    extra={"issuerApplicationData": iad_9f10}),
        "transaction": {
            "operator_instructions": {
                "amount": "100.00", "currency": "CNY",
                "entry_mode": "contactless", "pin_hint": "none",
                "notes": "将手机贴到 POS 读卡器；POS 将尝试联机授权，但测试环境"
                         "后台不响应，读写器应拒绝交易并提示商户/持卡人",
            },
            "expected_values": {"amount": "100.00", "currency": "156",
                                "entry_mode": "contactless"},
            "pos_params": [dict(p) for p in pos_params],
            "kernel_params": None,
        },
        "preconditions": [
            "手机 NFC 已开启，模拟 app 保持前台",
            "POS 具备联机能力，且联机通道不可用（收单/发卡行主机无响应），"
            "确保'联机交易不能完成'",
            "真实交互需真实 POS（mock 客户端不回放'主机无响应'场景）",
        ],
        "test_plan": [
            {"step": 1, "action": "主机下发卡片档案（acBehavior=online_request；"
                                  "GPO 响应含 9F10）并启动模拟",
             "expect": "安卓上报 emulating"},
            {"step": 2, "action": "主机向 POS 下发交易参数（tag 列表）",
             "expect": "POS 应用参数后进入交易"},
            {"step": 3, "action": "操作员在 POS 发起 100.00 元交易；卡片返回 GPO "
                                  "响应（含 9F10），终端尝试联机但主机无响应",
             "expect": "读写器拒绝交易并提示商户和持卡人，POS 回传 declined 结果"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": [
            {"path": "outcome", "op": "eq", "expect": "declined"},
            {"path": "resp_code", "op": "any_of", "expect": ["05", "91", "16", "80"]},
        ],
    })


def _gen_qpb_b_002_group(scheme_root: Path, scheme: dict, brand: str,
                         pos_params: list[dict]) -> None:
    """UnionPay QPB.B.002.00「状态检查请求（Status Check Requested）」分组演示。

    语义映射（对照银联测试计划 QPB.B.002.00）：
    * 授权金额为一个货币单位（0.01），且低于读写器非接触脱机金额/最低限额；
    * 模拟卡 SELECT AID 的 FCI 响应须含 9F38（PDOL）——请求 POS 在 GPO 命令数据
      中回传 9F37/9F02/5F2A/9F66，对应档案字段 profile.pdol（tag-长度模板，
      hex，由安卓侧包装成 FCI 中的 9F38）；
    * 通过标准：读写器在终端交易属性 9F66 字节2位8='1'（请求联机密文）——
      本 demo 实测样例 9F66=36804000（字节2=0x80）；若内核其它字节有差异，
      断言可改 regex ^..80（host 无位运算，用全值 eq 或正则表达）；
    * 终局（用户约定）：acBehavior=online_request 回 ARQC，联机批准
      （outcome=approved / online=online / resp_code=00，需测试收单主机）。
    """
    cases_dir = scheme_root / "contactless" / "cases"
    group_dir = cases_dir / "QPB-B-002"

    # PDOL tag-长度模板（12 字节）：请求 9F37(不可预知数) 9F02(金额)
    # 5F2A(币种) 9F66(终端交易属性 TTQ)
    pdol_9f66 = "9F37049F02065F2A029F6604"

    write(group_dir / "group.json", {
        "group_id": "QPB-B-002",
        "title": "QPB.B.002.00 状态检查请求（Status Check Requested）",
        "summary": "授权金额为一个货币单位且低于各限额时，读写器应在终端交易属性"
                   "9F66 中请求联机密文（字节2位8='1'）",
        "purpose": "读卡器支持状态检查；授权金额 < 读写器非接触脱机金额（若存在）；"
                   "授权金额 < 最低限额（若存在）；要求进行状态检查（即：授权金额为"
                   "一个货币单位）",
        "test_conditions": "读卡器支持状态检查；授权金额为一个货币单位且低于"
                           "读写器非接触脱机金额/最低限额",
        "scheme": brand,
        "entry_mode": "contactless",
        "test_plan": [
            {"step": 1, "action": "交易预处理和探测卡片成功完成",
             "expect": "进入应用选择流程"},
            {"step": 2, "action": "选择应用：卡片 SELECT AID 的 FCI 响应含 9F38 "
                                  "PDOL（请求 9F37/9F02/5F2A/9F66）",
             "expect": "读写器取得 PDOL"},
            {"step": 3, "action": "初始化应用处理：读写器按 PDOL 组 GPO 命令数据，"
                                  "其中含终端交易属性 9F66",
             "expect": "卡片收到含 9F66 的 GPO 命令并正常应答"},
            {"step": 4, "action": "授权金额（标签 9F02）为：小于读写器非接触交易最低"
                                  "限额；小于相关最低限额；一个货币单位（0.01）",
             "expect": "读写器在 9F66 字节2位8='1' 请求联机密文，执行联机授权"},
        ],
        "pass_criteria": [
            "读写器应在终端交易属性（标签 9F66）中表示请求联机密文，即字节2位8='1'"
            "（本 demo 期望 9F66=36804000）。",
            "本 host 断言映射（用户约定）：result.tags.gpo.9F66=36804000；"
            "outcome=approved、online=online、resp_code=00（需测试收单主机）。",
        ],
    })

    # 子案例 QPB-B-002-01：唯一可执行流程（后续同系列流程可补 -02…）
    case_id = "QPB-B-002-01"
    write(group_dir / f"{case_id}.json", {
        "case_id": case_id,
        "title": "状态检查请求（9F66 请求联机密文）",
        "summary": "金额 0.01（一个货币单位，低于各限额）；FCI 带 9F38 PDOL 请求 "
                   "9F66；终端 TTQ 9F66=36804000（字节2位8='1'）请求联机密文并联机"
                   "批准（档案随案例内嵌）",
        "scheme": brand,
        "entry_mode": "contactless",
        "profile": embedded_profile(scheme, brand, case_id,
                                    "状态检查请求（9F66 请求联机密文）",
                                    "online_request", 9,
                                    extra={"pdol": pdol_9f66}),
        "transaction": {
            "operator_instructions": {
                "amount": "0.01", "currency": "CNY",
                "entry_mode": "contactless", "pin_hint": "none",
                "notes": "将手机贴到 POS 读卡器；金额 0.01（一个货币单位）触发状态"
                         "检查，POS 应在 TTQ(9F66) 请求联机密文（36804000）并完成"
                         "联机授权",
            },
            "expected_values": {"amount": "0.01", "currency": "156",
                                "entry_mode": "contactless"},
            "pos_params": [
                {"tag": "9F02", "name": "amount", "value": "0.01"},
                {"tag": "5F2A", "name": "currency", "value": "156"},
                {"tag": "9F33", "name": "terminal_capabilities", "value": "E0F0C8"},
            ],
        },
        "preconditions": [
            "手机 NFC 已开启，模拟 app 保持前台",
            "POS 支持状态检查；金额 0.01 需低于 POS 非接交易最低限额/相关最低限额"
            "（读写器非接触脱机限额，若前者不存在则用终端最低限额）",
            "模拟卡 SELECT AID 响应含 9F38 PDOL（请求 9F37/9F02/5F2A/9F66），"
            "需支持该字段的安卓模拟实现 + 真实 POS",
            "联机批准需测试收单主机/卡 BIN 白名单（同 UPI-CL-003 环境假设；"
            "真实生产主机将拒绝假密文 ARQC）",
        ],
        "test_plan": [
            {"step": 1, "action": "主机下发卡片档案（acBehavior=online_request；"
                                  "FCI 含 9F38 PDOL 请求 9F66）并启动模拟",
             "expect": "安卓上报 emulating"},
            {"step": 2, "action": "主机向 POS 下发交易参数（金额 0.01 等）",
             "expect": "POS 应用参数后进入交易"},
            {"step": 3, "action": "操作员在 POS 发起 0.01 元交易（一个货币单位，"
                                  "低于各限额）",
             "expect": "POS 按 PDOL 组 GPO 命令（含 TTQ 9F66），9F66 字节2位8='1'"
                      "（期望 36804000）请求联机密文"},
            {"step": 4, "action": "交易联机批准（测试收单主机）",
             "expect": "POS 回传 approved/online 结果及 tags.gpo.9F66"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": [
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": "online"},
            # 9F66 字节2位8='1'（请求联机密文）；实测样例 36804000，若内核其它
            # 字节有差异可将该规则改为 regex ^..80
            {"path": "tags.gpo.9F66", "op": "eq", "expect": "36804000"},
        ],
    })


def _gen_qpb_d_001_group(scheme_root: Path, scheme: dict, brand: str,
                         pos_params: list[dict]) -> None:
    """UnionPay QPB.D.001.00「使用 PPSE 目录选择方式」分组演示。

    总案例 QPB-D-001 + 4 个可执行子案例（-01…-04），对照银联测试计划：
    * 案例01：SELECT PPSE 返回格式正确的 FCI（ppseMode=auto）→ 交易批准；
    * 案例02：FCI 缺 AID（custom，61 内仅 50 label + 87，无 4F=AID）
      → 终端终止交易并关闭非接界面，尝试用另一界面完成交易；
    * 案例03：FCI 无 61 模板（ppseMode=empty）→ 终端终止交易；
    * 案例04：错误响应码（ppseMode=disabled，回 6A82）→ 终端终止交易并关闭
      非接界面，尝试用另一界面完成交易。

    注：Visa 卡组下同名历史分组 OPBD-001（早期测试添加）保留不动；本组
    仅属于 UnionPay（AID A000000333010101，FCI 内 label = UNIONPAY）。
    """
    cases_dir = scheme_root / "contactless" / "cases"
    group_dir = cases_dir / "QPB-D-001"

    def missing_aid_fci() -> str:
        """PPSE FCI：目录条目(61)带 50 label + 87 priority，但无 4F=AID。"""
        label_hex = scheme["label"].encode().hex()  # UNIONPAY
        entry61 = _tlv("61", _tlv("50", label_hex) + "870101")
        fci = _tlv("6F", _tlv("84", "2PAY.SYS.DDF01".encode().hex())
                   + _tlv("A5", _tlv("BF0C", entry61)))
        return fci.upper()

    write(group_dir / "group.json", {
        "group_id": "QPB-D-001",
        "title": "QPB.D.001.00 使用PPSE目录选择方式",
        "summary": "PPSE 目录选择方式：SELECT PPSE 响应正确性分组（UnionPay）",
        "purpose": "确保读写器使用目录选择方式来选择PPSE",
        "test_conditions": "卡片对 SELECT PPSE 命令的响应为 FCI 和 '9000'",
        "scheme": brand,
        "entry_mode": "contactless",
        "test_plan": [
            {"step": 1, "action": "交易预处理和探测卡片处理成功完成",
             "expect": "进入 PPSE 选择流程"},
            {"step": 2, "action": "卡片对 SELECT PPSE 命令返回 PPSE FCI 和 '9000'",
             "expect": "读写器按 FCI 内容继续目录选择"},
        ],
        "pass_criteria": [
            "案例01：读写器应通过使用文件名称为 '2PAY.SYS.DDF01' 的选择命令，"
            "发送 PPSE 目录选择方式的第一个 SELECT 命令，交易批准。",
            "案例02：终端终止交易并关闭非接界面，尝试用另一界面完成交易。",
            "案例03：终端终止交易。",
            "案例04：终端终止交易并关闭非接界面，尝试用另一界面完成交易。",
        ],
    })

    sub_cases = [
        ("QPB-D-001-01", "卡片返回格式正确的FCI信息", "offline_approved", None,
         "approved",
         [{"path": "resp_code", "op": "eq", "expect": "00"},
          {"path": "outcome", "op": "eq", "expect": "approved"}]),
        ("QPB-D-001-02", "卡片返回的FCI信息中缺少AID信息", "offline_approved",
         {"ppseMode": "custom", "ppseData": missing_aid_fci()},
         "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
        ("QPB-D-001-03", "卡片返回的FCI信息中无61模板", "offline_approved",
         {"ppseMode": "empty"}, "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
        ("QPB-D-001-04", "卡片错误响应码", "offline_approved",
         {"ppseMode": "disabled"}, "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
    ]
    for pan_key, (case_id, sub_title, behavior, ppse_extra,
                  expected_behavior, rules) in enumerate(sub_cases, start=10):
        ppse_name = {
            "QPB-D-001-01": "PPSE 正常 FCI(auto)",
            "QPB-D-001-02": "PPSE FCI 缺 AID(custom)",
            "QPB-D-001-03": "PPSE 无 61 模板(empty)",
            "QPB-D-001-04": "PPSE 错误 SW(disabled)",
        }[case_id]
        expect_note = {
            "QPB-D-001-01": "读写器以 '2PAY.SYS.DDF01' 发第一个 SELECT 命令，交易批准",
            "QPB-D-001-02": "终端终止交易并关闭非接界面，尝试用另一界面完成交易",
            "QPB-D-001-03": "终端终止交易",
            "QPB-D-001-04": "终端终止交易并关闭非接界面，尝试用另一界面完成交易",
        }[case_id]
        write(group_dir / f"{case_id}.json", {
            "case_id": case_id,
            "title": sub_title,
            "summary": f"PPSE 子案例：{ppse_name}（档案随案例内嵌）",
            "scheme": brand,
            "entry_mode": "contactless",
            "profile": embedded_profile(scheme, brand, case_id, ppse_name,
                                        behavior, pan_key, extra=ppse_extra),
            "transaction": {
                "operator_instructions": {
                    "amount": "100.00", "currency": "CNY",
                    "entry_mode": "contactless", "pin_hint": "none",
                    "notes": "将手机贴到 POS 读卡器",
                },
                "expected_values": {"amount": "100.00", "currency": "156",
                                    "entry_mode": "contactless"},
                "pos_params": [dict(p) for p in pos_params],
                "kernel_params": None,
            },
            "preconditions": ["手机 NFC 已开启，模拟 app 保持前台"],
            "test_plan": [
                {"step": 1, "action": "主机下发卡片档案并启动模拟",
                 "expect": "安卓上报 emulating"},
                {"step": 2, "action": "主机向 POS 下发交易参数（tag 列表）",
                 "expect": "POS 应用参数后进入交易"},
                {"step": 3, "action": "操作员在 POS 上发起 100.00 元交易",
                 "expect": f"{expect_note}（期望 {expected_behavior}）"},
            ],
            "timeout_seconds": 180,
            "retry_on_fail": 0,
            "expected_results": rules,
        })


def _tlv(tag: str, payload_hex: str) -> str:
    """Build a TLV element (tag hex string + hex payload), length auto-computed
    (short form; long form when >= 0x80 bytes)."""
    length = len(payload_hex) // 2
    if length >= 0x80:
        raw = length.to_bytes(4, "big").lstrip(b"\x00") or b"\x00"
        return f"{tag}{0x80 | len(raw):02X}{raw.hex()}{payload_hex}"
    return f"{tag}{length:02X}{payload_hex}"


def _ppse_missing_aid_fci() -> str:
    """PPSE FCI where the directory entry (61) carries 50 label + 87 priority
    but NO 4F=AID (v1.1 N11 case-02 custom template, label 'VISA' unpadded).

    Built programmatically -> 6F20 840E "2PAY.SYS.DDF01" A50E BF0C0B 6109
    5004 56495341 870101 (lengths verified by _tlv + loader TLV check)."""
    entry61 = _tlv("61", _tlv("50", "VISA".encode().hex()) + "870101")
    fci = _tlv("6F", _tlv("84", "2PAY.SYS.DDF01".encode().hex())
               + _tlv("A5", _tlv("BF0C", entry61)))
    return fci.upper()


def _gen_ppse_group(scheme_root: Path, scheme: dict, brand: str,
                    pos_params: list[dict]) -> None:
    cases_dir = scheme_root / "contactless" / "cases"

    group_dir = cases_dir / "OPBD-001"
    write(group_dir / "group.json", {
        "group_id": "OPBD-001",
        "title": "OPB.D 001.00 使用PPSE目录选择方式",
        "summary": "PPSE 目录选择方式：SELECT PPSE 响应正确性分组",
        "purpose": "确保读写器使用目录选择方式来选择PPSE",
        "test_conditions": "卡片对 SELECT PPSE 命令的响应为 FCI 和 '9000'",
        "scheme": brand,
        "entry_mode": "contactless",
        "test_plan": [
            {"step": 1, "action": "交易预处理和探测卡片处理成功完成",
             "expect": "进入 PPSE 选择流程"},
            {"step": 2, "action": "卡片对 SELECT PPSE 命令返回 PPSE FCI 和 '9000'",
             "expect": "读写器按 FCI 内容继续目录选择"},
        ],
        "pass_criteria": [
            "案例01：读写器应通过使用文件名称为 '2PAY.SYS.DDF01' 的选择命令，"
            "发送 PPSE 目录选择方式的第一个 SELECT 命令，交易批准。",
            "案例02：终端终止交易并关闭非接界面，尝试用另一界面完成交易。",
            "案例03：终端中止交易。",
            "案例04：终端终止交易并关闭非接界面，尝试用另一界面完成交易。",
        ],
    })

    # sub-case -> (case_id, title, ac_behavior, ppse extra, expected outcome)
    sub_cases = [
        ("OPBD-001-01", "卡片返回格式正确的FCI信息", "offline_approved", None,
         "approved",
         [{"path": "resp_code", "op": "eq", "expect": "00"},
          {"path": "outcome", "op": "eq", "expect": "approved"}]),
        ("OPBD-001-02", "卡片返回的FCI信息中缺少AID信息", "offline_approved",
         {"ppseMode": "custom", "ppseData": _ppse_missing_aid_fci()},
         "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
        ("OPBD-001-03", "卡片返回的FCI信息中无61模板", "offline_approved",
         {"ppseMode": "empty"}, "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
        ("OPBD-001-04", "卡片错误响应码", "offline_approved",
         {"ppseMode": "disabled"}, "declined",
         [{"path": "outcome", "op": "eq", "expect": "declined"},
          {"path": "resp_code", "op": "any_of", "expect": ["05", "91"]}]),
    ]
    for pan_key, (case_id, sub_title, behavior, ppse_extra,
                  expected_behavior, rules) in enumerate(sub_cases, start=1):
        ppse_name = {
            "OPBD-001-01": "PPSE 正常 FCI(auto)",
            "OPBD-001-02": "PPSE FCI 缺 AID(custom)",
            "OPBD-001-03": "PPSE 无 61 模板(empty)",
            "OPBD-001-04": "PPSE 错误 SW(disabled)",
        }[case_id]
        write(group_dir / f"{case_id}.json", {
            "case_id": case_id,
            "title": sub_title,
            "summary": f"PPSE 子案例：{ppse_name}（档案随案例内嵌）",
            "scheme": brand,
            "entry_mode": "contactless",
            "profile": embedded_profile(scheme, brand, case_id, ppse_name,
                                        behavior, pan_key, extra=ppse_extra),
            "transaction": {
                "operator_instructions": {
                    "amount": "100.00", "currency": "CNY",
                    "entry_mode": "contactless", "pin_hint": "none",
                    "notes": "将手机贴到 POS 读卡器",
                },
                "expected_values": {"amount": "100.00", "currency": "156",
                                    "entry_mode": "contactless"},
                "pos_params": [dict(p) for p in pos_params],
                "kernel_params": None,
            },
            "preconditions": ["手机 NFC 已开启，模拟 app 保持前台"],
            "test_plan": [
                {"step": 1, "action": "主机下发卡片档案并启动模拟",
                 "expect": "安卓上报 emulating"},
                {"step": 2, "action": "主机向 POS 下发交易参数（tag 列表）",
                 "expect": "POS 应用参数后进入交易"},
                {"step": 3, "action": "操作员在 POS 上发起 100.00 元交易",
                 "expect": f"POS 行为按 PPSE 响应决定（期望 {expected_behavior}）"},
            ],
            "timeout_seconds": 180,
            "retry_on_fail": 0,
            "expected_results": rules,
        })


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cases")
