"""Pilotage de la maison : comprendre la commande, trouver l'appareil, agir via Home Assistant."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..config import MaisonConfig
from ..fournisseurs import Fournisseur
from ..home_assistant import HomeAssistant
from .alias import AliasAppris
from .commande import Commande, corriger_verbe, par_modele, par_regles
from .inventaire import Entite, Inventaire
from .resolution import resoudre
from .texte import mots, normaliser

ETATS_FR = {
    "on": "allumé", "off": "éteint", "open": "ouvert", "closed": "fermé", "opening": "en train de s'ouvrir",
    "closing": "en train de se fermer", "playing": "en lecture", "paused": "en pause", "idle": "inactif",
    "heat": "en chauffe", "cool": "en refroidissement", "unavailable": "indisponible", "unknown": "inconnu",
}
ATTENTE_MAX_S = 120   # une question « lequel ? » reste valable deux minutes


@dataclass
class ReponseMaison:
    traite: bool                  # True : la maison a répondu (action, état, ou question posée)
    texte: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class _Attente:
    commande: Commande
    candidats: list[Entite]
    depuis: float


def _service(action: str, e: Entite, valeur: float | None) -> tuple[str, str, dict] | None:
    d, ident = e.domaine, {"entity_id": e.entity_id}
    if action == "allumer":
        return (d, "open_cover", ident) if d == "cover" else (d, "turn_on", ident)
    if action == "eteindre":
        return (d, "close_cover", ident) if d == "cover" else (d, "turn_off", ident)
    if action == "ouvrir" and d == "cover":
        return d, "open_cover", ident
    if action == "fermer" and d == "cover":
        return d, "close_cover", ident
    if action == "regler" and valeur is not None:
        if d == "light":
            return d, "turn_on", {**ident, "brightness_pct": max(0, min(100, valeur))}
        if d == "climate":
            return d, "set_temperature", {**ident, "temperature": valeur}
        if d == "cover":
            return d, "set_cover_position", {**ident, "position": max(0, min(100, int(valeur)))}
        if d == "media_player":
            return d, "volume_set", {**ident, "volume_level": max(0, min(100, valeur)) / 100}
        if d == "fan":
            return d, "set_percentage", {**ident, "percentage": max(0, min(100, int(valeur)))}
    return None


def _decrire_etat(e: Entite, s: dict) -> str:
    etat = s.get("state", "")
    a = s.get("attributes") or {}
    txt = ETATS_FR.get(etat, etat)
    if e.domaine == "climate":
        actuelle, consigne = a.get("current_temperature"), a.get("temperature")
        bout = [txt]
        if actuelle is not None:
            bout.append(f"il fait {actuelle} °C")
        if consigne is not None:
            bout.append(f"consigne {consigne} °C")
        txt = ", ".join(bout)
    elif e.domaine == "light" and etat == "on" and a.get("brightness"):
        txt += f" à {round(a['brightness'] / 255 * 100)} %"
    elif e.domaine == "cover" and a.get("current_position") is not None and etat not in ("closed",):
        txt += f" à {a['current_position']} %"
    return f"{e.libelle} : {txt}."


VERBES_FAIT = {"allumer": "allumé", "eteindre": "éteint", "ouvrir": "ouvert", "fermer": "fermé", "regler": "réglé"}


class Maison:
    def __init__(self, cfg: MaisonConfig, ha: HomeAssistant, alias: AliasAppris,
                 extracteur: Fournisseur | None = None) -> None:
        self.cfg = cfg
        self.ha = ha
        self.alias = alias
        self.extracteur = extracteur
        self.inventaire = Inventaire(ha, cfg.domaines_autorises, cfg.rafraichir_s)
        self._attentes: dict[str, _Attente] = {}

    def reconnait(self, texte: str) -> bool:
        """La phrase ressemble-t-elle à une commande ou une question sur un appareil ?"""
        return par_regles(texte) is not None

    def en_attente(self, conversation: str) -> bool:
        a = self._attentes.get(conversation)
        return bool(a and time.monotonic() - a.depuis < ATTENTE_MAX_S)

    async def traiter(self, texte: str, conversation: str = "") -> ReponseMaison:
        debut = time.perf_counter()
        ms = lambda: round((time.perf_counter() - debut) * 1000)  # noqa: E731

        attente = self._attentes.pop(conversation, None)
        if attente and time.monotonic() - attente.depuis < ATTENTE_MAX_S:
            choix = self._choisir(texte, attente.candidats)
            if choix:
                cle = self.alias.apprendre(attente.commande.cible, [e.entity_id for e in choix])
                r = await self._executer(attente.commande, choix)
                r.detail.update(alias_appris=cle, duree_ms=ms())
                return r

        cmd = par_regles(texte)
        origine = "règles"
        if cmd is None and self.extracteur and self.cfg.extraction_par_modele:
            cmd = await par_modele(texte, self.extracteur)
            origine = "modèle"
        if cmd is None:
            return ReponseMaison(False, detail={"raison": "pas une commande de maison", "duree_ms": ms()})

        entites = await self.inventaire.obtenir()
        if not entites:
            return ReponseMaison(False, detail={"commande": cmd.__dict__, "analyse": origine,
                                                "raison": self.inventaire.erreur or "inventaire vide", "duree_ms": ms()})
        res = resoudre(cmd.cible, cmd.action, entites, cmd.tous, self.alias.table())
        detail = {"commande": cmd.__dict__, "analyse": origine, "resolution": res.raison}
        if res.ambigues:
            self._attentes[conversation] = _Attente(cmd, res.ambigues, time.monotonic())
            noms = [f"{i}. {e.libelle}" for i, e in enumerate(res.ambigues, 1)]
            q = "Lequel ? " + " ; ".join(noms) + ". Réponds par le numéro ou le nom (ou « tous »)."
            return ReponseMaison(True, q, {**detail, "candidats": [e.entity_id for e in res.ambigues], "duree_ms": ms()})
        if not res.entites:
            return ReponseMaison(False, self.introuvable(cmd.cible), {**detail, "duree_ms": ms()})
        r = await self._executer(cmd, res.entites)
        r.detail.update(detail, duree_ms=ms())
        return r

    def introuvable(self, cible: str) -> str:
        """Phrase honnête quand aucun appareil ne correspond : explication si l'appareil est écarté,
        sinon les noms les plus proches."""
        c = mots(cible) - {"tou", "tout", "toute"}
        propre = cible.strip().rstrip(" ?!.")
        for e in self.inventaire.ecartees:
            if c and c <= (mots(e.nom) | mots(e.entity_id.split(".", 1)[1].replace("_", " "))):
                return (f"Je trouve « {e.nom} » ({e.entity_id}), mais je ne le pilote pas : {e.raison}. "
                        "Rien n'a été fait.")
        proches = sorted(self.inventaire.entites, key=lambda e: -len(c & (e.mots_nom() | e.mots_piece())))
        proches = [e for e in proches[:3] if c & (e.mots_nom() | e.mots_piece())]
        suite = f" Tu voulais dire : {', '.join(e.libelle for e in proches)} ?" if proches else ""
        return f"Je ne trouve aucun appareil qui corresponde à « {propre} ». Rien n'a été fait.{suite}"

    def texte_pour_assist(self, texte: str) -> str:
        """Phrase envoyée à l'agent Assist : verbe mal tapé corrigé."""
        return corriger_verbe(texte.strip())

    def _choisir(self, texte: str, candidats: list[Entite]) -> list[Entite]:
        t = normaliser(texte)
        if re.fullmatch(r"(tous|toutes|les deux|tout)", t):
            return candidats
        ordinaux = {"premier": 1, "premiere": 1, "deuxieme": 2, "second": 2, "seconde": 2, "troisieme": 3,
                    "quatrieme": 4, "cinquieme": 5}
        m = re.search(r"\b(\d)\b", t)
        n = int(m.group(1)) if m else next((v for k, v in ordinaux.items() if k in t.split()), 0)
        if 1 <= n <= len(candidats):
            return [candidats[n - 1]]
        m_txt = mots(texte)
        notes = [(len(m_txt & (e.mots_nom() | e.mots_piece())), e) for e in candidats]
        notes.sort(key=lambda x: -x[0])
        if notes and notes[0][0] > 0 and (len(notes) == 1 or notes[0][0] > notes[1][0]):
            return [notes[0][1]]
        return []

    async def _executer(self, cmd: Commande, entites: list[Entite]) -> ReponseMaison:
        ids = [e.entity_id for e in entites]
        if cmd.action == "etat":
            lignes = []
            for e in entites:
                s = await self.ha.etat_entite(e.entity_id)
                lignes.append(_decrire_etat(e, s) if s else f"{e.libelle} : état inconnu.")
            return ReponseMaison(True, " ".join(lignes), {"entites": ids})
        faits, rates, impossibles = [], [], []
        for e in entites:
            svc = _service(cmd.action, e, cmd.valeur)
            if not svc:
                impossibles.append(e)
                continue
            ok = await self.ha.appeler_service(*svc)
            (faits if ok else rates).append(e)
        morceaux = []
        if faits:
            verbe = VERBES_FAIT.get(cmd.action, "fait")
            val = f" ({cmd.valeur:g}{' %' if cmd.action == 'regler' and faits[0].domaine != 'climate' else ' °C' if cmd.action == 'regler' else ''})" if cmd.valeur is not None else ""
            morceaux.append(f"C'est fait : {', '.join(e.libelle for e in faits)} — {verbe}{val}.")
        if rates:
            morceaux.append(f"Home Assistant a refusé : {', '.join(e.libelle for e in rates)}.")
        if impossibles:
            morceaux.append(f"Action impossible sur : {', '.join(e.libelle for e in impossibles)}.")
        return ReponseMaison(True, " ".join(morceaux), {"entites": ids, "echecs": [e.entity_id for e in rates]})
