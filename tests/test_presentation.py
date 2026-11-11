"""Carte ou texte : les règles de choix (presentation.py) et leur effet dans le chat."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import assistant.main as main_mod
from assistant.config import charger
from assistant.fournisseurs import Fournisseur, Registre
from assistant.main import creer_app
from assistant.presentation import (choisir, fermee, lire_marqueur, marqueur_possible, masquer_texte, phrase)
from assistant.rapports import Demande, Rapports
from tests.test_rapports import _transport
from tests.test_routeur import Classeur


# --- règles ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message, attendu", [
    ("montre-moi la météo à Lyon", ("meteo", "Lyon")),
    ("Affiche le CAC 40", ("bourse", "CAC 40")),
    ("montre moi mon agenda", ("agenda", "")),
    ("fais-moi voir les actus sur l'Ukraine", ("actus", "Ukraine")),
    ("la météo en carte stp", ("meteo", "")),
    ("tu peux me montrer l'état de docker ?", ("docker", "")),
])
def test_regle_1_montre_moi(message, attendu):
    c = choisir(message)
    assert c.regle == 1 and c.voir and (c.demande.type, c.demande.sujet) == attendu


def test_regle_1_sans_carte_toute_faite():
    c = choisir("montre-moi le salon")
    assert c.voir and c.demande is None and c.reste == "salon"
    c = choisir("montre-moi la température du salon")      # un capteur, pas la météo
    assert c.voir and c.demande is None
    assert choisir("montre-moi", Demande("meteo", "Lyon")).demande == Demande("meteo", "Lyon")


@pytest.mark.parametrize("message, attendu, repliee", [
    ("quel temps demain ?", ("meteo", ""), False),
    ("il pleut demain ?", ("meteo", ""), True),
    ("il va pleuvoir à Paris samedi", ("meteo", "Paris"), True),
    ("il fait quel temps à Marseille ?", ("meteo", "Marseille"), False),
    ("où en est le CAC ?", ("bourse", "CAC 40"), False),
    ("j'ai quoi cette semaine ?", ("agenda", ""), False),
    ("docker ça va ?", ("docker", ""), True),
])
def test_regle_2_sujet_a_carte_rapide(message, attendu, repliee):
    c = choisir(message)
    assert c.regle == 2 and (c.demande.type, c.demande.sujet) == attendu and c.repliee == repliee
    if c.demande.type == "bourse":
        assert c.demande.rapide           # sans l'analyse de Claude : pas d'attente


@pytest.mark.parametrize("message", ["quand il pleut ferme les volets", "rappelle-moi demain s'il pleut",
                                     "ajoute un rdv demain", "c'est quand mon rdv chez le dentiste ?", "allume le salon",
                                     "il fait 25 degrés dans le salon", "salut", "pourquoi plex ne marche pas"])
def test_pas_de_carte(message):
    assert choisir(message).demande is None


@pytest.mark.parametrize("message", ["explique-moi la météo", "dis-moi la météo", "pourquoi le CAC baisse ?",
                                     "raconte-moi ta journée"])
def test_regle_3_explication_texte_seul(message):
    c = choisir(message, Demande("meteo", "Lyon"))
    assert c.texte_seul and c.demande is None


@pytest.mark.parametrize("message, attendu", [
    ("et à Paris ?", ("meteo", "Paris")), ("et demain ?", ("meteo", "Lyon")), ("et Marseille", ("meteo", "Marseille")),
    ("et le week-end ?", ("meteo", "Lyon")), ("et à Nice samedi ?", ("meteo", "Nice")),
])
def test_regle_4_suite_meteo(message, attendu):
    c = choisir(message, Demande("meteo", "Lyon"))
    assert c.regle == 4 and (c.demande.type, c.demande.sujet) == attendu


def test_regle_4_autres_suites():
    assert choisir("et le DAX ?", Demande("bourse", "CAC 40", True)).demande == Demande("bourse", "DAX", True)
    assert choisir("et la BCE ?", Demande("libre", "Fed")).demande == Demande("libre", "BCE")
    assert choisir("et demain ?", Demande("agenda")).demande == Demande("agenda")
    for m in ("et toi ?", "et allume le salon", "et pourquoi ?", "et merci"):
        assert choisir(m, Demande("meteo", "Lyon")).demande is None
    assert choisir("et à Paris ?").demande is None       # pas de carte avant : rien à suivre
    # « quand il pleut ferme les volets » reste une règle de maison, même juste après une carte météo
    assert choisir("et si il pleut ferme les volets", Demande("meteo", "Lyon")).demande is None


def test_exclu_garde_le_montre_moi():
    assert choisir("quel temps demain ?", exclu=True).demande is None
    assert choisir("montre-moi la météo", exclu=True).demande == Demande("meteo", "")


def test_fermee():
    assert fermee("il pleut demain ?") and fermee("est-ce qu'il va neiger") and fermee("docker ça va ?")
    assert not fermee("quel temps demain ?") and not fermee("et à Paris ?") and not fermee("où en est le CAC ?")


# --- la phrase dite ----------------------------------------------------------------------------------------

METEO_CARTE = {"modele": "meteo", "demande": {"type": "meteo", "sujet": "Lyon"}, "meteo": {"jours": [
    {"jour": "Aujourd'hui", "ciel": "pluie faible", "min": 8, "max": 14, "pluie": 70},
    {"jour": "Demain", "ciel": "peu nuageux", "min": 5, "max": 13, "pluie": 10},
    {"jour": "ven. 13", "ciel": "ciel dégagé", "min": 4, "max": 13, "pluie": 0},
    {"jour": "sam. 14", "ciel": "averses", "min": 6, "max": 11, "pluie": 80}]}}
MERCREDI = date(2026, 11, 11)


def test_phrase_meteo_du_jour_demande():
    assert phrase("et demain ?", METEO_CARTE, "résumé", aujourdhui=MERCREDI) == \
        "Demain à Lyon : peu nuageux, 5 à 13 °C, 10 % de risque de pluie."
    assert phrase("il pleut samedi ?", METEO_CARTE, "résumé", aujourdhui=MERCREDI).startswith("Samedi à Lyon : averses")
    assert phrase("vendredi ?", METEO_CARTE, "r", aujourdhui=MERCREDI).endswith("pas de pluie prévue.")
    assert phrase("il pleut ?", METEO_CARTE, "r", repliee=True, aujourdhui=MERCREDI).startswith("Aujourd'hui à Lyon")
    assert phrase("quel temps ?", METEO_CARTE, "résumé", aujourdhui=MERCREDI) == "résumé"
    assert phrase("et jeudi ?", METEO_CARTE, "r", aujourdhui=MERCREDI).startswith("Demain")   # jeudi = demain


def test_phrase_agenda_du_jour_demande():
    carte = {"modele": "agenda", "agenda": [{"jour": "Demain", "evenements": [{"heure": "14h00", "titre": "Dentiste"}]}]}
    assert phrase("j'ai quoi demain ?", carte, "r", aujourdhui=MERCREDI) == "Demain : 14h00 Dentiste."
    assert phrase("j'ai quoi aujourd'hui ?", carte, "r", aujourdhui=MERCREDI) == "Aujourd'hui, rien à l'agenda."
    assert phrase("mon agenda", carte, "r", aujourdhui=MERCREDI) == "r"


# --- règle 5 : le marqueur ---------------------------------------------------------------------------------

def test_marqueur():
    assert lire_marqueur("[carte:meteo:Lyon] Voici la météo.") == (Demande("meteo", "Lyon"), "Voici la météo.")
    assert lire_marqueur(" [carte: météo ]Ok")[0] == Demande("meteo", "")
    assert lire_marqueur("[carte:bourse:DAX] x")[0] == Demande("bourse", "DAX", True)
    assert lire_marqueur("[carte:libre:la Fed] x")[0] is None       # carte lente : pas par le modèle
    assert lire_marqueur("Salut [carte:docker]")[0] is None          # seulement en tête
    assert [marqueur_possible(x) for x in ["", " [", "[ca", "[carte:me", "Bon", "[lien]"]] == \
        [True, True, True, True, False, False]


def test_masquer_texte():
    assert masquer_texte("C'est fait.", [{"type": "lumiere"}])
    assert masquer_texte("Long " * 100, [{"type": "rapport"}])
    assert not masquer_texte("Je crée cette règle, d'accord ? Dis oui ou non.", [{"type": "lumiere"}])
    assert not masquer_texte("Phrase " * 60, [{"type": "lumiere"}])
    assert not masquer_texte("C'est fait.", [])


# --- dans le chat ---------------------------------------------------------------------------------------

class Morceaux(Fournisseur):
    """Faux Claude qui répond en petits morceaux, comme un vrai flux."""

    def __init__(self, texte, taille=4):
        super().__init__("claude", "claude")
        self.texte, self.taille, self.appels = texte, taille, []

    async def repondre(self, messages, options=None):
        self.appels.append(messages)
        return self.texte

    async def flux(self, messages):
        self.appels.append(messages)
        for i in range(0, len(self.texte), self.taille):
            yield self.texte[i:i + self.taille]

    async def disponible(self):
        return True


@pytest.fixture
def app_meteo(monkeypatch):
    vus = []
    vrai = Rapports
    monkeypatch.setattr(main_mod, "Rapports", lambda *a, **k: vrai(*a, transport=_transport(vus), **k))

    def faire(claude):
        cfg = charger(env={"ASSISTANT_PROACTIF_VILLE": "Lyon"}, fichier=Path("inexistant.yaml"))
        cfg.proactif.ville = "Lyon"
        reg = Registre({"r": Classeur("rapide", "Salut"), "c": claude},
                       {"rapide": "r", "raisonnement": "c", "secours": "r"})
        return TestClient(creer_app(cfg, reg))
    return faire, vus


def _lignes(r):
    return [json.loads(x) for x in r.text.splitlines()]


def test_chat_question_meteo_carte_sans_texte(app_meteo):
    faire, vus = app_meteo
    claude = Classeur("claude", "jamais appelé")
    c = faire(claude)
    lignes = _lignes(c.post("/api/chat", json={"message": "il pleut demain ?", "conversation": "m", "flux": True}))
    assert [next(iter(x)) for x in lignes] == ["route", "cartes", "delta", "fin"]
    assert lignes[1]["masquer_texte"] and lignes[1]["cartes"][0]["modele"] == "meteo"
    assert lignes[1]["cartes"][0]["repliee"]                     # question fermée : carte repliée
    assert lignes[2]["delta"].startswith("Demain à Lyon : partiellement nuageux")
    assert not claude.appels                                      # aucune attente de modèle
    # règle 4 : la suite refait une carte météo
    r = c.post("/api/chat", json={"message": "et à Lyon ?", "conversation": "m"}).json()
    assert r["cartes"][0]["modele"] == "meteo" and r["masquer_texte"] and "règle 4" in r["route"]["raison"]


def test_chat_explication_reste_du_texte(app_meteo):
    faire, _ = app_meteo
    claude = Classeur("claude", "Parce que l'air se refroidit.")
    c = faire(claude)
    r = c.post("/api/chat", json={"message": "explique-moi pourquoi il pleut", "conversation": "e"}).json()
    assert "cartes" not in r and r["reponse"] == "Parce que l'air se refroidit."


def test_chat_claude_demande_une_carte_en_flux(app_meteo):
    faire, _ = app_meteo
    claude = Morceaux("[carte:meteo:Lyon] Voici la météo pour ta sortie.")
    c = faire(claude)
    lignes = _lignes(c.post("/api/chat", json={"message": "je sors à vélo cet après-midi, ça passe ?",
                                               "conversation": "v", "flux": True, "role": "raisonnement"}))
    cles = [next(iter(x)) for x in lignes]
    assert cles[:2] == ["route", "cartes"] and cles[-1] == "fin"
    assert lignes[1]["masquer_texte"] and lignes[1]["cartes"][0]["modele"] == "meteo"
    texte = "".join(x.get("delta", "") for x in lignes)
    assert "[carte" not in texte and texte.startswith("Voici la météo pour ta sortie.") and "12 °C" in texte
    assert "[carte:meteo" in claude.appels[0][0].content          # la consigne explique le marqueur


def test_chat_claude_sans_marqueur_texte_inchange(app_meteo):
    faire, _ = app_meteo
    c = faire(Morceaux("[Note] Bonjour, je vais bien."))
    lignes = _lignes(c.post("/api/chat", json={"message": "ça va ?", "conversation": "s", "flux": True,
                                               "role": "raisonnement"}))
    assert "".join(x.get("delta", "") for x in lignes) == "[Note] Bonjour, je vais bien."
    assert not any("cartes" in x for x in lignes)


def test_chat_claude_marqueur_sans_flux(app_meteo):
    faire, _ = app_meteo
    c = faire(Classeur("claude", "[carte:meteo] Voici."))
    r = c.post("/api/chat", json={"message": "je sors ?", "conversation": "n", "role": "raisonnement"}).json()
    assert r["reponse"].startswith("Voici. À Lyon, 12 °C") and r["cartes"][0]["modele"] == "meteo" and r["masquer_texte"]


# --- correctifs vus dans une vraie conversation (v0.25.0) ---------------------------------------------

def test_smiley_retire_mais_pas_dans_un_marqueur_ni_une_heure():
    from assistant.voix import sans_emoji
    assert sans_emoji("[carte:meteo:Douai] Voilà") == "[carte:meteo:Douai] Voilà"
    assert sans_emoji("à 10:30") == "à 10:30"
    assert sans_emoji("super :D") == "super "


def test_marqueur_abime_retire_et_lu_si_possible():
    from assistant.presentation import lire_marqueur
    d, reste = lire_marqueur("[carte:meteoouai] Voilà la météo à Douai.")
    assert d.type == "meteo" and d.sujet == "Douai" and reste == "Voilà la météo à Douai."
    d, reste = lire_marqueur("[carte:truc] Bonjour")
    assert d is None and reste == "Bonjour"


def test_periode_agenda():
    from assistant.rapports import periode_agenda
    assert periode_agenda("j'ai quoi cette semaine ?") == ""
    assert periode_agenda("mon agenda pour les deux prochaines semaines") == "14"
    assert periode_agenda("c'est quoi mon agenda pour tout le mois prochain ?") == "mois-prochain"
    assert periode_agenda("mes rendez-vous la semaine prochaine") == "semaine-prochaine"
    assert choisir("c'est quoi mon agenda pour les deux prochaines semaines ?").demande.sujet == "14"


def test_heure_et_date_sans_modele():
    from assistant.main import reponse_horloge
    assert reponse_horloge("quelle heure est-il ?").startswith("Il est ")
    assert reponse_horloge("Magnus, il est quelle heure ?").startswith("Il est ")
    assert reponse_horloge("on est quel jour ?").startswith("Nous sommes ")
    assert reponse_horloge("quelle heure est-il à Tokyo ?") is None
