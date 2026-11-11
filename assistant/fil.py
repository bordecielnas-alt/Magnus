"""Fil de conversation unique par compte : le même assistant sur tous les appareils.

- SQLite (<état>/fil.db) : chaque échange (question, réponse, annonce) est gardé, avec l'appareil d'où il vient.
- Un identifiant de conversation stable et secret par compte : la « réponse à Lequel ? », le « oui » à une
  proposition de souvenir ou de redémarrage marchent depuis n'importe quel appareil du compte.
- Abonnements en direct : chaque page ouverte reçoit la question et la réponse (mot à mot) des autres.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

TOURS_MAX = 1000           # par compte ; les plus anciens sont effacés
CONTEXTE_HEURES = 24       # le modèle reçoit les échanges des dernières 24 h (au plus HISTORIQUE_MAX)


def maintenant() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Fil:
    def __init__(self, dossier: Path) -> None:
        dossier.mkdir(parents=True, exist_ok=True)
        self.fichier = dossier / "fil.db"
        self._verrou = threading.Lock()
        self._db = sqlite3.connect(self.fichier, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS comptes (compte TEXT PRIMARY KEY, conversation TEXT NOT NULL UNIQUE,
                                                efface TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS tours (
                id INTEGER PRIMARY KEY, compte TEXT NOT NULL, quand TEXT NOT NULL, role TEXT NOT NULL,
                texte TEXT NOT NULL, appareil TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                question INTEGER, extra TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS tours_compte ON tours(compte, id);
        """)
        self._abonnes: dict[str, set[asyncio.Queue]] = {}
        self._boucle: asyncio.AbstractEventLoop | None = None

    # --- identité de la conversation ---------------------------------------------------------------------
    def conversation(self, compte: str) -> str:
        """Identifiant de conversation du compte (créé au premier appel, puis toujours le même)."""
        with self._verrou, self._db:
            r = self._db.execute("SELECT conversation FROM comptes WHERE compte=?", (compte,)).fetchone()
            if r:
                return r["conversation"]
            ident = "fil-" + secrets.token_hex(12)
            self._db.execute("INSERT INTO comptes(compte, conversation) VALUES (?,?)", (compte, ident))
            return ident

    def compte_de(self, conversation: str) -> str | None:
        r = self._db.execute("SELECT compte FROM comptes WHERE conversation=?", (conversation,)).fetchone()
        return r["compte"] if r else None

    # --- écriture ------------------------------------------------------------------------------------------
    def ajouter(self, compte: str, role: str, texte: str, appareil: str = "", source: str = "",
                question: int | None = None, extra: dict | None = None) -> dict:
        with self._verrou, self._db:
            cur = self._db.execute(
                "INSERT INTO tours(compte, quand, role, texte, appareil, source, question, extra) VALUES (?,?,?,?,?,?,?,?)",
                (compte, maintenant(), role, texte[:20000], appareil[:40], source, question,
                 json.dumps(extra or {}, ensure_ascii=False)))
            self._db.execute("DELETE FROM tours WHERE compte=? AND id <= (SELECT id FROM tours WHERE compte=? "
                             "ORDER BY id DESC LIMIT 1 OFFSET ?)", (compte, compte, TOURS_MAX))
        t = self._lire(cur.lastrowid)
        self.publier(compte, {"type": "tour", "tour": t})
        return t

    def effacer(self, compte: str, appareil: str = "") -> None:
        """« Nouvelle conversation » : le fil repart de zéro sur tous les appareils (la mémoire reste)."""
        with self._verrou, self._db:
            self._db.execute("DELETE FROM tours WHERE compte=?", (compte,))
        self.publier(compte, {"type": "efface", "appareil": appareil})

    # --- lecture -------------------------------------------------------------------------------------------
    def _lire(self, ident: int) -> dict:
        return self._json(self._db.execute("SELECT * FROM tours WHERE id=?", (ident,)).fetchone())

    @staticmethod
    def _json(r: sqlite3.Row) -> dict:
        t = dict(r)
        t["extra"] = json.loads(t.get("extra") or "{}")
        return t

    def tours(self, compte: str, depuis: int = 0, n: int = 60) -> list[dict]:
        """Les n derniers échanges (après « depuis » si donné), du plus ancien au plus récent."""
        rows = self._db.execute("SELECT * FROM tours WHERE compte=? AND id>? ORDER BY id DESC LIMIT ?",
                                (compte, depuis, n)).fetchall()
        return [self._json(r) for r in reversed(rows)]

    def contexte(self, compte: str, n: int, heures: float = CONTEXTE_HEURES) -> list[dict]:
        """Échanges récents pour le modèle : questions et réponses seulement, les plus récentes."""
        limite = (datetime.now().astimezone() - timedelta(hours=heures)).isoformat(timespec="seconds")
        rows = self._db.execute("SELECT role, texte FROM tours WHERE compte=? AND role IN ('user','assistant') "
                                "AND texte<>'' AND quand>=? ORDER BY id DESC LIMIT ?", (compte, limite, n)).fetchall()
        return [{"role": r["role"], "content": r["texte"]} for r in reversed(rows)]

    # --- direct ----------------------------------------------------------------------------------------------
    def abonner(self, compte: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._boucle = asyncio.get_running_loop()
        self._abonnes.setdefault(compte, set()).add(q)
        return q

    def desabonner(self, compte: str, q: asyncio.Queue) -> None:
        self._abonnes.get(compte, set()).discard(q)

    def ouverts(self, compte: str) -> int:
        return len(self._abonnes.get(compte, ()))

    def publier(self, compte: str, evenement: dict) -> None:
        if not self._abonnes.get(compte):
            return
        try:
            dans_la_boucle = asyncio.get_running_loop() is self._boucle
        except RuntimeError:
            dans_la_boucle = False
        if not dans_la_boucle and self._boucle and not self._boucle.is_closed():
            # appelé depuis un fil d'exécution (route synchrone) : les files asyncio ne sont pas partageables
            self._boucle.call_soon_threadsafe(self._distribuer, compte, evenement)
        else:
            self._distribuer(compte, evenement)

    def _distribuer(self, compte: str, evenement: dict) -> None:
        for q in list(self._abonnes.get(compte, ())):
            try:
                q.put_nowait(evenement)
            except asyncio.QueueFull:      # page bloquée : elle se resynchronisera en se reconnectant
                self.desabonner(compte, q)
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    def fermer(self) -> None:
        self._db.close()
