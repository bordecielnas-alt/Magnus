"""Règles « si… alors… » dictées à la voix (« si la température du salon descend sous 19 °C, allume le radiateur »).

Une règle = une condition + des actions, gardée dans <état>/regles.json :

- condition, un de ces types :
  - presence : {"type": "presence", "evenement": "arrive" | "part", "entity_id"?: "person.x"}
    sans entity_id : « quelqu'un arrive » (le premier) / « tout le monde est parti » (le dernier) ;
  - seuil : {"type": "seuil", "entity_id", "comparaison": "<" | ">", "valeur", "attribut"?}
    (température, humidité, luminosité… ; « attribut » pour un thermostat : current_temperature) ;
  - etat : {"type": "etat", "entity_id", "etat": "on" | "open" | …, "minutes"?: 0}
    (« si la porte du garage reste ouverte plus de 10 minutes »).
- plage (facultative) : « 22:00-07:00 », la règle ne se déclenche que dans ce créneau.
- actions : les mêmes étapes que les routines {entity_ids, action, valeur?, couleur?} (mêmes garde-fous :
  domaines autorisés, actions sensibles refusées) et/ou une annonce à dire (« préviens-moi »).

L'assistant reformule la règle (texte construit ici à partir de la structure, pas par le modèle) et ne l'enregistre
qu'après un « oui ». Une règle se déclenche sur un front : quand la condition devient vraie, pas tant qu'elle
l'est (avec une marge pour les seuils, pour ne pas répéter quand la valeur oscille). Chaque règle peut être mise
en pause sans être supprimée.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

from .habitudes import ACTIONS_ROUTINE
from .proactif import dans_heures_calmes

TYPES = ("presence", "seuil", "etat")
MARGE_SEUIL = 0.5            # la condition « sous 19 » ne redevient fausse qu'à 19,5 (pas de répétition)
ETATS_FR = {"on": "allumé", "off": "éteint", "open": "ouvert", "closed": "fermé", "home": "à la maison",
            "not_home": "absent", "unavailable": "injoignable", "locked": "verrouillé", "unlocked": "déverrouillé",
            "playing": "en lecture", "detected": "détecté", "clear": "dégagé"}
VERBES = {"allumer": "j'allume", "eteindre": "j'éteins", "ouvrir": "j'ouvre", "fermer": "je ferme", "regler": "je règle"}


def maintenant() -> datetime:
    return datetime.now().astimezone()


def _heure(h: str) -> str:
    return h.replace(":", "h")


def _nombre(v: float) -> str:
    return (f"{v:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def normaliser(condition: dict, actions: list[dict], annonce: str = "", plage: str = "") -> tuple[dict, list[dict], str, str]:
    """Vérifie et nettoie une règle proposée ; ValueError avec une raison lisible sinon."""
    t = condition.get("type")
    if t not in TYPES:
        raise ValueError(f"type de condition inconnu : {t!r} (presence, seuil ou etat)")
    eid = str(condition.get("entity_id") or "").strip()
    if t == "presence":
        ev = condition.get("evenement")
        if ev not in ("arrive", "part"):
            raise ValueError("presence : evenement doit être « arrive » ou « part »")
        if eid and not eid.startswith("person."):
            raise ValueError("presence : entity_id doit être une personne (person.…) ou vide")
        c = {"type": t, "evenement": ev, **({"entity_id": eid} if eid else {})}
    elif t == "seuil":
        if "." not in eid:
            raise ValueError("seuil : entity_id manquant")
        comp = {"<": "<", ">": ">", "sous": "<", "dessous": "<", "au-dessus": ">", "dessus": ">"}.get(
            str(condition.get("comparaison", "")).strip())
        if not comp:
            raise ValueError("seuil : comparaison doit être « < » ou « > »")
        try:
            v = float(condition["valeur"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError("seuil : valeur numérique manquante") from e
        c = {"type": t, "entity_id": eid, "comparaison": comp, "valeur": v,
             **({"attribut": str(condition["attribut"])[:40]} if condition.get("attribut") else {})}
    else:
        if "." not in eid or not str(condition.get("etat") or "").strip():
            raise ValueError("etat : entity_id et etat sont nécessaires")
        try:
            m = max(0.0, min(float(condition.get("minutes") or 0), 24 * 60))
        except (TypeError, ValueError) as e:
            raise ValueError("etat : minutes doit être un nombre") from e
        c = {"type": t, "entity_id": eid, "etat": str(condition["etat"]).strip()[:40], "minutes": m}
    propres = []
    for e in actions or []:
        if e.get("action") not in ACTIONS_ROUTINE or not e.get("entity_ids"):
            raise ValueError(f"action invalide : {e}")
        propres.append({"entity_ids": [str(x) for x in e["entity_ids"]][:30], "action": e["action"],
                        **({"valeur": float(e["valeur"])} if e.get("valeur") is not None else {}),
                        **({"couleur": str(e["couleur"])[:30]} if e.get("couleur") else {})})
    if len(propres) > 10:
        raise ValueError("10 actions au plus")
    annonce = " ".join(str(annonce or "").split())[:200]
    if not propres and not annonce:
        raise ValueError("une règle fait au moins une action ou une annonce")
    plage = (plage or "").replace(" ", "")
    if plage:
        try:
            d, f = plage.split("-")
            plage = f"{d.zfill(5)}-{f.zfill(5)}"
            dans_heures_calmes(plage)
        except ValueError as e:
            raise ValueError("plage au format 22:00-07:00") from e
    return c, propres, annonce, plage


def decrire(r: dict, noms: dict[str, str] | None = None, unites: dict[str, str] | None = None) -> str:
    """« Quand la température du salon passe sous 19 °C, j'allume le radiateur. » : ce que l'assistant relit."""
    noms, unites = noms or {}, unites or {}
    c = r["condition"]
    nom = noms.get(c.get("entity_id", ""), c.get("entity_id", ""))
    if c["type"] == "presence":
        if c.get("entity_id"):
            quand = f"quand {nom} arrive à la maison" if c["evenement"] == "arrive" else f"quand {nom} quitte la maison"
        else:
            quand = "quand quelqu'un arrive à la maison" if c["evenement"] == "arrive" else "quand tout le monde est parti"
    elif c["type"] == "seuil":
        u = unites.get(c["entity_id"], "")
        sens = "passe sous" if c["comparaison"] == "<" else "passe au-dessus de"
        quand = f"quand {nom} {sens} {_nombre(c['valeur'])}{(' ' + u) if u else ''}"
    else:
        etat = ETATS_FR.get(c["etat"], c["etat"])
        quand = (f"quand {nom} reste {etat} plus de {_nombre(c['minutes'])} minutes" if c.get("minutes")
                 else f"quand {nom} devient {etat}")
    if r.get("plage"):
        d, f = r["plage"].split("-")
        quand += f", entre {_heure(d)} et {_heure(f)}"
    faits = []
    for e in r.get("actions", []):
        cibles = ", ".join(noms.get(i, i) for i in e["entity_ids"])
        txt = f"{VERBES.get(e['action'], e['action'])} {cibles}"
        if e.get("valeur") is not None:
            u = "°C" if any(i.startswith("climate.") for i in e["entity_ids"]) else "%"
            txt += f" à {_nombre(e['valeur'])} {u}"
        if e.get("couleur"):
            txt += f" en {e['couleur']}"
        faits.append(txt)
    if r.get("annonce"):
        faits.append(f"je préviens : « {r['annonce']} »")
    alors = faits[0] if len(faits) == 1 else ", ".join(faits[:-1]) + " et " + faits[-1]
    return f"{quand[:1].upper()}{quand[1:]}, {alors}."


def valeur_condition(c: dict, etats: dict[str, dict], precedente: bool | None) -> bool | None:
    """La condition est-elle vraie maintenant ? None = on ne sait pas (capteur absent ou illisible)."""
    if c["type"] == "presence":
        if c.get("entity_id"):
            x = etats.get(c["entity_id"])
            if not x:
                return None
            chez_soi = x.get("state") == "home"
        else:
            personnes = [x for k, x in etats.items() if k.startswith("person.")]
            if not personnes:
                return None
            chez_soi = any(x.get("state") == "home" for x in personnes)
        return chez_soi if c["evenement"] == "arrive" else not chez_soi
    x = etats.get(c["entity_id"])
    if not x:
        return None
    if c["type"] == "seuil":
        brut = (x.get("attributes") or {}).get(c["attribut"]) if c.get("attribut") else x.get("state")
        try:
            v = float(brut)
        except (TypeError, ValueError):
            return None
        if c["comparaison"] == "<":
            return v < c["valeur"] if not precedente else v < c["valeur"] + MARGE_SEUIL
        return v > c["valeur"] if not precedente else v > c["valeur"] - MARGE_SEUIL
    if x.get("state") != c["etat"]:
        return False
    if not c.get("minutes"):
        return True
    try:
        depuis = datetime.fromisoformat(str(x.get("last_changed", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(depuis.tzinfo) - depuis).total_seconds() / 60 >= c["minutes"]


class Regles:
    def __init__(self, dossier: Path) -> None:
        self.fichier = dossier / "regles.json"
        self._verrou = threading.Lock()
        self.vraies: dict[str, bool] = {}       # dernière valeur connue de chaque condition (fronts)
        try:
            self.liste: list[dict] = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.liste = []

    def _ecrire(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.liste, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.fichier)

    def enregistrer(self, proposition: dict, compte: str = "") -> dict:
        if len(self.liste) >= 100:
            raise ValueError("100 règles au plus")
        c, actions, annonce, plage = normaliser(proposition["condition"], proposition.get("actions", []),
                                                proposition.get("annonce", ""), proposition.get("plage", ""))
        r = {"id": secrets.token_hex(4), "texte": proposition.get("texte") or "", "condition": c, "actions": actions,
             "annonce": annonce, "plage": plage, "active": True, "compte": compte,
             "creee": maintenant().isoformat(timespec="seconds"), "derniere": "", "resultat": ""}
        if not r["texte"]:
            r["texte"] = decrire(r)
        with self._verrou:
            self.liste.append(r)
            self._ecrire()
        return r

    def trouver(self, ident: str) -> dict | None:
        return next((r for r in self.liste if r["id"] == ident), None)

    def activer(self, ident: str, active: bool) -> dict | None:
        with self._verrou:
            r = self.trouver(ident)
            if r:
                r["active"] = bool(active)
                self.vraies.pop(ident, None)     # à la reprise, pas de déclenchement sur un état déjà vrai
                self._ecrire()
        return r

    def supprimer(self, ident: str) -> dict | None:
        with self._verrou:
            r = self.trouver(ident)
            if r:
                self.liste.remove(r)
                self.vraies.pop(ident, None)
                self._ecrire()
        return r

    def a_declencher(self, etats: dict[str, dict], quand: datetime | None = None) -> tuple[list[dict], list[dict]]:
        """(règles qui viennent de devenir vraies, règles qui viennent de redevenir fausses).

        Au premier passage (ou après une reprise), la valeur est seulement mémorisée : une règle ne se déclenche pas
        parce que l'assistant redémarre alors que la condition est déjà vraie.
        """
        quand = quand or maintenant()
        fronts, retombees = [], []
        for r in self.liste:
            if not r.get("active", True):
                continue
            avant = self.vraies.get(r["id"])
            v = valeur_condition(r["condition"], etats, avant)
            if v is None:
                continue
            self.vraies[r["id"]] = v
            if avant is None:
                continue
            if v and not avant and (not r.get("plage") or dans_heures_calmes(r["plage"], quand)):
                fronts.append(r)
            elif avant and not v:
                retombees.append(r)
        return fronts, retombees

    def noter(self, ident: str, resultat: str) -> None:
        with self._verrou:
            r = self.trouver(ident)
            if r:
                r["derniere"] = maintenant().isoformat(timespec="seconds")
                r["resultat"] = resultat[:300]
                self._ecrire()
