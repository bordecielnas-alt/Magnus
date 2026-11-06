"""Évalue le routeur sur assistant/donnees/phrases_routeur.yaml avec les vrais modèles.

Lancement : python -m assistant.eval_routeur  (ou le bouton du tableau de suivi)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

from .config import charger
from .fournisseurs import Registre
from .routeur import Routeur

PHRASES = Path(__file__).resolve().parent / "donnees" / "phrases_routeur.yaml"


def charger_phrases(chemin: Path = PHRASES) -> list[tuple[str, str]]:
    data = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return [(p, cat) for cat in ("simple", "complexe") for p in data.get(cat, [])]


async def evaluer() -> int:
    cfg = charger()
    reg = Registre.depuis_config(cfg)
    routeur = Routeur(cfg.routeur, reg)
    phrases = charger_phrases()
    justes, par_regle, durees_classeur, erreurs = 0, 0, [], []
    try:
        for texte, attendu in phrases:
            d = await routeur.decider(texte)
            ok = d.categorie == attendu
            justes += ok
            if d.raison.startswith("règle") or d.raison == "longueur":
                par_regle += 1
            elif d.raison == "classeur":
                durees_classeur.append(d.duree_ms)
            marque = "OK " if ok else "KO "
            origine = "règle" if d.raison.startswith("règle") else d.raison
            print(f"{marque} {attendu:9} -> {d.categorie:9} {d.duree_ms:7.1f} ms  {origine:10} {texte}")
            if not ok:
                erreurs.append(texte)
    finally:
        await reg.fermer()
    n = len(phrases)
    print()
    print(f"Précision : {justes}/{n} ({justes / n:.0%})")
    print(f"Par règle : {par_regle}/{n} ; par classeur : {len(durees_classeur)}/{n}"
          + (f", {sum(durees_classeur) / len(durees_classeur):.0f} ms en moyenne" if durees_classeur else ""))
    if erreurs:
        print("Mal routées :")
        for e in erreurs:
            print(f"  - {e}")
    return 0 if justes == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(evaluer()))
