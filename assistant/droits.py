"""Droits fins par personne : un catalogue de permissions (une par outil ou capacité) et des groupes.

Simple à lire : chaque personne est dans UN groupe (Admin, Foyer, Extérieur, ou un groupe créé ensuite).
- le groupe « Admin » a tout, y compris gérer les comptes et les réglages de la maison ; il ne se modifie pas ;
- un autre groupe donne ses `permissions`, et peut « parler comme à un enfant » ;
- quelques exceptions par personne restent possibles (ajouts, retraits), elles ont le dernier mot :
  effectif(personne) = (permissions du groupe ∪ ajouts) − retraits.

Les catégories d'appareils (« maison.piloter.lumieres »…) remplacent les anciens « interdits ».
"""

from __future__ import annotations

import re

# --- catalogue ---------------------------------------------------------------------------------------
# (identifiant, libellé) par rubrique : l'ordre est celui de la page d'administration.
RUBRIQUES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Maison", [
        ("maison.voir", "voir l'état des appareils (lumières, volets, capteurs…)"),
        ("maison.piloter.lumieres", "piloter les lumières"),
        ("maison.piloter.prises", "piloter les prises"),
        ("maison.piloter.volets", "piloter les volets"),
        ("maison.piloter.chauffage", "piloter le chauffage"),
        ("maison.piloter.medias", "piloter la télé et la musique"),
        ("maison.piloter.ventilateurs", "piloter les ventilateurs"),
        ("maison.historique", "voir l'historique des actions de toute la maison"),
    ]),
    ("Automatismes", [
        ("automatismes.regles", "créer des règles « si… alors… »"),
        ("automatismes.routines", "créer des routines"),
        ("automatismes.habitudes", "transformer une habitude en automatisation Home Assistant"),
    ]),
    ("Agenda et rappels", [
        ("agenda.lire", "lire l'agenda familial"),
        ("agenda.ecrire", "ajouter ou supprimer un rendez-vous dans l'agenda familial"),
        ("agenda.personnel", "brancher et utiliser son propre agenda"),
        ("rappels.utiliser", "créer ses rappels"),
    ]),
    ("Mémoire et questions", [
        ("memoire.utiliser", "souvenirs personnels"),
        ("memoire.foyer", "souvenirs partagés avec toute la maison"),
        ("assistant.web", "questions générales, recherche web, rapports (météo, bourse, actus…)"),
    ]),
    ("Serveur", [
        ("serveur.etat", "voir l'état des conteneurs et du serveur Unraid"),
        ("serveur.diagnostic", "diagnostic d'un conteneur (journaux, causes)"),
        ("serveur.alertes", "recevoir les alertes du serveur (conteneurs, disques, mises à jour)"),
        ("serveur.demarrer_arreter", "démarrer ou arrêter un conteneur"),
        ("serveur.redemarrer", "redémarrer un conteneur"),
        ("serveur.mettre_a_jour", "mettre à jour un conteneur"),
    ]),
    ("Telegram", [
        ("telegram.envoyer", "envoyer un message sur Telegram (le sien, relié dans Mon compte)"),
        ("telegram.recevoir", "relier son Telegram et y recevoir ses rappels et alertes"),
    ]),
]
PERMISSIONS: dict[str, str] = {p: lib for _, liste in RUBRIQUES for p, lib in liste}
CATEGORIES = ["lumieres", "prises", "volets", "chauffage", "medias", "ventilateurs"]

# Groupes créés au premier démarrage (modifiables ensuite, sauf Admin ; d'autres peuvent être ajoutés).
ADMIN = "admin"
TOUT_SAUF_SERVEUR = [p for p in PERMISSIONS if not p.startswith(("serveur.", "telegram.", "automatismes.habitudes",
                                                                  "maison.historique"))]
EXTERIEUR = ["maison.voir", "maison.piloter.lumieres", "maison.piloter.medias", "rappels.utiliser",
             "memoire.utiliser", "assistant.web"]
GROUPES_DEFAUT: dict[str, dict] = {
    ADMIN: {"nom": "Admin", "permissions": list(PERMISSIONS), "enfant": False},
    "foyer": {"nom": "Foyer", "permissions": TOUT_SAUF_SERVEUR, "enfant": False},
    "exterieur": {"nom": "Extérieur", "permissions": EXTERIEUR, "enfant": False},
}
GROUPE_PAR_DEFAUT = "foyer"


class DroitInvalide(ValueError):
    pass


def valider(perms) -> list[str]:
    """Liste triée, sans doublon ; une permission inconnue est une erreur (faute de frappe = droit perdu)."""
    perms = list(perms or [])
    inconnues = [p for p in perms if p not in PERMISSIONS]
    if inconnues:
        raise DroitInvalide(f"permission inconnue : {', '.join(inconnues)}")
    return sorted(set(perms))


def ident_groupe(nom: str) -> str:
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", nom.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:32]


def effectif(groupes: dict[str, dict], groupe: str, permissions=(), retraits=()) -> set[str]:
    """Droits d'une personne : ceux de son groupe, plus ses ajouts, moins ses retraits. Admin : tout."""
    if groupe == ADMIN:
        return set(PERMISSIONS)
    base = set((groupes.get(groupe) or {}).get("permissions") or [])
    return ((base | set(permissions or [])) - set(retraits or [])) & set(PERMISSIONS)


def interdits(perms: set[str]) -> list[str]:
    """Catégories d'appareils que la personne ne pilote pas (ce qu'attend maison.domaines_interdits)."""
    return sorted(c for c in CATEGORIES if f"maison.piloter.{c}" not in perms)


def catalogue() -> list[dict]:
    return [{"rubrique": r, "permissions": [{"id": p, "libelle": lib} for p, lib in liste]} for r, liste in RUBRIQUES]
