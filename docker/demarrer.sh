#!/bin/sh
# Démarrage du conteneur : dossiers de /config, modèles manquants, puis l'orchestrateur.
set -e
mkdir -p /config/claude /config/home "${ASSISTANT_MODELES:-/modeles}"
if [ ! -f /config/.env ]; then
  printf '# Secrets (jamais dans git). Jeton longue durée Home Assistant :\nHA_JETON=\n' > /config/.env
fi
python -m assistant.preparer
exec python -m assistant
