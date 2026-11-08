"""Codes à usage unique (TOTP, RFC 6238) et codes de secours.

Compatible avec Google Authenticator, Aegis, 2FAS, Bitwarden… (SHA-1, 6 chiffres, 30 s).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

PERIODE = 30
CHIFFRES = 6


def nouveau_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, compteur: int) -> str:
    cle = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(cle, struct.pack(">Q", compteur), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** CHIFFRES
    return str(n).zfill(CHIFFRES)


def code_actuel(secret: str, instant: float | None = None) -> str:
    return _code(secret, int((instant or time.time()) // PERIODE))


def verifier(secret: str, code: str, dernier_pas: int = -1, instant: float | None = None,
             fenetre: int = 1) -> int | None:
    """Renvoie le pas de temps du code s'il est valide (±`fenetre` pas), sinon None.
    Un pas déjà utilisé (`dernier_pas`) est refusé : un code ne sert qu'une fois."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != CHIFFRES:
        return None
    pas = int((instant or time.time()) // PERIODE)
    for p in range(pas - fenetre, pas + fenetre + 1):
        if p > dernier_pas and hmac.compare_digest(_code(secret, p), code):
            return p
    return None


def uri(secret: str, compte: str, emetteur: str) -> str:
    """otpauth:// à mettre dans le QR code."""
    etiquette = quote(f"{emetteur}:{compte}")
    return f"otpauth://totp/{etiquette}?secret={secret}&issuer={quote(emetteur)}&digits={CHIFFRES}&period={PERIODE}"


def qr_svg(texte: str) -> str:
    import io

    import segno
    tampon = io.BytesIO()
    segno.make(texte, error="m").save(tampon, kind="svg", scale=5, border=2, dark="#000", light="#fff",
                                       xmldecl=False, svgns=True)
    return tampon.getvalue().decode()


def codes_de_secours(n: int = 10) -> list[str]:
    """Codes lisibles « xxxx-xxxx », à usage unique."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
            for _ in range(n)]
