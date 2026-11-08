"""Fichier de diagnostic : événements envoyés par la page (micro, voix, chat…) et journal du serveur.

- La page envoie ses lignes toutes les 3 s (POST /api/diagnostic/evenements).
- Chaque appareil a son fichier <état>/diagnostic/<appareil>.log ; le serveur a serveur.log.
- GET /diagnostic assemble les 2 derniers jours, triés par heure, secrets masqués.
Une ligne par événement : « heure-UTC-ISO<TAB>source<TAB>texte » (les retours à la ligne sont échappés).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAILLE_MAX = 2 * 1024 * 1024      # par fichier ; au-delà on garde la seconde moitié
GARDER_JOURS = 7
LIGNE_MAX = 2048
# « jeton=abc », « "password": "x" », « Authorization: Bearer x », « Cookie: … »
RE_SECRET = re.compile(
    r"""(?i)((?:token|jeton|secret|password|mot_de_passe|mdp|totp|authorization|cookie|api[_-]?key)[\w-]*["']?\s*[:=]\s*["']?)"""
    r"""(?:bearer\s+)?[^\s"',;}&]+""")
RE_JWT = re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{5,}")


def masquer(texte: str) -> str:
    return RE_JWT.sub("•••", RE_SECRET.sub(r"\1•••", texte))


def _utc(heure: str | None = None) -> str:
    if heure:
        try:
            d = datetime.fromisoformat(heure.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _aplatir(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace("\t", " ")


class Diagnostic:
    def __init__(self, dossier: Path) -> None:
        self.dossier = dossier / "diagnostic"
        self._verrou = threading.Lock()
        self._purge = 0.0

    # --- écriture -----------------------------------------------------------------------------------
    def _ajouter(self, nom: str, lignes: list[tuple[str, str, str]]) -> None:
        texte = "".join(f"{h}\t{s}\t{_aplatir(t)[:LIGNE_MAX * 2]}\n" for h, s, t in lignes)
        with self._verrou:
            try:
                self.dossier.mkdir(parents=True, exist_ok=True)
                f = self.dossier / f"{nom}.log"
                with open(f, "a", encoding="utf-8") as sortie:
                    sortie.write(texte)
                if f.stat().st_size > TAILLE_MAX:
                    contenu = f.read_bytes()[-TAILLE_MAX // 2:]
                    f.write_bytes(contenu[contenu.find(b"\n") + 1:])
                if time.time() - self._purge > 3600:
                    self._purge = time.time()
                    self._purger()
            except OSError:
                pass       # le diagnostic ne doit jamais gêner l'assistant

    def _purger(self) -> None:
        limite = time.time() - GARDER_JOURS * 86400
        for f in self.dossier.glob("*.log"):
            if f.stat().st_mtime < limite:
                f.unlink(missing_ok=True)

    def evenements(self, appareil: str, page: str, lignes: list[dict], contexte: str | None = None,
                   compte: str = "", ip: str = "") -> None:
        # nom de fichier sûr : jamais de « ../ » ; sans identifiant (stockage du navigateur bloqué) -> « inconnu »
        appareil = re.sub(r"[^A-Za-z0-9_-]", "", appareil)[:8] or "inconnu"
        page = re.sub(r"[^A-Za-z0-9_-]", "", page)[:8] or "?"
        source = f"{appareil}/{page}"
        a_ecrire = []
        if contexte:
            a_ecrire.append((_utc(), source, f"CONTEXTE ({compte or '?'} depuis {ip}) :\n{contexte[:4096]}"))
        for l in lignes[:500]:
            t = str(l.get("texte", ""))[:LIGNE_MAX]
            if "ms" in l:
                t = f"+{l['ms']} ms  {t}"
            a_ecrire.append((_utc(l.get("heure")), source, t))
        if a_ecrire:
            self._ajouter(appareil, a_ecrire)

    def serveur(self, texte: str) -> None:
        self._ajouter("serveur", [(_utc(), "serveur", texte)])

    # --- lecture ------------------------------------------------------------------------------------------
    def rapport(self, jours: float = 2, entete: str = "") -> str:
        limite = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        lignes: list[tuple[str, str, str]] = []
        fichiers = sorted(self.dossier.glob("*.log")) if self.dossier.is_dir() else []
        for f in fichiers:
            try:
                for brut in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    h, _, reste = brut.partition("\t")
                    s, _, t = reste.partition("\t")
                    if h >= limite:
                        lignes.append((h, s, t))
            except OSError:
                continue
        lignes.sort(key=lambda x: x[0])
        sortie = [entete, f"Appareils : {', '.join(f.stem for f in fichiers) or 'aucun'} · {len(lignes)} lignes depuis {limite}", ""]
        for h, s, t in lignes:
            texte = t.replace("\\n", "\n" + " " * 4).replace("\\\\", "\\")
            sortie.append(f"{h}  [{s}]  {texte}")
        return masquer("\n".join(sortie))


class GestionnaireJournal(logging.Handler):
    """Copie les messages du serveur (infos, avertissements, erreurs avec leur trace) dans serveur.log."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(logging.INFO)
        self.diagnostic = diagnostic

    def emit(self, record: logging.LogRecord) -> None:
        try:
            texte = f"{record.levelname} {record.name}: {record.getMessage()}"
            if record.exc_info:
                texte += "\n" + "".join(traceback.format_exception(*record.exc_info))
            self.diagnostic.serveur(texte)
        except Exception:  # noqa: BLE001
            pass
