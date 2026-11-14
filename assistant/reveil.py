"""Wake word par transcription : « <nom>, allume le globe ».

La page écoute en continu et envoie chaque morceau de parole (découpé par le silence).
Whisper (GPU) le transcrit ; s'il commence par le wake word (configurable, jamais écrit ici),
on garde ce qui suit comme commande. Après un réveil, une fenêtre de conversation laisse
parler sans répéter le nom, pendant `fenetre_s` secondes (ou toujours).

Avantage : aucun entraînement, et le wake word suit le nom de l'assistant dès qu'il change.
Un modèle openWakeWord dédié (t205) pourra plus tard servir de premier filtre plus léger.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .maison.texte import normaliser

# Phrases que Whisper invente sur du bruit ou du silence (« sous-titres… »)
HALLUCINATIONS = (
    "sous-titres", "sous titres", "merci d'avoir regardé", "abonnez-vous", "amara.org",
    "sous-titrage", "transcription par",
)
POLITESSES = {"ok", "dis", "hey", "he", "eh", "salut", "bonjour", "coucou", "alors", "hé", "oh"}


def distance(a: str, b: str) -> int:
    """Distance de Levenshtein (petits mots)."""
    if a == b:
        return 0
    prec = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cour = [i]
        for j, cb in enumerate(b, 1):
            cour.append(min(prec[j] + 1, cour[j - 1] + 1, prec[j - 1] + (ca != cb)))
        prec = cour
    return prec[-1]


def tolerance(mot: str) -> int:
    return 0 if len(mot) <= 3 else 1 if len(mot) <= 6 else 2


@dataclass
class Detection:
    reveil: bool          # le wake word a été entendu
    commande: str         # ce qui suit le wake word (vide si « <nom> ? » seul)


def detecter(texte: str, wake_word: str | list[str]) -> Detection:
    """Cherche le wake word dans les trois premiers mots (« Ok <nom>, … », « Dis <nom>… »).
    `wake_word` peut être une liste de variantes acceptées (« hal », « hall »…)."""
    if isinstance(wake_word, (list, tuple)):
        for w in wake_word:
            d = detecter(texte, w)
            if d.reveil:
                return d
        return Detection(False, "")
    cible = normaliser(wake_word).split()
    if not cible:
        return Detection(False, "")
    jetons = list(re.finditer(r"[\w'’-]+", texte))
    mots = [normaliser(m.group(0)) for m in jetons]
    n = len(cible)
    for i in range(0, min(3, len(mots) - n + 1)):
        if any(m not in POLITESSES for m in mots[:i]):
            break
        candidat = " ".join(mots[i:i + n])
        if distance(candidat, " ".join(cible)) <= tolerance(" ".join(cible)):
            fin = jetons[i + n - 1].end()
            reste = texte[fin:].lstrip(" ,.!?;:-—").strip()
            # « Hal 9000, … » : le numéro fait partie du nom
            reste = re.sub(r"^(9000|9 000|neuf mille)\b[\s,.!?;:—-]*", "", reste, flags=re.I)
            # « <nom> <nom> <nom>… » : Whisper répète parfois le nom sur du bruit -> simple réveil
            suite = detecter(reste, wake_word) if reste else None
            return Detection(True, suite.commande if suite and suite.reveil else reste)
    return Detection(False, "")


def est_hallucination(texte: str) -> bool:
    t = texte.lower().strip()
    if not t or any(h in t for h in HALLUCINATIONS) or len(normaliser(t)) < 2:
        return True
    # « Cough, Cough, Cough, Cough » : un même mot répété sur un bruit (toux, claquement)
    m = normaliser(t).split()
    return len(m) >= 3 and len(set(m)) == 1


MOTS_COURANTS = set("""le la les l un une des de du d au aux et ou à a est es sont tu je il elle on nous vous ils me te se
mon ma mes ton ta tes son sa ses ce cet cette ces qui que quoi quel quelle quels quelles comment combien pourquoi où quand
pas ne plus oui non ok dans sur sous avec pour par en y fais fait mets met allume éteins eteins ouvre ferme lance arrête
arrete démarre demarre redémarre dis donne montre regarde envoie état etat est-ce c ça ca il-y quelle""".split())


def semble_charabia(texte: str) -> bool:
    """« Escortrack, SIL, SALVAF » : une suite de morceaux sans aucun mot courant, typique d'un bruit mal transcrit.
    Une vraie demande contient presque toujours un mot outil (le, de, est, allume…)."""
    morceaux = [m.strip() for m in re.split(r"[,;]", texte) if m.strip()]
    mots = [normaliser(m) for m in re.findall(r"[\w'’-]+", texte)]
    if not mots or len(mots) > 6:
        return False
    if any(m in MOTS_COURANTS or m.split("'")[0] in MOTS_COURANTS for m in mots):
        return False
    return len(morceaux) >= 3 or len(mots) >= 3


class Reveil:
    """État d'écoute par conversation (page) : réveillé jusqu'à telle heure.

    `fenetre_s` et `toujours` sont les valeurs de la maison ; chaque conversation peut avoir les siennes
    (celles de la personne connectée, voir regler)."""

    def __init__(self, fenetre_s: float, toujours: bool = False) -> None:
        self.fenetre_s = fenetre_s
        self.toujours = toujours
        self._jusqu_a: dict[str, float] = {}
        self._propres: dict[str, tuple[float, bool]] = {}

    def regler(self, conversation: str, fenetre_s: float, toujours: bool) -> None:
        """Réglages d'écoute de la personne qui parle dans cette conversation."""
        if conversation:
            self._propres[conversation] = (float(fenetre_s), bool(toujours))

    def _fenetre(self, conversation: str) -> float:
        return self._propres.get(conversation, (self.fenetre_s, self.toujours))[0]

    def _toujours(self, conversation: str) -> bool:
        return self._propres.get(conversation, (self.fenetre_s, self.toujours))[1]

    def eveille(self, conversation: str) -> bool:
        return self._toujours(conversation) or time.monotonic() < self._jusqu_a.get(conversation, 0)

    def prolonger(self, conversation: str) -> None:
        self._jusqu_a[conversation] = time.monotonic() + self._fenetre(conversation)

    def endormir(self, conversation: str) -> None:
        self._jusqu_a.pop(conversation, None)

    def restant_s(self, conversation: str) -> float | None:
        if self._toujours(conversation):
            return None
        return max(0.0, round(self._jusqu_a.get(conversation, 0) - time.monotonic(), 1))

    def analyser(self, texte: str, wake_word: str | list[str], conversation: str) -> dict:
        """Décide quoi faire d'un morceau de parole entendu en écoute continue.

        action : « ignorer » (pas pour l'assistant), « reveil » (nom seul : on écoute la suite),
                 « commande » (texte à traiter comme un message).
        """
        if est_hallucination(texte):
            return {"action": "ignorer", "raison": "bruit"}
        d = detecter(texte, wake_word)
        if d.reveil:
            self.prolonger(conversation)
            if not d.commande or semble_charabia(d.commande):
                # « <nom>, Escortrack, SIL » : le nom est bien là, la suite est du bruit -> on écoute la suite
                return {"action": "reveil", "fenetre_s": self.restant_s(conversation)}
            return {"action": "commande", "commande": d.commande, "fenetre_s": self.restant_s(conversation)}
        if self.eveille(conversation):
            if semble_charabia(texte):
                return {"action": "ignorer", "raison": "incompréhensible"}
            if not self._toujours(conversation):
                self.prolonger(conversation)   # la conversation continue : on prolonge la fenêtre
            return {"action": "commande", "commande": texte.strip(), "fenetre_s": self.restant_s(conversation)}
        return {"action": "ignorer", "raison": "pas de wake word"}
