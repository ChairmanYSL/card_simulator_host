#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联调逐用例归因报告生成器（只读数据源，输出 Markdown）。

用法（在仓库根目录执行）::

    python tools/joint_debug/report.py \
        --inputs logs/joint_debug/smoke_1_mastercard.json \
                 logs/joint_debug/smoke_2_visa.json \
                 logs/joint_debug/smoke_3_unionpay.json \
        --baseline logs/joint_debug/run_20260909T114059.json \
        --out logs/joint_debug/full_run_9_report.md

数据源（全部只读，不产生任何写操作）：
  * ``--inputs``  本轮 run 汇总 JSON：``[{run:{run_id,scheme,mode,status,queue,results},
    cases:[{case_id,status,note,failed_checks}]}]``（``logs/joint_debug/smoke_*.json``）
  * 逐用例原始结果：``<runs-root>/<YYYY-MM-DD>/<run_id>/results/<case_id>.json``
    （``raw_result.{resp_code,outcome,online,currency,tags}``、``verdict.status``、``verdict.rules[]``）
  * 卡端 trace：``<runs-root>/<YYYY-MM-DD>/<run_id>/trace/<case_id>.jsonl``
  * ``--baseline`` 上一轮全量基线汇总 JSON（同 inputs 结构，可含多个 run）
  * 用例库：``<cases-root>/*/contactless/cases/**/*.json``（取 ``case_id``/``title``/``expected_results``）

归因规则集中在文件顶部的 ``ATTRIBUTION_RULES``，按 case_id 写死，便于后续维护：
新增/修订一条规则只需改那张表，报告正文（归因类别 / 理由 / 证据）自动跟着变。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# 1. 固定枚举与归因规则表
# --------------------------------------------------------------------------- #

#: 归因类别固定取值（报告“归因类别”列只允许出现这些值，多个用 “+” 连接）
FIXED_CATEGORIES = (
    "案例设置",
    "协议通信",
    "代码逻辑(卡端)",
    "代码逻辑(POS端)",
    "代码逻辑(host)",
    "环境阻塞",
    "设计负例",
)

#: 未命中规则时的占位值
UNATTRIBUTED = "未归因"
PASS_CATEGORY = "-"

#: 按 case_id 写死的归因规则表。
#: categories -> 归因类别（FIXED_CATEGORIES 子集，可多选）
#: reason     -> 一句话结论
#: evidence   -> 证据行（断言 / 内核 retcode / logcat 行 / trace 路径等）
ATTRIBUTION_RULES: Dict[str, Dict[str, Any]] = {
    # ---- 规则按轮次维护：2026-09-09 round #14（POS 1.0.10 + 卡端 1.0.30）---- #
    # VISA / DISC：内核非接离线分支 END(102) 不认 CID=TC —— POS 侧加 CID 兜底
    "OPBD-001-01": {
        "categories": ["代码逻辑(POS端)"],
        "reason": "AID 级 9F1B 已改大（不再误判联机），但 VISA 非接离线分支对 CID=TC 仍 "
                  "END(102)；POS 未做 CID 兜底 → 05/declined。",
        "evidence": [
            "断言：resp_code eq '00' actual='05'；outcome eq 'approved' actual='declined'",
            "POS logcat：VISAEMVCORE [QPBOC_TransProcess - 655] END(102) → "
            "KernelDriver 交易完成 retcode=102 -> resp=05",
            "卡端 GPO/记录已给 9F27=40(TC)；对照 ARQC 走 pCTQ:0 → NXT(22) → retcode=103/approved",
        ],
    },
    "VISA-CL-001": {
        "categories": ["代码逻辑(POS端)"],
        "reason": "同 OPBD-001-01：VISA 102 分支不认 CID=0x40，POS 映射 05/declined；"
                  "用户在 round #14 裁决由 POS 加 CID 兜底（102 + TC → 101/approved/offline）。",
        "evidence": [
            "断言：online eq 'offline' actual='online'（早期）；round #14 变为 resp_code eq '00' actual='05'",
            "POS logcat：gaid 已含 9F1B04000186A0（AID 级生效）；"
            "VISAEMVCORE [QPBOC_TransProcess - 655] END(102) → retcode=102 -> resp=05",
            "回归红线：VISA-CL-002(AAC) 必须保持 declined",
        ],
    },
    "VISA-CL-004": {
        "categories": ["代码逻辑(POS端)"],
        "reason": "同 VISA-CL-001（同 AID/同 102 分支）；该用例未下发 9F1B，说明肇因在 POS 静态 AID 参数 + 102 分支缺兜底。",
        "evidence": [
            "断言：resp_code eq '00' actual='05'",
            "POS logcat：QPBOC_TransProcess END(102) → retcode=102 -> resp=05",
        ],
    },
    "DISC-CL-001": {
        "categories": ["代码逻辑(POS端)"],
        "reason": "卡端 GPO 已给 9F27=40 + 9F26/9F10，内核在通用 qPBOC 分支仍 END(102)；"
                  "对照 PURE 走 PUREEMVCORE 得 101 → 差异在内核路由/AID 参数与 102 分支无 CID 兜底。",
        "evidence": [
            "POS logcat：CONTINUE retcode=7 暂存 9F27=40 → [QPBOC_TransProcess - 624 IAD[10]] → [-684] END(102)",
            "卡端 trace seq=6：7727 82021880 940408010100 9F36020002 9F2608FA.. 9F270140 9F10..  9000",
            "PURE 对照（run #13）：PURE-info CID indicate 40 → emv_process ret=101 → 00/approved",
        ],
    },
    # ---- 卡端 HCE 会话窗口（现场/操作）--------------------------------------- #
    "MC-CL-003": {
        "categories": ["环境阻塞", "代码逻辑(POS端)"],
        "reason": "卡端 HCE 会话 164ms/2 条命令后被拆链，内核第 3 条命令 9s 超时 SW=0000 → -1133，"
                  "从未走到在线；run #13 同用例窗口 918ms 时 PASS。POS 侧建议把 9s 超时降到 1–2s 并重开读卡器。",
        "evidence": [
            "卡端 logcat：onSeListenDeactivated / NFC field deactivated, reason: 0 / "
            "Dropping service response message; service no longer active",
            "POS logcat：icccommand SW1SW2=[0000] → ERR(-1133) → 回传 05/declined",
            "对照：MC-CL-002 窗口 1008ms、MC-CL-004 484ms 本轮 PASS",
        ],
    },
    "AMEX-CL-001": {
        "categories": ["环境阻塞"],
        "reason": "卡端未收到 GPO（会话窗口 231ms/3 条），POS select_ddf 9s 超时 SW=0000 → -1102；"
                  "run #13 窗口 759ms 时 PASS，属现场贴卡时长而非代码缺陷。",
        "evidence": [
            "POS logcat：select_ddf → icccommand [0000] → ERR(-1102)",
            "卡端 logcat：NFC field deactivated, reason: 0（会话过早结束）",
        ],
    },
    "PURE-CL-001": {
        "categories": ["环境阻塞"],
        "reason": "卡端会话窗口 329ms/2 条即拆链，PureKernel 收尾 -103；run #13 窗口 990ms 时 PASS。",
        "evidence": [
            "POS logcat：emvCardType cardType=8 / PureKernel → emvCallback status=0 retcode=-103",
            "卡端 logcat：NFC field deactivated, reason: 0",
        ],
    },
    # ---- 编排 / 超时 --------------------------------------------------------- #
    "MC-CL-001": {
        "categories": ["环境阻塞", "代码逻辑(POS端)"],
        "reason": "本轮根本没开卡：上一笔 UPI-CL-007 结果迟到被 host 隔离，POS 的 ack 门控把已配对的 "
                  "case_started 吞掉（不执行也不重试）→ host 空等到超时。",
        "evidence": [
            "POS logcat：上一笔交易尚未收到结果 ack，继续本次尝试：case=MC-CL-001",
            "此后无 开始人工交易 / reader.open / EMVLIB 行；卡端 trace 仅 1 条 field off",
            "host：上一笔 UPI-CL-007 结果迟到 6s → quarantined",
        ],
    },
    "UPI-CL-007": {
        "categories": ["协议通信", "环境阻塞"],
        "reason": "N12 静默让 POS 内核收尾 185.6s，超过用例当时 180s 超时 → 结果迟到被隔离；"
                  "host 已把 UPI-CL-006/007 超时改为 300s，POS 侧建议给 GPO 无响应加 ≤60s 固定上限。",
        "evidence": [
            "POS logcat：case_started 18:29:55.631 → 内核错误 retcode=-112 于 18:33:01.189（185.6s）",
            "卡端 logcat：CMD CLA=80 INS=A8 后无 RSP（noResponseCommands times=1）",
            "host：用例 timeout_seconds 已 180 → 300",
        ],
    },
    "QPB-I-010-01": {
        "categories": ["代码逻辑(POS端)", "案例设置"],
        "reason": "POS 在“在线无法完成”时内核回 103，POS 映射成 00/approved，与用例“读写器拒绝交易”期望冲突；"
                  "用户裁决由 POS 修（无 DF8101=01 时在线失败 → declined），并给 VISA-CL-003 补 DF8101=01。",
        "evidence": [
            "断言：outcome eq 'declined' actual='approved'；resp_code any_of[05,91,16,80] actual='00'",
            "POS logcat：交易完成 retcode=103 -> resp=00 outcome=approved（卡端按用例回 9F27=80 ARQC）",
            "卡端结论：与 1.0.30「全 CID 带 AC」无关（ARQC 符合用例设置）",
        ],
    },
}


# --------------------------------------------------------------------------- #
# 2. 基础读取工具
# --------------------------------------------------------------------------- #

def _read_json(path: Path) -> Tuple[Any, str]:
    """读 JSON，返回 (对象, 实际编码)。UTF-8 失败时回退 GB18030（用例库存在历史编码文件）。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return json.loads(raw.decode(enc)), enc
        except UnicodeDecodeError:
            continue
    # 最后再试一次，让 JSON 语法错误如实抛出
    return json.loads(raw.decode("utf-8", errors="replace")), "utf-8(replace)"


def _run_date(run_id: str) -> Optional[str]:
    """run_id 形如 20260909T154047_04027efc -> 2026-09-09。"""
    m = run_id[:8]
    if len(m) == 8 and m.isdigit():
        return f"{m[0:4]}-{m[4:6]}-{m[6:8]}"
    return None


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _cell(text: Any, limit: int = 160) -> str:
    """Markdown 单元格：转义竖线、压平换行、超长截断。"""
    if text is None:
        return "-"
    s = str(text).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    if not s:
        return "-"
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def _pct(hit: int, total: int) -> str:
    if total <= 0:
        return "-"
    return f"{hit * 100.0 / total:.1f}%"


# --------------------------------------------------------------------------- #
# 3. 数据装载
# --------------------------------------------------------------------------- #

def load_case_library(cases_root: Path) -> Dict[str, Dict[str, Any]]:
    """扫描用例库，返回 case_id -> 用例元数据（title/expected_results/文件路径/修改时间）。"""
    lib: Dict[str, Dict[str, Any]] = {}
    if not cases_root.is_dir():
        return lib
    for path in sorted(cases_root.glob("*/contactless/cases/**/*.json")):
        if not path.is_file():
            continue
        try:
            obj, enc = _read_json(path)
        except (OSError, ValueError) as exc:  # 坏文件不应打断报告
            print(f"[warn] 用例库文件解析失败，已跳过: {path} ({exc})", file=sys.stderr)
            continue
        if not isinstance(obj, dict):
            continue
        case_id = obj.get("case_id")
        if not case_id:  # group.json 之类没有 case_id
            continue
        lib[str(case_id)] = {
            "title": obj.get("title") or "",
            "scheme": obj.get("scheme") or "",
            "expected_results": obj.get("expected_results") or [],
            "ac_behavior": (obj.get("profile") or {}).get("acBehavior"),
            "path": str(path),
            "mtime": path.stat().st_mtime,
            "encoding": enc,
        }
    return lib


def find_run_dir(runs_root: Path, run_id: str) -> Optional[Path]:
    """按 run_id 定位 runs/<日期>/<run_id>/；日期推断失败时全局兜底扫描。"""
    date = _run_date(run_id)
    if date:
        candidate = runs_root / date / run_id
        if candidate.is_dir():
            return candidate
    if runs_root.is_dir():
        for candidate in sorted(runs_root.glob(f"*/{run_id}")):
            if candidate.is_dir():
                return candidate
    return None


def load_case_result(run_dir: Optional[Path], case_id: str) -> Dict[str, Any]:
    """读取 runs/<日期>/<run_id>/results/<case_id>.json，缺失时返回空结构。"""
    empty = {
        "found": False,
        "path": None,
        "status": None,
        "note": None,
        "resp_code": None,
        "outcome": None,
        "online": None,
        "currency": None,
        "tags": {},
        "failed_rules": [],
        "verdict_status": None,
    }
    if run_dir is None:
        return empty
    path = run_dir / "results" / f"{case_id}.json"
    if not path.is_file():
        return empty
    try:
        obj, _ = _read_json(path)
    except (OSError, ValueError) as exc:
        print(f"[warn] 结果文件解析失败: {path} ({exc})", file=sys.stderr)
        return empty
    if not isinstance(obj, dict):
        return empty

    raw = obj.get("raw_result") or {}
    tags = raw.get("tags") or {}
    verdict = obj.get("verdict") or {}
    failed_rules = []
    for rule in verdict.get("rules") or []:
        if isinstance(rule, dict) and rule.get("ok") is False:
            failed_rules.append(
                rule.get("message")
                or f"{rule.get('path')} {rule.get('op')} expect={rule.get('expect')!r} actual={rule.get('actual')!r}"
            )
    meta = obj.get("pos_meta") or {}
    return {
        "found": True,
        "path": str(path),
        "status": obj.get("status"),
        "note": obj.get("note"),
        "resp_code": raw.get("resp_code"),
        "outcome": raw.get("outcome"),
        "online": raw.get("online"),
        "currency": raw.get("currency"),
        "tags": tags if isinstance(tags, dict) else {},
        "failed_rules": failed_rules,
        "verdict_status": verdict.get("status"),
        # 诊断字段（docs/protocol.md §7，可缺失）
        "app_version": raw.get("app_version") or meta.get("app_version"),
        "kernel_retcode": raw.get("kernel_retcode"),
        "kernel_stage": raw.get("kernel_stage"),
        "card_present_ms": raw.get("card_present_ms"),
    }


def load_trace(run_dir: Optional[Path], case_id: str) -> Dict[str, Any]:
    """读取卡端 trace jsonl，汇总 field/APDU 概览，供报告自动取证。"""
    summary: Dict[str, Any] = {
        "found": False,
        "path": None,
        "events": 0,
        "apdu_cmds": 0,
        "has_gpo": False,
        "has_gen_ac": False,
        "field_on": False,
        "field_off": False,
        "sw": [],
    }
    if run_dir is None:
        return summary
    path = run_dir / "trace" / f"{case_id}.jsonl"
    if not path.is_file():
        return summary
    summary["found"] = True
    summary["path"] = str(path)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            msg = record.get("message") or {}
            summary["events"] += 1
            event = msg.get("event")
            if event == "field":
                if msg.get("direction") == "on":
                    summary["field_on"] = True
                elif msg.get("direction") == "off":
                    summary["field_off"] = True
            elif event == "apdu" and msg.get("direction") == "COMMAND":
                summary["apdu_cmds"] += 1
                ins = msg.get("ins")
                if ins == 0xA8:
                    summary["has_gpo"] = True
                elif ins == 0xAE:
                    summary["has_gen_ac"] = True
            sw = msg.get("sw")
            if sw and sw not in summary["sw"]:
                summary["sw"].append(sw)
    return summary


def trace_digest(summary: Dict[str, Any]) -> str:
    """把 trace 概览压成一句人读的取证文字。"""
    if not summary.get("found"):
        return "无卡端 trace"
    bits = [f"{summary['events']} 事件", f"{summary['apdu_cmds']} 条 APDU 命令"]
    bits.append("有 GPO(80A8)" if summary["has_gpo"] else "无 GPO(80A8)")
    bits.append("有 GENERATE AC(80AE)" if summary["has_gen_ac"] else "无 GENERATE AC(80AE)")
    if summary["field_on"] and summary["field_off"]:
        bits.append("field on→off")
    elif summary["field_on"]:
        bits.append("field on")
    if summary["sw"]:
        bits.append("SW=" + "/".join(summary["sw"][:4]))
    return "、".join(bits)


# --------------------------------------------------------------------------- #
# 4. 归因判定
# --------------------------------------------------------------------------- #

def classify(case_id: str, status: str) -> Tuple[List[str], str, List[str]]:
    """返回 (归因类别列表, 理由, 静态证据列表)。PASS 一律 '-'。"""
    if status == "PASS":
        return [PASS_CATEGORY], "", []
    rule = ATTRIBUTION_RULES.get(case_id)
    if rule is None:
        return [UNATTRIBUTED], "规则表中没有该 case_id，需补充归因规则。", []
    categories = [c for c in rule.get("categories", []) if c in FIXED_CATEGORIES]
    if not categories:
        categories = [UNATTRIBUTED]
    return list(categories), str(rule.get("reason", "")), [str(e) for e in rule.get("evidence", [])]


def build_entries(inputs: Sequence[Path], runs_root: Path, cases_root: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把本轮 inputs 展开成逐用例条目；返回 (条目列表, run 概要列表)。"""
    lib = load_case_library(cases_root)
    entries: List[Dict[str, Any]] = []
    run_infos: List[Dict[str, Any]] = []

    for path in inputs:
        obj, _ = _read_json(path)
        blocks = obj if isinstance(obj, list) else [obj]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            run = block.get("run") or {}
            run_id = str(run.get("run_id") or "")
            scheme = str(run.get("scheme") or "")
            run_dir = find_run_dir(runs_root, run_id)
            run_finished_at = run.get("finished_at")
            cases = block.get("cases") or []
            results = run.get("results") or {}
            run_infos.append(
                {
                    "run_id": run_id,
                    "scheme": scheme,
                    "mode": run.get("mode"),
                    "status": run.get("status"),
                    "cases": len(cases),
                    "source": str(path),
                    "run_dir": str(run_dir) if run_dir else None,
                }
            )
            for case in cases:
                if not isinstance(case, dict):
                    continue
                case_id = str(case.get("case_id") or "")
                status = str(case.get("status") or (results.get(case_id) or {}).get("status") or "UNKNOWN")
                result = load_case_result(run_dir, case_id)
                trace = load_trace(run_dir, case_id)
                meta = lib.get(case_id) or {}
                categories, reason, evidence = classify(case_id, status)
                entries.append(
                    {
                        "case_id": case_id,
                        "scheme": scheme or meta.get("scheme") or "-",
                        "run_id": run_id,
                        "status": status,
                        "note": case.get("note") or result.get("note") or "",
                        "failed_checks": [str(c) for c in (case.get("failed_checks") or [])],
                        "result": result,
                        "trace": trace,
                        "trace_digest": trace_digest(trace),
                        "case_title": meta.get("title") or "",
                        "case_path": meta.get("path"),
                        "case_mtime": meta.get("mtime"),
                        "run_finished_at": run_finished_at,
                        "case_ac_behavior": meta.get("ac_behavior"),
                        "categories": categories,
                        "reason": reason,
                        "evidence": evidence,
                    }
                )
    return entries, run_infos


def _diagnostics_line(result: Dict[str, Any]) -> str:
    """把 POS 上报的诊断字段（docs/protocol.md §7）拼成一行；缺失返回空串。"""
    parts = []
    for key, label in (("app_version", "app_version"),
                       ("kernel_retcode", "kernel_retcode"),
                       ("kernel_stage", "kernel_stage"),
                       ("card_present_ms", "card_present_ms")):
        value = result.get(key)
        if value not in (None, "", []):
            parts.append(f"`{label}`={value}")
    return "、".join(parts)


def load_baseline(path: Optional[Path]) -> Tuple[Dict[str, str], List[str]]:
    """基线汇总 JSON -> ({case_id: status}, [run_id...])。"""
    if path is None or not path.is_file():
        return {}, []
    obj, _ = _read_json(path)
    blocks = obj if isinstance(obj, list) else [obj]
    status_map: Dict[str, str] = {}
    run_ids: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        run = block.get("run") or {}
        if run.get("run_id"):
            run_ids.append(str(run["run_id"]))
        for case in block.get("cases") or []:
            if isinstance(case, dict) and case.get("case_id"):
                status_map[str(case["case_id"])] = str(case.get("status") or "-")
    return status_map, run_ids


def _case_drift_note(entry: Dict[str, Any]) -> str:
    """用例库文件在本轮 run 结束之后被改动时给出提示（判定仍按 run 当时的定义）。"""
    mtime = entry.get("case_mtime")
    finished = entry.get("run_finished_at")
    if not mtime or not finished:
        return ""
    try:
        if float(mtime) <= float(finished) + 60:  # 1 分钟容差
            return ""
    except (TypeError, ValueError):
        return ""
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        "用例库漂移：用例文件 mtime "
        f"{datetime.fromtimestamp(float(mtime)).strftime(fmt)} 晚于本轮 run 结束 "
        f"{datetime.fromtimestamp(float(finished)).strftime(fmt)}，"
        "本轮判定按 run 当时的用例定义执行，复核时需按最新用例库重跑。"
    )


# --------------------------------------------------------------------------- #
# 5. Markdown 渲染
# --------------------------------------------------------------------------- #

def render_report(
    entries: Sequence[Dict[str, Any]],
    run_infos: Sequence[Dict[str, Any]],
    baseline_map: Dict[str, str],
    baseline_runs: Sequence[str],
    baseline_path: Optional[Path],
    inputs: Sequence[Path],
    label: str,
) -> str:
    now = datetime.now().astimezone()
    out: List[str] = []
    add = out.append

    # ---------- 1. 标题 / 数据来源 ------------------------------------------ #
    add(f"# 联调逐用例归因报告 — {label}")
    add("")
    add("## 1. 概览")
    add("")
    add(f"- 生成时间：{now.strftime('%Y-%m-%d %H:%M:%S %z')}")
    add("- 生成脚本：`tools/joint_debug/report.py`（只读数据源，标准库实现）")
    add("")
    add("### 数据来源（本轮）")
    add("")
    add("| 卡组 | run_id | 状态 | 用例数 | 汇总文件 | run 目录 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for info in run_infos:
        add(
            f"| {_cell(info['scheme'])} | `{_cell(info['run_id'])}` | {_cell(info['status'])} | "
            f"{info['cases']} | `{_cell(info['source'])}` | `{_cell(info['run_dir'])}` |"
        )
    add("")
    add("### 基线对照")
    add("")
    if baseline_path is not None:
        add(f"- 基线文件：`{baseline_path}`")
    add(f"- 基线 run_id（{len(baseline_runs)} 个）：" + ("、".join(f"`{r}`" for r in baseline_runs) or "-"))
    add("- 基线口径：卡端 1.0.29 + POS 1.0.2 首版的全量结果，作为“基线(run#8)”列。")
    add("")
    add("### 设备版本（优先取真实上报值）")
    add("")
    real_versions = sorted({str((entry.get("result") or {}).get("app_version"))
                            for entry in entries
                            if (entry.get("result") or {}).get("app_version")})
    if real_versions:
        add(f"- POS 上报的 `app_version`：{', '.join('`' + v + '`' for v in real_versions)}"
            "（来自 `result.app_version` / `pos_meta.app_version`，见 `docs/protocol.md` §7）。")
    else:
        add("- 本轮逐用例结果里 `app_version` 为空（POS 尚未上报该诊断字段）。")
    add("- 卡端版本以 run 期间的 `/api/health` 与设备 `dumpsys` 为准；"
        "本轮明细请见 §6 人工核对章节。")
    add("")

    # ---------- 2. 汇总表 ---------------------------------------------------- #
    add("## 2. 汇总（按卡组）")
    add("")
    add("| 卡组 | 用例数 | PASS | FAIL | INCONCLUSIVE | 其中设计负例 | 通过率(含负例) | 通过率(不含负例) |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")

    scheme_order: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        if entry["scheme"] not in grouped:
            grouped[entry["scheme"]] = []
            scheme_order.append(entry["scheme"])
        grouped[entry["scheme"]].append(entry)

    totals = {"n": 0, "pass": 0, "fail": 0, "incon": 0, "neg": 0}
    for scheme in scheme_order:
        rows = grouped[scheme]
        n = len(rows)
        n_pass = sum(1 for r in rows if r["status"] == "PASS")
        n_fail = sum(1 for r in rows if r["status"] == "FAIL")
        n_incon = sum(1 for r in rows if r["status"] not in ("PASS", "FAIL"))
        n_neg = sum(1 for r in rows if "设计负例" in r["categories"])
        totals["n"] += n
        totals["pass"] += n_pass
        totals["fail"] += n_fail
        totals["incon"] += n_incon
        totals["neg"] += n_neg
        add(
            f"| {_cell(scheme)} | {n} | {n_pass} | {n_fail} | {n_incon} | {n_neg} | "
            f"{_pct(n_pass, n)} | {_pct(n_pass, n - n_neg)} |"
        )
    add(
        f"| **合计** | {totals['n']} | {totals['pass']} | {totals['fail']} | {totals['incon']} | "
        f"{totals['neg']} | {_pct(totals['pass'], totals['n'])} | "
        f"{_pct(totals['pass'], totals['n'] - totals['neg'])} |"
    )
    add("")
    add(
        f"- 口径说明：`通过率(含负例) = PASS / 全部用例`；`通过率(不含负例) = PASS / (全部用例 − 设计负例)`。"
        f"设计负例共 {totals['neg']} 条，按设计必然 FAIL，不计入“不含负例”分母。"
    )
    # 与基线的状态迁移（只列方向性变化，便于一眼看出回归/收敛）
    if baseline_map:
        better = [e["case_id"] for e in entries
                  if baseline_map.get(e["case_id"]) not in (None, "—") and baseline_map.get(e["case_id"]) != e["status"]
                  and e["status"] == "PASS"]
        worse = [e["case_id"] for e in entries
                 if baseline_map.get(e["case_id"]) == "PASS" and e["status"] != "PASS"]
        add(f"- 基线→本轮 转好（PASS）：{'、'.join(f'`{c}`' for c in better) if better else '无'}")
        add(f"- 基线→本轮 转差：{'、'.join(f'`{c}`' for c in worse) if worse else '无'}")
    add("")

    # ---------- 3. 逐用例表 -------------------------------------------------- #
    add("## 3. 逐用例")
    add("")
    add("| case_id | 卡组 | 本轮 | 基线(run#8) | resp/outcome/online | 诊断(retcode/stage/在场ms) | 失败断言 | 归因类别 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for entry in entries:
        result = entry["result"]
        if result.get("found") and result.get("resp_code") is not None:
            roo = f"{_fmt_num(result['resp_code'])}/{_fmt_num(result['outcome'])}/{_fmt_num(result['online'])}"
        else:
            roo = "-"
        failed = "; ".join(entry["failed_checks"]) or (entry["note"] or "-")
        diagnostic = " / ".join(
            str(v) for v in (result.get("kernel_retcode"),
                             result.get("kernel_stage"),
                             result.get("card_present_ms")) if v not in (None, "")) or "-"
        add(
            f"| `{_cell(entry['case_id'])}` | {_cell(entry['scheme'])} | {_cell(entry['status'])} | "
            f"{_cell(baseline_map.get(entry['case_id'], '—'))} | {_cell(roo)} | "
            f"{_cell(diagnostic, 80)} | {_cell(failed, 200)} | "
            f"{_cell('+'.join(entry['categories']))} |"
        )
    add("")
    add("> 归因类别固定取值：`案例设置` / `协议通信` / `代码逻辑(卡端)` / `代码逻辑(POS端)` / "
        "`代码逻辑(host)` / `环境阻塞` / `设计负例`；PASS 记 `-`。")
    add("")

    # ---------- 4. 归因明细 -------------------------------------------------- #
    add("## 4. 归因明细")
    add("")
    detail_rows = [e for e in entries if e["status"] != "PASS"]
    if not detail_rows:
        add("- 本轮无非 PASS 用例。")
    for entry in detail_rows:
        result = entry["result"]
        add(f"### {entry['case_id']}（{entry['scheme']} · 本轮 {entry['status']} · 基线 "
            f"{baseline_map.get(entry['case_id'], '—')}）")
        add("")
        add(f"- **归因类别**：`{' + '.join(entry['categories'])}`")
        if entry["case_title"]:
            add(f"- **用例标题**：{_cell(entry['case_title'], 200)}")
        add(f"- **结论**：{_cell(entry['reason'], 400)}")
        add("- **证据**：")
        for check in entry["failed_checks"]:
            add(f"  - 失败断言：`{check}`")
        if not entry["failed_checks"] and entry["note"]:
            add(f"  - 结果备注：`{entry['note']}`")
        if result.get("found"):
            add(f"  - 结果文件：`{result['path']}`"
                f"（status={_fmt_num(result.get('status'))}"
                f"，resp_code={_fmt_num(result.get('resp_code'))}"
                f"，outcome={_fmt_num(result.get('outcome'))}"
                f"，online={_fmt_num(result.get('online'))}）")
            if result.get("failed_rules"):
                add(f"  - verdict 未通过规则 {len(result['failed_rules'])} 条："
                    + "；".join(f"`{_cell(m, 120)}`" for m in result["failed_rules"][:6]))
        else:
            add("  - 结果文件：无（未产出 raw_result/verdict）")
        add(f"  - 卡端 trace：`{entry['trace']['path'] or '无'}`（{entry['trace_digest']}）")
        if entry.get("case_path"):
            add(f"  - 用例文件：`{entry['case_path']}`")
        drift = _case_drift_note(entry)
        if drift and not any("用例库漂移" in line for line in entry["evidence"]):
            add(f"  - {drift}")
        for line in entry["evidence"]:
            add(f"  - 规则证据：{line}")
        diagnostics = _diagnostics_line(entry.get("result") or {})
        if diagnostics:
            add(f"  - 诊断字段：{diagnostics}")
        add("")

    # 规则表覆盖度自查
    covered = {e["case_id"] for e in entries}
    unused = sorted(k for k in ATTRIBUTION_RULES if k not in covered)
    if unused:
        add("### 规则表中本轮未覆盖的条目")
        add("")
        add("以下 case_id 有写死的归因规则，但不在本轮 `--inputs` 里（可作为跨轮对照）：")
        add("")
        for case_id in unused:
            rule = ATTRIBUTION_RULES[case_id]
            add(f"- `{case_id}` → `{' + '.join(rule['categories'])}`：{_cell(rule['reason'], 260)}")
        add("")

    # 迟到/未归属结果（quarantine.jsonl）：run 超时后 POS 才回传的结果会被归档、
    # 不参与判定；这里单列出来，便于区分"真的没跑"与"跑完但迟到"。
    quarantined: List[Dict[str, Any]] = []
    for info in run_infos:
        run_dir = info.get("run_dir")
        if not run_dir:
            continue
        path = Path(run_dir) / "quarantine.jsonl"
        if not path.is_file():
            continue
        try:
            rows = [json.loads(line) for line in
                    path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip()]
        except (OSError, ValueError):
            rows = []
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("run_id", info.get("run_id"))
                # quarantine.jsonl 的形态：{at, reason, frame:{case_id, attempt_id, result}}
                frame = row.get("frame") if isinstance(row.get("frame"), dict) else {}
                for key in ("case_id", "attempt_id", "result"):
                    if key not in row and key in frame:
                        row[key] = frame[key]
                quarantined.append(row)
    add("### 迟到 / 未归属结果（quarantine）")
    add("")
    if not quarantined:
        add("本轮无 `quarantine.jsonl` 记录（没有迟到或未归属的 `transaction_result`）。")
    else:
        add(f"本轮共 **{len(quarantined)}** 条迟到/未归属结果（**不参与判定**，"
            "通常意味着该用例在 host 侧已超时，POS 随后才回传）：")
        add("")
        add("| run_id | case_id | attempt_id | resp/outcome | 说明 |")
        add("| --- | --- | --- | --- | --- |")
        for row in quarantined:
            result = row.get("result") or {}
            add(f"| `{row.get('run_id')}` | `{row.get('case_id') or '-'}` | "
                f"`{row.get('attempt_id') or '-'}` | "
                f"{result.get('resp_code', '-')}/{result.get('outcome', '-')} | "
                f"{_cell(row.get('reason') or row.get('note') or '迟到/未归属', 60)} |")
    add("")

    # ---------- 5. 下一步 ---------------------------------------------------- #
    add("## 5. 下一步")
    add("")
    add("1. **重跑全量（28 例）**：等 POS 部署含 `DF8101=01` 的版本后，按 "
        "`mastercard(5) → visa(9) → unionpay(13)` 驱动一轮，run 期间由 "
        "`run_cases.py` 自动抓双端 logcat（`logs/joint_debug/logcat/<stamp>-{pos,card}.log`）。")
    add("2. **已裁决并落库（2026-09-09，用户）**：")
    add("   - `UPI-CL-007`（N12）→ 终端超时/declined 即 PASS（`any_of[80,91,05]` + `declined`），卡端不改；")
    add("   - 在线授权 → 仅当 host 下发 `DF8101=01` 时 POS 返回占位 ARPC 模拟批准；"
        "`QPB-B-002-01` 另补 `9F66=36804000`；")
    add("   - 设计负例 `VISA-CL-005 / UPI-CL-005 / MC-CL-005` → 已从用例库删除（31 → 28）。")
    add("3. **回归重点**：`MC-CL-001/004`（PayPass gen_ac 9F26/9F10 + 5F2A 0156）、"
        "`VISA-CL-001`（EMV_REQ_CONFIRM_FORCE_ONLINE）、`MC-CL-003 / UPI-CL-003 / QPB-B-002-01`"
        "（DF8101 在线授权 + 9F66 覆盖）、`AMEX/DISC/PURE-CL-001`（卡端时序，需双端 logcat 交叉验证）。")
    add("4. 重跑后用本脚本重新生成报告（`ATTRIBUTION_RULES` 已按新裁决更新），并更新 `docs/CHANGELOG.md`。")
    add("")
    add("---")
    add("")
    add("本报告由 `tools/joint_debug/report.py` 生成；归因规则表位于脚本顶部 `ATTRIBUTION_RULES`，"
        "按 case_id 维护，修改后重跑即可。")
    add("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 6. CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report.py",
        description="联调逐用例归因报告生成器：读本轮 run 汇总 + 逐用例结果 + 卡端 trace + 用例库 + 基线，输出 Markdown。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/joint_debug/report.py \\\n"
            "      --inputs logs/joint_debug/smoke_1_mastercard.json \\\n"
            "               logs/joint_debug/smoke_2_visa.json \\\n"
            "               logs/joint_debug/smoke_3_unionpay.json \\\n"
            "      --baseline logs/joint_debug/run_20260909T114059.json \\\n"
            "      --out logs/joint_debug/full_run_9_report.md\n"
        ),
    )
    parser.add_argument("--inputs", nargs="+", required=True, metavar="JSON",
                        help="本轮 run 汇总 JSON（可多个，按给定顺序进入报告）")
    parser.add_argument("--baseline", metavar="JSON", default=None,
                        help="上一轮全量基线汇总 JSON（可选），用于“基线(run#8)”列")
    parser.add_argument("--out", required=True, metavar="MD", help="输出 Markdown 路径")
    parser.add_argument("--runs-root", default="runs", metavar="DIR",
                        help="runs 根目录（默认 runs），用于定位 results/<case>.json 与 trace/<case>.jsonl")
    parser.add_argument("--cases-root", default="cases", metavar="DIR",
                        help="用例库根目录（默认 cases），用于读取 title/expected_results")
    parser.add_argument("--label", default=None, metavar="NAME",
                        help="报告标签（默认取 --out 的文件名）")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # 中文输出：Git Bash / UTF-8 终端按 UTF-8 写出，避免 cp936 控制台乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)

    input_paths = [Path(p) for p in args.inputs]
    missing = [str(p) for p in input_paths if not p.is_file()]
    if missing:
        print(f"[error] 找不到 --inputs 文件: {', '.join(missing)}", file=sys.stderr)
        return 2
    baseline_path = Path(args.baseline) if args.baseline else None
    if baseline_path is not None and not baseline_path.is_file():
        print(f"[warn] 基线文件不存在，跳过基线列: {baseline_path}", file=sys.stderr)
        baseline_path = None

    out_path = Path(args.out)
    label = args.label or out_path.stem
    if label.endswith("_report"):
        label = label[: -len("_report")]
    runs_root = Path(args.runs_root)
    cases_root = Path(args.cases_root)

    entries, run_infos = build_entries(input_paths, runs_root, cases_root)
    baseline_map, baseline_runs = load_baseline(baseline_path)

    markdown = render_report(
        entries, run_infos, baseline_map, baseline_runs, baseline_path, input_paths, label
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(markdown)

    n_pass = sum(1 for e in entries if e["status"] == "PASS")
    n_fail = sum(1 for e in entries if e["status"] == "FAIL")
    n_other = len(entries) - n_pass - n_fail
    print(f"报告已生成: {out_path}")
    print(f"  用例 {len(entries)} 条: PASS {n_pass} / FAIL {n_fail} / 其它 {n_other}")
    print(f"  覆盖 run: {', '.join(i['run_id'] for i in run_infos)}")
    if baseline_path is not None:
        print(f"  基线: {baseline_path}（{len(baseline_runs)} 个 run）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
