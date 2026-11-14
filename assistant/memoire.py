"""Mémoire de l'assistant : souvenirs du foyer (partagés) et privés (par compte), validés un par un.

- SQLite (<état>/memoire.db), recherche plein texte FTS5 (accents ignorés), sans modèle à télécharger.
- Cycle d'un souvenir : « propose » (repéré dans la conversation) -> « valide » (la personne a dit oui)
  ou « refuse ». Seuls les souvenirs validés sont utilisés dans les réponses.
- Chaque création, validation, modification ou suppression est notée dans l'historique (avant / après).
- Un souvenir privé n'est visible que par son compte ; un souvenir du foyer par tous.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

PORTEES = ("foyer", "prive")
STATUTS = ("propose", "valide", "refuse", "supprime")
MOTS_VIDES = {"le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "a", "à", "au", "aux", "en", "est",
              "que", "qui", "quoi", "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "mon", "ma", "mes",
              "ton", "ta", "tes", "son", "sa", "ses", "ce", "cette", "pour", "pas", "ne", "se", "sur", "dans",
              "par", "avec", "moi", "toi", "lui", "y", "d", "l", "j", "c", "qu", "s", "t", "m", "n"}


def maintenant() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Memoire:
    def __init__(self, dossier: Path) -> None:
        dossier.mkdir(parents=True, exist_ok=True)
        self.fichier = dossier / "memoire.db"
        self._verrou = threading.Lock()
        self._db = sqlite3.connect(self.fichier, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS souvenirs (
                id INTEGER PRIMARY KEY, texte TEXT NOT NULL, portee TEXT NOT NULL, compte TEXT NOT NULL DEFAULT '',
                statut TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL, maj TEXT NOT NULL,
                conversation TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS historique (
                id INTEGER PRIMARY KEY, souvenir INTEGER NOT NULL, quand TEXT NOT NULL, compte TEXT NOT NULL,
                action TEXT NOT NULL, avant TEXT, apres TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS recherche USING fts5(
                texte, content='souvenirs', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
            CREATE TRIGGER IF NOT EXISTS s_ai AFTER INSERT ON souvenirs BEGIN
                INSERT INTO recherche(rowid, texte) VALUES (new.id, new.texte); END;
            CREATE TRIGGER IF NOT EXISTS s_ad AFTER DELETE ON souvenirs BEGIN
                INSERT INTO recherche(recherche, rowid, texte) VALUES ('delete', old.id, old.texte); END;
            CREATE TRIGGER IF NOT EXISTS s_au AFTER UPDATE ON souvenirs BEGIN
                INSERT INTO recherche(recherche, rowid, texte) VALUES ('delete', old.id, old.texte);
                INSERT INTO recherche(rowid, texte) VALUES (new.id, new.texte); END;
        """)
        colonnes = {r[1] for r in self._db.execute("PRAGMA table_info(souvenirs)")}
        if "remplace" not in colonnes:      # mise à jour d'un souvenir existant (proposée, puis validée)
            self._db.execute("ALTER TABLE souvenirs ADD COLUMN remplace INTEGER")
            self._db.commit()

    # --- écriture -------------------------------------------------------------------------------------
    def _noter(self, souvenir: int, compte: str, action: str, avant: str | None, apres: str | None) -> None:
        self._db.execute("INSERT INTO historique(souvenir, quand, compte, action, avant, apres) VALUES (?,?,?,?,?,?)",
                         (souvenir, maintenant(), compte, action, avant, apres))

    def ajouter(self, texte: str, portee: str, compte: str, statut: str = "propose", source: str = "",
                conversation: str = "", par: str = "", remplace: int | None = None) -> dict:
        texte = re.sub(r"\s+", " ", texte).strip()
        if not texte or len(texte) > 500:
            raise ValueError("souvenir vide ou trop long (500 caractères maximum)")
        if portee not in PORTEES or statut not in ("propose", "valide"):
            raise ValueError("portée ou statut invalide")
        with self._verrou, self._db:
            existant = self._db.execute(
                "SELECT id FROM souvenirs WHERE lower(texte)=lower(?) AND statut IN ('propose','valide') "
                "AND (portee='foyer' OR compte=?)", (texte, compte)).fetchone()
            if existant:
                return self.lire(existant["id"])
            t = maintenant()
            cur = self._db.execute(
                "INSERT INTO souvenirs(texte, portee, compte, statut, source, cree, maj, conversation, remplace) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (texte, portee, compte, statut, source, t, t, conversation, remplace))
            self._noter(cur.lastrowid, par or compte, "proposé" if statut == "propose" else "ajouté", None, texte)
        return self.lire(cur.lastrowid)

    def changer_statut(self, ident: int, statut: str, par: str) -> dict:
        if statut not in STATUTS:
            raise ValueError("statut invalide")
        with self._verrou, self._db:
            s = self._db.execute("SELECT * FROM souvenirs WHERE id=?", (ident,)).fetchone()
            if not s:
                raise KeyError(ident)
            self._db.execute("UPDATE souvenirs SET statut=?, maj=? WHERE id=?", (statut, maintenant(), ident))
            if statut == "valide" and s["remplace"]:
                # la mise à jour validée remplace l'ancien souvenir
                ancien = self._db.execute("SELECT texte FROM souvenirs WHERE id=?", (s["remplace"],)).fetchone()
                self._db.execute("UPDATE souvenirs SET statut='supprime', maj=? WHERE id=?", (maintenant(), s["remplace"]))
                if ancien:
                    self._noter(s["remplace"], par, "remplacé", ancien["texte"], s["texte"])
            self._noter(ident, par, {"valide": "validé", "refuse": "refusé", "supprime": "supprimé",
                                     "propose": "reproposé"}[statut], s["statut"], statut)
        return self.lire(ident)

    def restaurer(self, ident: int, par: str) -> dict:
        """Annule un oubli ou un refus : le souvenir reprend le statut qu'il avait (validé par défaut)."""
        with self._verrou, self._db:
            s = self._db.execute("SELECT * FROM souvenirs WHERE id=?", (ident,)).fetchone()
            if not s:
                raise KeyError(ident)
            if s["statut"] not in ("refuse", "supprime"):
                raise ValueError("ce souvenir n'est ni oublié ni refusé")
            doublon = self._db.execute(
                "SELECT id FROM souvenirs WHERE id<>? AND lower(texte)=lower(?) AND statut IN ('propose','valide') "
                "AND (portee='foyer' OR compte=?)", (ident, s["texte"], s["compte"])).fetchone()
            if doublon:
                raise ValueError("un souvenir identique est déjà en mémoire")
            dernier = self._db.execute(
                "SELECT avant FROM historique WHERE souvenir=? AND action IN ('supprimé','refusé') ORDER BY id DESC "
                "LIMIT 1", (ident,)).fetchone()
            statut = dernier["avant"] if dernier and dernier["avant"] in ("propose", "valide") else "valide"
            self._db.execute("UPDATE souvenirs SET statut=?, maj=? WHERE id=?", (statut, maintenant(), ident))
            self._noter(ident, par, "restauré", s["statut"], statut)
        return self.lire(ident)

    def modifier(self, ident: int, texte: str | None, portee: str | None, par: str) -> dict:
        with self._verrou, self._db:
            s = self._db.execute("SELECT * FROM souvenirs WHERE id=?", (ident,)).fetchone()
            if not s:
                raise KeyError(ident)
            nouveau = re.sub(r"\s+", " ", texte).strip() if texte is not None else s["texte"]
            p = portee or s["portee"]
            if not nouveau or len(nouveau) > 500 or p not in PORTEES:
                raise ValueError("souvenir vide, trop long ou portée invalide")
            # un souvenir du foyer passé en privé devient celui de la personne qui le change
            compte = par if p == "prive" and s["portee"] == "foyer" and par else s["compte"]
            self._db.execute("UPDATE souvenirs SET texte=?, portee=?, compte=?, maj=? WHERE id=?",
                             (nouveau, p, compte, maintenant(), ident))
            self._noter(ident, par, "modifié", f"{s['portee']} : {s['texte']}", f"{p} : {nouveau}")
        return self.lire(ident)

    # --- lecture ----------------------------------------------------------------------------------------
    def lire(self, ident: int) -> dict:
        s = self._db.execute("SELECT * FROM souvenirs WHERE id=?", (ident,)).fetchone()
        if not s:
            raise KeyError(ident)
        return dict(s)

    def visible(self, s: dict, compte: str, admin: bool = False) -> bool:
        """Foyer : tout le monde ; privé : son compte, et l'administrateur (écran Mémoire)."""
        return admin or s["portee"] == "foyer" or s["compte"] == compte

    def liste(self, compte: str, statuts: tuple[str, ...] = ("propose", "valide"), tous: bool = False) -> list[dict]:
        """Foyer + privés du compte ; `tous` (administrateur, écran Mémoire) : aussi les privés des autres."""
        q = ("SELECT * FROM souvenirs WHERE statut IN (%s) AND (? OR portee='foyer' OR compte=?) ORDER BY maj DESC"
             % ",".join("?" * len(statuts)))
        return [dict(r) for r in self._db.execute(q, (*statuts, 1 if tous else 0, compte))]

    def historique(self, ident: int) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM historique WHERE souvenir=? ORDER BY id DESC", (ident,))]

    def proposition_en_attente(self, conversation: str, compte: str, delai_s: int = 300) -> dict | None:
        """Souvenir que l'assistant vient de proposer dans cette conversation (« oui » / « non » attendu)."""
        from datetime import timedelta
        limite = (datetime.now().astimezone() - timedelta(seconds=delai_s)).isoformat(timespec="seconds")
        r = self._db.execute("SELECT * FROM souvenirs WHERE statut='propose' AND conversation=? AND compte=? "
                             "AND cree >= ? ORDER BY id DESC LIMIT 1", (conversation, compte, limite)).fetchone()
        return dict(r) if r else None

    def chercher(self, texte: str, compte: str, prive: bool = True, n: int = 6) -> list[dict]:
        """Souvenirs validés en rapport avec le message (mots communs), foyer + privés du compte."""
        mots = [m for m in re.findall(r"\w+", texte.lower()) if m not in MOTS_VIDES and len(m) > 2][:12]
        if not mots:
            return []
        requete = " OR ".join(f'"{m}"*' for m in mots)
        filtre = "(s.portee='foyer' OR (s.portee='prive' AND s.compte=?))" if prive else "s.portee='foyer'"
        params = (requete, compte, n) if prive else (requete, n)
        try:
            rows = self._db.execute(
                f"SELECT s.* FROM recherche r JOIN souvenirs s ON s.id=r.rowid WHERE recherche MATCH ? "
                f"AND s.statut='valide' AND {filtre} ORDER BY bm25(recherche) LIMIT ?", params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    def renommer_compte(self, compte: str, ancien: str, nouveau: str) -> int:
        """Les souvenirs « Ancien nom : … » de ce compte prennent le nouveau nom."""
        with self._verrou, self._db:
            lignes = self._db.execute("SELECT id, texte FROM souvenirs WHERE compte=? AND texte LIKE ?",
                                      (compte, f"{ancien} : %")).fetchall()
            for r in lignes:
                self._db.execute("UPDATE souvenirs SET texte=?, maj=? WHERE id=?",
                                 (nouveau + r["texte"][len(ancien):], maintenant(), r["id"]))
        return len(lignes)

    def fermer(self) -> None:
        self._db.close()
