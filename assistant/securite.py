"""Accès à l'assistant : réseaux autorisés, mot de passe, session par appareil.

En attendant les comptes (phase 3), une seule règle :
- seuls les réseaux autorisés (local, VPN) peuvent joindre l'assistant ;
- hors de la machine elle-même, il faut se connecter avec le mot de passe ; l'appareil
  garde ensuite une session signée (cookie) pendant `session_jours` jours ;
- le premier mot de passe ne peut être choisi que depuis la machine elle-même.

Derrière Nginx Proxy Manager, l'adresse du client est lue dans X-Forwarded-For,
seulement si la requête vient d'un proxy déclaré de confiance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from pathlib import Path

from .config import SecuriteConfig

NOM_COOKIE = "assistant_session"
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


# --- mot de passe -------------------------------------------------------------------
def hacher(mot_de_passe: str) -> str:
    sel = secrets.token_bytes(16)
    h = hashlib.scrypt(mot_de_passe.encode(), salt=sel, **SCRYPT)
    return "scrypt$" + base64.b64encode(sel).decode() + "$" + base64.b64encode(h).decode()


def verifier(mot_de_passe: str, empreinte: str) -> bool:
    try:
        algo, sel, h = empreinte.split("$")
        if algo != "scrypt":
            return False
        calcule = hashlib.scrypt(mot_de_passe.encode(), salt=base64.b64decode(sel), **SCRYPT)
        return hmac.compare_digest(calcule, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# --- réseau -------------------------------------------------------------------------------
def _reseaux(liste: list[str]) -> list[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(r, strict=False) for r in liste]


def dans(ip: str, reseaux: list[ipaddress._BaseNetwork]) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return any(a in r for r in reseaux)


def est_locale(ip: str) -> bool:
    return dans(ip, _reseaux(["127.0.0.0/8", "::1/128"]))


class Securite:
    def __init__(self, cfg: SecuriteConfig, dossier_etat: Path) -> None:
        self.cfg = cfg
        self.autorises = _reseaux(cfg.reseaux_autorises)
        self.proxies = _reseaux(cfg.proxies_de_confiance)
        self.fichier = dossier_etat / "securite.json"
        self._etat = self._lire()
        self._echecs: dict[str, list[float]] = {}

    # --- état persistant (empreinte du mot de passe, clé de signature des sessions) ------------
    def _lire(self) -> dict:
        try:
            return json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._etat, indent=2), encoding="utf-8")
        os.replace(tmp, self.fichier)
        try:
            os.chmod(self.fichier, 0o600)
        except OSError:
            pass

    def _cle(self) -> bytes:
        if "cle" not in self._etat:
            self._etat["cle"] = secrets.token_hex(32)
            self._ecrire()
        return bytes.fromhex(self._etat["cle"])

    @property
    def mot_de_passe_defini(self) -> bool:
        return bool(self._etat.get("mot_de_passe"))

    def definir_mot_de_passe(self, mot_de_passe: str) -> None:
        self._etat["mot_de_passe"] = hacher(mot_de_passe)
        self._etat["cle"] = secrets.token_hex(32)   # nouveau mot de passe : toutes les sessions tombent
        self._ecrire()

    # --- adresse du client -------------------------------------------------------------------
    def ip_client(self, ip_directe: str, entetes: dict[str, str]) -> str:
        """IP réelle : celle du proxy de confiance remplacée par X-Forwarded-For / X-Real-IP."""
        if self.proxies and dans(ip_directe, self.proxies):
            xff = entetes.get("x-forwarded-for", "")
            if xff:
                # dernière adresse non-proxy de la chaîne (la plus proche, donc la moins falsifiable)
                for a in reversed([x.strip() for x in xff.split(",") if x.strip()]):
                    if not dans(a, self.proxies):
                        return a
            if entetes.get("x-real-ip"):
                return entetes["x-real-ip"].strip()
        return ip_directe

    def reseau_autorise(self, ip: str) -> bool:
        return est_locale(ip) or dans(ip, self.autorises)

    # --- sessions ------------------------------------------------------------------------------
    def creer_session(self) -> str:
        expire = int(time.time() + self.cfg.session_jours * 86400)
        charge = f"{expire}.{secrets.token_hex(8)}"
        sig = hmac.new(self._cle(), charge.encode(), hashlib.sha256).hexdigest()
        return f"{charge}.{sig}"

    def session_valide(self, jeton: str | None) -> bool:
        if not jeton or not self.mot_de_passe_defini:
            return False
        try:
            expire, alea, sig = jeton.split(".")
        except ValueError:
            return False
        attendu = hmac.new(self._cle(), f"{expire}.{alea}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, attendu) and int(expire) > time.time()

    # --- anti force brute ------------------------------------------------------------------------
    def bloque(self, ip: str) -> int:
        """Secondes d'attente restantes si trop d'échecs récents depuis cette IP (0 sinon)."""
        maintenant = time.time()
        recents = [t for t in self._echecs.get(ip, []) if maintenant - t < self.cfg.blocage_s]
        self._echecs[ip] = recents
        if len(recents) >= self.cfg.essais_max:
            return int(self.cfg.blocage_s - (maintenant - recents[0])) + 1
        return 0

    def echec(self, ip: str) -> None:
        self._echecs.setdefault(ip, []).append(time.time())

    def reussite(self, ip: str) -> None:
        self._echecs.pop(ip, None)

    def connexion(self, ip: str, mot_de_passe: str) -> tuple[bool, str]:
        attente = self.bloque(ip)
        if attente:
            return False, f"Trop d'essais. Réessaie dans {attente} s."
        if self.mot_de_passe_defini and verifier(mot_de_passe, self._etat["mot_de_passe"]):
            self.reussite(ip)
            return True, ""
        self.echec(ip)
        return False, "Mot de passe incorrect."

    # --- décision ------------------------------------------------------------------------------------
    def exige_connexion(self, ip: str) -> bool:
        if not self.cfg.active:
            return False
        if est_locale(ip) and self.cfg.machine_sans_mot_de_passe:
            return False
        return True
