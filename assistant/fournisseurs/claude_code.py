"""Fournisseur Claude via le binaire officiel Claude Code, en mode headless (abonnement).

L'orchestrateur lance `claude -p` sans aucun outil : Claude répond, il n'agit pas.
Le binaire n'est pas modifié ; il utilise le compte connecté par `claude auth login`.
"""

from __future__ import annotations

import asyncio
import json
import re
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
# Recherche web : seuls outils intégrés autorisés (ni fichiers ni commandes)
OUTILS_WEB = ["WebSearch", "WebFetch"]
NOTE_WEB = (
    "Tu peux chercher sur le web (WebSearch, WebFetch) : fais-le pour l'actualité, la météo, les horaires, les "
    "prix, les résultats sportifs ou toute information récente ou que tu ne connais pas avec certitude. "
    "Sinon réponds directement, sans chercher. Donne le résultat en une ou deux phrases. Ta réponse est lue à "
    "voix haute : ne cite jamais de sources, de sites ni de liens, même si l'outil de recherche te le demande, "
    "et n'en parle pas. "
    "Tu ne peux ni exécuter de commande, ni lire ou écrire de fichier. Les informations techniques ajoutées "
    "sur ta machine d'exécution (dossier, système, shell) ne concernent pas l'utilisateur : n'en parle pas."
)

# Carte de rapport : la réponse est un objet JSON lu par le serveur, pas une phrase lue à voix haute.
# Sources et liens y sont voulus (champs « url ») : ni la consigne orale ni le nettoyage ne s'appliquent.
NOTE_WEB_JSON = (
    "Tu peux chercher sur le web (WebSearch, WebFetch) : fais-le pour toute information récente. "
    "Ta réponse n'est pas lue à voix haute : elle est lue par un programme. Réponds uniquement par l'objet JSON "
    "demandé, sans texte avant ni après, et mets les adresses des sources dans ses champs « url ». "
    "Tu ne peux ni exécuter de commande, ni lire ou écrire de fichier."
)


# La recherche web demande au modèle de lister ses sources : la réponse est lue à voix haute, on les retire.
RE_SOURCES = re.compile(r"(?:^|\n)[ \t]*[*_#>-]*[ \t]*(?:sources?|r[ée]f[ée]rences?|liens?)[ \t]*[*_]*[ \t]*:", re.I)
RE_LIEN = re.compile(r"\[([^\]\n]+)\]\((?:https?://)[^)\s]+\)")
RE_URL = re.compile(r"\s*\(?<?https?://[^\s)>]+>?\)?")
RE_SOURCE_ENTRE_PARENTHESES = re.compile(r"\s*\((?:sources?|d'après|selon)\s*:?[^)]*\)", re.I)


def nettoyer_sources(texte: str) -> str:
    return RE_SOURCE_ENTRE_PARENTHESES.sub("", RE_URL.sub("", RE_LIEN.sub(r"\1", texte)))


async def sans_sources(flux: AsyncIterator[str]) -> AsyncIterator[str]:
    """Laisse passer le texte au fil de l'eau, mais s'arrête à « Sources : » et retire liens et adresses.
    Retient seulement ce qui pourrait être le début d'un marqueur (fin de ligne) ou d'un lien inachevé."""
    tampon, coupe = "", False
    async for x in flux:
        if coupe:
            continue                     # on vide le flux sans rien envoyer
        tampon += x
        m = RE_SOURCES.search(tampon)
        if m:
            reste = nettoyer_sources(tampon[:m.start()]).rstrip()
            if reste:
                yield reste
            tampon, coupe = "", True
            continue
        sur = len(tampon)
        nl = tampon.rfind("\n")
        if nl >= 0 and len(tampon) - nl < 40:
            sur = len(tampon[:nl].rstrip())   # blancs compris : « \nSources : » peut encore arriver
        cr = tampon.rfind("[")
        if cr >= 0 and ")" not in tampon[cr:] and len(tampon) - cr < 300:
            sur = min(sur, cr)           # lien markdown pas encore fermé
        ht = tampon.rfind("http")
        if ht >= 0 and not re.search(r"\s", tampon[ht:]):
            sur = min(sur, ht)           # adresse peut-être pas finie
        if sur > 0:
            sortie = nettoyer_sources(tampon[:sur])
            tampon = tampon[sur:]
            if sortie:
                yield sortie
    if tampon and not coupe:
        fin = nettoyer_sources(tampon)
        if fin.strip():
            yield fin.rstrip()


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
    accepte_outils = True   # le registre peut lui passer des outils MCP (outils maison)

    def __init__(
        self,
        ident: str,
        modele: str,
        commande: str = "claude",
        timeout: float = 120,
        effort: str = "",
        recherche_web: bool = True,
    ) -> None:
        super().__init__(ident, modele)
        self.recherche_web = recherche_web
        self.commande = commande
        self.timeout = timeout
        self.effort = effort
        self.dossier = Path(tempfile.gettempdir()) / "assistant-claude"  # dossier neutre, sans CLAUDE.md
        self._dispo: tuple[float, bool] | None = None

    def _arguments(self, binaire: str, systeme: str, outils: dict | None = None, json_brut: bool = False) -> list[str]:
        args = [
            binaire, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            # outils intégrés : la recherche web au plus, jamais de fichiers ni de commandes
            "--tools", ",".join(OUTILS_WEB) if self.recherche_web else "",
            "--strict-mcp-config",         # seulement les serveurs MCP passés ici (les outils maison)
            "--setting-sources", "",       # ignore les réglages utilisateur et projet
            "--no-session-persistence",
            "--model", self.modele,
        ]
        autoriser = (OUTILS_WEB if self.recherche_web else []) + (outils["autoriser"] if outils else [])
        if outils:
            args += ["--mcp-config", json.dumps(outils["mcp"])]
        if autoriser:
            args += ["--allowedTools", *autoriser]
        note = NOTE_WEB if self.recherche_web else NOTE_SANS_OUTILS
        if outils:
            note = outils["note"] + "\n\n" + (NOTE_WEB if self.recherche_web else "")
        if json_brut:
            note = NOTE_WEB_JSON if self.recherche_web else NOTE_SANS_OUTILS
        args += ["--system-prompt", (systeme + "\n\n" + note).strip()]
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

    async def flux(self, messages: list[Message], outils: dict | None = None) -> AsyncIterator[str]:
        async for x in sans_sources(self._flux_brut(messages, outils)):
            yield x

    async def _flux_brut(self, messages: list[Message], outils: dict | None = None,
                         json_brut: bool = False) -> AsyncIterator[str]:
        binaire = resoudre_binaire(self.commande)
        if not binaire:
            raise FournisseurIndisponible(f"{self.ident} : commande « {self.commande} » introuvable")
        systeme, invite = construire_invite(messages)
        try:
            proc = self._lancer(self._arguments(binaire, systeme, outils, json_brut))
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
        envoye = separer = False
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
                    if e.get("type") == "message_start" and envoye:
                        separer = True   # nouveau tour après un appel d'outil : on sépare les phrases
                    if e.get("type") == "content_block_delta" and d.get("type") == "text_delta" and d.get("text"):
                        if separer:
                            yield "\n\n"
                            separer = False
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

    async def repondre(self, messages: list[Message], options: dict | None = None) -> str:
        return "".join([m async for m in self.flux(messages)])

    async def repondre_json(self, messages: list[Message]) -> str:
        """Réponse brute pour une carte de rapport : sans « Sources : » coupé ni adresses retirées
        (sinon les champs « url » du JSON deviennent des guillemets ouverts et le JSON est illisible)."""
        return "".join([m async for m in self._flux_brut(messages, json_brut=True)])

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
