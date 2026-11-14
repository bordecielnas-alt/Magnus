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

from . import droits as D
from . import totp
from .config import SecuriteConfig

NOM_COOKIE = "assistant_session"
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
DUREE_ETAPE_S = 300            # entre le mot de passe et le code TOTP
DUREE_INVITATION_S = 7 * 86400
COMPTE_MACHINE = "machine"
PROFIL_DEFAUT = {"type": "adulte", "ton": "normal", "consigne": "", "piece": ""}
# Droits par personne : un groupe (Admin, Foyer, Extérieur…) et quelques exceptions (voir droits.py).


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
        groupes = self._etat.get("groupes")
        if groupes is None or any("parent" in g or "retraits" in g for g in groupes.values()):
            self._etat["groupes"] = self._migrer_groupes(groupes)
            if self.fichier.exists():
                self._ecrire()

    def _migrer_groupes(self, anciens: dict | None) -> dict:
        """0.26 / 0.27 (groupes, sous-groupes, type Adulte / Enfant, case admin) -> un groupe par personne.
        Adultes -> Foyer ; Enfants et les groupes créés -> gardés à plat (droits calculés, « enfant » coché pour
        Enfants) s'ils ont des membres ; la case admin -> groupe Admin. Avant la 0.26 : « ne pilote pas »."""
        groupes = {g: {**v, "permissions": list(v["permissions"])} for g, v in D.GROUPES_DEFAUT.items()}
        anciens = anciens or {}

        def a_plat(gid: str) -> set[str]:        # l'ancien calcul (héritage parent -> sous-groupe)
            chaine, vus = [], set()
            while gid and gid in anciens and gid not in vus:
                vus.add(gid)
                chaine.append(gid)
                gid = anciens[gid].get("parent") or ""
            perms: set[str] = set()
            for g in reversed(chaine):
                perms = (perms | set(anciens[g].get("permissions") or [])) - set(anciens[g].get("retraits") or [])
            return perms
        for c in self._etat["comptes"].values():
            refus = (c.get("profil") or {}).pop("interdits", None)
            if refus is not None:                            # avant la 0.26 : « ne pilote pas : … »
                self._choisir_categories(c, refus)
            type_ = (c.get("profil") or {}).get("type") or "adulte"
            membre_de = c.pop("groupes", None)
            if membre_de is None:
                membre_de = ["enfants"] if type_ == "enfant" and anciens else []
            if c.get("admin"):
                c["groupe"] = D.ADMIN
                continue
            garde = next((g for g in membre_de if g in anciens and g != "adultes"), None)
            if garde:
                gid = garde if garde not in groupes else garde + "-2"
                if gid not in groupes:
                    groupes[gid] = {"nom": anciens[garde].get("nom", garde), "permissions": sorted(a_plat(garde)),
                                    "enfant": garde == "enfants"}
                c["groupe"] = gid
            elif type_ == "enfant":
                groupes.setdefault("enfants", {"nom": "Enfants", "enfant": True, "permissions": sorted(
                    set(D.TOUT_SAUF_SERVEUR) - {"maison.piloter.chauffage", "agenda.ecrire", "automatismes.regles",
                                                "automatismes.routines", "memoire.foyer"})})
                c["groupe"] = "enfants"
            else:
                c["groupe"] = D.GROUPE_PAR_DEFAUT
        return groupes

    @staticmethod
    def _choisir_categories(c: dict, refusees: list[str]) -> None:
        pil = {f"maison.piloter.{x}" for x in D.CATEGORIES}
        refus = {f"maison.piloter.{x}" for x in refusees if x in D.CATEGORIES}
        c["retraits"] = sorted((set(c.get("retraits") or []) - pil) | refus)
        c["permissions"] = sorted((set(c.get("permissions") or []) - pil) | (pil - refus))

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
        self.comptes[ident] = {"nom": nom, "mdp": hacher(mot_de_passe), "admin": True, "groupe": D.ADMIN, "gen": 1}
        self._ecrire()
        return ident

    def definir_mot_de_passe(self, compte: str, mot_de_passe: str) -> None:
        c = self.comptes[compte]
        c["mdp"] = hacher(mot_de_passe)
        c["gen"] = c.get("gen", 0) + 1          # ferme les sessions existantes de ce compte
        c.pop("invitation", None)
        self._ecrire()

    def creer_compte(self, nom: str, admin: bool = False, groupe: str = "") -> tuple[str, str]:
        """Compte sans mot de passe + jeton d'invitation (à usage unique, 7 jours), dans un groupe."""
        ident = identifiant(nom)
        if ident in self.comptes or ident == COMPTE_MACHINE:
            raise AccesRefuse(f"le compte « {nom} » existe déjà")
        groupe = D.ADMIN if admin else (groupe or D.GROUPE_PAR_DEFAUT)
        if groupe not in self.groupes:
            raise D.DroitInvalide(f"groupe inconnu : {groupe}")
        self.comptes[ident] = {"nom": nom.strip(), "mdp": None, "admin": groupe == D.ADMIN, "groupe": groupe,
                               "gen": 1}
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
        if compte in self.comptes and self.est_admin(compte) and self._nb_admins() <= 1:
            raise AccesRefuse("impossible de supprimer le dernier administrateur")
        self.comptes.pop(compte, None)
        self._ecrire()

    def reinitialiser_totp(self, compte: str) -> None:
        c = self.comptes[compte]
        for cle in ("totp", "totp_attente", "totp_pas", "secours"):
            c.pop(cle, None)
        c["gen"] = c.get("gen", 0) + 1
        self._ecrire()

    # --- profils -----------------------------------------------------------------------------------
    def profil(self, compte: str | None) -> dict:
        """Ton, consigne personnelle, pièce par défaut, type (adulte / enfant) et droits du compte."""
        p = dict(PROFIL_DEFAUT)
        p.update({k: v for k, v in ((self.comptes.get(compte or "") or {}).get("profil") or {}).items()
                  if k in PROFIL_DEFAUT})
        p["interdits"] = D.interdits(self.permissions(compte)) if compte in self.comptes else []
        # le ton « enfant » vient du groupe (case « parler comme à un enfant »)
        enfant = compte in self.comptes and bool(self.groupes.get(self.groupe_de(compte), {}).get("enfant"))
        p["type"] = "enfant" if enfant else "adulte"
        return p

    def definir_profil(self, compte: str, maj: dict) -> dict:
        c = self.comptes[compte]
        p = {k: v for k, v in {**PROFIL_DEFAUT, **(c.get("profil") or {})}.items() if k in PROFIL_DEFAUT}
        for cle, val in maj.items():
            if cle in PROFIL_DEFAUT and val is not None:
                p[cle] = val
        c["profil"] = p
        if maj.get("interdits") is not None:     # « ne pilote pas : … » (ancien écran)
            self._choisir_categories(c, maj["interdits"])
        self._ecrire()
        return self.profil(compte)

    def est_admin(self, compte: str | None) -> bool:
        return compte == COMPTE_MACHINE or (compte in self.comptes and self.groupe_de(compte) == D.ADMIN)

    def _nb_admins(self) -> int:
        return sum(1 for i in self.comptes if self.groupe_de(i) == D.ADMIN)

    # --- groupes et permissions ------------------------------------------------------------------------
    @property
    def groupes(self) -> dict[str, dict]:
        return self._etat.setdefault("groupes", {})

    def groupe_de(self, compte: str | None) -> str:
        c = self.comptes.get(compte or "") or {}
        g = c.get("groupe") or (D.ADMIN if c.get("admin") else D.GROUPE_PAR_DEFAUT)
        return g if g in self.groupes else D.GROUPE_PAR_DEFAUT

    def permissions(self, compte: str | None) -> set[str]:
        """Permissions effectives ; le groupe Admin (et la machine elle-même) a tout."""
        if self.est_admin(compte):
            return set(D.PERMISSIONS)
        c = self.comptes.get(compte or "")
        if not c:
            return set()
        return D.effectif(self.groupes, self.groupe_de(compte), c.get("permissions"), c.get("retraits"))

    def peut(self, compte: str | None, permission: str) -> bool:
        if permission not in D.PERMISSIONS:
            raise D.DroitInvalide(f"permission inconnue : {permission}")
        return permission in self.permissions(compte)

    def definir_droits(self, compte: str, groupe: str | None = None, permissions: list[str] | None = None,
                       retraits: list[str] | None = None) -> None:
        """Son groupe, et ses exceptions (ajouts, retraits). Le dernier Admin le reste."""
        c = self.comptes[compte]
        if groupe is not None:
            if groupe not in self.groupes:
                raise D.DroitInvalide(f"groupe inconnu : {groupe}")
            if self.groupe_de(compte) == D.ADMIN and groupe != D.ADMIN and self._nb_admins() <= 1:
                raise AccesRefuse("il faut garder au moins un administrateur")
        nouvelles = D.valider(permissions) if permissions is not None else None
        nouveaux_r = D.valider(retraits) if retraits is not None else None
        if groupe is not None:
            c["groupe"] = groupe
            c["admin"] = groupe == D.ADMIN           # gardé pour une version plus ancienne
        if nouvelles is not None:
            c["permissions"] = nouvelles
        if nouveaux_r is not None:
            c["retraits"] = nouveaux_r
        self._ecrire()

    def enregistrer_groupe(self, gid: str, nom: str, permissions=(), enfant: bool = False) -> str:
        """Crée (gid vide) ou modifie un groupe. Le groupe Admin ne se modifie pas (il a tout)."""
        if not nom.strip():
            raise D.DroitInvalide("un groupe a un nom")
        gid = gid or D.ident_groupe(nom)
        if not gid:
            raise D.DroitInvalide("nom de groupe invalide")
        if gid == D.ADMIN:
            raise D.DroitInvalide("le groupe Admin a tous les droits et ne se modifie pas")
        self.groupes[gid] = {"nom": nom.strip()[:40], "permissions": D.valider(permissions), "enfant": bool(enfant)}
        self._ecrire()
        return gid

    def supprimer_groupe(self, gid: str) -> None:
        if gid not in self.groupes:
            raise D.DroitInvalide("groupe inconnu")
        if gid in (D.ADMIN, D.GROUPE_PAR_DEFAUT):
            raise D.DroitInvalide("les groupes Admin et Foyer restent (Foyer accueille les nouveaux comptes)")
        membres = [c.get("nom", i) for i, c in self.comptes.items() if self.groupe_de(i) == gid]
        if membres:
            raise D.DroitInvalide(f"il a encore des membres : {', '.join(membres)}")
        del self.groupes[gid]
        self._ecrire()

    def liste_groupes(self) -> list[dict]:
        return [{"id": g, "nom": v.get("nom", g), "enfant": bool(v.get("enfant")), "fige": g == D.ADMIN,
                 "permissions": sorted(D.effectif(self.groupes, g)),
                 "membres": [i for i in self.comptes if self.groupe_de(i) == g]} for g, v in self.groupes.items()]

    def liste(self) -> list[dict]:
        return [{"id": i, "nom": c.get("nom", i), "admin": self.est_admin(i), "actif": bool(c.get("mdp")),
                 "type": self.profil(i)["type"], "interdits": self.profil(i)["interdits"],
                 "groupe": self.groupe_de(i), "permissions_propres": c.get("permissions") or [],
                 "retraits": c.get("retraits") or [], "effectives": sorted(self.permissions(i)),
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
