"""Notifications sur le téléphone par un bot Telegram (sans que la page soit ouverte).

- Le jeton du bot (donné par @BotFather) est le secret TELEGRAM_JETON, jamais dans config.yaml.
- Le chat de destination (telegram.chat_id) se trouve automatiquement : on écrit « /start » au bot,
  puis « Trouver mon chat » dans ⚙ lit les derniers messages reçus par le bot.
- Envoi seulement : l'assistant ne lit pas les messages Telegram (pas de commande à distance).
"""

from __future__ import annotations

import httpx

API = "https://api.telegram.org"


class Telegram:
    def __init__(self, jeton: str, chat_id: str = "", timeout: float = 10,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.jeton, self.chat_id = jeton.strip(), str(chat_id or "").strip()
        self.timeout, self._transport = timeout, transport

    @property
    def configure(self) -> bool:
        return bool(self.jeton and self.chat_id)

    async def _appel(self, methode: str, **donnees) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
            r = await c.post(f"{API}/bot{self.jeton}/{methode}", json=donnees)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not d.get("ok"):
            raise RuntimeError(f"Telegram : {d.get('description') or r.status_code}")
        return d

    async def envoyer(self, texte: str) -> None:
        if not self.configure:
            raise RuntimeError("Telegram non configuré (jeton du bot et chat)")
        await self._appel("sendMessage", chat_id=self.chat_id, text=texte[:4000], disable_web_page_preview=True)

    async def trouver_chat(self) -> dict | None:
        """Le dernier chat privé qui a écrit au bot (après « /start »)."""
        if not self.jeton:
            raise RuntimeError("jeton du bot manquant")
        d = await self._appel("getUpdates", limit=20, allowed_updates=["message"])
        for u in reversed(d.get("result") or []):
            chat = ((u.get("message") or {}).get("chat")) or {}
            if chat.get("type") == "private":
                return {"id": str(chat["id"]), "nom": chat.get("first_name") or chat.get("username") or ""}
        return None
