"""Droits fins par personne : un catalogue de permissions (une par outil ou capacité), des groupes et
sous-groupes, et des réglages propres à chaque compte.

Calcul (fonctions pures, testées à part) :
- un groupe donne ses `permissions` et retire ses `retraits`, après avoir hérité de son `parent` :
  effectif(groupe) = (effectif(parent) ∪ permissions) − retraits ;
- une personne cumule ses groupes, puis ses propres ajouts et retraits (le dernier mot est le sien) :
  effectif(compte) = (∪ effectif(groupes) ∪ permissions) − retraits ;
- un administrateur a tout (et lui seul gère comptes, groupes et réglages de l'assistant).

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
        ("agenda.ecrire", "ajouter ou supprimer un rendez-vous"),
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
        ("telegram.envoyer", "envoyer un message sur le Telegram de la maison"),
        ("telegram.recevoir", "recevoir ses rappels et alertes sur Telegram"),
    ]),
]
PERMISSIONS: dict[str, str] = {p: lib for _, liste in RUBRIQUES for p, lib in liste}
CATEGORIES = ["lumieres", "prises", "volets", "chauffage", "medias", "ventilateurs"]

# Groupes créés au premier démarrage (modifiables ensuite). « Enfants » est un sous-groupe de « Adultes ».
TOUT_SAUF_SERVEUR = [p for p in PERMISSIONS if not p.startswith(("serveur.", "telegram.", "automatismes.habitudes",
                                                                  "maison.historique"))]
GROUPES_DEFAUT: dict[str, dict] = {
    "adultes": {"nom": "Adultes", "parent": "", "permissions": TOUT_SAUF_SERVEUR, "retraits": []},
    "enfants": {"nom": "Enfants", "parent": "adultes", "permissions": [],
                "retraits": ["maison.piloter.chauffage", "agenda.ecrire", "automatismes.regles",
                             "automatismes.routines", "memoire.foyer"]},
}


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


def ancetres(groupes: dict[str, dict], gid: str) -> list[str]:
    """gid, son parent, le parent du parent… (une boucle est coupée)."""
    vus: list[str] = []
    while gid and gid in groupes and gid not in vus:
        vus.append(gid)
        gid = groupes[gid].get("parent") or ""
    return vus


def effectif_groupe(groupes: dict[str, dict], gid: str) -> set[str]:
    perms: set[str] = set()
    for g in reversed(ancetres(groupes, gid)):          # du plus haut au groupe lui-même
        perms = (perms | set(groupes[g].get("permissions") or [])) - set(groupes[g].get("retraits") or [])
    return perms


def effectif(groupes: dict[str, dict], membre_de: list[str], permissions=(), retraits=()) -> set[str]:
    perms: set[str] = set()
    for g in membre_de or []:
        perms |= effectif_groupe(groupes, g)
    return ((perms | set(permissions or [])) - set(retraits or [])) & set(PERMISSIONS)


def cree_une_boucle(groupes: dict[str, dict], gid: str, parent: str) -> bool:
    return bool(parent) and gid in ancetres(groupes, parent)


def interdits(perms: set[str]) -> list[str]:
    """Catégories d'appareils que la personne ne pilote pas (ce qu'attend maison.domaines_interdits)."""
    return sorted(c for c in CATEGORIES if f"maison.piloter.{c}" not in perms)


def catalogue() -> list[dict]:
    return [{"rubrique": r, "permissions": [{"id": p, "libelle": lib} for p, lib in liste]} for r, liste in RUBRIQUES]
