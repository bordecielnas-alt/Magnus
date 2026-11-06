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
CONTEXTE_MAX_S = 600  # « éteins-le », « la cuisine aussi » : valables dix minutes
PAS_RELATIF = {"light": 20, "cover": 20, "media_player": 10, "fan": 20, "climate": 1}


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


@dataclass
class _Contexte:
    commande: Commande            # dernière commande exécutée
    entites: list[Entite]         # appareils concernés
    avant: list[dict]             # états lus juste avant l'action (pour « remets comme avant »)
    depuis: float


def _restauration(s: dict) -> tuple[str, str, dict] | None:
    """Service qui remet une entité dans l'état `s` (état HA lu avant une action)."""
    eid = s["entity_id"]
    d, etat, a = eid.split(".", 1)[0], s.get("state"), s.get("attributes") or {}
    ident = {"entity_id": eid}
    if etat in ("unavailable", "unknown", None):
        return None
    if d == "light":
        if etat == "off":
            return d, "turn_off", ident
        data = dict(ident)
        if a.get("brightness") is not None:
            data["brightness"] = a["brightness"]
        mode = a.get("color_mode")
        if mode == "color_temp" and a.get("color_temp_kelvin"):
            data["color_temp_kelvin"] = a["color_temp_kelvin"]
        elif mode in ("hs", "rgb", "rgbw", "rgbww", "xy") and a.get("rgb_color"):
            data["rgb_color"] = a["rgb_color"]
        return d, "turn_on", data
    if d in ("switch", "fan", "media_player") and etat in ("on", "off"):
        if d == "fan" and etat == "on" and a.get("percentage") is not None:
            return d, "set_percentage", {**ident, "percentage": a["percentage"]}
        return d, "turn_on" if etat == "on" else "turn_off", ident
    if d == "media_player" and a.get("volume_level") is not None:
        return d, "volume_set", {**ident, "volume_level": a["volume_level"]}
    if d == "cover" and a.get("current_position") is not None:
        return d, "set_cover_position", {**ident, "position": a["current_position"]}
    if d == "cover" and etat in ("open", "closed"):
        return d, "open_cover" if etat == "open" else "close_cover", ident
    if d == "climate" and a.get("temperature") is not None:
        return d, "set_temperature", {**ident, "temperature": a["temperature"]}
    return None


def _relatif(e: Entite, s: dict | None, sens: float) -> tuple[str, str, dict] | None:
    """Plus / moins : luminosité, volume, position, vitesse ou consigne, d'un cran."""
    d, ident, pas = e.domaine, {"entity_id": e.entity_id}, PAS_RELATIF.get(e.domaine, 0)
    a = (s or {}).get("attributes") or {}
    if d == "light":
        return d, "turn_on", {**ident, "brightness_step_pct": pas * sens}
    if d == "fan":
        return d, "set_percentage", {**ident, "percentage": max(0, min(100, (a.get("percentage") or 0) + pas * sens))}
    if d == "cover" and a.get("current_position") is not None:
        return d, "set_cover_position", {**ident, "position": int(max(0, min(100, a["current_position"] + pas * sens)))}
    if d == "media_player" and a.get("volume_level") is not None:
        return d, "volume_set", {**ident, "volume_level": round(max(0, min(1, a["volume_level"] + pas * sens / 100)), 2)}
    if d == "climate" and a.get("temperature") is not None:
        return d, "set_temperature", {**ident, "temperature": a["temperature"] + pas * sens}
    return None


def _service(action: str, e: Entite, valeur: float | None,
             couleur: str | None = None, kelvin: int | None = None) -> tuple[str, str, dict] | None:
    d, ident = e.domaine, {"entity_id": e.entity_id}
    if action == "allumer" and d == "light" and (couleur or kelvin or valeur is not None):
        data = dict(ident)
        if couleur:
            data["color_name"] = couleur
        if kelvin:
            data["color_temp_kelvin"] = kelvin
        if valeur is not None:
            data["brightness_pct"] = max(0, min(100, valeur))
        return d, "turn_on", data
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
        self._contextes: dict[str, _Contexte] = {}

    def _contexte(self, conversation: str) -> _Contexte | None:
        c = self._contextes.get(conversation)
        return c if c and time.monotonic() - c.depuis < CONTEXTE_MAX_S else None

    def reconnait(self, texte: str, conversation: str = "") -> bool:
        """La phrase ressemble-t-elle à une commande ou une question sur un appareil ?
        Avec un contexte récent, les suites (« éteins-le », « plus fort ») comptent aussi."""
        return par_regles(texte, avec_contexte=self._contexte(conversation) is not None) is not None

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
                r = await self._executer(attente.commande, choix, conversation)
                r.detail.update(alias_appris=cle, duree_ms=ms())
                return r

        ctx = self._contexte(conversation)
        cmd = par_regles(texte, avec_contexte=ctx is not None)
        origine = "règles"
        if cmd and cmd.action == "restaurer" and not ctx:
            return ReponseMaison(True, "Je n'ai pas d'action récente à annuler dans cette conversation. Rien n'a été fait.",
                                 {"resolution": "rien à annuler", "duree_ms": ms()})
        if cmd and ctx and cmd.action in ("restaurer", "relatif"):
            r = await (self._restaurer(ctx) if cmd.action == "restaurer" else self._plus_moins(ctx, cmd.valeur or 1))
            r.detail.update(analyse="suite de conversation", resolution=cmd.action, duree_ms=ms())
            if cmd.action == "restaurer":
                ctx.avant = []   # on garde les appareils (« plus fort » reste possible), pas un 2e retour
            return r
        if cmd and ctx and cmd.vers_contexte:
            # « éteins-le » : mêmes appareils que la dernière fois
            r = await self._executer(cmd, ctx.entites, conversation)
            r.detail.update(analyse="suite de conversation", resolution="mêmes appareils", duree_ms=ms())
            return r
        suite_meme = bool(cmd and ctx and cmd.action == "meme")
        if suite_meme:
            # « la cuisine aussi » : même action, nouvelle cible
            cmd = Commande(ctx.commande.action, cmd.cible, ctx.commande.valeur, cmd.tous,
                           ctx.commande.couleur, ctx.commande.kelvin)
            origine = "suite de conversation"
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
            if suite_meme:  # « moi aussi » n'était sans doute pas une commande : on laisse le modèle répondre
                return ReponseMaison(False, detail={"raison": "suite non reconnue", "duree_ms": ms()})
            return ReponseMaison(False, self.introuvable(cmd.cible), {**detail, "duree_ms": ms()})
        r = await self._executer(cmd, res.entites, conversation)
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

    async def apprendre_d_assist(self, texte: str, entity_ids: list[str], conversation: str = "") -> str:
        """L'agent Assist a agi là où la maison n'a rien trouvé : on retient la cible comme alias
        (la prochaine fois, la maison la pilotera elle-même) et on garde le contexte (« éteins-le »)."""
        cmd = par_regles(texte)
        if not cmd or not cmd.cible or not entity_ids:
            return ""
        entites = [e for e in await self.inventaire.obtenir() if e.entity_id in set(entity_ids)]
        if not entites:
            return ""
        cle = self.alias.apprendre(cmd.cible, [e.entity_id for e in entites], source="assist")
        self._contextes[conversation] = _Contexte(cmd, entites, [], time.monotonic())
        return cle

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

    async def _executer(self, cmd: Commande, entites: list[Entite], conversation: str = "") -> ReponseMaison:
        ids = [e.entity_id for e in entites]
        if cmd.action == "etat":
            lignes = []
            for e in entites:
                s = await self.ha.etat_entite(e.entity_id)
                lignes.append(_decrire_etat(e, s) if s else f"{e.libelle} : état inconnu.")
            # « éteins-le » juste après « le projo est-il allumé ? » vise le projo
            self._contextes[conversation] = _Contexte(cmd, entites, [], time.monotonic())
            return ReponseMaison(True, " ".join(lignes), {"entites": ids})
        avant = [s for s in [await self.ha.etat_entite(e.entity_id) for e in entites] if s]
        faits, rates, impossibles = [], [], []
        for e in entites:
            svc = _service(cmd.action, e, cmd.valeur, cmd.couleur, cmd.kelvin)
            if not svc:
                impossibles.append(e)
                continue
            ok = await self.ha.appeler_service(*svc)
            (faits if ok else rates).append(e)
        if faits:
            self._contextes[conversation] = _Contexte(cmd, faits, avant, time.monotonic())
        morceaux = []
        if faits:
            verbe = VERBES_FAIT.get(cmd.action, "fait")
            details = []
            if cmd.valeur is not None:
                details.append(f"{cmd.valeur:g} °C" if faits[0].domaine == "climate" else f"{cmd.valeur:g} %")
            if cmd.couleur or cmd.kelvin:
                details.append(_nom_couleur(cmd))
            val = f" ({', '.join(details)})" if details else ""
            morceaux.append(f"C'est fait : {', '.join(e.libelle for e in faits)} — {verbe}{val}.")
        if rates:
            morceaux.append(f"Home Assistant a refusé : {', '.join(e.libelle for e in rates)}.")
        if impossibles:
            morceaux.append(f"Action impossible sur : {', '.join(e.libelle for e in impossibles)}.")
        return ReponseMaison(True, " ".join(morceaux), {"entites": ids, "echecs": [e.entity_id for e in rates]})


    async def _restaurer(self, ctx: _Contexte) -> ReponseMaison:
        if not ctx.avant:
            return ReponseMaison(True, "Je n'ai pas d'état précédent à remettre pour ces appareils.")
        faits, rates = [], []
        noms = {e.entity_id: e.libelle for e in ctx.entites}
        for s in ctx.avant:
            svc = _restauration(s)
            if not svc:
                continue
            ok = await self.ha.appeler_service(*svc)
            (faits if ok else rates).append(noms.get(s["entity_id"], s["entity_id"]))
        if not faits and not rates:
            return ReponseMaison(True, "Je ne sais pas remettre l'état précédent de ces appareils.")
        txt = f"C'est remis comme avant : {', '.join(faits)}." if faits else ""
        if rates:
            txt += f" Home Assistant a refusé : {', '.join(rates)}."
        return ReponseMaison(True, txt.strip(), {"entites": [s["entity_id"] for s in ctx.avant]})

    async def _plus_moins(self, ctx: _Contexte, sens: float) -> ReponseMaison:
        avant = [await self.ha.etat_entite(e.entity_id) for e in ctx.entites]
        faits, impossibles = [], []
        for e, s in zip(ctx.entites, avant):
            svc = _relatif(e, s, sens)
            if svc and await self.ha.appeler_service(*svc):
                faits.append(e)
            else:
                impossibles.append(e)
        if faits:
            self._contextes_maj(ctx, [x for x in avant if x])
        mot = "plus" if sens > 0 else "moins"
        txt = f"C'est fait : {', '.join(e.libelle for e in faits)} — un cran {mot}." if faits else ""
        if impossibles:
            txt += f" Je ne sais pas régler {', '.join(e.libelle for e in impossibles)} en {mot}."
        return ReponseMaison(True, txt.strip(), {"entites": [e.entity_id for e in ctx.entites]})

    def _contextes_maj(self, ctx: _Contexte, avant: list[dict]) -> None:
        ctx.avant, ctx.depuis = avant, time.monotonic()


NOMS_COULEURS = {"red": "rouge", "blue": "bleu", "green": "vert", "yellow": "jaune", "orange": "orange",
                 "purple": "violet", "pink": "rose", "cyan": "cyan", "turquoise": "turquoise",
                 "magenta": "magenta", "indigo": "indigo", "gold": "doré"}


def _nom_couleur(cmd: Commande) -> str:
    if cmd.couleur:
        return NOMS_COULEURS.get(cmd.couleur, cmd.couleur)
    k = cmd.kelvin or 0
    return "blanc chaud" if k <= 3000 else "blanc froid" if k >= 5500 else "blanc"
