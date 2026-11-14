"""Un profil par personne : droits (ce qu'elle ne pilote pas), appliqués aussi quand sa voix est reconnue,
et rappels personnels."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from assistant.config import SecuriteConfig, charger
from assistant.maison import domaines_interdits, interdits
from assistant.maison.alias import AliasAppris
from assistant.securite import Securite
from tests.test_home_assistant import CFG_HA, faux_ha
from tests.test_maison import FauxHA, dire, maison  # noqa: F401 - fixture
from tests.test_routeur import routeur
from tests.test_voix import FausseVoix

MDP = "mot de passe solide"


# --- Maison : droits de la personne qui parle ------------------------------------------------------
def test_droits_refusent_sans_toucher_a_home_assistant(maison):  # noqa: F811
    m, ha = maison
    jeton = interdits.set(domaines_interdits(["lumieres"]))
    try:
        r = dire(m, "allume la lampe bureau")
        assert "pas le droit" in r.texte and r.detail["interdits"] == ["light.lampe_bureau"]
        r = dire(m, "éteins la prise TV")                  # une prise reste permise
        assert r.texte.startswith("C'est") and ha.appels[-1][0] == "switch"
        assert all(a[0] != "light" for a in ha.appels)
    finally:
        interdits.reset(jeton)
    assert dire(m, "allume la lampe bureau").texte.startswith("C'est")   # hors de ce contexte : permis


def test_annule_et_plus_fort_respectent_les_droits(maison):  # noqa: F811
    m, ha = maison
    dire(m, "allume la lampe bureau", "k")             # fait par un adulte
    n = len(ha.appels)
    jeton = interdits.set(domaines_interdits(["lumieres"]))
    try:
        assert "Impossible" in dire(m, "plus fort", "k").texte
        assert "Refusé" in dire(m, "annule", "k").texte or "Rien" in dire(m, "annule", "k").texte
        assert len(ha.appels) == n
    finally:
        interdits.reset(jeton)


def test_categories():
    assert domaines_interdits(["chauffage", "medias"]) == {"climate", "water_heater", "media_player"}
    assert domaines_interdits(["inconnue"]) == frozenset()


# --- Profils : valeurs par défaut et qui peut les changer ------------------------------------------
def test_ne_pilote_pas_par_categorie(tmp_path):
    sec = Securite(SecuriteConfig(), tmp_path)
    sec.creer_premier(MDP)
    sec.creer_compte("Léa")
    assert sec.profil("lea")["interdits"] == []                              # groupe Foyer
    sec.definir_profil("lea", {"ton": "chaleureux"})            # un autre champ ne fige pas les droits
    assert sec.profil("lea")["interdits"] == []
    assert sec.definir_profil("lea", {"interdits": ["volets", "chauffage"]})["interdits"] == ["chauffage", "volets"]
    assert sec.definir_profil("lea", {"interdits": []})["interdits"] == []   # choix explicite gardé
    assert [c["interdits"] for c in sec.liste() if c["id"] == "lea"] == [[]]


def test_seul_un_administrateur_change_les_droits(tmp_path):
    from tests.test_securite import LAN, app, client
    a, sec = app(tmp_path, totp_obligatoire=False)
    sec.creer_premier(MDP)
    machine = client(a, "127.0.0.1")
    lien = machine.post("/api/comptes", json={"nom": "Léa"}).json()["lien"]
    c = client(a, LAN)
    c.post("/api/invitation", json={"invitation": lien.split("invitation=")[1], "mot_de_passe": "mot de passe de léa"})
    c.post("/api/connexion", json={"identifiant": "lea", "mot_de_passe": "mot de passe de léa"})
    assert c.post("/api/compte/profil", json={"interdits": []}).status_code == 403
    assert c.post("/api/compte/profil", json={"ton": "concis"}).status_code == 200
    r = machine.post("/api/comptes/lea/profil", json={"interdits": ["lumieres"]})
    assert r.json()["profil"]["interdits"] == ["lumieres"]
    assert machine.post("/api/comptes/lea/profil", json={"interdits": ["serrures"]}).status_code == 422


def test_note_profil_previent_le_modele():
    from assistant.main import note_profil
    note = note_profil("Léa", {"type": "enfant", "interdits": ["chauffage"]})
    assert "le chauffage" in note and "pas le droit" in note


# --- Application : session et voix ----------------------------------------------------------------
def _app(tmp_path, monkeypatch, voix_de=None):
    voix_de = voix_de if voix_de is not None else ["lea"]   # qui parle (modifiable pendant le test)
    import assistant.main as main
    import assistant.voix as voix_mod
    from assistant.config import VoixConfig
    from assistant.locuteur import Locuteurs
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    monkeypatch.setattr(VoixConfig, "whisper_present", lambda self: True)
    monkeypatch.setattr(Locuteurs, "disponible", property(lambda self: True))
    monkeypatch.setattr(Locuteurs, "identifier", lambda self, audio, comptes=None:
                        {"compte": voix_de[0], "score": 0.9} if voix_de[0] in (comptes or []) else None)
    monkeypatch.setattr(voix_mod, "decoder_audio", lambda b: np.zeros(16000, dtype=np.float32))
    sec = Securite(SecuriteConfig(), tmp_path)
    sec.creer_premier(MDP)
    sec.creer_compte("Léa")
    sec.definir_profil("lea", {"type": "enfant", "interdits": ["lumieres"]})
    (tmp_path / "voix_comptes.json").write_text(json.dumps({"lea": {"echantillons": [[1.0, 0.0]]},
                                                            "principal": {"echantillons": [[0.0, 1.0]]}}))
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml")).model_copy(update={"home_assistant": CFG_HA})
    ha = faux_ha()
    faux = FauxHA()
    ha.ws, ha.appeler_service, ha.etat_entite = faux.ws, faux.appeler_service, faux.etat_entite
    a = main.creer_app(cfg, r.registre, ha, AliasAppris(tmp_path / "a.json"), voix=FausseVoix(), securite=sec)
    return TestClient(a, client=("127.0.0.1", 1)), faux


def test_voix_d_enfant_reconnue_reduit_les_droits(tmp_path, monkeypatch):
    c, faux = _app(tmp_path, monkeypatch)
    # sans voix reconnue : la machine (administrateur) pilote les lumières
    r = c.post("/api/chat", json={"message": "allume la lampe bureau", "conversation": "t"}).json()
    assert r["reponse"].startswith("C'est") and faux.appels
    faux.appels.clear()
    # Léa parle sur le même appareil : ses limites s'appliquent
    q = c.post("/api/voix/transcrire?conversation=v", content=b"x" * 3000,
               headers={"Content-Type": "audio/webm"}).json()
    assert q["locuteur"]["compte"] == "lea"
    r = c.post("/api/chat", json={"message": "éteins la lampe bureau", "conversation": "v", "source": "voix"}).json()
    assert "pas le droit" in r["reponse"] and r["route"]["locuteur"] == "Léa" and faux.appels == []
    assert not r.get("cartes")
    # les outils de Claude et les cartes de cette conversation gardent la limite
    a = c.post("/interne/maison/agir", json={"entity_ids": ["light.lampe_bureau"], "action": "allumer",
                                              "conversation": "v"}, headers={"X-Jeton-Interne": "j"}).json()
    assert "pas le droit" in a["resultat"] and faux.appels == []
    g = c.post("/api/maison/carte", json={"entity_id": "light.lampe_bureau", "allume": True, "conversation": "v"})
    assert g.status_code == 409 and faux.appels == []
    # les autres appareils restent permis
    r = c.post("/api/chat", json={"message": "éteins la prise TV", "conversation": "v", "source": "voix"}).json()
    assert r["reponse"].startswith("C'est") and faux.appels[-1][0] == "switch"


def test_rappels_a_la_personne_reconnue(tmp_path, monkeypatch):
    qui = ["lea"]
    c, _ = _app(tmp_path, monkeypatch, qui)
    h = {"X-Jeton-Interne": "j"}
    quand = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    # Léa, reconnue à la voix, demande un rappel : il est à elle
    c.post("/api/voix/transcrire?conversation=v", content=b"x" * 3000, headers={"Content-Type": "audio/webm"})
    c.post("/api/chat", json={"message": "quelle heure est-il", "conversation": "v", "source": "voix"})
    a = c.post("/interne/rappels", json={"quand": quand, "texte": "ranger ma chambre", "conversation": "v"},
               headers=h).json()
    # Principal, reconnu sur un autre appareil, a le sien
    qui[0] = "principal"
    c.post("/api/voix/transcrire?conversation=p", content=b"x" * 3000, headers={"Content-Type": "audio/webm"})
    c.post("/api/chat", json={"message": "quelle heure est-il", "conversation": "p", "source": "voix"})
    b = c.post("/interne/rappels", json={"quand": quand, "texte": "payer la cantine", "conversation": "p"},
               headers=h).json()
    lea = c.get("/interne/rappels", params={"conversation": "v"}, headers=h).json()
    assert [x["texte"] for x in lea] == ["ranger ma chambre"]
    # Léa ne supprime pas le rappel d'un autre
    assert c.delete(f"/interne/rappels/{b['id']}", params={"conversation": "v"}, headers=h).status_code == 404
    assert c.delete(f"/interne/rappels/{a['id']}", params={"conversation": "v"}, headers=h).status_code == 200

