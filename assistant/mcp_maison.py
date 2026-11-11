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
                       "lignes d'erreur récentes, dernières lignes du journal, causes_probables (déduites des journaux et "
                       "du code de sortie) et redemarrage_possible. Lecture seule : pour redémarrer, passe par "
                       "demander_redemarrage (après le oui de la personne).",
        "inputSchema": {"type": "object", "properties": {"nom": {"type": "string"}}, "required": ["nom"]},
    },
]


OUTILS_AGENDA = [
    {
        "name": "lire_agenda",
        "description": "Événements de l'agenda familial de aujourd'hui à dans N jours (par défaut 7), avec leur "
                       "« ref » (pour supprimer_evenement).",
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


OUTILS_ROUTINES = [
    {
        "name": "creer_routine",
        "description": "Enregistre une routine nommée (ex. « mode cinéma ») : une suite d'étapes {entity_ids, action, "
                       "valeur?, couleur?} avec les mêmes actions que agir. Elle se lance ensuite en disant son nom. "
                       "Cherche d'abord les appareils avec lister_appareils ; récapitule la routine en une phrase.",
        "inputSchema": {"type": "object", "properties": {
            "nom": {"type": "string"},
            "etapes": {"type": "array", "items": {"type": "object", "properties": {
                "entity_ids": {"type": "array", "items": {"type": "string"}},
                "action": {"type": "string", "enum": ["allumer", "eteindre", "ouvrir", "fermer", "regler"]},
                "valeur": {"type": "number"}, "couleur": {"type": "string"}}, "required": ["entity_ids", "action"]}}},
            "required": ["nom", "etapes"]},
    },
    {
        "name": "lister_routines",
        "description": "Routines enregistrées (nom et étapes).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


OUTILS_MEMOIRE = [
    {
        "name": "proposer_souvenir",
        "description": "Propose de retenir une information (ou de mettre à jour un souvenir existant : remplace = son id, "
                       "donné entre crochets dans « Ce que tu sais »). Rien n'est gardé tant que la personne n'a pas dit "
                       "oui : termine ta réponse par la question « Je retiens « … » ? ». portee : foyer (toute la maison) "
                       "ou prive (seulement la personne).",
        "inputSchema": {"type": "object", "properties": {
            "texte": {"type": "string"}, "portee": {"type": "string", "enum": ["foyer", "prive"]},
            "remplace": {"type": "integer"}}, "required": ["texte"]},
    },
]


OUTILS_AGENDA.append({
    "name": "supprimer_evenement",
    "description": "Supprime UN événement de l'agenda, par sa ref (lue avec lire_agenda). Seulement quand la personne "
                   "l'a demandé clairement et qu'un seul événement correspond ; sinon demande lequel. Dis ensuite "
                   "en une phrase ce qui a été supprimé.",
    "inputSchema": {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
})


OUTILS_DOCKER += [
    {
        "name": "etat_serveur",
        "description": "État du serveur Unraid : array (démarré ?), capacité, chaque disque (état, température, "
                       "erreurs, SMART, espace libre), dernier contrôle de parité, et liste des alertes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "demander_redemarrage",
        "description": "Prépare le redémarrage, le démarrage (« allume ») ou l'arrêt (« éteins ») d'un conteneur. "
                       "Rien n'est fait tout de suite : termine ta réponse par « Je redémarre <nom> ? », « Je démarre "
                       "<nom> ? » ou « J'arrête <nom> ? » ; l'action a lieu seulement si la personne répond oui. "
                       "Le nom peut être approximatif (transcription vocale) : utilise le nom renvoyé.",
        "inputSchema": {"type": "object", "properties": {
            "nom": {"type": "string"},
            "action": {"type": "string", "enum": ["redemarrer", "demarrer", "arreter"], "default": "redemarrer"}},
            "required": ["nom"]},
    },
]


OUTILS_RAPPELS = [
    {
        "name": "creer_rappel",
        "description": "Rappel dans le chat (pas dans le calendrier) : « rappelle-moi dans 10 min de… », « mets un rappel "
                       "à 18 h pour… ». quand : date-heure ISO locale (ex. 2026-11-09T18:00). Un rendez-vous (médecin, "
                       "réunion…) va plutôt dans l'agenda ; si ce n'est pas clair, demande. Confirme en une phrase courte.",
        "inputSchema": {"type": "object", "properties": {"quand": {"type": "string"}, "texte": {"type": "string"}},
                        "required": ["quand", "texte"]},
    },
    {
        "name": "lister_rappels",
        "description": "Rappels à venir (id, heure, texte).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "supprimer_rappel",
        "description": "Supprime un rappel à venir par son id (lu avec lister_rappels).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
]


OUTILS_RAPPELS = [
    {
        "name": "creer_rappel",
        "description": "Rappel dans le chat (pas dans le calendrier) : « rappelle-moi dans 10 min de… », « mets un rappel "
                       "à 18 h pour… ». quand : date-heure ISO locale (ex. 2026-11-09T18:00). Un rendez-vous (médecin, "
                       "réunion…) va plutôt dans l'agenda ; si ce n'est pas clair, demande. Confirme en une phrase courte.",
        "inputSchema": {"type": "object", "properties": {"quand": {"type": "string"}, "texte": {"type": "string"}},
                        "required": ["quand", "texte"]},
    },
    {
        "name": "lister_rappels",
        "description": "Rappels à venir (id, heure, texte).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "supprimer_rappel",
        "description": "Supprime un rappel à venir par son id (lu avec lister_rappels).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
]


def outils() -> list[dict]:
    return (OUTILS + OUTILS_ROUTINES + OUTILS_RAPPELS + OUTILS_RAPPELS + OUTILS_MEMOIRE + (OUTILS_DOCKER if os.environ.get("ASSISTANT_DOCKER") == "1" else [])
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
        elif nom == "etat_serveur":
            r = c.get("/interne/unraid")
        elif nom == "demander_redemarrage":
            r = c.post("/interne/docker/redemarrer", json={"nom": args["nom"], "action": args.get("action", "redemarrer"),
                                                           "conversation": conv})
        elif nom == "lister_conteneurs":
            r = c.get("/interne/docker/conteneurs")
        elif nom == "diagnostiquer_conteneur":
            r = c.get("/interne/docker/diagnostic", params={"nom": args["nom"]})
        elif nom == "lire_agenda":
            r = c.get("/interne/agenda", params={"jours": args.get("jours", 7)})
        elif nom == "supprimer_evenement":
            r = c.post("/interne/agenda/supprimer", json={"ref": args["ref"], "conversation": conv})
        elif nom == "ajouter_evenement":
            r = c.post("/interne/agenda", json=args)
        elif nom == "creer_routine":
            r = c.post("/interne/routines", json=args)
        elif nom == "lister_routines":
            r = c.get("/interne/routines")
        elif nom == "proposer_souvenir":
            r = c.post("/interne/memoire", json={**args, "conversation": conv})
        elif nom == "creer_rappel":
            r = c.post("/interne/rappels", json={**args, "conversation": conv})
        elif nom == "lister_rappels":
            r = c.get("/interne/rappels", params={"conversation": conv})
        elif nom == "supprimer_rappel":
            r = c.delete(f"/interne/rappels/{args['id']}")
        elif nom == "creer_rappel":
            r = c.post("/interne/rappels", json={**args, "conversation": conv})
        elif nom == "lister_rappels":
            r = c.get("/interne/rappels", params={"conversation": conv})
        elif nom == "supprimer_rappel":
            r = c.delete(f"/interne/rappels/{args['id']}")
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
