"""Routeur local : décide si une demande est simple (modèle rapide) ou complexe (Claude).

1. Règles (expressions régulières de la config), en moins d'une milliseconde.
2. Sinon, un petit modèle local classe la demande (un mot : SIMPLE ou COMPLEXE).
3. Sinon (classeur absent, trop lent ou réponse illisible), la catégorie par défaut.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass

from .config import RouteurConfig
from .fournisseurs import FournisseurIndisponible, Message, Registre

SIMPLE, COMPLEXE = "simple", "complexe"  # mêmes valeurs que RouteurConfig.defaut

CONSIGNE_CLASSEUR = (
    "Tu classes des demandes faites à un assistant domestique. Réponds par un seul mot.\n"
    "SIMPLE : commande de la maison (lumière, volet, chauffage, prise), minuteur, heure, "
    "météo du moment, question factuelle très courte, salutation, remerciement.\n"
    "COMPLEXE : explication, raisonnement, conseil, rédaction, résumé, comparaison, "
    "diagnostic, planification, ou toute question qui demande plusieurs phrases de réponse.\n"
    "Réponds uniquement SIMPLE ou COMPLEXE."
)


@dataclass
class Decision:
    categorie: str
    role: str
    raison: str       # « règle : … », « classeur », « longueur », « défaut : … »
    duree_ms: float

    def en_dict(self) -> dict:
        return asdict(self)


class Routeur:
    def __init__(self, cfg: RouteurConfig, registre: Registre) -> None:
        self.cfg = cfg
        self.registre = registre
        self._en_cours: set[asyncio.Future] = set()   # demandes au classeur qui continuent en arrière-plan
        self._simples = [re.compile(r, re.IGNORECASE) for r in cfg.regles_simples]
        self._complexes = [re.compile(r, re.IGNORECASE) for r in cfg.regles_complexes]

    def _role(self, categorie: str) -> str:
        return self.cfg.role_simple if categorie == SIMPLE else self.cfg.role_complexe

    def par_regles(self, texte: str) -> tuple[str, str] | None:
        """(catégorie, raison) si une règle s'applique, sinon None."""
        t = texte.strip()
        for r in self._simples:
            if r.search(t):
                return SIMPLE, f"règle : {r.pattern}"
        for r in self._complexes:
            if r.search(t):
                return COMPLEXE, f"règle : {r.pattern}"
        if len(t) >= self.cfg.longueur_complexe:
            return COMPLEXE, "longueur"
        return None

    async def par_classeur(self, texte: str, precedent: str = "") -> str | None:
        if not self.cfg.classeur or self.cfg.classeur not in self.registre.roles:
            return None
        f = self.registre.pour_role(self.cfg.classeur)
        contenu = f"Demande : {texte}"
        if precedent:
            contenu = f"Demande précédente : {precedent}\n" + contenu
        msgs = [Message("system", CONSIGNE_CLASSEUR), Message("user", contenu)]
        # La demande continue en arrière-plan si on n'attend plus : couper la connexion ferait abandonner à
        # Ollama le chargement du modèle (qui resterait froid à chaque fois).
        tache = asyncio.ensure_future(f.repondre(msgs, options={"temperature": 0, "max_tokens": 4}))
        self._en_cours.add(tache)
        tache.add_done_callback(lambda t: (self._en_cours.discard(t), t.cancelled() or t.exception()))
        try:
            rep = await asyncio.wait_for(asyncio.shield(tache), timeout=self.cfg.delai_classeur_ms / 1000)
        except (FournisseurIndisponible, asyncio.TimeoutError):
            return None
        mot = rep.strip().upper()
        # le petit modèle ajoute parfois un préfixe (« Réponse : SIMPLE ») : on cherche le mot
        if "COMPLEX" in mot:
            return COMPLEXE
        if "SIMPLE" in mot:
            return SIMPLE
        return None

    async def decider(self, texte: str, precedent: str = "") -> Decision:
        debut = time.perf_counter()
        ms = lambda: round((time.perf_counter() - debut) * 1000, 1)  # noqa: E731
        r = self.par_regles(texte)
        if r:
            return Decision(r[0], self._role(r[0]), r[1], ms())
        c = await self.par_classeur(texte, precedent)
        if c:
            return Decision(c, self._role(c), "classeur", ms())
        d = self.cfg.defaut
        return Decision(d, self._role(d), "défaut : classeur indisponible ou indécis", ms())
