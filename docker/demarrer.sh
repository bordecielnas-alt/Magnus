#!/bin/sh
# Démarrage du conteneur : dossiers de /config, Ollama intégré, modèles manquants, puis l'orchestrateur.
set -e
MODELES="${ASSISTANT_MODELES:-/modeles}"
mkdir -p /config/claude /config/home "$MODELES"
if [ ! -f /config/.env ]; then
  printf '# Secrets (jamais dans git). Jeton longue durée Home Assistant :\nHA_JETON=\n' > /config/.env
fi

# Ollama intégré (petit modèle local : routeur, réponses rapides, secours). ASSISTANT_OLLAMA_INTEGRE=0 pour le couper.
if [ "${ASSISTANT_OLLAMA_INTEGRE:-1}" = "1" ] && command -v ollama >/dev/null 2>&1; then
  export OLLAMA_MODELS="$MODELES/ollama" OLLAMA_HOST="127.0.0.1:11434" OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-24h}"
  mkdir -p "$OLLAMA_MODELS"
  ollama serve > /config/ollama.log 2>&1 &
  for i in $(seq 1 30); do
    if ollama list >/dev/null 2>&1; then break; fi
    sleep 1
  done
  MODELE="${ASSISTANT_OLLAMA_MODELE:-qwen2.5:3b}"
  if [ "${ASSISTANT_TELECHARGER_MODELES:-0}" = "1" ] && ! ollama list | grep -q "^$MODELE"; then
    echo "Téléchargement du modèle Ollama $MODELE (~2 Go)…"
    ollama pull "$MODELE"
  fi
  echo "Ollama intégré prêt (journal : /config/ollama.log)."
fi

python -m assistant.preparer
exec python -m assistant
