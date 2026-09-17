"""Pydantic models for the case library.

The host currently runs **contactless (非接) tests only**.  The library layout
(root = ``--cases`` dir) is::

    cases/
      <brand>/                 e.g. visa
        brand.json             brand metadata + suite policy + card_template
        contactless/cases/<case_id>.json          (self-contained TestCase)
        contactless/cases/<group>/group.json      (总案例 description)
        contactless/cases/<group>/<group>-NN.json (子案例, self-contained)

Profiles are **bound to cases**: every executable case embeds its full
CardProfile (and optional KeysBundle) in its own JSON file; there is no
brand-level shared profile library.  A 总案例 (group) carries no profile —
only its 子案例 do.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EntryMode(str, Enum):
    CONTACTLESS = "contactless"
    CONTACT = "contact"


class SuitePolicy(BaseModel):
    on_failure: Literal["stop", "continue"] = "stop"
    retry_count: int = Field(default=0, ge=0, le=5)


class PosParam(BaseModel):
    """One POS-side transaction parameter item (sent to the POS app, never to
    the Android card side).  ``tag`` is an EMV tag (1–3 bytes, uppercase hex)
    that the POS app interprets; users may freely set/change tags per case."""

    model_config = ConfigDict(extra="allow")

    tag: str = Field(min_length=2, max_length=6,
                     description="EMV tag, 1-3 bytes uppercase hex (e.g. 9F02)")
    name: str = Field(default="", max_length=100)
    value: str = Field(default="", max_length=200)

    @field_validator("tag")
    @classmethod
    def _normalize_tag(cls, value: str) -> str:
        tag = value.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(?:[0-9A-F]{2}){0,2}", tag):
            raise ValueError(f"tag 须为 1-3 字节大写 hex：{value!r}")
        if len(tag) > 2:
            first = int(tag[:2], 16)
            if first & 0x1F != 0x1F:
                raise ValueError(f"多字节 tag 首字节低 5 位须为 0x1F：{value!r}")
        return tag


# ---------------------------------------------------------------------------
# Kernel parameters (v1.2) — AID / CAPK / TERM pushed to the POS kernel.
#
# The POS app applies these through updateAidParam/updateCAPKParam/
# setTerminalParam; the wire format is concatenated BER-TLV (no outer
# template), encoded by ``protocol/kernel_tlv.py``.  Model fields mirror the
# source asset (POS app/src/main/assets/emv/params) so validation can check
# every TLV element the POS will re-parse.
# ---------------------------------------------------------------------------


class TlvField(BaseModel):
    """One TLV element of an AID entry, after the leading ``9F06``."""

    model_config = ConfigDict(extra="forbid")

    tag: str = Field(description="EMV tag, 1-2 bytes hex (e.g. DF11/9F1B)")
    value: str = Field(default="", description="even-length hex value")

    @field_validator("tag")
    @classmethod
    def _normalize_tag(cls, value: str) -> str:
        tag = value.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(?:[0-9A-F]{2})?", tag):
            raise ValueError(f"TLV tag 须为 1-2 字节 hex：{value!r}")
        return tag

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str) -> str:
        return value.strip().upper()


class AidKernelParam(BaseModel):
    """One AID entry: ``9F06`` (5–8 byte AID/RID) plus its trailing TLV list.

    ``fields`` deliberately does NOT contain 9F06 — ``encode_aid`` prepends it,
    so the tag cannot be duplicated or reordered by accident.
    """

    model_config = ConfigDict(extra="forbid")

    aid: str = Field(description="9F06 value: 5-8 bytes hex")
    fields: list[TlvField] = Field(default_factory=list)

    @field_validator("aid")
    @classmethod
    def _normalize_aid(cls, value: str) -> str:
        aid = value.strip().upper().replace(" ", "")
        if not re.fullmatch(r"(?:[0-9A-F]{2}){5,8}", aid):
            raise ValueError(f"AID 须为 5-8 字节偶数长度 hex：{value!r}")
        return aid


class CapkParam(BaseModel):
    """One CAPK entry, one field per TLV element of the POS asset format:

    9F06 RID(5B) + 9F22 index(1B) + DF05 expiry + DF06 hash alg(1B)
    + DF07 key alg(1B) + DF02 modulus + DF04 exponent(1-8B) + DF03 checksum(20B)

    ``expiry`` is the canonical date (YYYYMMDD) and ``expiry_encoding`` selects
    how it is written into DF05 (C2): ``ascii8`` (8 ASCII bytes, the format all
    current assets use), ``bcd4`` (4 bytes, YYYY as packed BCD), ``bcd3``
    (3 bytes, YY+MM+DD), or ``raw`` (``expiry_raw`` verbatim, for vendor samples
    that are not calendar dates at all).

    ``checksum`` is verified (and may be filled in) by
    ``kernel_tlv.compute_capk_checksum``:
    DF03 = SHA1(9F06 ‖ 9F22 ‖ DF02 ‖ DF04) — independent of the DF05 encoding.
    """

    model_config = ConfigDict(extra="forbid")

    rid: str = Field(description="9F06 value: exactly 5 bytes hex")
    key_index: str = Field(description="9F22 value: 1 byte hex (e.g. 08)")
    expiry: str = Field(default="", max_length=8,
                        description="canonical DF05 date: 8 digits YYYYMMDD")
    expiry_encoding: Literal["ascii8", "bcd4", "bcd3", "raw"] = Field(
        default="ascii8", description="how DF05 is encoded on the wire (C2)")
    expiry_raw: str = Field(default="",
                            description="DF05 hex verbatim; only with encoding=raw")
    # DF06/DF07 may be 00 in vendor test vectors (Amex Expresspay), so the
    # model only bounds them to a byte instead of requiring a non-zero alg id.
    hash_alg: int = Field(default=1, ge=0, le=255, description="DF06 value")
    key_alg: int = Field(default=1, ge=0, le=255, description="DF07 value")
    modulus: str = Field(description="DF02 value: raw modulus, even-length hex")
    exponent: str = Field(default="03", description="DF04 value: even-length hex (1-8B)")
    checksum: str = Field(default="",
                          description="DF03 value: 20-byte SHA1 hex; empty = compute")

    @field_validator("rid", "key_index", "modulus", "exponent", "checksum",
                     "expiry_raw")
    @classmethod
    def _normalize_hex(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")

    @field_validator("expiry")
    @classmethod
    def _check_expiry(cls, value: str) -> str:
        expiry = value.strip()
        if expiry and not (len(expiry) == 8 and expiry.isdigit()):
            raise ValueError(f"DF05 须为 8 位数字 YYYYMMDD：{value!r}")
        return expiry

    @model_validator(mode="after")
    def _check_expiry_encoding(self) -> "CapkParam":
        """C2: DF05 travels in four shapes; keep the combination coherent here
        so a mis-declared entry fails at parse time, not on the wire."""
        if self.expiry_encoding == "raw":
            raw = self.expiry_raw
            if len(raw) % 2 or (raw and not re.fullmatch(r"[0-9A-F]+", raw)):
                raise ValueError(f"expiry_encoding=raw 时 expiry_raw 须为偶数长度 hex："
                                 f"{self.expiry_raw!r}")
        elif self.expiry_raw:
            raise ValueError("expiry_raw 只能在 expiry_encoding=raw 时提供")
        return self


class DrlParam(BaseModel):
    """One DRL entry (Amex Expresspay calls the file ``CRL``).

    Wire shape: ``9F06`` RID(5B) + ``8F`` index(1B) + ``DF8105`` serial number.
    The host carries vendor test vectors verbatim, so only the obvious shape
    (RID 5 bytes / index 1 byte / non-empty even hex serial) is enforced.
    """

    model_config = ConfigDict(extra="forbid")

    rid: str = Field(description="9F06 value: exactly 5 bytes hex")
    index: str = Field(description="8F value: 1 byte hex (e.g. 01)")
    serial: str = Field(default="", description="DF8105 value: even-length hex")

    @field_validator("rid", "index", "serial")
    @classmethod
    def _normalize_hex(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")


class ExceptionFileParam(BaseModel):
    """One exception-file entry: ``5A`` PAN + ``5F34`` PAN sequence number."""

    model_config = ConfigDict(extra="forbid")

    pan: str = Field(default="", description="5A value: even-length hex")
    pan_sequence: str = Field(default="", description="5F34 value: even-length hex")

    @field_validator("pan", "pan_sequence")
    @classmethod
    def _normalize_hex(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")


class KernelParams(BaseModel):
    """POS kernel parameter set for one brand (or a case-level override).

    Every field is optional: an omitted/empty ``KernelParams`` means "do not
    push pos_kernel_config for this case" -- the POS keeps its built-in tables.
    Host-side data normally comes from the out-of-repo kernel params directory
    (``<kernel-params-dir>/<brand>.json`` or the named library
    ``<kernel-params-dir>/<brand>/<ref>.json``); see
    tools/export_kernel_params.py and tools/import_expresspay_xml.py.
    """

    model_config = ConfigDict(extra="forbid")

    aids: list[AidKernelParam] = Field(default_factory=list)
    caps: list[CapkParam] = Field(default_factory=list,
                                  description="CAPK entries ('capk' on the wire)")
    drl: list[DrlParam] = Field(
        default_factory=list,
        description="DRL entries (Amex calls the file CRL); wire key 'drl'")
    exception_file: list[ExceptionFileParam] = Field(
        default_factory=list,
        description="exception-file entries; wire key 'exception_file'")
    term: str = Field(default="", description="concatenated TERM TLV(s), hex")
    #: Host-side metadata only (never sent on the wire): vendor config revision,
    #: e.g. "4.2.0" for the Amex Expresspay 4.2.0 set.
    version: str = Field(default="", description="config revision, host-side only")

    @field_validator("term")
    @classmethod
    def _normalize_term(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")

    @property
    def empty(self) -> bool:
        return (not self.aids and not self.caps and not self.drl
                and not self.exception_file and not self.term)

    def payload(self) -> dict[str, Any]:
        """Wire form of ``config_push{config_type:"pos_kernel_config"}``.

        aid[]/capk[]/drl[]/exception_file[] are concatenated BER-TLV hex strings
        with no outer template; term is concatenated TLV hex.  Empty lists are
        allowed; an entirely empty set means the case does not push kernel
        params.  ``version`` is host-side metadata and is NOT emitted.
        """
        from ..protocol.kernel_tlv import (
            encode_aid,
            encode_capk,
            encode_drl,
            encode_exception_file,
        )

        return {
            "aid": [encode_aid(a) for a in self.aids],
            "capk": [encode_capk(c) for c in self.caps],
            "drl": [encode_drl(d) for d in self.drl],
            "exception_file": [encode_exception_file(e)
                               for e in self.exception_file],
            "term": self.term,
        }


class BrandDef(BaseModel):
    brand: str
    display_name: str
    description: str = ""
    entry_modes: list[EntryMode] = [EntryMode.CONTACTLESS, EntryMode.CONTACT]
    suite_policy: SuitePolicy = Field(default_factory=SuitePolicy)
    # brand-level default card (cloned by the add-case page) and the brand's
    # default POS transaction parameter template (per-brand requirements)
    card_template: CardProfile | None = None
    pos_params: list[PosParam] = Field(default_factory=list)
    # v1.2: POS kernel parameters (AID/CAPK/TERM) pushed per case, before
    # pos_config.  Loaded from the gitignored local cases/<brand>/kernel_params.json;
    # a case-level KernelParams overrides this brand default entirely.
    kernel_params: KernelParams | None = None
    environment_assumptions: str = ""


# ---------------------------------------------------------------------------
# CardProfile — canonical schema (v1.1, the Android app's SAF-import schema).
#
# Field names are camelCase ON PURPOSE: profile JSON is a payload shared 1:1
# with the app's file import (single schema, two channels).  The host reads
# only the subset marked in docs/protocol.md (id/name/pan/aids/afl/records/
# acBehavior/...); every other field passes through verbatim (extra="allow").
# Keys are NOT part of a profile (v1.1 N2): they travel beside it in
# card_config{profile, keys}.
# ---------------------------------------------------------------------------


class AidConfig(BaseModel):
    aid: str = Field(min_length=2, max_length=64)
    label: str = ""
    priority: int = 0


class CvmEntry(BaseModel):
    cvmCode: int = Field(ge=0, le=0xFFFF)
    cvmCondition: int = Field(ge=0, le=0xFFFF)
    applySucceedingCvm: int = Field(default=1, ge=1, le=2)


class RecordConfig(BaseModel):
    sfi: int = Field(ge=1, le=31)
    record: int = Field(ge=1)
    fields: list[str] = Field(default_factory=list, min_length=1)


class NoResponseSpec(BaseModel):
    """One "don't respond to this command" entry.

    A plain string (e.g. ``"GPO"``) is shorthand for ``{"command":"GPO"}`` =
    silence on EVERY occurrence.  ``times`` (>=1) limits the silence to the
    first N occurrences; from occurrence N+1 the card responds normally.
    """

    model_config = ConfigDict(extra="allow")  # extensible for the Android app

    command: str = Field(min_length=1, max_length=32)
    times: int | None = Field(default=None, ge=1)

    @field_validator("command")
    @classmethod
    def _normalize_command(cls, value: str) -> str:
        return value.strip().upper()


class CardProfile(BaseModel):
    model_config = ConfigDict(extra="allow")  # pass through unknown fields

    # id is required by the canonical SAF schema; inside a case file it may be
    # omitted and is then auto-filled with the case_id (profile↔case binding)
    id: str = Field(default="", max_length=64)
    name: str = ""
    description: str = ""
    network: str = ""
    isActive: bool = False
    aids: list[AidConfig] = Field(default_factory=list)
    pan: str = ""
    panSequenceNumber: int = Field(default=1, ge=0)
    applicationExpirationDate: str = ""   # YYMMDD
    cardholderName: str = ""
    track2EquivalentData: str = ""
    applicationLabel: str = ""
    applicationVersionNumber: str = "0001"
    aip: str = "0000"
    afl: str = ""
    issuerApplicationData: str = ""
    issuerCountryCode: str = ""
    cvmList: list[CvmEntry] = Field(default_factory=list)
    pdol: str = ""
    cdol1: str = ""
    cdol2: str = ""
    tdol: str = ""
    ddol: str = ""
    symmetricKeyId: str = ""
    rsaKeyId: str = ""
    certificateProfileId: str = ""
    records: list[RecordConfig] = Field(default_factory=list)
    # online_request | offline_approved | offline_declined; invalid values are
    # treated as "absent" (= follow terminal P1) with a load-time warning
    acBehavior: str = ""
    createdAt: int | None = None
    updatedAt: int | None = None

    # PPSE behavior overrides (v1.1 N11, all optional; "" = default)
    #   ppseMode: "auto" | "disabled" | "empty" | "custom"  (default "auto")
    #   ppseSw:   uppercase 4-hex SW override, applies to ANY mode
    #   ppseData: even-length hex full PPSE FCI template (custom mode only)
    ppseMode: str = ""
    ppseSw: str = ""
    ppseData: str = ""

    # Command no-response (silence) behavior (v1.1 N12): entries are either a
    # command-name string ("GPO") = silence on EVERY occurrence, or a
    # NoResponseSpec {"command": "GPO", "times": N} = silence only the first N
    # occurrences, then respond normally.  Empty list = always respond.
    noResponseCommands: list[str | NoResponseSpec] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_optional_id(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError(f"id 只能含字母/数字/下划线/连字符：{value!r}")
        return value

    @field_validator("noResponseCommands")
    @classmethod
    def _normalize_no_response(cls, value: list[str | NoResponseSpec]) -> list[str | NoResponseSpec]:
        out: list[str | NoResponseSpec] = []
        for item in value:
            if isinstance(item, str):
                name = item.strip().upper()
                if name:
                    out.append(name)
            else:
                out.append(item)
        return out


class KeysBundle(BaseModel):
    """card_config.keys payload (v1.1 N2, parallel to profile)."""

    symmetric_keys: list[dict[str, Any]] = Field(default_factory=list)
    rsa_keys: list[dict[str, Any]] = Field(default_factory=list)
    certificates: dict[str, Any] = Field(default_factory=dict)


class OperatorInstructions(BaseModel):
    """What the human operator must do on the POS (shown in the web UI)."""
    amount: str = Field(default="0.00", max_length=20)
    currency: str = Field(default="CNY", max_length=3, pattern=r"^[A-Z]{3}$")
    entry_mode: EntryMode = EntryMode.CONTACTLESS
    pin_hint: str = Field(default="none", max_length=20)  # none | insert | bypass
    notes: str = Field(default="", max_length=500)


class ExpectedValues(BaseModel):
    """Machine-checkable expectations; never shown as operator guidance."""
    amount: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, max_length=3, pattern=r"^\d{3}$")
    entry_mode: EntryMode | None = None
    cvm_required: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ActParam(BaseModel):
    """One CTEL2 ``ACT`` signalData item (transaction data object).

    ``id`` is an EMV tag (e.g. 9F02) or a proprietary id; ``value`` is an
    uppercase hex string, ``""`` for a zero-length tag, or ``None`` when the
    tag is NOT present.  See docs/protocol-v2-design.md section 5.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=32)
    value: str | None = None

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper().replace(" ", "")


class TransactionParams(BaseModel):
    operator_instructions: OperatorInstructions = Field(default_factory=OperatorInstructions)
    expected_values: ExpectedValues = Field(default_factory=ExpectedValues)
    # v2: CTEL2 ACT transaction data (id/value hex), the only POS transaction
    # parameters.  ``pos_params`` is retained for legacy case data but is NOT
    # sent to the POS any more (v2 refactor, no coexistence).
    act: list[ActParam] = Field(default_factory=list)
    # POS-side transaction parameters (legacy v1.2 tag list; no longer pushed)
    pos_params: list[PosParam] = Field(default_factory=list)


class TestPlanStep(BaseModel):
    step: int = Field(ge=1, le=100)
    action: str = Field(min_length=1, max_length=1000)
    expect: str = Field(min_length=1, max_length=1000)


class MatchRule(BaseModel):
    """One assertion against the POS transaction result message.

    ``path`` is a dotted key path, e.g. ``resp_code``, ``outcome``,
    ``tags.gen_ac.9F26``.  Operations:

    * ``eq`` / ``neq`` / ``contains`` / ``prefix``: string compare against ``expect``;
    * ``regex``: ``expect`` is a regex searched against the string value;
    * ``any_of``: ``expect`` is a list of accepted values;
    * ``present`` / ``absent``: key existence, ``expect`` unused;
    * ``range``: ``expect`` is ``[min, max]`` (numeric, inclusive).
    """

    path: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "neq", "contains", "regex", "any_of", "present", "absent",
                "prefix", "range"] = "eq"
    expect: Any = None
    severity: Literal["pass", "warn"] = "pass"

    @model_validator(mode="after")
    def _check_expect(self) -> "MatchRule":
        if self.op in ("present", "absent"):
            return self
        if self.op in ("any_of", "range"):
            if not isinstance(self.expect, list) or not self.expect:
                raise ValueError(f"op {self.op} requires a non-empty list for expect")
            return self
        if self.expect is None:
            raise ValueError(f"op {self.op} requires expect")
        return self


class TestCase(BaseModel):
    case_id: str = Field(
        min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="unique case id; also used as the JSON file name")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    brand: str = Field(min_length=1, max_length=64)
    entry_mode: EntryMode = EntryMode.CONTACTLESS
    # profile is EMBEDDED (bound to this case) — no shared profile library.
    # profile.id may be omitted in the file and is auto-filled with case_id.
    profile: CardProfile = Field(default_factory=CardProfile)
    # optional embedded keys bundle (v1.1 N2 parallel schema); demo cases omit it
    keys: KeysBundle | None = None
    group_id: str | None = Field(default=None, max_length=64,
                                 description="set by the loader when the case "
                                             "is a 子案例 inside a group dir")
    transaction: TransactionParams = Field(default_factory=TransactionParams)
    # v1.2: case-level POS kernel parameter override; when None the brand
    # default applies, when empty the case pushes nothing (POS keeps its tables)
    kernel_params: KernelParams | None = None
    # v1.2.x: modular named POS configs, freely paired by the case.  Both are
    # resolved from the out-of-repo library
    # (``<kernel-params-dir>/<brand>/<ref>.json``) in memory only -- CAPK
    # moduli never enter the case file.  Both are mutually exclusive with an
    # inline ``kernel_params``.
    #: CONF module (AID/CAPK/DRL/ExceptionFile + TerminalParameter term)
    kernel_params_ref: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
        description="CONF module id under <kernel-params-dir>/<brand>/")
    #: Profile module (Profile tags as term); appended after the CONF term
    terminal_profile_ref: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
        description="PROFILE module id under <kernel-params-dir>/<brand>/")
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    test_plan: list[TestPlanStep] = Field(default_factory=list, max_length=50)
    timeout_seconds: int = Field(default=180, ge=10, le=3600)
    retry_on_fail: int = Field(default=0, ge=0, le=5)
    expected_results: list[MatchRule] = Field(default_factory=list, max_length=50)


class GroupDef(BaseModel):
    """A 总案例 (case group): a non-executable description that holds the
    shared test plan; the executable 子案例 live as sibling JSON files inside
    the same directory, each a normal TestCase with ``group_id`` set."""

    group_id: str = Field(min_length=1, max_length=64,
                          pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    purpose: str = Field(default="", max_length=2000)          # 测试目的
    test_conditions: str = Field(default="", max_length=2000)  # 测试条件
    brand: str = Field(min_length=1, max_length=64)
    entry_mode: EntryMode = EntryMode.CONTACTLESS
    test_plan: list[TestPlanStep] = Field(default_factory=list, max_length=50)
    pass_criteria: list[str] = Field(default_factory=list, max_length=50)
