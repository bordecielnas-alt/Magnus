"""Pilotage de la maison : comprendre la commande, trouver l'appareil, agir via Home Assistant."""

from __future__ import annotations

import asyncio
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from ..config import MaisonConfig
from ..fournisseurs import Fournisseur
from ..home_assistant import HomeAssistant
from .alias import AliasAppris
from .historique import Historique

# Pièce par défaut de la personne qui parle (profil) : « allume la lumière » sans pièce y vise d'abord
piece_preferee: ContextVar[str] = ContextVar("piece_preferee", default="")
# Catégories d'informations à ne pas révéler dans le contexte de la demande (distant, appareil partagé, enfant)
masques: ContextVar[frozenset] = ContextVar("masques", default=frozenset())
NON_REVELE = "information non donnée sur cet appareil"
# Domaines que la personne qui parle n'a pas le droit de piloter (profil : « droits par personne »)
interdits: ContextVar[frozenset] = ContextVar("interdits", default=frozenset())
# Catégories proposées dans les profils -> domaines Home Assistant
CATEGORIES_DROITS: dict[str, tuple[str, ...]] = {
    "lumieres": ("light",), "prises": ("switch",), "volets": ("cover",), "chauffage": ("climate", "water_heater"),
    "medias": ("media_player",), "ventilateurs": ("fan",),
}
REFUS_DROITS = "Tu n'as pas le droit de piloter {noms}."


def domaines_interdits(categories) -> frozenset:
    return frozenset(d for c in categories or () for d in CATEGORIES_DROITS.get(c, ()))
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
    type: str = "lequel"          # « lequel » (appareil ambigu) ou « piece » (« tout » sans pièce)


MOTS_TOUTE_LA_MAISON = {"maison", "partout", "appartement", "etage"}


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
             couleur: str | None = None, kelvin: int | None = None,
             hs: tuple[float, float] | None = None) -> tuple[str, str, dict] | None:
    d, ident = e.domaine, {"entity_id": e.entity_id}
    if action == "allumer" and d == "light" and (couleur or kelvin or hs or valeur is not None):
        data = dict(ident)
        if hs:
            data["hs_color"] = [round(hs[0] % 360, 1), round(max(0, min(100, hs[1])), 1)]
        elif couleur:
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


# mots qui évoquent la maison sans nommer d'appareil (« il fait sombre », « ambiance cinéma »)
MOTS_AMBIANCE = {"sombre", "noir", "ambiance", "cinema", "film", "lumineux", "eclaire", "eclairage", "allume",
                 "eteint", "chauffe", "froid", "chaud", "maison", "piece", "nuit", "dodo", "couche", "dors",
                 "pars", "rentre", "reveil", "ouvert", "ferme", "resté", "reste"}


# Phrase qui vise un appareil sensible : jamais confiée à Assist (il pourrait ouvrir une serrure)
RE_SENSIBLE = re.compile(r"\b(serrures?|verrou\w*|d[ée]verrouill\w*|porte d.entr[ée]e|alarmes?|portails?|garages?|"
                         r"chauffages?|thermostats?|radiateurs?|chaudi[eè]res?)\b", re.I)
REFUS_SENSIBLE = "Je ne pilote pas {noms} : action sensible, à faire depuis Home Assistant."

VERBES_FAIT = {"allumer": "allumé", "eteindre": "éteint", "ouvrir": "ouvert", "fermer": "fermé", "regler": "réglé"}


class Maison:
    def __init__(self, cfg: MaisonConfig, ha: HomeAssistant, alias: AliasAppris,
                 extracteur: Fournisseur | None = None, historique: Historique | None = None) -> None:
        self.cfg = cfg
        self.historique = historique
        self.ha = ha
        self.alias = alias
        self.extracteur = extracteur
        self.inventaire = Inventaire(ha, cfg.domaines_autorises, cfg.rafraichir_s)
        self._attentes: dict[str, _Attente] = {}
        self._contextes: dict[str, _Contexte] = {}

    def sensible(self, e: Entite) -> bool:
        """Appareil qu'on ne pilote pas : chauffage, portail, garage, porte… (lecture de l'état permise)."""
        if e.domaine in self.cfg.domaines_sensibles:
            return True
        if e.domaine == "cover" and (e.attributs or {}).get("device_class") in self.cfg.classes_sensibles:
            return True
        if e.domaine == "light":
            return False        # « Lumière garage » reste une lumière
        nom = f"{e.nom} {e.nom_appareil}"
        if e.domaine == "cover" and re.search(r"\b(garage|porte)\b", nom, re.I):
            return True
        return bool(self.cfg.noms_sensibles and re.search(rf"\b({self.cfg.noms_sensibles})\b", nom, re.I))

    def interdit(self, e: Entite) -> bool:
        """Appareil que la personne qui parle n'a pas le droit de piloter (lecture de l'état permise)."""
        return e.domaine in interdits.get()

    def masque(self, e: Entite) -> bool:
        return "etat_sensible" in masques.get() and self.sensible(e)

    def mentionne_sensible(self, texte: str) -> bool:
        return bool(RE_SENSIBLE.search(texte))

    def _contexte(self, conversation: str) -> _Contexte | None:
        c = self._contextes.get(conversation)
        return c if c and time.monotonic() - c.depuis < CONTEXTE_MAX_S else None

    def reconnait(self, texte: str, conversation: str = "") -> bool:
        """La phrase ressemble-t-elle à une commande ou une question sur un appareil ?
        Avec un contexte récent, les suites (« éteins-le », « plus fort ») comptent aussi."""
        return (par_regles(texte, avec_contexte=self._contexte(conversation) is not None) is not None
                or self.reconnait_plusieurs(texte))

    def en_attente(self, conversation: str) -> bool:
        a = self._attentes.get(conversation)
        return bool(a and time.monotonic() - a.depuis < ATTENTE_MAX_S)

    def decouper_ordres(self, texte: str) -> list[str]:
        """« allume le globe et éteins le projo, puis ferme le volet » -> trois ordres.
        On ne coupe que devant un verbe d'ordre : « allume le globe et le projo » reste un seul ordre."""
        from .commande import _RE_ORDRE_DEBUT, corriger_verbe
        morceaux = re.split(r"\s*(?:,|;|\bet\b|\bpuis\b|\bensuite\b)\s+", texte.strip())
        ordres: list[str] = []
        for m in morceaux:
            if not m:
                continue
            if ordres and not _RE_ORDRE_DEBUT.match(corriger_verbe(m)):
                ordres[-1] += " et " + m   # pas un nouveau verbe : suite de l'ordre précédent
            else:
                ordres.append(m)
        return ordres

    def reconnait_plusieurs(self, texte: str) -> bool:
        ordres = self.decouper_ordres(texte)
        return len(ordres) > 1 and all(par_regles(o) is not None for o in ordres)

    async def traiter(self, texte: str, conversation: str = "") -> ReponseMaison:
        ordres = self.decouper_ordres(texte)
        if len(ordres) > 1 and all(par_regles(o) is not None for o in ordres):
            # exécutés dans l'ordre ; une question (« Lequel ? ») arrête la série
            debut = time.perf_counter()
            textes = []
            for o in ordres:
                r = await self._traiter_un(o, conversation)
                textes.append(r.texte or f"« {o} » : pas compris.")
                if self.en_attente(conversation):
                    break
            return ReponseMaison(True, " ".join(textes),
                                 {"resolution": f"{len(ordres)} ordres", "duree_ms": round((time.perf_counter() - debut) * 1000)})
        return await self._traiter_un(texte, conversation)

    async def _traiter_un(self, texte: str, conversation: str = "") -> ReponseMaison:
        debut = time.perf_counter()
        ms = lambda: round((time.perf_counter() - debut) * 1000)  # noqa: E731

        attente = self._attentes.pop(conversation, None)
        if attente and attente.type == "piece" and time.monotonic() - attente.depuis < ATTENTE_MAX_S:
            choix = self._choisir_piece(texte, attente.candidats)
            if choix:
                r = await self._executer(attente.commande, choix, conversation)
                r.detail.update(resolution="pièce précisée", duree_ms=ms())
                return r
            attente = None   # réponse sans pièce reconnue : on la traite comme une nouvelle demande
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
        if cmd and cmd.action == "inventaire":
            return ReponseMaison(True, await self.lister(cmd.cible), {"resolution": "inventaire", "duree_ms": ms()})
        if cmd and cmd.action == "restaurer" and not ctx:
            return ReponseMaison(True, "Rien à annuler.",
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
                           ctx.commande.couleur, ctx.commande.kelvin, ctx.commande.hs)
            origine = "suite de conversation"
        if cmd is None and self.extracteur and self.cfg.extraction_par_modele and await self._evoque_la_maison(texte):
            cmd = await par_modele(texte, self.extracteur)
            origine = "modèle"
        if cmd is None:
            return ReponseMaison(False, detail={"raison": "pas une commande de maison", "duree_ms": ms()})

        entites = await self.inventaire.obtenir()
        if not entites:
            return ReponseMaison(False, detail={"commande": cmd.__dict__, "analyse": origine,
                                                "raison": self.inventaire.erreur or "inventaire vide", "duree_ms": ms()})
        parties = [p.strip() for p in re.split(r"\s*(?:,|\bet\b|\bpuis\b)\s*", cmd.cible) if p.strip()]
        if len(parties) > 1:
            r = await self._plusieurs(cmd, parties, entites, conversation)
            r.detail.update(commande=cmd.__dict__, analyse=origine, duree_ms=ms())
            return r
        res = resoudre(cmd.cible, cmd.action, entites, cmd.tous, self.alias.table())
        detail = {"commande": cmd.__dict__, "analyse": origine, "resolution": res.raison}
        pref = normaliser(piece_preferee.get())
        if res.ambigues and pref:
            chez_moi = [e for e in res.ambigues if pref in {normaliser(e.piece), *map(normaliser, e.alias_piece)}]
            if chez_moi and len(chez_moi) < len(res.ambigues) and (len(chez_moi) == 1 or cmd.tous):
                r = await self._executer(cmd, chez_moi, conversation)
                r.detail.update(commande=cmd.__dict__, analyse=origine, resolution="pièce du profil", duree_ms=ms())
                return r
        if res.ambigues:
            self._attentes[conversation] = _Attente(cmd, res.ambigues, time.monotonic())
            q = f"Lequel : {_ou(_noms_courts(res.ambigues))} ?"
            return ReponseMaison(True, q, {**detail, "candidats": [e.entity_id for e in res.ambigues], "duree_ms": ms()})
        pieces = sorted({e.piece or "Sans pièce" for e in res.entites})
        if cmd.tous and cmd.action != "etat" and len(pieces) > 1 and not self._piece_precisee(cmd.cible):
            # Sécurité : « éteins toutes les lumières » sans pièce -> on demande où avant d'agir sur toute la maison
            self._attentes[conversation] = _Attente(cmd, res.entites, time.monotonic(), "piece")
            q = f"Quelle pièce : {_ou(pieces + ['toute la maison'])} ?"
            return ReponseMaison(True, q, {**detail, "resolution": "pièce demandée", "duree_ms": ms()})
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
                return f"Je ne pilote pas {e.nom} ({e.raison})."
        proches = sorted(self.inventaire.entites, key=lambda e: -len(c & (e.mots_nom() | e.mots_piece())))
        proches = [e for e in proches[:3] if c & (e.mots_nom() | e.mots_piece())]
        suite = f" {_ou([e.nom for e in proches])} ?" if proches else ""
        return f"« {propre} » introuvable, rien n'a été fait.{suite}"

    async def _evoque_la_maison(self, texte: str) -> bool:
        """La phrase cite-t-elle un type d'appareil, une pièce ou un appareil connu ? Sinon (« bonjour »),
        inutile de demander au modèle d'en extraire une commande."""
        from .resolution import INDICES_DOMAINE
        m = mots(texte)
        if m & (set(INDICES_DOMAINE) | MOTS_AMBIANCE):
            return True
        connus: set[str] = set()
        for e in await self.inventaire.obtenir():
            connus |= e.mots_nom() | e.mots_piece()
        return bool(m & connus)

    async def _plusieurs(self, cmd: Commande, parties: list[str], entites: list[Entite],
                         conversation: str) -> ReponseMaison:
        """Chaque partie est cherchée séparément ; on agit sur celles qui sont claires et on dit le reste."""
        from .resolution import INDICES_DOMAINE
        trouves: list[Entite] = []
        introuvables, ambigues = [], []
        # « la lumière de la cuisine et du salon » : le type (« lumière ») vaut pour toutes les parties
        type_commun = [w for w in mots(parties[0]) if w in INDICES_DOMAINE]
        parties = [p if (mots(p) & set(INDICES_DOMAINE)) or not type_commun or i == 0
                   else f"{' '.join(type_commun)} {p}" for i, p in enumerate(parties)]
        for p in parties:
            res = resoudre(p, cmd.action, entites, cmd.tous, self.alias.table())
            if res.entites:
                trouves += [e for e in res.entites if e not in trouves]
            elif res.ambigues:
                ambigues.append((p, res.ambigues))
            else:
                introuvables.append(p)
        if not trouves and not ambigues:
            return ReponseMaison(False, self.introuvable(" et ".join(parties)), {"resolution": "aucun appareil ne correspond"})
        textes, detail = [], {"resolution": "plusieurs cibles"}
        if trouves:
            r = await self._executer(Commande(cmd.action, "", cmd.valeur, False, cmd.couleur, cmd.kelvin, cmd.hs),
                                     trouves, conversation)
            textes.append(r.texte)
            detail.update(r.detail)
        if introuvables:
            textes.append(f"Introuvable : {', '.join(introuvables)}.")
        if ambigues:
            p, cands = ambigues[0]
            self._attentes[conversation] = _Attente(Commande(cmd.action, p, cmd.valeur, False, cmd.couleur, cmd.kelvin),
                                                    cands, time.monotonic())
            textes.append(f"Pour « {p} », lequel : {_ou(_noms_courts(cands))} ?")
            detail["candidats"] = [e.entity_id for e in cands]
        return ReponseMaison(True, " ".join(textes), detail)

    async def lister(self, filtre: str) -> str:
        """« Fais l'inventaire des lumières de la cave » : appareils groupés par pièce, avec leur état."""
        from .resolution import INDICES_DOMAINE
        entites = await self.inventaire.obtenir()
        if not entites:
            return f"Je n'ai pas pu lire l'inventaire ({self.inventaire.erreur or 'vide'})."
        f = mots(filtre) - {"appareil", "equipement", "inventaire", "liste", "maison", "objet", "truc", "chose"}
        domaines = set().union(*(INDICES_DOMAINE.get(w, set()) for w in f)) if f else set()
        pieces = f - set(INDICES_DOMAINE)
        from .resolution import MOTS_LUMIERE
        lumieres_seules = bool(f & MOTS_LUMIERE) and not f & {"prise", "interrupteur"}
        choix = [e for e in entites
                 if (not domaines or e.domaine in domaines)
                 and not (lumieres_seules and e.domaine == "switch" and not e.mots_nom() & MOTS_LUMIERE)
                 and (not pieces or pieces & (e.mots_piece() | e.mots_nom()))]
        if not choix:
            return f"Je ne vois aucun appareil pour « {filtre} »."
        par_piece: dict[str, list[str]] = {}
        for e in sorted(choix, key=lambda x: (x.piece or "~", x.nom.lower())):
            etat = "?" if self.masque(e) else ETATS_FR.get(e.etat, e.etat or "?")
            par_piece.setdefault(e.piece or "Sans pièce", []).append(f"{e.nom} ({etat})")
        lignes = [f"{p} : {', '.join(n)}" for p, n in par_piece.items()]
        entete = f"{len(choix)} appareil{'s' if len(choix) > 1 else ''} pilotable{'s' if len(choix) > 1 else ''}"
        return entete + " —\n" + "\n".join(lignes)

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

    def _piece_precisee(self, cible: str) -> bool:
        m = mots(cible)
        if m & MOTS_TOUTE_LA_MAISON:
            return True
        return any(m & e.mots_piece() for e in self.inventaire.entites)

    def _choisir_piece(self, texte: str, candidats: list[Entite]) -> list[Entite]:
        m = mots(texte)
        if m & MOTS_TOUTE_LA_MAISON or normaliser(texte) in ("tout", "toutes", "tous", "partout", "oui"):
            return candidats
        return [e for e in candidats if m & e.mots_piece()]

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
                if self.masque(e):
                    lignes.append(f"{e.libelle} : {NON_REVELE}.")
                    continue
                s = await self.ha.etat_entite(e.entity_id)
                lignes.append(_decrire_etat(e, s) if s else f"{e.libelle} : état inconnu.")
            # « éteins-le » juste après « le projo est-il allumé ? » vise le projo
            self._contextes[conversation] = _Contexte(cmd, entites, [], time.monotonic())
            return ReponseMaison(True, " ".join(lignes), {"entites": ids})
        refuses = [e for e in entites if self.sensible(e)]
        sans_droit = [e for e in entites if not self.sensible(e) and self.interdit(e)]
        entites = [e for e in entites if not self.sensible(e) and not self.interdit(e)]
        if (refuses or sans_droit) and not entites:
            texte = " ".join(t for t in (
                REFUS_SENSIBLE.format(noms=_et(_noms_courts(refuses))) if refuses else "",
                REFUS_DROITS.format(noms=_et(_noms_courts(sans_droit))) if sans_droit else "") if t)
            return ReponseMaison(True, texte, {"entites": ids, "sensibles": [e.entity_id for e in refuses],
                                               "interdits": [e.entity_id for e in sans_droit]})
        avant = [s for s in [await self.ha.etat_entite(e.entity_id) for e in entites] if s]
        faits, rates, impossibles = [], [], []
        for e in entites:
            svc = _service(cmd.action, e, cmd.valeur, cmd.couleur, cmd.kelvin, cmd.hs)
            if not svc:
                impossibles.append(e)
                continue
            ok = await self.ha.appeler_service(*svc)
            (faits if ok else rates).append(e)
        if faits:
            self._contextes[conversation] = _Contexte(cmd, faits, avant, time.monotonic())
            await self._noter(faits, cmd.action, avant)
        morceaux = []
        if faits:
            verbe = VERBES_FAIT.get(cmd.action, "fait")
            details = []
            if cmd.valeur is not None and cmd.action in ("regler", "allumer"):
                details.append(f"{cmd.valeur:g} °C" if faits[0].domaine == "climate" else f"{cmd.valeur:g} %")
            if cmd.couleur or cmd.kelvin or cmd.hs:
                details.append(_nom_couleur(cmd))
            val = f" ({', '.join(details)})" if details else ""
            if len(faits) <= 3:
                # « c'est » : pas d'accord ; jusqu'à trois appareils, on les nomme (on sait ce qui a bougé)
                morceaux.append(f"C'est {verbe} : {_et(_noms_courts(faits))}{val}.")
            else:
                morceaux.append(f"C'est {verbe}{val} : {len(faits)} appareils.")
        if rates:
            morceaux.append(f"Sauf {_et(_noms_courts(rates))} : refusé.")
        if impossibles:
            morceaux.append(f"Impossible sur {_et(_noms_courts(impossibles))}.")
        if refuses:
            morceaux.append(f"Pas {_et(_noms_courts(refuses))} : action sensible.")
        if sans_droit:
            morceaux.append(f"Pas {_et(_noms_courts(sans_droit))} : tu n'as pas le droit.")
        return ReponseMaison(True, " ".join(morceaux), {"entites": ids, "echecs": [e.entity_id for e in rates],
                                                       "sensibles": [e.entity_id for e in refuses],
                                                       "interdits": [e.entity_id for e in sans_droit]})


    async def _restaurer(self, ctx: _Contexte) -> ReponseMaison:
        if not ctx.avant:
            return ReponseMaison(True, "Rien à remettre.")
        faits, rates = [], []
        noms = {e.entity_id: e.libelle for e in ctx.entites}
        for s in ctx.avant:
            if s["entity_id"].split(".")[0] in interdits.get():
                rates.append(noms.get(s["entity_id"], s["entity_id"]))
                continue
            svc = _restauration(s)
            if not svc:
                continue
            ok = await self.ha.appeler_service(*svc)
            (faits if ok else rates).append(noms.get(s["entity_id"], s["entity_id"]))
            if ok and self.historique:
                self.historique.noter(s["entity_id"], noms.get(s["entity_id"], s["entity_id"]), "remettre comme avant",
                                      s, await self.ha.etat_entite(s["entity_id"]), annulable=False)
        if not faits and not rates:
            return ReponseMaison(True, "Je ne sais pas les remettre comme avant.")
        txt = f"C'est remis comme avant : {', '.join(faits)}." if faits else ""
        if rates:
            txt += f" Refusé : {', '.join(rates)}."
        return ReponseMaison(True, txt.strip(), {"entites": [s["entity_id"] for s in ctx.avant]})

    async def _plus_moins(self, ctx: _Contexte, sens: float) -> ReponseMaison:
        avant = [await self.ha.etat_entite(e.entity_id) for e in ctx.entites]
        faits, impossibles = [], []
        for e, s in zip(ctx.entites, avant):
            if self.sensible(e) or self.interdit(e):
                impossibles.append(e)
                continue
            svc = _relatif(e, s, sens)
            if svc and await self.ha.appeler_service(*svc):
                faits.append(e)
            else:
                impossibles.append(e)
        if faits:
            self._contextes_maj(ctx, [x for x in avant if x])
            await self._noter(faits, "plus" if sens > 0 else "moins", [x for x in avant if x])
        mot = "plus" if sens > 0 else "moins"
        txt = f"{_et(_noms_courts(faits))} : un cran {mot}." if faits else ""
        if impossibles:
            txt += f" Impossible sur {_et(_noms_courts(impossibles))}."
        return ReponseMaison(True, txt.strip(), {"entites": [e.entity_id for e in ctx.entites]})

    def _contextes_maj(self, ctx: _Contexte, avant: list[dict]) -> None:
        ctx.avant, ctx.depuis = avant, time.monotonic()


    # --- historique des actions et état connu des appareils --------------------------------------
    async def _noter(self, faits: list[Entite], action: str, avant: list[dict]) -> None:
        if not self.historique:
            return
        par_id = {x["entity_id"]: x for x in avant if x}
        await asyncio.sleep(0.3)        # laisse Home Assistant prendre en compte le changement
        for e in faits:
            apres = await self.ha.etat_entite(e.entity_id)
            av = par_id.get(e.entity_id)
            self.historique.noter(e.entity_id, e.libelle, action, av, apres,
                                  annulable=bool(av and _restauration(av)))

    async def annuler_action(self, ident: str) -> tuple[bool, str]:
        """Remet l'appareil dans l'état lu juste avant cette action. (False, raison) si impossible."""
        if not self.historique:
            return False, "pas d'historique"
        a = self.historique.trouver(ident)
        if not a:
            return False, "action inconnue"
        if a.get("annulee"):
            return False, "déjà annulée"
        if not a.get("annulable") or not a.get("avant"):
            return False, "cette action ne peut pas être annulée"
        if a["appareil"].split(".")[0] in interdits.get():
            return False, "tu n'as pas le droit de piloter cet appareil"
        avant = {"entity_id": a["appareil"], "state": a["avant"]["etat"], "attributes": a["avant"].get("attributs", {})}
        svc = _restauration(avant)
        if not svc:
            return False, "je ne sais pas remettre cet appareil comme avant"
        actuel = await self.ha.etat_entite(a["appareil"])
        if not await self.ha.appeler_service(*svc):
            return False, "refusé par Home Assistant"
        self.historique.marquer_annulee(ident)
        await asyncio.sleep(0.3)
        self.historique.noter(a["appareil"], a["nom"], "annulation", actuel, await self.ha.etat_entite(a["appareil"]),
                              annulable=bool(actuel and _restauration(actuel)))
        return True, f"{a['nom']} remis comme avant."

    async def etats_appareils(self) -> list[dict]:
        """État de tous les appareils : celui de Home Assistant s'il répond, sinon le dernier connu."""
        if not self.historique:
            return []
        try:
            entites = await self.inventaire.obtenir()
        except Exception:  # noqa: BLE001 - Home Assistant injoignable : on garde la mémoire
            entites = []
        if entites:
            self.historique.memoriser_tout([(e.entity_id, e.libelle, e.etat) for e in entites])
        return self.historique.appareils()

    # --- opérations utilisées par les outils de Claude (serveur MCP « maison ») -------------

    async def evoque(self, texte: str) -> bool:
        return await self._evoque_la_maison(texte)

    async def appareils(self, filtre: str = "") -> list[dict]:
        entites = await self.inventaire.obtenir()
        f = mots(filtre)
        if f:
            from .resolution import INDICES_DOMAINE
            domaines = set().union(*(INDICES_DOMAINE.get(w, set()) for w in f))
            autres = f - set(INDICES_DOMAINE)
            entites = [e for e in entites if (not domaines or e.domaine in domaines)
                       and (not autres or autres & (e.mots_nom() | e.mots_piece()))]
        return [{"entity_id": e.entity_id, "nom": e.nom, "piece": e.piece, "type": e.domaine,
                 "etat": NON_REVELE if self.masque(e) else ETATS_FR.get(e.etat, e.etat)} for e in entites]

    async def etat_detaille(self, entity_id: str) -> dict | None:
        connus = {e.entity_id: e for e in await self.inventaire.obtenir()}
        if entity_id not in connus:
            return None
        if self.masque(connus[entity_id]):
            return {"entity_id": entity_id, "etat": NON_REVELE, "description": f"{connus[entity_id].libelle} : {NON_REVELE}."}
        s = await self.ha.etat_entite(entity_id)
        if not s:
            return None
        a = s.get("attributes") or {}
        garde = ("friendly_name", "brightness", "color_mode", "rgb_color", "color_temp_kelvin", "current_position",
                 "current_temperature", "temperature", "hvac_mode", "volume_level", "media_title", "percentage")
        return {"entity_id": entity_id, "etat": s.get("state"), "description": _decrire_etat(connus[entity_id], s),
                **{k: a[k] for k in garde if k in a}}

    async def agir_ids(self, entity_ids: list[str], action: str, valeur: float | None = None,
                       couleur: str | None = None, conversation: str = "") -> ReponseMaison:
        from .commande import BLANCS, COULEURS
        connus = {e.entity_id: e for e in await self.inventaire.obtenir()}
        refuses = [i for i in entity_ids if i not in connus]
        cibles = [connus[i] for i in entity_ids if i in connus]
        coul = kelvin = None
        if couleur:
            n = normaliser(couleur)
            coul, kelvin = COULEURS.get(n), BLANCS.get(n)
        cmd = Commande(action, "", valeur, False, coul, kelvin)
        precedent = self._contexte(conversation)
        r = await self._executer(cmd, cibles, conversation) if cibles else ReponseMaison(False, "")
        nouveau = self._contexte(conversation)
        if precedent and nouveau and nouveau is not precedent and nouveau.depuis - precedent.depuis < 120:
            # plusieurs actions d'outil pour une même demande : « annule » doit tout défaire
            deja = {s["entity_id"] for s in precedent.avant}
            nouveau.avant = precedent.avant + [s for s in nouveau.avant if s["entity_id"] not in deja]
            nouveau.entites = precedent.entites + [e for e in nouveau.entites if e not in precedent.entites]
        if refuses:
            r.texte = (r.texte + f" Appareils inconnus ou non autorisés, rien fait : {', '.join(refuses)}.").strip()
        return r

    async def annuler(self, conversation: str = "") -> ReponseMaison:
        ctx = self._contexte(conversation)
        if not ctx:
            return ReponseMaison(False, "Aucune action récente à annuler.")
        r = await self._restaurer(ctx)
        ctx.avant = []
        return r



def _noms_courts(entites: list[Entite], maxi: int = 4) -> list[str]:
    """Noms sans la pièce quand tous les candidats sont dans la même pièce ; au plus `maxi`."""
    meme_piece = len({e.piece for e in entites}) == 1
    noms = [e.nom if meme_piece else e.libelle for e in entites[:maxi]]
    if len(entites) > maxi:
        noms.append(f"les {len(entites)}")
    return noms


def _et(elements: list[str]) -> str:
    return elements[0] if len(elements) == 1 else ", ".join(elements[:-1]) + " et " + elements[-1]


def _ou(elements: list[str]) -> str:
    return elements[0] if len(elements) == 1 else ", ".join(elements[:-1]) + " ou " + elements[-1]

NOMS_COULEURS = {"red": "rouge", "blue": "bleu", "green": "vert", "yellow": "jaune", "orange": "orange",
                 "purple": "violet", "pink": "rose", "cyan": "cyan", "turquoise": "turquoise",
                 "magenta": "magenta", "indigo": "indigo", "gold": "doré"}


# cercle chromatique des cartes : teinte (degrés) -> nom dit dans la réponse
TEINTES = [(0, "rouge"), (30, "orange"), (50, "jaune"), (85, "vert anis"), (120, "vert"), (155, "vert d'eau"),
           (185, "cyan"), (210, "bleu ciel"), (235, "bleu"), (265, "violet"), (295, "magenta"), (330, "rose")]


def nom_teinte(teinte: float, saturation: float = 100) -> str:
    """Nom français le plus proche d'une teinte ; « pâle » quand la couleur tire vers le blanc."""
    t = teinte % 360
    nom = min(TEINTES, key=lambda x: min(abs(t - x[0]), 360 - abs(t - x[0])))[1]
    return "blanc" if saturation < 12 else f"{nom} pâle" if saturation < 45 else nom


def _nom_couleur(cmd: Commande) -> str:
    if cmd.hs:
        return nom_teinte(*cmd.hs)
    if cmd.couleur:
        return NOMS_COULEURS.get(cmd.couleur, cmd.couleur)
    k = cmd.kelvin or 0
    return "blanc chaud" if k <= 3000 else "blanc froid" if k >= 5500 else "blanc"


