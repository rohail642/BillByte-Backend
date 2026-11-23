"""Signed per-table tokens for QR self-ordering.

A token encodes (restaurant_id, table_number) plus an HMAC so a guest can't
edit it to order for another table or restaurant. The HMAC also covers the
restaurant's `qr_token_version`: bumping it ("Regenerate QR codes") revokes
every printed code at once. No DB table is needed.
"""
import base64
import hashlib
import hmac
from typing import Optional, Tuple

from app.core.config import settings

_DOMAIN = b"billbyte-qr-table-v1|"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(restaurant_id: int, table: str, version: int) -> str:
    msg = _DOMAIN + f"{restaurant_id}:{table}:{version}".encode()
    return _b64e(hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).digest()[:12])


def make_table_token(restaurant_id: int, table: str, version: int) -> str:
    return f"{_b64e(f'{restaurant_id}:{table}'.encode())}.{_sig(restaurant_id, table, version)}"


def parse_table_token(token: str) -> Optional[Tuple[int, str, str]]:
    """Return (restaurant_id, table, sig) WITHOUT verifying — caller must call
    verify_table_token with the restaurant's current version."""
    try:
        body, sig = token.split(".", 1)
        rid_s, table = _b64d(body).decode().split(":", 1)
        return int(rid_s), table, sig
    except Exception:
        return None


def verify_table_token(restaurant_id: int, table: str, sig: str, version: int) -> bool:
    return hmac.compare_digest(sig, _sig(restaurant_id, table, version))


def restaurant_tables(rest) -> list[str]:
    """Same rule as the POS: named tables from sections, else 1..table_count."""
    sections = rest.table_sections or []
    if sections:
        out = []
        for s in sections:
            for t in (s.get("tables") or []):
                if str(t) not in out:
                    out.append(str(t))
        return out
    return [str(i + 1) for i in range(rest.table_count or 0)]
