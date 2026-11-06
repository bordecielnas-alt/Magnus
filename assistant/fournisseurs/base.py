"""Interface commune à tous les fournisseurs de modèles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def en_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class FournisseurIndisponible(Exception):
    """Le fournisseur n'a pas pu répondre (réseau, délai, erreur HTTP, quota…)."""


class Fournisseur(ABC):
    """Un modèle interchangeable. L'orchestrateur ne connaît que cette interface."""

    def __init__(self, ident: str, modele: str) -> None:
        self.ident = ident
        self.modele = modele

    @abstractmethod
    async def repondre(self, messages: list[Message]) -> str:
        """Réponse complète. Lève FournisseurIndisponible en cas d'échec."""

    @abstractmethod
    def flux(self, messages: list[Message]) -> AsyncIterator[str]:
        """Réponse morceau par morceau. Lève FournisseurIndisponible en cas d'échec."""

    @abstractmethod
    async def disponible(self) -> bool:
        """Vérification rapide que le fournisseur répond."""

    async def fermer(self) -> None:  # pragma: no cover - rien à fermer par défaut
        return None
