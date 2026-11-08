"""Mode proactif : heures calmes, présence, file d'annonces, surveillance."""

from datetime import datetime

from assistant.proactif import Annonces, dans_heures_calmes


def test_heures_calmes():
    assert dans_heures_calmes("22:00-07:30", datetime(2026, 1, 1, 23, 0))
    assert dans_heures_calmes("22:00-07:30", datetime(2026, 1, 1, 6, 59))
    assert not dans_heures_calmes("22:00-07:30", datetime(2026, 1, 1, 12, 0))
    assert dans_heures_calmes("13:00-14:00", datetime(2026, 1, 1, 13, 30))
    assert not dans_heures_calmes("", datetime(2026, 1, 1, 3, 0))


def test_file_d_annonces(tmp_path):
    a = Annonces(tmp_path, "22:00-07:30")
    x = a.ajouter("Le conteneur radarr s'est arrêté.", "important", "docker", "docker:radarr")
    assert a.ajouter("encore", "important", "docker", "docker:radarr") is None      # pas de doublon
    u = a.ajouter("Fumée détectée !", "urgent")
    nuit, jour = datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 1, 12, 0)
    assert [n["parler"] for n in a.depuis(maintenant=nuit)] == [False, True]          # la nuit : urgent seulement
    assert all(n["parler"] for n in a.depuis(maintenant=jour))
    a.presents = False                                                                 # personne à la maison
    assert [n["parler"] for n in a.depuis(maintenant=jour)] == [False, True]
    assert [n["id"] for n in a.depuis(x["id"])] == [u["id"]]
    a.marquer_lue(x["id"])
    assert [n["id"] for n in Annonces(tmp_path).depuis()] == [u["id"]]                # gardé sur disque
    a.resoudre("docker:radarr")
    assert a.ajouter("radarr retombé", "important", "docker", "docker:radarr") is not None


def test_api_annonces(tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_securite import app
    application, _ = app(tmp_path)
    c = TestClient(application, client=("127.0.0.1", 1))
    assert c.get("/api/annonces").json()["annonces"] == []
    assert c.post("/api/annonces/inconnue/lue").status_code == 404


def test_surveillance_regles_et_presence(tmp_path):
    import asyncio
    from pathlib import Path

    from assistant.config import RegleSurveillance, charger
    from assistant.main import creer_app
    from tests.test_maison import FauxHA
    from tests.test_routeur import routeur

    class HA(FauxHA):
        async def ws(self, commandes):
            return {"get_states": [
                {"entity_id": "person.benjamin", "state": "home"},
                {"entity_id": "cover.garage", "state": "open", "last_changed": "2020-01-01T00:00:00+00:00",
                 "attributes": {"friendly_name": "Porte du garage"}},
            ]}
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    cfg.proactif.surveiller = [RegleSurveillance(entity_id="cover.garage", etat="open", minutes=10)]
    cfg.securite.machine_sans_mot_de_passe = True
    from assistant.config import SecuriteConfig
    from assistant.securite import Securite
    a = creer_app(cfg, r.registre, HA(), securite=Securite(SecuriteConfig(), tmp_path))
    veille = {}
    asyncio.run(a.state.surveiller_une_fois(veille))
    asyncio.run(a.state.surveiller_une_fois(veille))         # pas de doublon
    textes = [x["texte"] for x in a.state.annonces.liste]
    assert len(textes) == 1 and textes[0].startswith("Porte du garage est ouvert depuis")
    assert a.state.annonces.presents is True


def test_formats_d_heure():
    import pytest

    from assistant.config import ProactifConfig
    p = ProactifConfig(heures_calmes="22:00 - 7:30", briefing_heure="7:30")
    assert p.heures_calmes == "22:00-07:30" and p.briefing_heure == "07:30"
    with pytest.raises(ValueError):
        ProactifConfig(heures_calmes="la nuit")
