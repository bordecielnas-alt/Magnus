"""Fournisseur pour toute API compatible OpenAI (Ollama, LM Studio, vLLM, clé API…)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import Fournisseur, FournisseurIndisponible, Message


class FournisseurOpenAI(Fournisseur):
    def __init__(
        self,
        ident: str,
        url: str,
        modele: str,
        cle: str = "",
        timeout: float = 60,
        options: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(ident, modele)
        self.url = url.rstrip("/")
        self.options = dict(options or {})
        entetes = {"Authorization": f"Bearer {cle}"} if cle else {}
        self._client = httpx.AsyncClient(
            base_url=self.url, headers=entetes, timeout=timeout, transport=transport
        )

    def _corps(self, messages: list[Message], flux: bool, options: dict | None = None) -> dict[str, Any]:
        return {
            **self.options,
            **(options or {}),
            "model": self.modele,
            "messages": [m.en_dict() for m in messages],
            "stream": flux,
        }

    async def repondre(self, messages: list[Message], options: dict | None = None) -> str:
        try:
            r = await self._client.post("/chat/completions", json=self._corps(messages, False, options))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise FournisseurIndisponible(f"{self.ident} : {e}") from e

    async def flux(self, messages: list[Message]) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=self._corps(messages, True)
            ) as r:
                r.raise_for_status()
                async for ligne in r.aiter_lines():
                    if not ligne.startswith("data:"):
                        continue
                    donnee = ligne[5:].strip()
                    if donnee == "[DONE]":
                        break
                    choix = json.loads(donnee).get("choices") or [{}]
                    morceau = (choix[0].get("delta") or {}).get("content")
                    if morceau:
                        yield morceau
        except (httpx.HTTPError, ValueError) as e:
            raise FournisseurIndisponible(f"{self.ident} : {e}") from e

    async def disponible(self) -> bool:
        try:
            r = await self._client.get("/models", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def fermer(self) -> None:
        await self._client.aclose()
