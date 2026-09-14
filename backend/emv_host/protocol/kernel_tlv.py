"""BER-TLV encoding for POS kernel parameters (v1.2).

Pure functions, no I/O, shared by case validation (``cases/validation.py``) and
the run executor (``runner/executor.py``).

Wire format — v1.2 §1.5: each entry of ``pos_kernel_config.kernel_params`` is
**concatenated BER-TLV with no outer template**, uppercase hex:

* ``aid[]``  : ``9F06`` (5–8 byte AID/RID) followed by the remaining elements
               in tag order;
* ``capk[]`` : ``9F06 ‖ 9F22 ‖ DF05 ‖ DF06 ‖ DF07 ‖ DF02 ‖ DF04 ‖ DF03``;
* ``term``   : one TLV, e.g. ``9F6604360000809F1A0201569F7A0101``.

The CAPK layout mirrors the POS asset file
(``app/src/main/assets/emv/params``), which is what ``updateCAPKParam`` on the
device parses, so an exported entry and a hand-written one are byte-identical.

Length encoding (v1.2 §1.9): ``<= 0x7F`` uses the short form, ``0x80–0xFF``
uses ``81 xx``.  CAPK moduli top out at 248 bytes, so the two-byte form is the
only long form this module needs.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from ..cases.models import AidKernelParam, CapkParam

# CAPK element tags, in the mandatory order enforced by encode_capk()
TAG_RID = "9F06"
TAG_KEY_INDEX = "9F22"
TAG_EXPIRY = "DF05"
TAG_HASH_ALG = "DF06"
TAG_KEY_ALG = "DF07"
TAG_MODULUS = "DF02"
TAG_EXPONENT = "DF04"
TAG_CHECKSUM = "DF03"

#: Modulus byte lengths allowed by the POS kernel assets (1024/1152/1408/1984 bit)
MODULUS_LENGTHS = (128, 144, 176, 248)


def ber_length(length: int) -> bytes:
    """Encode a BER length: short form up to 0x7F, then ``81 xx``."""
    if length < 0:
        raise ValueError(f"negative TLV length: {length}")
    if length <= 0x7F:
        return bytes([length])
    if length <= 0xFF:
        return bytes([0x81, length])
    raise ValueError(f"TLV length {length} needs a form this encoder does not "
                     f"emit (max 255)")


def tlv(tag_hex: str, value_hex: str) -> str:
    """Encode one TLV element as uppercase hex."""
    tag = tag_hex.strip().upper()
    value = value_hex.strip().upper().replace(" ", "")
    if len(tag) % 2 or not tag:
        raise ValueError(f"TLV tag must be whole bytes: {tag_hex!r}")
    if len(value) % 2:
        raise ValueError(f"TLV value must be whole bytes for tag {tag}: {value_hex!r}")
    raw = bytes.fromhex(value)
    return tag + ber_length(len(raw)).hex().upper() + value


def encode_aid(aid: "AidKernelParam") -> str:
    """Encode one ``aid[]`` entry: 9F06 first, then the remaining elements.

    Element order is preserved exactly as configured (the POS asset keeps its
    own DFxx ordering, which the kernel accepts); ``fields`` never contains
    9F06 by construction.
    """
    parts = [tlv(TAG_RID, aid.aid)]
    for field in aid.fields:
        if field.tag == TAG_RID:
            raise ValueError(f"AID {aid.aid}: 9F06 must not appear in fields")
        parts.append(tlv(field.tag, field.value))
    return "".join(parts)


def compute_capk_checksum(rid: str, key_index: str, modulus: str,
                          exponent: str) -> str:
    """DF03 = SHA1(9F06 value ‖ 9F22 value ‖ DF02 value ‖ DF04 value), hex.

    All four inputs are bare values (no tags, no lengths); the modulus is hashed
    as raw bytes.
    """
    material = (bytes.fromhex(rid.strip().upper())
                + bytes.fromhex(key_index.strip().upper())
                + bytes.fromhex(modulus.strip().upper())
                + bytes.fromhex(exponent.strip().upper()))
    return hashlib.sha1(material).hexdigest().upper()


def encode_capk(cap: "CapkParam") -> str:
    """Encode one ``capk[]`` entry in the mandatory POS element order.

    ``DF05`` is 8 ASCII digits (``"20441231"`` -> ``3230343431323331``).  When
    ``cap.checksum`` is empty the SHA1 is computed here, so callers may supply
    only the key material.
    """
    checksum = cap.checksum.strip().upper() or compute_capk_checksum(
        cap.rid, cap.key_index, cap.modulus, cap.exponent)
    expiry_hex = cap.expiry.encode("ascii").hex().upper()
    return "".join((
        tlv(TAG_RID, cap.rid),
        tlv(TAG_KEY_INDEX, cap.key_index),
        tlv(TAG_EXPIRY, expiry_hex),
        tlv(TAG_HASH_ALG, f"{cap.hash_alg:02X}"),
        tlv(TAG_KEY_ALG, f"{cap.key_alg:02X}"),
        tlv(TAG_MODULUS, cap.modulus),
        tlv(TAG_EXPONENT, cap.exponent),
        tlv(TAG_CHECKSUM, checksum),
    ))


def decode_tlv_map(hex_data: str) -> dict[str, str]:
    """Parse concatenated BER-TLV into ``{tag: value_hex}``.

    Used for validation and tests only.  Raises ``ValueError`` on a truncated
    element, an unsupported length form, or trailing garbage.
    """
    data = hex_data.strip().upper().replace(" ", "")
    if len(data) % 2:
        raise ValueError(f"TLV data must be whole bytes: {len(data)} hex chars")
    raw = bytes.fromhex(data)
    out: dict[str, str] = {}
    pos = 0
    while pos < len(raw):
        tag, pos = _read_tag(raw, pos)
        length, pos = _read_length(raw, pos)
        if pos + length > len(raw):
            raise ValueError(f"tag {tag} declares {length} bytes but only "
                             f"{len(raw) - pos} remain")
        out[tag] = raw[pos:pos + length].hex().upper()
        pos += length
    return out


def _read_tag(raw: bytes, pos: int) -> tuple[str, int]:
    if pos >= len(raw):
        raise ValueError("truncated tag")
    first = raw[pos]
    if first & 0x1F == 0x1F:  # two-byte tag (9Fxx / DFxx / BFxx)
        if pos + 1 >= len(raw):
            raise ValueError("truncated two-byte tag")
        return raw[pos:pos + 2].hex().upper(), pos + 2
    return raw[pos:pos + 1].hex().upper(), pos + 1


def _read_length(raw: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(raw):
        raise ValueError("truncated length")
    first = raw[pos]
    pos += 1
    if first & 0x80:
        count = first & 0x7F
        if count == 0 or count > 2:
            raise ValueError(f"unsupported BER length form 0x{first:02X}")
        if pos + count > len(raw):
            raise ValueError("truncated long-form length")
        return int.from_bytes(raw[pos:pos + count], "big"), pos + count
    return first, pos
