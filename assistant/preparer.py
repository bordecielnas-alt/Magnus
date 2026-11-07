"""Préparation au démarrage du conteneur : télécharge les modèles voix manquants dans /modeles.

Activé par ASSISTANT_TELECHARGER_MODELES=1 (le template Unraid le met à 1). Sources :
- Whisper large-v3-turbo : Hugging Face deepdml/faster-whisper-large-v3-turbo-ct2 (~1,6 Go)
- Kokoro v1.0 : GitHub thewh1teagle/kokoro-onnx, release model-files-v1.0 (~354 Mo)
- Piper fr_FR-siwis-medium : Hugging Face rhasspy/piper-voices (~63 Mo, voix de secours)
Rien n'est retéléchargé si le fichier est déjà là.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

from .config import MODELES

FICHIERS = {
    "kokoro/kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "kokoro/voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    "piper/fr_FR-siwis-medium.onnx": "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx",
    "piper/fr_FR-siwis-medium.onnx.json": "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json",
}


def telecharger(url: str, cible: Path) -> None:
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_suffix(cible.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for bloc in r.iter_bytes(1 << 20):
                f.write(bloc)
    os.replace(tmp, cible)


def main() -> int:
    if os.environ.get("ASSISTANT_TELECHARGER_MODELES", "0") != "1":
        return 0
    print(f"Modèles voix dans {MODELES}", flush=True)
    for rel, url in FICHIERS.items():
        cible = MODELES / rel
        if not cible.exists():
            print(f"  téléchargement {rel}…", flush=True)
            telecharger(url, cible)
    turbo = MODELES / "whisper-large-v3-turbo"
    if not (turbo / "model.bin").exists():
        print("  téléchargement whisper-large-v3-turbo (~1,6 Go)…", flush=True)
        from faster_whisper import download_model
        download_model("deepdml/faster-whisper-large-v3-turbo-ct2", output_dir=str(turbo))
    print("Modèles prêts.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
