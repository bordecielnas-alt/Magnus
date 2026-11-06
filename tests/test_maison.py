"""Pilotage de la maison avec un faux Home Assistant (inventaire, résolution, ambiguïté, alias)."""

import asyncio

import pytest

from assistant.config import HomeAssistantConfig, MaisonConfig
from assistant.maison import Maison
from assistant.maison.alias import AliasAppris
from assistant.maison.commande import par_regles
from assistant.maison.inventaire import CMD_APPAREILS, CMD_ENTITES, CMD_ETATS, CMD_PIECES, construire

PIECES = [{"area_id": "salon", "name": "Salon", "aliases": ["séjour"]},
          {"area_id": "cuisine", "name": "Cuisine", "aliases": []},
          {"area_id": "bureau", "name": "Bureau", "aliases": []},
          {"area_id": "chambre", "name": "Chambre", "aliases": []}]
APPAREILS = [{"id": "d1", "area_id": "salon", "name": "Hue plafond", "name_by_user": None}]
ENTITES = [
    {"entity_id": "light.plafonnier", "device_id": "d1", "area_id": None, "name": "Plafonnier", "aliases": []},
    {"entity_id": "light.lampe_pied", "area_id": "salon", "name": "Lampe sur pied", "aliases": []},
    {"entity_id": "light.cuisine", "area_id": "cuisine", "name": "Lumière cuisine", "aliases": []},
    {"entity_id": "light.lampe_bureau", "area_id": "bureau", "name": "Lampe bureau", "aliases": []},
    {"entity_id": "switch.prise_tv", "area_id": "salon", "name": "Prise TV", "aliases": []},
    {"entity_id": "cover.volet_chambre", "area_id": "chambre", "name": "Volet", "aliases": []},
    {"entity_id": "climate.salon", "area_id": "salon", "name": "Thermostat", "aliases": ["chauffage"]},
    {"entity_id": "lock.porte", "area_id": "salon", "name": "Serrure entrée", "aliases": []},
    {"entity_id": "light.cachee", "area_id": "salon", "name": "Cachée", "hidden_by": "user", "aliases": []},
    {"entity_id": "switch.config", "area_id": "salon", "name": "Mode", "entity_category": "config", "aliases": []},
]
ETATS = [
    {"entity_id": "light.plafonnier", "state": "on", "attributes": {"friendly_name": "Plafonnier", "brightness": 128}},
    {"entity_id": "light.lampe_pied", "state": "off", "attributes": {"friendly_name": "Lampe sur pied"}},
    {"entity_id": "light.cuisine", "state": "off", "attributes": {"friendly_name": "Lumière cuisine"}},
    {"entity_id": "light.lampe_bureau", "state": "off", "attributes": {"friendly_name": "Lampe bureau"}},
    {"entity_id": "switch.prise_tv", "state": "on", "attributes": {"friendly_name": "Prise TV"}},
    {"entity_id": "cover.volet_chambre", "state": "open", "attributes": {"friendly_name": "Volet", "current_position": 100}},
    {"entity_id": "climate.salon", "state": "heat",
     "attributes": {"friendly_name": "Thermostat", "current_temperature": 19.5, "temperature": 20}},
    {"entity_id": "lock.porte", "state": "locked", "attributes": {"friendly_name": "Serrure entrée"}},
]
DOMAINES = MaisonConfig().domaines_autorises


class FauxHA:
    """Imite l'API utilisée par Maison/Inventaire : ws, appeler_service, etat_entite."""

    def __init__(self, registres=True):
        self.cfg = HomeAssistantConfig(url="https://ha.test")
        self.configure = True
        self.registres = registres
        self.appels = []

    async def ws(self, commandes):
        base = {CMD_ETATS: ETATS, CMD_PIECES: PIECES, CMD_APPAREILS: APPAREILS, CMD_ENTITES: ENTITES}
        if not self.registres:
            base = {CMD_ETATS: ETATS, CMD_PIECES: None, CMD_APPAREILS: None, CMD_ENTITES: None}
        return {c: base[c] for c in commandes}

    async def appeler_service(self, domaine, service, donnees):
        self.appels.append((domaine, service, donnees))
        return True

    async def etat_entite(self, eid):
        return next((s for s in ETATS if s["entity_id"] == eid), None)


@pytest.fixture
def maison(tmp_path):
    ha = FauxHA()
    return Maison(MaisonConfig(), ha, AliasAppris(tmp_path / "alias.json")), ha


def dire(m, texte, conv="c1"):
    return asyncio.run(m.traiter(texte, conv))


# --- inventaire --------------------------------------------------------------

def test_inventaire_filtre_domaines_caches_et_config():
    res = asyncio.run(FauxHA().ws([CMD_ETATS, CMD_PIECES, CMD_APPAREILS, CMD_ENTITES]))
    entites, registres = construire(res, set(DOMAINES))
    ids = {e.entity_id for e in entites}
    assert registres and "lock.porte" not in ids and "light.cachee" not in ids and "switch.config" not in ids
    plaf = next(e for e in entites if e.entity_id == "light.plafonnier")
    assert plaf.piece == "Salon"  # pièce héritée de l'appareil


def test_inventaire_sans_droits_registres():
    res = asyncio.run(FauxHA(registres=False).ws([CMD_ETATS, CMD_PIECES, CMD_APPAREILS, CMD_ENTITES]))
    entites, registres = construire(res, set(DOMAINES))
    assert not registres and len(entites) == 7 and all(not e.piece for e in entites)


# --- compréhension -------------------------------------------------------------

@pytest.mark.parametrize("texte,action,cible,valeur", [
    ("Allume la lumière du salon", "allumer", "la lumière du salon", None),
    ("peux-tu éteindre le plafonnier ?", "eteindre", "le plafonnier ?", None),
    ("Baisse le chauffage à 19 degrés", "regler", "le chauffage", 19),
    ("Mets la lampe du bureau à 30 %", "regler", "la lampe du bureau", 30),
    ("Ferme le volet de la chambre", "fermer", "le volet de la chambre", None),
    ("La lumière du salon est-elle allumée ?", "etat", "La lumière du salon", None),
    ("est-ce que le volet de la chambre est ouvert", "etat", "le volet de la chambre", None),
])
def test_regles_commande(texte, action, cible, valeur):
    c = par_regles(texte)
    assert (c.action, c.cible.rstrip(" ?"), c.valeur) == (action, cible.rstrip(" ?"), valeur)


@pytest.mark.parametrize("texte", ["Merci", "Pourquoi la mer est salée ?", "Mets la musique", "Bonjour"])
def test_pas_une_commande(texte):
    assert par_regles(texte) is None


# --- résolution et actions ---------------------------------------------------

def test_nom_exact(maison):
    m, ha = maison
    r = dire(m, "Éteins le plafonnier")
    assert r.traite and ha.appels == [("light", "turn_off", {"entity_id": "light.plafonnier"})]


def test_piece_et_type_sans_accents(maison):
    m, ha = maison
    r = dire(m, "allume la lumiere de la cuisine")
    assert r.traite and ha.appels[0][2]["entity_id"] == "light.cuisine"


def test_nom_qui_contient_la_piece(maison):
    m, ha = maison
    dire(m, "Allume la lampe du bureau")
    assert ha.appels[0][2]["entity_id"] == "light.lampe_bureau"


def test_alias_de_piece(maison):
    m, ha = maison
    r = dire(m, "Quelle température dans le séjour ?") or None
    # pas une phrase reconnue par les règles d'état, mais « règle le chauffage du séjour à 21 » l'est
    dire(m, "Règle le chauffage du séjour à 21")
    assert ("climate", "set_temperature", {"entity_id": "climate.salon", "temperature": 21.0}) in ha.appels


def test_ambigu_puis_choix_et_alias_appris(maison):
    m, ha = maison
    q = dire(m, "Allume la lumière du salon")
    assert q.traite and q.texte.startswith("Lequel ?") and not ha.appels
    assert set(q.detail["candidats"]) == {"light.plafonnier", "light.lampe_pied"}
    assert m.en_attente("c1")
    r = dire(m, "le plafonnier")
    assert ha.appels == [("light", "turn_on", {"entity_id": "light.plafonnier"})]
    assert r.detail["alias_appris"] == "la lumiere du salon"
    # la fois suivante, plus de question
    dire(m, "Éteins la lumière du salon")
    assert ha.appels[-1] == ("light", "turn_off", {"entity_id": "light.plafonnier"})


def test_choix_par_numero(maison):
    m, ha = maison
    q = dire(m, "Allume la lumière du salon")
    dire(m, "2")
    assert ha.appels[0][2]["entity_id"] == q.detail["candidats"][1]


def test_choix_dans_une_autre_conversation_ignore(maison):
    m, ha = maison
    dire(m, "Allume la lumière du salon", conv="tablette")
    assert not m.en_attente("telephone")


def test_toutes_les_lumieres(maison):
    m, ha = maison
    dire(m, "Éteins toutes les lumières du salon")
    assert {a[2]["entity_id"] for a in ha.appels} == {"light.plafonnier", "light.lampe_pied"}


def test_etat(maison):
    m, ha = maison
    r = dire(m, "Le plafonnier est-il allumé ?")
    assert r.traite and "allumé à 50 %" in r.texte and not ha.appels


def test_etat_chauffage(maison):
    m, _ = maison
    assert "19.5 °C" in dire(m, "Est-ce que le thermostat est allumé ?").texte


def test_serrure_jamais_pilotee(maison):
    m, ha = maison
    r = dire(m, "Ouvre la serrure de l'entrée")
    assert not any(a[2].get("entity_id") == "lock.porte" for a in ha.appels)
    assert not r.traite or "serrure" not in r.texte.lower()


def test_appareil_inconnu(maison):
    m, ha = maison
    r = dire(m, "Allume la licorne")
    assert not r.traite and not ha.appels and "commande" in r.detail


def test_alias_oublie(maison, tmp_path):
    m, ha = maison
    dire(m, "Allume la lumière du salon")
    dire(m, "1")
    assert m.alias.oublier("la lumière du salon")
    assert AliasAppris(tmp_path / "alias.json").table() == {}


def test_config_refuse_domaine_sensible():
    with pytest.raises(ValueError, match="sensibles"):
        MaisonConfig(domaines_autorises=["light", "lock"])


@pytest.mark.parametrize("texte,action", [
    ("etteins projo", "eteindre"), ("alume la cuisine", "allumer"), ("ÉTTEINS le salon", "eteindre"),
    ("le pupitre est il allumé ?", "etat"), ("projo est-il allumée ?", "etat"),
])
def test_fautes_de_frappe_et_tournures(texte, action):
    assert par_regles(texte).action == action


@pytest.mark.parametrize("texte", ["Activités pour le week-end ?", "Fermeture des magasins dimanche ?"])
def test_pas_de_fausse_correction(texte):
    assert par_regles(texte) is None


def test_licorne_reponse_honnete_sans_modele(maison):
    m, ha = maison
    r = dire(m, "Allume la licorne")
    assert not r.traite and "Rien n'a été fait" in r.texte and "licorne" in r.texte and not ha.appels


def test_introuvable_suggere_des_noms_proches(maison):
    m, _ = maison
    assert "Lampe bureau" in dire(m, "Allume la lampe du grenier").texte


def test_entite_ecartee_expliquee(maison):
    m, _ = maison
    r = dire(m, "Ouvre la serrure de l'entrée")
    assert "lock.porte" in r.texte and "non autorisé" in r.texte


def test_entite_yaml_hors_registre_incluse():
    etats = ETATS + [{"entity_id": "light.l3", "state": "off", "attributes": {"friendly_name": "L3"}}]
    res = {CMD_ETATS: etats, CMD_PIECES: PIECES, CMD_APPAREILS: APPAREILS, CMD_ENTITES: ENTITES}
    entites, _ = construire(res, set(DOMAINES))
    assert "light.l3" in {e.entity_id for e in entites}


@pytest.mark.parametrize("texte", ["a combien est le projo en luminosité ?", "Quelle est la luminosité du projo ?"])
def test_question_de_niveau(texte):
    c = par_regles(texte)
    assert c.action == "etat" and c.cible.strip(" ?") == ("le projo" if "combien" in texte else "projo")


# --- itération 5 ter : appareils, couleurs, suites de conversation ----------------

def test_nom_d_appareil_reconnu(maison):
    m, ha = maison
    dire(m, "Allume le hue plafond")  # nom de l'appareil (device), pas de l'entité
    assert ha.appels[0][2]["entity_id"] == "light.plafonnier"


@pytest.mark.parametrize("texte,attendu", [
    ("Mets le plafonnier en rouge", {"color_name": "red"}),
    ("allume le plafonnier en blanc chaud", {"color_temp_kelvin": 2700}),
    ("passe le plafonnier en bleu à 40 %", {"color_name": "blue", "brightness_pct": 40.0}),
])
def test_couleurs(maison, texte, attendu):
    m, ha = maison
    r = dire(m, texte)
    assert r.traite and ha.appels[0][1] == "turn_on"
    assert {k: ha.appels[0][2][k] for k in attendu} == attendu


def test_eteins_le_apres_une_question(maison):
    m, ha = maison
    dire(m, "Le plafonnier est-il allumé ?")
    assert m.reconnait("éteins-le", "c1") and not m.reconnait("éteins-le", "autre")
    dire(m, "éteins-le")
    assert ha.appels == [("light", "turn_off", {"entity_id": "light.plafonnier"})]


def test_la_cuisine_aussi(maison):
    m, ha = maison
    dire(m, "Allume le plafonnier en rouge")
    dire(m, "la cuisine aussi")
    assert ha.appels[-1] == ("light", "turn_on", {"entity_id": "light.cuisine", "color_name": "red"})


def test_moi_aussi_laisse_le_modele(maison):
    m, ha = maison
    dire(m, "Allume le plafonnier")
    r = dire(m, "moi aussi")
    assert not r.traite and not r.texte and len(ha.appels) == 1


def test_plus_fort(maison):
    m, ha = maison
    dire(m, "Allume le plafonnier")
    dire(m, "un peu plus")
    assert ha.appels[-1] == ("light", "turn_on", {"entity_id": "light.plafonnier", "brightness_step_pct": 20})


def test_remets_comme_avant(maison):
    m, ha = maison
    dire(m, "Mets le plafonnier en rouge")           # il était allumé à 50 % (brightness 128)
    r = dire(m, "remets le comme il était")
    assert "remis comme avant" in r.texte
    assert ha.appels[-1] == ("light", "turn_on", {"entity_id": "light.plafonnier", "brightness": 128})
    dire(m, "Éteins la lampe sur pied")             # elle était éteinte
    dire(m, "annule")
    assert ha.appels[-1] == ("light", "turn_off", {"entity_id": "light.lampe_pied"})


def test_suites_sans_contexte_ignorees(maison):
    m, _ = maison
    assert not m.reconnait("plus fort", "nouvelle") and not m.reconnait("éteins-le", "nouvelle")


def test_reviens_en_arriere(maison):
    m, ha = maison
    dire(m, "Éteins le plafonnier")
    assert m.reconnait("REVIENS EN ARRIERE", "c1")
    dire(m, "REVIENS EN ARRIERE")
    assert ha.appels[-1][1] == "turn_on"


def test_annule_sans_contexte(maison):
    m, ha = maison
    r = dire(m, "annule", conv="neuve")
    assert r.traite and "rien" in r.texte.lower() and not ha.appels


def test_plus_fort_apres_annule_garde_l_appareil(maison):
    m, ha = maison
    dire(m, "Allume le plafonnier")
    dire(m, "annule")
    dire(m, "plus fort")
    assert ha.appels[-1][2]["entity_id"] == "light.plafonnier"


def test_apprend_d_assist(maison):
    m, _ = maison
    cle = asyncio.run(m.apprendre_d_assist("allume le pupitre", ["light.lampe_bureau"], "c1"))
    assert cle == "le pupitre" and m.alias.table()["le pupitre"] == ["light.lampe_bureau"]
    assert m.reconnait("éteins-le", "c1")


def test_extraction_refuse_cible_inventee():
    from assistant.maison.commande import par_modele

    class Modele:
        async def repondre(self, msgs, options=None):
            return '{"action": "regler", "cible": "Lampe Cristal", "valeur": null}'
    assert asyncio.run(par_modele("plus fort", Modele())) is None


@pytest.mark.parametrize("texte,action", [
    ("plusfort", "relatif"), ("moinsfort", "relatif"), ("reviens", "restaurer"), ("remets", "restaurer"),
    ("remets la lampe bureau", "restaurer"), ("repasse la lampe bureau en orange", "allumer"),
    ("remets le plafonnier en bleu", "allumer"),
])
def test_suites_tapees_vite(texte, action):
    assert par_regles(texte, avec_contexte=True).action == action


@pytest.mark.parametrize("texte", [
    "fais l'inventaire des appareils", "liste des lumières de la cave", "quels appareils as-tu ?",
    "donne-moi la liste des lampes du salon",
])
def test_inventaire_reconnu(texte):
    assert par_regles(texte).action == "inventaire"


def test_inventaire_filtre(maison):
    m, _ = maison
    tout = dire(m, "fais l'inventaire des appareils").texte
    assert "Salon" in tout and "Cuisine" in tout and "Serrure" not in tout
    salon = dire(m, "liste des lumières du salon").texte
    assert "Plafonnier" in salon and "Lumière cuisine" not in salon and "Prise TV" not in salon


def test_nombre_dans_un_nom_n_est_pas_une_valeur():
    c = par_regles("etteins globe et L3")
    assert c.valeur is None and c.cible == "globe et L3"
    assert par_regles("mets le salon à 30").valeur == 30


def test_plusieurs_cibles(maison):
    m, ha = maison
    r = dire(m, "éteins le plafonnier et la lumière de la cuisine")
    assert {a[2]["entity_id"] for a in ha.appels} == {"light.plafonnier", "light.cuisine"}
    assert "Plafonnier" in r.texte and "Lumière cuisine" in r.texte and "%" not in r.texte


def test_plusieurs_cibles_dont_une_introuvable(maison):
    m, ha = maison
    r = dire(m, "allume le plafonnier, la licorne et le volet")
    assert {a[2]["entity_id"] for a in ha.appels} == {"light.plafonnier", "cover.volet_chambre"}
    assert "licorne" in r.texte


def test_type_partage_entre_cibles(maison):
    m, ha = maison
    dire(m, "allume la lumière de la cuisine et du salon")
    ids = {a[2]["entity_id"] for a in ha.appels}
    assert "light.cuisine" in ids and "switch.prise_tv" not in ids


def test_bonjour_ne_consulte_pas_l_extracteur(tmp_path):
    class Espion:
        appels = 0
        async def repondre(self, msgs, options=None):
            Espion.appels += 1
            return "{}"
    m = Maison(MaisonConfig(), FauxHA(), AliasAppris(tmp_path / "a.json"), Espion())
    dire(m, "bonjour")
    assert Espion.appels == 0
    dire(m, "fais briller le plafonnier")  # tournure inconnue mais appareil connu : le modèle est consulté
    assert Espion.appels == 1
