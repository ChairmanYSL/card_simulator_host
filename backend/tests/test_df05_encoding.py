"""TS-6 / C2: DF05 (CAPK expiry) four-encoding support.

DF05 is historically a short field, so the POS assets and kernels disagree on
its shape.  The host now models the value canonically (``expiry`` = YYYYMMDD)
plus ``expiry_encoding``:

===========  ==========================  =========================
encoding     wire bytes (20241231)       note
===========  ==========================  =========================
``ascii8``   32 30 32 34 31 32 33 31     what every current asset uses
``bcd4``     20 24 12 31                 packed BCD, year in 2 bytes
``bcd3``     24 12 31                    packed BCD, year modulo 100
``raw``      expiry_raw verbatim         vendor value that is not a date
===========  ==========================  =========================

DF03 covers RID|index|modulus|exponent only, so the DF05 encoding must never
change the checksum — that is asserted explicitly below.
"""

from __future__ import annotations

import pytest

from emv_host.cases.models import CapkParam, KernelParams
from emv_host.cases.validation import validate_kernel_params
from emv_host.protocol.kernel_tlv import (
    compute_capk_checksum,
    decode_expiry,
    decode_tlv_map,
    encode_capk,
    encode_expiry,
)

MODULUS = "AB" * 128
BASE = {"rid": "A000000003", "key_index": "01", "modulus": MODULUS}


def cap(**kw) -> CapkParam:
    return CapkParam(**{**BASE, **kw})


def df05_of(entry: CapkParam) -> str:
    return decode_tlv_map(encode_capk(entry))["DF05"]


# -- encode / decode round trips ---------------------------------------------

@pytest.mark.parametrize(("encoding", "expected"), [
    ("ascii8", "3230323431323331"),
    ("bcd4", "20241231"),
    ("bcd3", "241231"),
])
def test_encode_expiry_shapes(encoding, expected):
    assert encode_expiry("20241231", encoding) == expected


@pytest.mark.parametrize(("encoding", "date"), [
    ("ascii8", "20241231"),
    ("bcd4", "20241231"),
    ("bcd3", "20241231"),
    ("ascii8", "20000101"),
    ("bcd4", "20000101"),
    ("bcd3", "20000101"),
    ("bcd3", "20991231"),
    ("bcd4", "20991231"),
])
def test_round_trip(encoding, date):
    wire = encode_expiry(date, encoding)
    back_encoding, back = decode_expiry(wire)
    assert back == date
    assert back_encoding == encoding


def test_boundaries():
    """bcd3/bcd4 span exactly 2000-2099."""
    assert encode_expiry("20000101", "bcd3") == "000101"
    assert encode_expiry("20991231", "bcd3") == "991231"
    assert encode_expiry("20000101", "bcd4") == "20000101"
    assert encode_expiry("20991231", "bcd4") == "20991231"
    assert decode_expiry("000101") == ("bcd3", "20000101")
    assert decode_expiry("991231") == ("bcd3", "20991231")


def test_leap_day_accepted():
    assert encode_expiry("20240229", "bcd3") == "240229"
    assert decode_expiry("240229") == ("bcd3", "20240229")


# -- the wire is unchanged for the existing assets ---------------------------

def test_ascii8_is_byte_identical_to_the_old_encoder():
    """Regression guard: the default path must not change any existing CAPK."""
    entry = cap(expiry="20441231")
    assert df05_of(entry) == "3230343431323331"
    # the model default is ascii8, so an entry built without the field matches
    assert fc_same(entry, cap(expiry="20441231", expiry_encoding="ascii8"))


def fc_same(a: CapkParam, b: CapkParam) -> bool:
    return encode_capk(a) == encode_capk(b)


# -- DF03 is independent of the DF05 encoding --------------------------------

def test_df03_does_not_depend_on_df05_encoding():
    sums = {decode_tlv_map(encode_capk(cap(expiry="20241231",
                                           expiry_encoding=enc)))["DF03"]
            for enc in ("ascii8", "bcd4", "bcd3")}
    assert len(sums) == 1
    assert sums.pop() == compute_capk_checksum(
        BASE["rid"], BASE["key_index"], MODULUS, "03")


def test_df03_unchanged_in_raw_mode():
    raw = cap(expiry_encoding="raw", expiry_raw="5048575749505149")
    assert decode_tlv_map(encode_capk(raw))["DF03"] == \
        compute_capk_checksum(BASE["rid"], BASE["key_index"], MODULUS, "03")


# -- raw escape hatch (the 银联 PHWWIPQI sample) ------------------------------

def test_raw_passthrough_of_the_phwwipqi_sample():
    entry = cap(expiry_encoding="raw", expiry_raw="5048575749505149")
    assert df05_of(entry) == "5048575749505149"
    assert decode_expiry("5048575749505149") == ("raw", "5048575749505149")


def test_phwwipqi_is_rejected_as_a_date_but_allowed_as_raw():
    """U-4 becomes two cases: ascii8 refuses it, raw passes with a warning."""
    # model level: it is simply not a date
    with pytest.raises(Exception):
        cap(expiry="PHWWIPQI")

    # validator level: raw is accepted, but never silently
    raw = KernelParams(caps=[cap(expiry_encoding="raw",
                                 expiry_raw="5048575749505149")])
    report = validate_kernel_params(raw, "x")
    assert not report.errors, [i.message for i in report.errors]
    assert any("raw" in i.message and i.severity == "warning"
               for i in report.issues), [i.message for i in report.issues]


# -- validator rules ---------------------------------------------------------

def errors_for(entry: CapkParam) -> list[str]:
    report = validate_kernel_params(KernelParams(caps=[entry]), "x")
    return [i.message for i in report.errors]


def test_validator_accepts_all_encodings():
    for enc in ("ascii8", "bcd4", "bcd3"):
        assert not errors_for(cap(expiry="20241231", expiry_encoding=enc)), enc


def test_validator_rejects_bcd3_before_2000():
    msgs = errors_for(cap(expiry="19991231", expiry_encoding="bcd3"))
    assert any("2000-2099" in m for m in msgs), msgs


def test_validator_rejects_impossible_dates():
    for date in ("20241332", "20240230", "20240010", "20241200"):
        assert errors_for(cap(expiry=date)), date


def test_validator_rejects_raw_without_a_value():
    """A raw declaration with no expiry_raw is a load-time error."""
    entry = CapkParam.model_construct(
        rid=BASE["rid"], key_index=BASE["key_index"], modulus=MODULUS,
        expiry="", expiry_encoding="raw", expiry_raw="",
        hash_alg=1, key_alg=1, exponent="03", checksum="")
    assert any("expiry_raw" in m for m in errors_for(entry))


def test_validator_rejects_non_hex_raw():
    """Second line of defence: kernel_params built without model validation.

    A parsed kernel_params.json can never carry a non-hex raw value (the model
    rejects it at parse time — see test_model_rejects_non_hex_raw below), so this
    calls the report function directly on a constructed entry.
    """
    from emv_host.cases.validation import Report  # noqa: F401  (documents intent)
    entry = CapkParam.model_construct(
        rid=BASE["rid"], key_index=BASE["key_index"], modulus=MODULUS,
        expiry="", expiry_encoding="raw", expiry_raw="ZZZZ",
        hash_alg=1, key_alg=1, exponent="03", checksum="")
    report = validate_kernel_params(KernelParams.model_construct(caps=[entry]), "x")
    assert any("hex" in i.message for i in report.errors), \
        [i.message for i in report.issues]


# -- model-level combination rules ------------------------------------------

def test_model_rejects_expiry_raw_outside_raw_encoding():
    with pytest.raises(Exception):
        cap(expiry="20241231", expiry_raw="5048575749505149")


def test_model_rejects_odd_length_raw():
    with pytest.raises(Exception):
        cap(expiry_encoding="raw", expiry_raw="50485")


def test_model_rejects_unknown_encoding():
    with pytest.raises(Exception):
        cap(expiry="20241231", expiry_encoding="bcd2")


def test_model_rejects_non_hex_raw():
    with pytest.raises(Exception):
        cap(expiry_encoding="raw", expiry_raw="ZZZZ")


def test_model_rejects_non_numeric_expiry():
    with pytest.raises(Exception):
        cap(expiry="2024-12-31")
