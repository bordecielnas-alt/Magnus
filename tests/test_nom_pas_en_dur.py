"""Le nom par défaut de l'assistant ne doit apparaître que dans assistant/defaults.yaml."""

from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
EXTENSIONS = {".py", ".html", ".js", ".css", ".ts", ".json"}


def test_nom_par_defaut_absent_du_code():
    defauts = yaml.safe_load((RACINE / "assistant" / "defaults.yaml").read_text(encoding="utf-8"))
    nom = defauts["identite"]["nom"].lower()
    fautifs = []
    for f in (RACINE / "assistant").rglob("*"):
        if f.suffix in EXTENSIONS and nom in f.read_text(encoding="utf-8").lower():
            fautifs.append(str(f.relative_to(RACINE)))
    assert not fautifs, f"nom « {nom} » écrit en dur dans : {fautifs}"
