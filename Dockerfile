# Image de l'assistant : orchestrateur, voix (Whisper GPU, Kokoro, Piper), Claude Code.
# GPU : lancer avec --runtime=nvidia (Unraid : plugin Nvidia Driver). Sans GPU, tout passe sur processeur.
# Ollama officiel : seulement pour en copier le binaire et ses bibliothèques (CUDA comprises)
FROM ollama/ollama:latest AS ollama

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Node.js pour la commande officielle « claude » (abonnement), installée via npm
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm ca-certificates tini \
 && npm install -g @anthropic-ai/claude-code \
 && npm cache clean --force \
 && apt-get purge -y npm && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY assistant ./assistant
# Bibliothèques CUDA pour faster-whisper (CTranslate2) : cuBLAS et cuDNN 9, compatibles Pascal (P2000) en int8
RUN pip install . "nvidia-cublas-cu12" "nvidia-cudnn-cu12>=9,<10" \
 && rm -rf /app/assistant
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.13/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.13/site-packages/nvidia/cudnn/lib

# Ollama intégré : petit modèle local (qwen2.5:3b) dans le même conteneur, modèles dans /modeles/ollama
COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/lib/ollama

COPY docker/demarrer.sh /usr/local/bin/demarrer
RUN chmod 755 /usr/local/bin/demarrer

# /config  : config.yaml, .env (secrets), alias appris, sécurité, connexion Claude (/config/claude)
# /modeles : modèles voix (téléchargés au premier démarrage si ASSISTANT_TELECHARGER_MODELES=1)
ENV ASSISTANT_ETAT=/config \
    ASSISTANT_SECRETS=/config/.env \
    ASSISTANT_MODELES=/modeles \
    CLAUDE_CONFIG_DIR=/config/claude \
    HOME=/config/home \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility
VOLUME ["/config", "/modeles"]
EXPOSE 8080

# 99:100 = nobody:users, l'utilisateur habituel des appdata Unraid
USER 99:100
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "demarrer"]
