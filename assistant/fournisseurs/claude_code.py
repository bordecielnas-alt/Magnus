"""Fournisseur Claude via le binaire officiel Claude Code, en mode headless (abonnement).

L'orchestrateur lance `claude -p` sans aucun outil : Claude répond, il n'agit pas.
Le binaire n'est pas modifié ; il utilise le compte connecté par `claude auth login`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from .base import Fournisseur, FournisseurIndisponible, Message

FIN = object()  # marque la fin du flux dans la file

# Variables héritées qu'il ne faut pas transmettre : une clé API ferait facturer
# l'API au lieu de l'abonnement, et les variables de session Claude Code
# feraient croire au binaire qu'il tourne dans une autre session.
VARS_EXCLUES_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_AGENT_")
VARS_EXCLUES = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}

# Le binaire ajoute toujours des infos sur sa machine (dossier, système, shell) ; sans cette
# note, le modèle prétend avoir un shell alors qu'il n'a aucun outil.
NOTE_SANS_OUTILS = (
    "Tu n'as aucun outil : tu ne peux ni exécuter de commande, ni lire ou écrire de fichier, "
    "ni naviguer sur le web. Les informations techniques ajoutées sur ta machine d'exécution "
    "(dossier, système, shell) ne concernent pas l'utilisateur : n'en parle pas."
)


def resoudre_binaire(commande: str) -> str | None:
    """Chemin du binaire. Sous Windows, préfère claude.exe au lanceur npm claude.cmd."""
    chemin = shutil.which(commande)
    if not chemin:
        return None
    p = Path(chemin)
    if p.suffix.lower() in {".cmd", ".ps1", ""} and sys.platform == "win32":
        natif = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if natif.is_file():
            return str(natif)
    return str(p)


def env_enfant(env: Mapping[str, str] | None = None) -> dict[str, str]:
    src = os.environ if env is None else env
    return {
        k: v for k, v in src.items()
        if k not in VARS_EXCLUES and not k.startswith(VARS_EXCLUES_PREFIXES)
    }


def construire_invite(messages: list[Message]) -> tuple[str, str]:
    """(consigne système, texte envoyé sur l'entrée standard)."""
    systeme = "\n\n".join(m.content for m in messages if m.role == "system")
    conv = [m for m in messages if m.role != "system"]
    if len(conv) == 1:
        return systeme, conv[0].content
    noms = {"user": "Utilisateur", "assistant": "Assistant"}
    lignes = ["Historique de la conversation :"]
    lignes += [f"{noms[m.role]} : {m.content}" for m in conv[:-1]]
    lignes += ["", "Nouveau message de l'utilisateur :", conv[-1].content]
    return systeme, "\n".join(lignes)


class FournisseurClaudeCode(Fournisseur):
    def __init__(
        self,
        ident: str,
        modele: str,
        commande: str = "claude",
        timeout: float = 120,
        effort: str = "",
    ) -> None:
        super().__init__(ident, modele)
        self.commande = commande
        self.timeout = timeout
        self.effort = effort
        self.dossier = Path(tempfile.gettempdir()) / "assistant-claude"  # dossier neutre, sans CLAUDE.md
        self._dispo: tuple[float, bool] | None = None

    def _arguments(self, binaire: str, systeme: str) -> list[str]:
        args = [
            binaire, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "",                 # aucun outil : pas de fichiers, pas de commandes
            "--strict-mcp-config",         # aucun serveur MCP
            "--setting-sources", "",       # ignore les réglages utilisateur et projet
            "--no-session-persistence",
            "--model", self.modele,
        ]
        args += ["--system-prompt", (systeme + "\n\n" + NOTE_SANS_OUTILS).strip()]
        if self.effort:
            args += ["--effort", self.effort]
        return args

    def _lancer(self, args: list[str]) -> subprocess.Popen:
        self.dossier.mkdir(parents=True, exist_ok=True)
        drapeaux = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.Popen(
            args, cwd=self.dossier, env=env_enfant(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=drapeaux,
        )

    async def flux(self, messages: list[Message]) -> AsyncIterator[str]:
        binaire = resoudre_binaire(self.commande)
        if not binaire:
            raise FournisseurIndisponible(f"{self.ident} : commande « {self.commande} » introuvable")
        systeme, invite = construire_invite(messages)
        try:
            proc = self._lancer(self._arguments(binaire, systeme))
        except OSError as e:
            raise FournisseurIndisponible(f"{self.ident} : {e}") from e

        boucle = asyncio.get_running_loop()
        file: asyncio.Queue = asyncio.Queue()

        def lire() -> None:
            # Lecture bloquante dans un thread : marche avec toutes les boucles asyncio (Windows compris)
            try:
                proc.stdin.write(invite.encode("utf-8"))
                proc.stdin.close()
                for brut in proc.stdout:
                    boucle.call_soon_threadsafe(file.put_nowait, brut)
            except OSError:
                pass
            finally:
                boucle.call_soon_threadsafe(file.put_nowait, FIN)

        threading.Thread(target=lire, daemon=True).start()
        envoye = False
        resultat = None
        limite = time.monotonic() + self.timeout
        try:
            while True:
                reste = limite - time.monotonic()
                if reste <= 0:
                    raise FournisseurIndisponible(f"{self.ident} : pas de réponse après {self.timeout:.0f} s")
                try:
                    brut = await asyncio.wait_for(file.get(), timeout=reste)
                except asyncio.TimeoutError:
                    continue
                if brut is FIN:
                    break
                try:
                    evt = json.loads(brut)
                except ValueError:
                    continue
                t = evt.get("type")
                if t == "stream_event":
                    e = evt.get("event") or {}
                    d = e.get("delta") or {}
                    if e.get("type") == "content_block_delta" and d.get("type") == "text_delta" and d.get("text"):
                        envoye = True
                        yield d["text"]
                elif t == "result":
                    resultat = evt
        finally:
            if proc.poll() is None:
                proc.kill()

        if resultat is None:
            err = proc.stderr.read().decode("utf-8", "replace").strip()[-300:] if proc.stderr else ""
            raise FournisseurIndisponible(f"{self.ident} : arrêt sans résultat {err}".strip())
        if resultat.get("is_error"):
            raise FournisseurIndisponible(f"{self.ident} : {resultat.get('result') or 'erreur'}")
        if not envoye and resultat.get("result"):
            yield resultat["result"]

    async def repondre(self, messages: list[Message]) -> str:
        return "".join([m async for m in self.flux(messages)])

    async def disponible(self) -> bool:
        """Binaire présent et compte connecté (`claude auth status`), en cache 60 s."""
        maintenant = time.monotonic()
        if self._dispo and maintenant - self._dispo[0] < 60:
            return self._dispo[1]
        ok = await asyncio.to_thread(self._verifier)
        self._dispo = (maintenant, ok)
        return ok

    def _verifier(self) -> bool:
        binaire = resoudre_binaire(self.commande)
        if not binaire:
            return False
        try:
            drapeaux = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            r = subprocess.run([binaire, "auth", "status"], capture_output=True, timeout=10,
                               env=env_enfant(), creationflags=drapeaux)
            return bool(json.loads(r.stdout or b"{}").get("loggedIn"))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
