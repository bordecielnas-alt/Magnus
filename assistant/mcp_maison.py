"""Serveur MCP « maison » (stdio), lancé par Claude Code pour une demande.

Il ne parle jamais directement à Home Assistant : il relaie vers l'orchestrateur
(API interne sur 127.0.0.1, protégée par un jeton tiré au démarrage). Les mêmes règles
s'appliquent donc : domaines autorisés, inventaire, état d'avant pour « annule ».

Protocole MCP minimal (JSON-RPC 2.0 sur stdin/stdout) : initialize, tools/list, tools/call, ping.
Variables : ASSISTANT_INTERNE_URL, ASSISTANT_INTERNE_JETON, ASSISTANT_CONVERSATION.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

OUTILS = [
    {
        "name": "lister_appareils",
        "description": "Liste les appareils pilotables de la maison (id, nom, pièce, type, état). "
                       "Filtre facultatif : mots d'une pièce, d'un type (lumière, volet…) ou d'un nom.",
        "inputSchema": {"type": "object", "properties": {"filtre": {"type": "string"}}},
    },
    {
        "name": "lire_etat",
        "description": "État détaillé d'un appareil (luminosité, couleur, température, position…).",
        "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "string"}},
                        "required": ["entity_id"]},
    },
    {
        "name": "agir",
        "description": "Agit sur un ou plusieurs appareils. action : allumer, eteindre, ouvrir, fermer, regler. "
                       "valeur : pourcentage (luminosité, position, volume) ou température en °C. "
                       "couleur : rouge, bleu, vert, jaune, orange, violet, rose, blanc chaud, blanc froid…",
        "inputSchema": {"type": "object", "properties": {
            "entity_ids": {"type": "array", "items": {"type": "string"}},
            "action": {"type": "string", "enum": ["allumer", "eteindre", "ouvrir", "fermer", "regler"]},
            "valeur": {"type": "number"},
            "couleur": {"type": "string"},
        }, "required": ["entity_ids", "action"]},
    },
    {
        "name": "annuler",
        "description": "Remet les appareils de la dernière action dans leur état d'avant.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


OUTILS_DOCKER = [
    {
        "name": "lister_conteneurs",
        "description": "Liste les conteneurs Docker du serveur Unraid (nom, image, état, santé). Lecture seule.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "diagnostiquer_conteneur",
        "description": "Diagnostic d'un conteneur : état, santé, redémarrages, code de sortie, manque de mémoire, "
                       "lignes d'erreur récentes et dernières lignes du journal. Lecture seule : tu ne peux rien "
                       "redémarrer ni modifier, seulement proposer un correctif.",
        "inputSchema": {"type": "object", "properties": {"nom": {"type": "string"}}, "required": ["nom"]},
    },
]


OUTILS_AGENDA = [
    {
        "name": "lire_agenda",
        "description": "Événements de l'agenda familial de aujourd'hui à dans N jours (par défaut 7).",
        "inputSchema": {"type": "object", "properties": {"jours": {"type": "integer"}}},
    },
    {
        "name": "ajouter_evenement",
        "description": "Ajoute un événement (avec rappel) dans l'agenda. debut et fin : date-heure ISO locale "
                       "(ex. 2026-11-12T14:30) ; fin facultative (1 h par défaut). journee=true pour un jour entier "
                       "(debut = la date). Confirme ensuite en une phrase ce qui a été ajouté.",
        "inputSchema": {"type": "object", "properties": {
            "titre": {"type": "string"}, "debut": {"type": "string"}, "fin": {"type": "string"},
            "lieu": {"type": "string"}, "journee": {"type": "boolean"}}, "required": ["titre", "debut"]},
    },
]


def outils() -> list[dict]:
    return (OUTILS + (OUTILS_DOCKER if os.environ.get("ASSISTANT_DOCKER") == "1" else [])
            + (OUTILS_AGENDA if os.environ.get("ASSISTANT_AGENDA") == "1" else []))


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ.get("ASSISTANT_INTERNE_URL", "http://127.0.0.1:8080"),
        headers={"X-Jeton-Interne": os.environ.get("ASSISTANT_INTERNE_JETON", "")},
        timeout=20,
    )


def appeler(nom: str, args: dict) -> str:
    conv = os.environ.get("ASSISTANT_CONVERSATION", "")
    with _client() as c:
        if nom == "lister_appareils":
            r = c.get("/interne/maison/appareils", params={"filtre": args.get("filtre", ""), "conversation": conv})
        elif nom == "lire_etat":
            r = c.get(f"/interne/maison/etat/{args['entity_id']}", params={"conversation": conv})
        elif nom == "agir":
            r = c.post("/interne/maison/agir", json={**args, "conversation": conv})
        elif nom == "lister_conteneurs":
            r = c.get("/interne/docker/conteneurs")
        elif nom == "diagnostiquer_conteneur":
            r = c.get("/interne/docker/diagnostic", params={"nom": args["nom"]})
        elif nom == "lire_agenda":
            r = c.get("/interne/agenda", params={"jours": args.get("jours", 7)})
        elif nom == "ajouter_evenement":
            r = c.post("/interne/agenda", json=args)
        elif nom == "annuler":
            r = c.post("/interne/maison/annuler", json={"conversation": conv})
        else:
            raise ValueError(f"outil inconnu : {nom}")
    if r.status_code >= 400:
        raise ValueError(r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return r.text


def traiter(msg: dict) -> dict | None:
    mid, methode = msg.get("id"), msg.get("method", "")
    if mid is None:  # notification (initialized…) : pas de réponse
        return None
    if methode == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        res = {"protocolVersion": version, "capabilities": {"tools": {}},
               "serverInfo": {"name": "maison", "version": "1"}}
    elif methode == "ping":
        res = {}
    elif methode == "tools/list":
        res = {"tools": outils()}
    elif methode == "tools/call":
        p = msg.get("params") or {}
        try:
            texte = appeler(p.get("name", ""), p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": texte}], "isError": False}
        except Exception as e:  # noqa: BLE001 - l'erreur est renvoyée au modèle
            res = {"content": [{"type": "text", "text": f"Erreur : {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"méthode inconnue : {methode}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> None:
    sortie = sys.stdout
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            rep = traiter(json.loads(ligne))
        except ValueError:
            continue
        if rep is not None:
            sortie.write(json.dumps(rep, ensure_ascii=False) + "\n")
            sortie.flush()


if __name__ == "__main__":
    main()
