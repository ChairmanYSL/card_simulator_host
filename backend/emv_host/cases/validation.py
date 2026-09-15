"""Card profile sanity checks (run at load time), aligned with the Android
app's import validation rules (v1.1 CardProfile schema).

Host-consumed subset checks:
* ``pan``: digits only (non-digits ignored by the app); Luhn validated;
* ``applicationExpirationDate``: YYMMDD digits when present;
* hex fields (aip/afl/track2EquivalentData/...): even length;
* ``afl``: length must be a multiple of 4 and every entry first<=last;
* ``records[]``: sfi in [1,31], record >= 1; when records is non-empty every
  AFL (sfi, record) should have a matching config (missing -> 0x6A83 in the
  app, so it may be intentional: reported as a warning);
* ``aids[]``: aid hex even-length;
* ``acBehavior``: invalid values are tolerated (follow terminal P1) but
  flagged as a warning;
* embedded keys bundle (in a case JSON): key values must be valid base64;
* pos_params (scheme template & per-case): tag format is enforced by the
  PosParam model itself; duplicate tags are flagged here as a warning.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .models import CardProfile, KeysBundle, KernelParams, PosParam
from ..protocol.kernel_tlv import (
    MAX_EXPONENT_BYTES,
    MAX_MODULUS_BYTES,
    ascii_date_ok,
    compute_capk_checksum,
    decode_expiry,
    decode_tlv_map,
    encode_expiry,
)

__all__ = [
    "Issue", "Report", "luhn_ok", "ppse_tlv_ok", "validate_keys",
    "validate_kernel_params", "validate_pos_params", "validate_profile",
]

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX_FIELDS = ("aip", "afl", "track2EquivalentData", "issuerApplicationData",
               "issuerCountryCode", "pdol", "cdol1", "cdol2", "tdol", "ddol")
_AC_BEHAVIORS = {"online_request", "offline_approved", "offline_declined"}
_PPSE_MODES = {"auto", "disabled", "empty", "custom"}
# v1.1 N12: command-name registry for noResponseCommands (silence behavior)
_NO_RESPONSE_COMMANDS = {
    "PPSE", "SELECT", "GPO", "READ_RECORD", "GET_DATA", "GENERATE_AC", "VERIFY",
}
# TLV skeleton containers used by the ppseData consistency check
_PPSE_CONTAINERS = {0x6F, 0xA5, 0x61, 0xBF0C}


@dataclass
class Issue:
    profile_id: str
    severity: str  # error | warning
    message: str


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    def add(self, profile_id: str, severity: str, message: str) -> None:
        self.issues.append(Issue(profile_id, severity, message))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


def luhn_ok(pan: str) -> bool:
    digits = [c for c in pan if c.isdigit()]
    if len(digits) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_hex(value: str) -> bool:
    return len(value) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in value)


def _parse_afl(afl: str) -> list[tuple[int, int]]:
    """Parse an AFL hex string into (SFI, record) pairs."""
    pairs: list[tuple[int, int]] = []
    if not _is_hex(afl):
        return pairs
    raw = bytes.fromhex(afl)
    for i in range(0, len(raw), 4):
        if i + 4 > len(raw):
            break
        b0, b1, b2, _ = raw[i:i + 4]
        sfi = (b0 >> 3) & 0x1F
        for rec in range(b1, b2 + 1):
            pairs.append((sfi, rec))
    return pairs


def validate_profile(profile: CardProfile) -> Report:
    report = Report()
    pid = profile.id

    pan_digits = "".join(c for c in profile.pan if c.isdigit())
    if profile.pan and pan_digits != profile.pan:
        report.add(pid, "warning", f"PAN 含非数字字符（应用会忽略）：{profile.pan!r}")
    if pan_digits and not luhn_ok(pan_digits):
        report.add(pid, "error", f"PAN 未通过 Luhn 校验：{profile.pan!r}")

    expiry = profile.applicationExpirationDate
    if expiry and not (len(expiry) == 6 and expiry.isdigit()):
        report.add(pid, "error",
                   f"applicationExpirationDate 须为 YYMMDD 六位数字：{expiry!r}")

    for key in _HEX_FIELDS:
        value = getattr(profile, key)
        if value and not _is_hex(value):
            report.add(pid, "error", f"{key} 不是偶数长度 hex：{value!r}")

    afl = profile.afl
    if afl:
        if len(afl) % 8 != 0:
            report.add(pid, "error", f"afl 长度必须是 4N（{afl!r}）")
        else:
            raw = bytes.fromhex(afl)
            for i in range(0, len(raw), 4):
                _b0, first, last, _ = raw[i:i + 4]
                if first > last:
                    report.add(pid, "error",
                               f"afl 条目 firstRecord({first}) > lastRecord({last})")

    seen_records: set[tuple[int, int]] = set()
    for cfg in profile.records:
        if cfg.sfi < 1 or cfg.sfi > 31:
            report.add(pid, "error", f"records[].sfi 须在 [1,31]：{cfg.sfi}")
        key = (cfg.sfi, cfg.record)
        if key in seen_records:
            report.add(pid, "error", f"records[] 重复配置 (sfi={cfg.sfi}, record={cfg.record})")
        seen_records.add(key)

    # AFL -> records consistency (only meaningful when records is configured)
    if afl and profile.records:
        configured = {(cfg.sfi, cfg.record) for cfg in profile.records}
        missing = [pair for pair in _parse_afl(afl) if pair not in configured]
        if missing:
            report.add(pid, "warning",
                       f"AFL 指向未配置的记录 {missing}（应用中该 (sfi,record) 将回 0x6A83）")

    for cfg in profile.aids:
        if not _is_hex(cfg.aid):
            report.add(pid, "error", f"aids[].aid 不是偶数长度 hex：{cfg.aid!r}")

    if profile.acBehavior and profile.acBehavior not in _AC_BEHAVIORS:
        report.add(pid, "warning",
                   f"acBehavior={profile.acBehavior!r} 非法，按缺省（跟随终端 P1）处理")

    # PPSE behavior overrides (v1.1 N11)
    if profile.ppseMode and profile.ppseMode not in _PPSE_MODES:
        report.add(pid, "warning",
                   f"ppseMode={profile.ppseMode!r} 非法，按缺省 auto 处理")
    if profile.ppseSw and not re.fullmatch(r"[0-9A-F]{4}", profile.ppseSw):
        report.add(pid, "warning",
                   f"ppseSw 须为大写 4 位 hex：{profile.ppseSw!r}")
    if profile.ppseData and not _is_hex(profile.ppseData):
        report.add(pid, "warning",
                   f"ppseData 不是偶数长度 hex：{profile.ppseData!r}")
    elif profile.ppseData:
        ok, tlv_message = ppse_tlv_ok(profile.ppseData)
        if not ok:
            report.add(pid, "warning",
                       f"ppseData TLV 长度不自洽（{tlv_message}）")

    # no-response (silence) commands (v1.1 N12)
    seen_nr: set[str] = set()
    for item in profile.noResponseCommands:
        if isinstance(item, str):
            name = item
            times = None
        else:
            name = item.command
            times = item.times
        if name not in _NO_RESPONSE_COMMANDS:
            report.add(pid, "warning",
                       f"noResponseCommands 指令 {name!r} 不在注册表 "
                       f"{sorted(_NO_RESPONSE_COMMANDS)}，安卓侧将忽略（host 放行）")
        if name in seen_nr:
            report.add(pid, "warning",
                       f"noResponseCommands 重复配置指令 {name!r}")
        seen_nr.add(name)
        if times is not None and times < 1:
            report.add(pid, "warning",
                       f"noResponseCommands {name!r} times 须 >= 1（缺省=每次都静默）")

    return report


# -- ppseData TLV skeleton check ---------------------------------------------

def ppse_tlv_ok(hex_data: str) -> tuple[bool, str]:
    """Structural sanity check of a PPSE FCI TLV template.

    Walks container tags (6F/A5/BF0C/61) recursively and verifies every
    declared length matches the actual bytes (single-byte and 2-byte tags,
    short and long length forms).  Catches hand-transcription bugs such as
    ``50 06`` with only 4 bytes of data, or an outer length that is too big.
    """
    if not _is_hex(hex_data):
        return False, "not even-length hex"
    data = bytes.fromhex(hex_data)

    def walk(value: bytes) -> tuple[bool, str]:
        pos = 0
        end = len(value)
        while pos < end:
            t0 = value[pos]
            if t0 & 0x1F == 0x1F:  # 2-byte tag (BF0C ...)
                if pos + 1 >= end:
                    return False, "truncated tag"
                tag = (t0 << 8) | value[pos + 1]
                pos += 2
            else:
                tag = t0
                pos += 1
            if pos >= end:
                return False, "truncated length"
            l0 = value[pos]
            pos += 1
            if l0 & 0x80:  # long-form length
                nbytes = l0 & 0x7F
                if nbytes == 0 or nbytes > 4 or pos + nbytes > end:
                    return False, "bad long-form length"
                length = int.from_bytes(value[pos:pos + nbytes], "big")
                pos += nbytes
            else:
                length = l0
            if pos + length > end:
                return False, f"length {length} overruns buffer at byte {pos}"
            inner = value[pos:pos + length]
            if tag in _PPSE_CONTAINERS:
                ok, message = walk(inner)
                if not ok:
                    return False, message
            pos += length
        return True, ""

    if not data:
        return True, ""
    return walk(data)


def _check_b64(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        base64.b64decode(value, validate=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def validate_keys(keys: KeysBundle, owner: str) -> Report:
    """Coarse key-bundle checks (host transports keys verbatim afterwards)."""
    report = Report()

    def check_dict(item: Any, kind: str) -> None:
        if not isinstance(item, dict):
            report.add(owner, "error", f"{kind} 条目必须是对象")
            return
        kid = item.get("id", "")
        if not (isinstance(kid, str) and _ID_PATTERN.match(kid)):
            report.add(owner, "error",
                       f"{kind} 的 id 须匹配 {_ID_PATTERN.pattern!r}：{kid!r}")
        for value_field in ("key_data", "public_key", "private_key"):
            value = item.get(value_field)
            if value is not None and not _check_b64(value):
                report.add(owner, "error",
                           f"{kind} {kid!r} 的 {value_field} 不是合法 base64")

    for item in keys.symmetric_keys:
        check_dict(item, "symmetric_keys")
    for item in keys.rsa_keys:
        check_dict(item, "rsa_keys")
    for name, value in (keys.certificates or {}).items():
        if value is not None and not _check_b64(value):
            report.add(owner, "error",
                       f"certificates.{name} 不是合法 base64")
    return report


def validate_pos_params(params: list[PosParam], owner: str) -> Report:
    """Per-case / per-scheme POS transaction parameter checks.

    Tag *format* and length limits are enforced by the PosParam model (raises
    at parse time, which surfaces as an invalid case/scheme file in the
    loader).  Here we only flag cross-item issues: duplicate tags (warning —
    the POS app decides whether duplicates are meaningful, e.g. a repeated tag
    per card instance).
    """
    report = Report()
    seen: set[str] = set()
    for i, param in enumerate(params, start=1):
        if param.tag in seen:
            report.add(owner, "warning",
                       f"pos_params 第 {i} 项 tag={param.tag} 重复")
        seen.add(param.tag)
    return report


# -- v1.2 POS kernel parameters (AID / CAPK / TERM) --------------------------

_HEX_RE = re.compile(r"^[0-9A-F]+$")
_SHA1_HEX_LEN = 40


def validate_kernel_params(params: KernelParams | None, owner: str, *,
                           trust_checksum: bool = False) -> Report:
    """Validate one ``KernelParams`` set before it is pushed to the POS kernel.

    Per v1.2 §1.9 the host only rejects what the kernel itself would reject:

    * AID: ``9F06`` must be 5–8 bytes, whole-byte hex, unique per entry, and no
      entry may repeat an AID; every trailing field must be even-length hex;
    * CAPK: RID exactly 5 bytes, key index 1 byte, ``DF05`` a valid date,
      ``DF06``/``DF07`` present, modulus a whole number of bytes at most
      ``MAX_MODULUS_BYTES``, and ``DF03`` must equal
      SHA1(RID+index+modulus+exp); RID+index must be unique.  When
      ``trust_checksum`` is set (vendor/named POS configs) a DF03 mismatch
      is reported as a warning instead of an error;
    * DRL: 9F06 RID(5B) + 8F index(1B) + DF8105 serial (non-empty hex);
    * exception_file: 5A PAN + 5F34 PAN sequence (non-empty hex);
    * TERM: even-length hex that parses as concatenated BER-TLV.
    """
    report = Report()
    if params is None:
        return report

    seen_aids: set[str] = set()
    for i, aid in enumerate(params.aids, start=1):
        where = f"kernel_params.aid[{i}]"
        if len(aid.aid) % 2 or len(aid.aid) < 10 or len(aid.aid) > 16 \
                or not _HEX_RE.fullmatch(aid.aid or "0"):
            report.add(owner, "error",
                       f"{where} 9F06 须为 5-8 字节偶数长度 hex：{aid.aid!r}")
        if aid.aid in seen_aids:
            report.add(owner, "error", f"{where} AID 重复：{aid.aid}")
        seen_aids.add(aid.aid)
        if not aid.fields:
            report.add(owner, "warning",
                       f"{where} {aid.aid} 除 9F06 外没有任何元素")
        seen_tags: set[str] = set()
        for field in aid.fields:
            if field.tag in seen_tags:
                report.add(owner, "error",
                           f"{where} {aid.aid} 内 tag {field.tag} 重复")
            seen_tags.add(field.tag)
            if len(field.value) % 2 or (field.value and
                                        not _HEX_RE.fullmatch(field.value)):
                report.add(owner, "error",
                           f"{where} tag {field.tag} 值不是偶数长度 hex："
                           f"{field.value!r}")

    seen_caps: set[tuple[str, str]] = set()
    for i, cap in enumerate(params.caps, start=1):
        where = f"kernel_params.capk[{i}]"
        if len(cap.rid) != 10 or not _HEX_RE.fullmatch(cap.rid or "0"):
            report.add(owner, "error",
                       f"{where} 9F06 (RID) 须为 5 字节 hex：{cap.rid!r}")
        if len(cap.key_index) != 2 or not _HEX_RE.fullmatch(cap.key_index or "0"):
            report.add(owner, "error",
                       f"{where} 9F22 (索引) 须为 1 字节 hex：{cap.key_index!r}")
        key = (cap.rid, cap.key_index)
        if key in seen_caps:
            report.add(owner, "error",
                       f"{where} CAPK 的 RID+索引重复：{cap.rid}/{cap.key_index}")
        seen_caps.add(key)
        # C2 §3.4: DF05 validation depends on the declared encoding
        if cap.expiry_encoding == "raw":
            if not cap.expiry_raw:
                report.add(owner, "error",
                           f"{where} expiry_encoding=raw 但未提供 expiry_raw")
            elif len(cap.expiry_raw) % 2 or \
                    not _HEX_RE.fullmatch(cap.expiry_raw):
                report.add(owner, "error",
                           f"{where} expiry_raw 不是偶数长度 hex：{cap.expiry_raw!r}")
            else:
                report.add(owner, "warning",
                           f"{where} DF05 为 raw 透传（非日期样例）：{cap.expiry_raw}"
                           f"；仅在该值经厂商确认时必须下发时使用")
        elif not ascii_date_ok(cap.expiry, require_20xx=False):
            report.add(owner, "error",
                       f"{where} DF05 须为 8 位数字且为合法日期 YYYYMMDD："
                       f"{cap.expiry!r}"
                       f"（银联 A000000333/08 资产里的 PHWWIPQI 是异常样例，"
                       f"禁止照抄；确需下发请用 expiry_encoding=raw）")
        else:
            # encode_expiry enforces the 2000-2099 range bcd3/bcd4 can express
            # and the BCD round-trip, so one call covers the remaining rules.
            try:
                wire = encode_expiry(cap.expiry, cap.expiry_encoding)
            except ValueError as exc:
                report.add(owner, "error", f"{where} {exc}")
            else:
                back_encoding, back = decode_expiry(wire)
                if back != cap.expiry:
                    report.add(owner, "error",
                               f"{where} DF05 {cap.expiry_encoding} 往返不一致："
                               f"{cap.expiry!r} -> {wire} -> {back!r} "
                               f"(判为 {back_encoding})")
        if len(cap.modulus) % 2 or not _HEX_RE.fullmatch(cap.modulus or "0"):
            report.add(owner, "error",
                       f"{where} DF02 模数不是偶数长度 hex")
        elif not cap.modulus:
            report.add(owner, "error", f"{where} DF02 模数为空")
        elif len(cap.modulus) // 2 > MAX_MODULUS_BYTES:
            report.add(owner, "error",
                       f"{where} DF02 模数长度 {len(cap.modulus) // 2} 字节超过上限 "
                       f"{MAX_MODULUS_BYTES}：{cap.rid}/{cap.key_index}")
        if (len(cap.exponent) % 2 or not cap.exponent
                or not _HEX_RE.fullmatch(cap.exponent)):
            report.add(owner, "error",
                       f"{where} DF04 指数须为非空偶数长度 hex：{cap.exponent!r}")
        elif len(cap.exponent) // 2 > MAX_EXPONENT_BYTES:
            report.add(owner, "error",
                       f"{where} DF04 指数长度 {len(cap.exponent) // 2} 字节超过上限 "
                       f"{MAX_EXPONENT_BYTES}：{cap.exponent!r}")
        if len(cap.modulus) % 2 == 0 and cap.modulus and \
                len(cap.key_index) == 2 and len(cap.rid) == 10 and \
                len(cap.exponent) % 2 == 0 and cap.exponent:
            expected = compute_capk_checksum(cap.rid, cap.key_index,
                                             cap.modulus, cap.exponent)
            if cap.checksum and cap.checksum != expected:
                report.add(owner, "warning" if trust_checksum else "error",
                           f"{where} DF03 校验和不符：期望 {expected}，"
                           f"实际 {cap.checksum}"
                           + ("（vendor 样例按原样保留，不阻断）"
                              if trust_checksum else ""))
            elif cap.checksum and len(cap.checksum) != _SHA1_HEX_LEN:
                report.add(owner, "error",
                           f"{where} DF03 须为 20 字节 (40 hex)：{cap.checksum}")

    seen_drl: set[tuple[str, str, str]] = set()
    for i, entry in enumerate(params.drl, start=1):
        where = f"kernel_params.drl[{i}]"
        if len(entry.rid) != 10 or not _HEX_RE.fullmatch(entry.rid or "0"):
            report.add(owner, "error",
                       f"{where} 9F06 (RID) 须为 5 字节 hex：{entry.rid!r}")
        if len(entry.index) != 2 or not _HEX_RE.fullmatch(entry.index or "0"):
            report.add(owner, "error",
                       f"{where} 8F (索引) 须为 1 字节 hex：{entry.index!r}")
        if (len(entry.serial) % 2 or not entry.serial
                or not _HEX_RE.fullmatch(entry.serial)):
            report.add(owner, "error",
                       f"{where} DF8105 (SN) 须为非空偶数长度 hex：{entry.serial!r}")
        key = (entry.rid, entry.index, entry.serial)
        if key in seen_drl:
            report.add(owner, "warning", f"{where} DRL 条目重复：{key}")
        seen_drl.add(key)

    for i, entry in enumerate(params.exception_file, start=1):
        where = f"kernel_params.exception_file[{i}]"
        if not entry.pan or len(entry.pan) % 2 or not _HEX_RE.fullmatch(entry.pan):
            report.add(owner, "error",
                       f"{where} 5A (PAN) 须为非空偶数长度 hex：{entry.pan!r}")
        if (not entry.pan_sequence or len(entry.pan_sequence) % 2
                or not _HEX_RE.fullmatch(entry.pan_sequence)):
            report.add(owner, "error",
                       f"{where} 5F34 (PSN) 须为非空偶数长度 hex："
                       f"{entry.pan_sequence!r}")

    if params.term:
        if len(params.term) % 2 or not _HEX_RE.fullmatch(params.term):
            report.add(owner, "error", f"kernel_params.term 不是偶数长度 hex")
        else:
            try:
                decode_tlv_map(params.term)
            except ValueError as exc:
                report.add(owner, "error", f"kernel_params.term TLV 无法解析：{exc}")
    else:
        report.add(owner, "warning", "kernel_params 未提供 term（终端参数将不下发）")

    return report
