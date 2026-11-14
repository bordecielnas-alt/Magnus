"""Client Home Assistant : agent de conversation Assist (sans LLM) et état de la connexion.

L'assistant n'agit sur la maison qu'à travers Home Assistant, avec un jeton dédié.
Seules les entités « exposées à Assist » dans HA sont pilotables : le choix reste dans HA.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import HomeAssistantConfig

REUSSITES = {"action_done", "query_answer"}


@dataclass
class ReponseHA:
    compris: bool          # True si HA a exécuté l'action ou répondu à la question
    texte: str             # phrase de HA (en français)
    type: str              # action_done, query_answer, error
    code: str = ""         # code d'erreur HA (no_intent_match, no_valid_targets…)
    entites: list[str] = field(default_factory=list)   # entity_id touchés (action_done)


class HomeAssistant:
    def __init__(self, cfg: HomeAssistantConfig, jeton: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = cfg
        self.jeton = jeton if jeton is not None else os.environ.get(cfg.jeton_env, "")
        self._client = httpx.AsyncClient(
            base_url=cfg.url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.jeton}"} if self.jeton else {},
            timeout=cfg.timeout,
            transport=transport,
        ) if cfg.url else None
        self._cache_etat: tuple[float, dict] | None = None

    @property
    def configure(self) -> bool:
        return bool(self.cfg.url and self.jeton)

    async def conversation(self, texte: str) -> ReponseHA:
        """Passe la phrase à l'agent Assist. Ne lève pas : renvoie compris=False en cas d'échec."""
        if not self.configure:
            return ReponseHA(False, "", "error", "non_configure")
        corps: dict[str, Any] = {"text": texte, "language": self.cfg.langue}
        if self.cfg.agent:
            corps["agent_id"] = self.cfg.agent
        try:
            r = await self._client.post("/api/conversation/process", json=corps)
            r.raise_for_status()
            rep = r.json().get("response") or {}
        except (httpx.HTTPError, ValueError) as e:
            return ReponseHA(False, "", "error", f"injoignable : {e.__class__.__name__}")
        type_ = rep.get("response_type", "error")
        texte_ha = ((rep.get("speech") or {}).get("plain") or {}).get("speech", "")
        data = rep.get("data") or {}
        code = data.get("code", "") if type_ == "error" else ""
        cibles = [c.get("id") for c in (data.get("success") or []) if c.get("type") == "entity" and c.get("id")]
        return ReponseHA(type_ in REUSSITES, texte_ha, type_, code, cibles)

    async def etat(self, cache_s: float = 30) -> dict:
        """Connexion, version et nombre d'entités exposées à Assist (mis en cache)."""
        if self._cache_etat and time.monotonic() - self._cache_etat[0] < cache_s:
            return self._cache_etat[1]
        e = await self._etat()
        self._cache_etat = (time.monotonic(), e)
        return e

    async def _etat(self) -> dict:
        if not self.cfg.url:
            return {"configure": False, "message": "adresse non renseignée (home_assistant.url)"}
        if not self.jeton:
            return {"configure": False, "message": f"jeton absent (variable {self.cfg.jeton_env})"}
        e: dict[str, Any] = {"configure": True, "url": self.cfg.url, "connecte": False}
        try:
            r = await self._client.get("/api/config")
            if r.status_code == 401:
                return {**e, "message": "jeton refusé par Home Assistant"}
            r.raise_for_status()
            e.update(connecte=True, version=r.json().get("version", ""))
        except (httpx.HTTPError, ValueError) as ex:
            return {**e, "message": f"injoignable ({ex.__class__.__name__})"}
        e["entites_exposees"] = await self._compter_exposees()
        return e

    async def ws(self, commandes: list[str]) -> dict[str, Any]:
        """Envoie des commandes WebSocket (sans paramètre) sur une seule connexion.

        Renvoie {commande: résultat} ; une commande refusée (droits) vaut None.
        Lève ConnectionError si la connexion ou l'authentification échoue.
        """
        import websockets
        url = self.cfg.url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
        sortie: dict[str, Any] = {}
        try:
            async with websockets.connect(url, open_timeout=self.cfg.timeout, close_timeout=1,
                                          max_size=32 * 1024 * 1024) as ws:
                await ws.recv()  # auth_required
                await ws.send(json.dumps({"type": "auth", "access_token": self.jeton}))
                if json.loads(await ws.recv()).get("type") != "auth_ok":
                    raise ConnectionError("jeton refusé")
                for i, cmd in enumerate(commandes, start=1):
                    await ws.send(json.dumps({"id": i, "type": cmd}))
                    while True:
                        rep = json.loads(await ws.recv())
                        if rep.get("id") == i:
                            break
                    sortie[cmd] = rep.get("result") if rep.get("success") else None
        except (OSError, ValueError) as e:
            raise ConnectionError(str(e)) from e
        except Exception as e:  # noqa: BLE001 - erreurs de la bibliothèque websockets
            if isinstance(e, ConnectionError):
                raise
            raise ConnectionError(e.__class__.__name__) from e
        return sortie

    async def appeler_service(self, domaine: str, service: str, donnees: dict) -> bool:
        if not self.configure:
            return False
        try:
            r = await self._client.post(f"/api/services/{domaine}/{service}", json=donnees)
            return r.status_code < 300
        except httpx.HTTPError:
            return False

    async def creer_automatisation(self, ident: str, config: dict) -> bool:
        """Crée (ou remplace) une automatisation via l'API de configuration (jeton d'un administrateur HA)."""
        if not self.configure:
            return False
        try:
            r = await self._client.post(f"/api/config/automation/config/{ident}", json=config)
            return r.status_code < 300
        except httpx.HTTPError:
            return False

    async def supprimer_automatisation(self, ident: str) -> bool:
        if not self.configure:
            return False
        try:
            r = await self._client.delete(f"/api/config/automation/config/{ident}")
            return r.status_code < 300 or r.status_code == 404      # déjà supprimée dans Home Assistant
        except httpx.HTTPError:
            return False

    async def etat_entite(self, entity_id: str) -> dict | None:
        if not self.configure:
            return None
        try:
            r = await self._client.get(f"/api/states/{entity_id}")
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def _compter_exposees(self) -> int | None:
        """Via l'API WebSocket (homeassistant/expose_entity/list). None si indisponible."""
        try:
            import websockets
        except ImportError:  # pragma: no cover
            return None
        url = self.cfg.url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
        try:
            async with websockets.connect(url, open_timeout=self.cfg.timeout, close_timeout=1) as ws:
                await ws.recv()  # auth_required
                await ws.send(json.dumps({"type": "auth", "access_token": self.jeton}))
                if json.loads(await ws.recv()).get("type") != "auth_ok":
                    return None
                await ws.send(json.dumps({"id": 1, "type": "homeassistant/expose_entity/list"}))
                rep = json.loads(await ws.recv())
            ent = (rep.get("result") or {}).get("exposed_entities") or {}
            return sum(1 for v in ent.values() if v.get("conversation"))
        except Exception:  # noqa: BLE001 - simple indicateur, ne doit jamais faire échouer l'état
            return None

    async def fermer(self) -> None:
        if self._client:
            await self._client.aclose()
