"""Construit les fournisseurs depuis la config et les associe à des rôles."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from ..config import Config, FournisseurConfig
from .base import Fournisseur, FournisseurIndisponible, Message
from .claude_code import FournisseurClaudeCode
from .openai_compat import FournisseurOpenAI

ROLE_SECOURS = "secours"


def _openai(ident: str, fc: FournisseurConfig) -> Fournisseur:
    return FournisseurOpenAI(ident, fc.url, fc.modele, fc.cle(), fc.timeout, fc.options)


def _claude_code(ident: str, fc: FournisseurConfig) -> Fournisseur:
    return FournisseurClaudeCode(ident, fc.modele, fc.commande, fc.timeout, fc.effort, fc.recherche_web)


# type (dans la config) -> fabrique
TYPES: dict[str, Callable[[str, FournisseurConfig], Fournisseur]] = {
    "openai": _openai,
    "claude_code": _claude_code,
}


@dataclass
class Reponse:
    texte: str
    fournisseur: str
    modele: str
    secours: bool  # True si le fournisseur du rôle demandé a échoué


class Registre:
    def __init__(self, fournisseurs: dict[str, Fournisseur], roles: dict[str, str]) -> None:
        self.fournisseurs = fournisseurs
        self.roles = roles

    @classmethod
    def depuis_config(cls, cfg: Config) -> "Registre":
        f = {ident: TYPES[fc.type](ident, fc) for ident, fc in cfg.fournisseurs.items()}
        return cls(f, dict(cfg.roles))

    def pour_role(self, role: str) -> Fournisseur:
        if role not in self.roles:
            raise KeyError(f"rôle inconnu : {role}")
        return self.fournisseurs[self.roles[role]]

    def _candidats(self, role: str) -> list[Fournisseur]:
        principal = self.pour_role(role)
        secours = self.fournisseurs.get(self.roles.get(ROLE_SECOURS, ""))
        return [principal] + ([secours] if secours and secours is not principal else [])

    async def repondre(self, role: str, messages: list[Message], outils: dict | None = None,
                       json_brut: bool = False) -> Reponse:
        if outils:
            morceaux, fin = [], None
            async for x in self.flux(role, messages, outils):
                if isinstance(x, Reponse):
                    fin = x
                else:
                    morceaux.append(x)
            return fin
        return await self._repondre(role, messages, json_brut)

    async def _repondre(self, role: str, messages: list[Message], json_brut: bool = False) -> Reponse:
        erreurs = []
        for i, f in enumerate(self._candidats(role)):
            try:
                brut = getattr(f, "repondre_json", None) if json_brut else None
                texte = await (brut(messages) if brut else f.repondre(messages))
                return Reponse(texte, f.ident, f.modele, secours=i > 0)
            except FournisseurIndisponible as e:
                erreurs.append(str(e))
        raise FournisseurIndisponible(" ; ".join(erreurs))

    async def flux(self, role: str, messages: list[Message],
                   outils: dict | None = None) -> AsyncIterator[str | Reponse]:
        """Produit les morceaux de texte, puis une Reponse finale (texte complet, qui a répondu).

        Le secours n'est tenté que si le principal échoue AVANT d'avoir envoyé du texte.
        """
        erreurs = []
        for i, f in enumerate(self._candidats(role)):
            morceaux: list[str] = []
            try:
                flot = f.flux(messages, outils=outils) if outils and getattr(f, "accepte_outils", False) else f.flux(messages)
                async for m in flot:
                    morceaux.append(m)
                    yield m
                yield Reponse("".join(morceaux), f.ident, f.modele, secours=i > 0)
                return
            except FournisseurIndisponible as e:
                if morceaux:
                    raise
                erreurs.append(str(e))
        raise FournisseurIndisponible(" ; ".join(erreurs))

    async def etat(self) -> list[dict]:
        idents = list(self.fournisseurs)
        dispo = await asyncio.gather(*(self.fournisseurs[i].disponible() for i in idents))
        return [
            {
                "id": i,
                "modele": self.fournisseurs[i].modele,
                "disponible": d,
                "roles": sorted(r for r, f in self.roles.items() if f == i),
            }
            for i, d in zip(idents, dispo)
        ]

    async def fermer(self) -> None:
        await asyncio.gather(*(f.fermer() for f in self.fournisseurs.values()))
