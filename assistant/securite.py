"""Accès à l'assistant : réseaux autorisés, comptes, double authentification (TOTP), sessions.

- Seuls les réseaux autorisés peuvent joindre l'assistant (sauf la machine elle-même).
- Un compte par personne : mot de passe (scrypt) + code TOTP à chaque nouvelle connexion,
  codes de secours à usage unique. L'appareil garde ensuite une session signée.
- Le premier compte se crée depuis la machine ; les suivants par une invitation (lien à usage
  unique) créée par un administrateur.
- Changer un mot de passe ou réinitialiser le TOTP ferme toutes les sessions de ce compte.
Derrière Nginx Proxy Manager / Cloudflare, l'IP réelle est lue seulement si la requête vient
d'un proxy de confiance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import totp
from .config import SecuriteConfig

NOM_COOKIE = "assistant_session"
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
DUREE_ETAPE_S = 300            # entre le mot de passe et le code TOTP
DUREE_INVITATION_S = 7 * 86400
COMPTE_MACHINE = "machine"


# --- empreintes -------------------------------------------------------------------------
def hacher(secret: str) -> str:
    sel = secrets.token_bytes(16)
    h = hashlib.scrypt(secret.encode(), salt=sel, **SCRYPT)
    return "scrypt$" + base64.b64encode(sel).decode() + "$" + base64.b64encode(h).decode()


def verifier(secret: str, empreinte: str) -> bool:
    try:
        algo, sel, h = empreinte.split("$")
        if algo != "scrypt":
            return False
        calcule = hashlib.scrypt(secret.encode(), salt=base64.b64decode(sel), **SCRYPT)
        return hmac.compare_digest(calcule, base64.b64decode(h))
    except (ValueError, TypeError, AttributeError):
        return False


def _empreinte_rapide(texte: str) -> str:
    """Pour les jetons d'invitation (longs et aléatoires : un simple SHA-256 suffit)."""
    return hashlib.sha256(texte.encode()).hexdigest()


# --- réseau ---------------------------------------------------------------------------------
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


def identifiant(nom: str) -> str:
    """« Benjamin » -> « benjamin » ; lettres, chiffres et tirets seulement."""
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", nom.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:32] or "compte"


class AccesRefuse(Exception):
    pass


class Securite:
    def __init__(self, cfg: SecuriteConfig, dossier_etat: Path) -> None:
        self.cfg = cfg
        self.autorises = _reseaux(cfg.reseaux_autorises)
        self.proxies = _reseaux(cfg.proxies_de_confiance)
        self.fichier = dossier_etat / "securite.json"
        self._etat = self._lire()
        self._migrer()
        self._echecs: dict[str, list[float]] = {}

    # --- état persistant ------------------------------------------------------------------------
    def _lire(self) -> dict:
        try:
            return json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._etat, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.fichier)
        try:
            os.chmod(self.fichier, 0o600)
        except OSError:
            pass

    def _migrer(self) -> None:
        """Ancien format (un seul mot de passe) -> un compte « principal » administrateur."""
        if "mot_de_passe" in self._etat and "comptes" not in self._etat:
            self._etat["comptes"] = {"principal": {"nom": "Principal", "mdp": self._etat.pop("mot_de_passe"),
                                                   "admin": True, "gen": 1}}
            self._ecrire()
        self._etat.setdefault("comptes", {})

    def _cle(self) -> bytes:
        if "cle" not in self._etat:
            self._etat["cle"] = secrets.token_hex(32)
            self._ecrire()
        return bytes.fromhex(self._etat["cle"])

    def _signer(self, charge: str, usage: str) -> str:
        """Signature liée à son usage : un jeton d'étape ne peut jamais servir de session (et inversement)."""
        return hmac.new(self._cle(), f"{usage}|{charge}".encode(), hashlib.sha256).hexdigest()

    @property
    def comptes(self) -> dict[str, dict]:
        return self._etat["comptes"]

    # --- comptes ----------------------------------------------------------------------------------
    @property
    def mot_de_passe_defini(self) -> bool:
        return any(c.get("mdp") for c in self.comptes.values())

    def trouver(self, ident: str) -> str | None:
        """Par identifiant ou par nom ; vide = le seul compte actif s'il n'y en a qu'un."""
        actifs = [i for i, c in self.comptes.items() if c.get("mdp")]
        if not ident.strip():
            return actifs[0] if len(actifs) == 1 else None
        cle = identifiant(ident)
        if cle in self.comptes:
            return cle
        return next((i for i, c in self.comptes.items() if identifiant(c.get("nom", "")) == cle), None)

    def creer_premier(self, mot_de_passe: str, nom: str = "Principal") -> str:
        if self.mot_de_passe_defini:
            raise AccesRefuse("un compte existe déjà")
        ident = identifiant(nom)
        self.comptes[ident] = {"nom": nom, "mdp": hacher(mot_de_passe), "admin": True, "gen": 1}
        self._ecrire()
        return ident

    def definir_mot_de_passe(self, compte: str, mot_de_passe: str) -> None:
        c = self.comptes[compte]
        c["mdp"] = hacher(mot_de_passe)
        c["gen"] = c.get("gen", 0) + 1          # ferme les sessions existantes de ce compte
        c.pop("invitation", None)
        self._ecrire()

    def creer_compte(self, nom: str, admin: bool = False) -> tuple[str, str]:
        """Compte sans mot de passe + jeton d'invitation (à usage unique, 7 jours)."""
        ident = identifiant(nom)
        if ident in self.comptes or ident == COMPTE_MACHINE:
            raise AccesRefuse(f"le compte « {nom} » existe déjà")
        self.comptes[ident] = {"nom": nom.strip(), "mdp": None, "admin": admin, "gen": 1}
        jeton = self.nouvelle_invitation(ident)
        return ident, jeton

    def nouvelle_invitation(self, compte: str) -> str:
        jeton = secrets.token_urlsafe(24)
        self.comptes[compte]["invitation"] = {"empreinte": _empreinte_rapide(jeton),
                                              "expire": int(time.time() + DUREE_INVITATION_S)}
        self._ecrire()
        return f"{compte}.{jeton}"

    def utiliser_invitation(self, jeton_complet: str, mot_de_passe: str) -> str:
        compte, _, jeton = jeton_complet.partition(".")
        c = self.comptes.get(compte)
        inv = (c or {}).get("invitation")
        if not inv or inv["expire"] < time.time() or not hmac.compare_digest(inv["empreinte"], _empreinte_rapide(jeton)):
            raise AccesRefuse("invitation invalide ou expirée")
        self.definir_mot_de_passe(compte, mot_de_passe)
        c.pop("totp", None)
        c.pop("secours", None)
        self._ecrire()
        return compte

    def renommer(self, compte: str, nom: str) -> None:
        self.comptes[compte]["nom"] = nom.strip()
        self._ecrire()

    def supprimer(self, compte: str) -> None:
        if self.comptes.get(compte, {}).get("admin") and sum(1 for c in self.comptes.values() if c.get("admin")) <= 1:
            raise AccesRefuse("impossible de supprimer le dernier administrateur")
        self.comptes.pop(compte, None)
        self._ecrire()

    def reinitialiser_totp(self, compte: str) -> None:
        c = self.comptes[compte]
        for cle in ("totp", "totp_attente", "totp_pas", "secours"):
            c.pop(cle, None)
        c["gen"] = c.get("gen", 0) + 1
        self._ecrire()

    def est_admin(self, compte: str | None) -> bool:
        return compte == COMPTE_MACHINE or bool(self.comptes.get(compte or "", {}).get("admin"))

    def liste(self) -> list[dict]:
        return [{"id": i, "nom": c.get("nom", i), "admin": bool(c.get("admin")), "actif": bool(c.get("mdp")),
                 "totp": bool(c.get("totp")), "secours_restants": len(c.get("secours", [])),
                 "invitation_en_cours": bool(c.get("invitation")), "derniere_connexion": c.get("derniere_connexion")}
                for i, c in self.comptes.items()]

    # --- adresse du client ------------------------------------------------------------------------
    def ip_client(self, ip_directe: str, entetes: dict[str, str]) -> str:
        """IP réelle : celle du proxy de confiance remplacée par CF-Connecting-IP / X-Forwarded-For."""
        if self.proxies and dans(ip_directe, self.proxies):
            if entetes.get("cf-connecting-ip"):
                return entetes["cf-connecting-ip"].strip()
            xff = entetes.get("x-forwarded-for", "")
            if xff:
                for a in reversed([x.strip() for x in xff.split(",") if x.strip()]):
                    if not dans(a, self.proxies):
                        return a
            if entetes.get("x-real-ip"):
                return entetes["x-real-ip"].strip()
        return ip_directe

    def reseau_autorise(self, ip: str) -> bool:
        return est_locale(ip) or dans(ip, self.autorises)

    def exige_connexion(self, ip: str) -> bool:
        if not self.cfg.active:
            return False
        return not (est_locale(ip) and self.cfg.machine_sans_mot_de_passe)

    # --- anti force brute ---------------------------------------------------------------------------
    def bloque(self, ip: str) -> int:
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

    def _verifier_bloque(self, ip: str) -> None:
        attente = self.bloque(ip)
        if attente:
            raise AccesRefuse(f"Trop d'essais. Réessaie dans {attente} s.")

    # --- connexion en deux étapes ---------------------------------------------------------------------
    def connexion(self, ip: str, ident: str, mot_de_passe: str) -> tuple[str, str]:
        """Étape 1. Renvoie (étape suivante, compte) : « totp », « configurer_totp » ou « session »."""
        self._verifier_bloque(ip)
        compte = self.trouver(ident)
        c = self.comptes.get(compte or "")
        if not c or not c.get("mdp") or not verifier(mot_de_passe, c["mdp"]):
            self.echec(ip)
            raise AccesRefuse("Identifiant ou mot de passe incorrect.")
        if c.get("totp"):
            return "totp", compte
        if self.cfg.totp_obligatoire:
            return "configurer_totp", compte
        self.reussite(ip)
        return "session", compte

    def jeton_etape(self, compte: str, etape: str) -> str:
        charge = f"{etape}.{compte}.{self.comptes[compte].get('gen', 0)}.{int(time.time() + DUREE_ETAPE_S)}"
        return charge + "." + self._signer(charge, "etape")

    def lire_etape(self, jeton: str, etape: str) -> str:
        try:
            e, compte, gen, expire, sig = jeton.split(".")
        except (ValueError, AttributeError):
            raise AccesRefuse("étape de connexion invalide") from None
        charge = f"{e}.{compte}.{gen}.{expire}"
        c = self.comptes.get(compte)
        if (e != etape or not c or not hmac.compare_digest(sig, self._signer(charge, "etape"))
                or not expire.isdigit() or int(expire) < time.time() or str(c.get("gen", 0)) != gen):
            raise AccesRefuse("étape de connexion expirée : recommence")
        return compte

    def verifier_code(self, ip: str, compte: str, code: str) -> str:
        """Étape 2 : code TOTP, ou code de secours (« xxxx-xxxx », usage unique). Renvoie le type utilisé."""
        self._verifier_bloque(ip)
        c = self.comptes[compte]
        brut = code.strip().lower()
        if "-" in brut or any(ch.isalpha() for ch in brut):
            for i, emp in enumerate(c.get("secours", [])):
                if verifier(brut, emp):
                    c["secours"].pop(i)
                    self._ecrire()
                    self.reussite(ip)
                    return "secours"
        elif c.get("totp"):
            pas = totp.verifier(c["totp"], brut, c.get("totp_pas", -1))
            if pas is not None:
                c["totp_pas"] = pas
                self._ecrire()
                self.reussite(ip)
                return "totp"
        self.echec(ip)
        raise AccesRefuse("Code incorrect.")

    def preparer_totp(self, compte: str, emetteur: str) -> dict:
        c = self.comptes[compte]
        if not c.get("totp_attente"):
            c["totp_attente"] = totp.nouveau_secret()
            self._ecrire()
        u = totp.uri(c["totp_attente"], c.get("nom", compte), emetteur)
        return {"secret": c["totp_attente"], "uri": u, "qr_svg": totp.qr_svg(u)}

    def activer_totp(self, ip: str, compte: str, code: str) -> list[str]:
        self._verifier_bloque(ip)
        c = self.comptes[compte]
        secret = c.get("totp_attente")
        pas = totp.verifier(secret, code) if secret else None
        if pas is None:
            self.echec(ip)
            raise AccesRefuse("Code incorrect : vérifie l'heure du téléphone et réessaie.")
        c["totp"], c["totp_pas"] = secret, pas
        c.pop("totp_attente", None)
        codes = totp.codes_de_secours()
        c["secours"] = [hacher(x) for x in codes]
        self._ecrire()
        self.reussite(ip)
        return codes

    def nouveaux_codes_secours(self, compte: str) -> list[str]:
        codes = totp.codes_de_secours()
        self.comptes[compte]["secours"] = [hacher(x) for x in codes]
        self._ecrire()
        return codes

    # --- sessions ---------------------------------------------------------------------------------------
    def creer_session(self, compte: str) -> str:
        c = self.comptes[compte]
        c["derniere_connexion"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._ecrire()
        expire = int(time.time() + self.cfg.session_jours * 86400)
        charge = f"{expire}.{compte}.{c.get('gen', 0)}.{secrets.token_hex(8)}"
        return charge + "." + self._signer(charge, "session")

    def session(self, jeton: str | None) -> str | None:
        """Compte de la session, ou None si invalide, expirée ou révoquée."""
        if not jeton:
            return None
        try:
            expire, compte, gen, alea, sig = jeton.split(".")
        except ValueError:
            return None
        c = self.comptes.get(compte)
        if not c or not c.get("mdp"):
            return None
        if not hmac.compare_digest(sig, self._signer(f"{expire}.{compte}.{gen}.{alea}", "session")):
            return None
        if not expire.isdigit() or int(expire) < time.time() or str(c.get("gen", 0)) != gen:
            return None
        return compte

    def session_valide(self, jeton: str | None) -> bool:
        return self.session(jeton) is not None
