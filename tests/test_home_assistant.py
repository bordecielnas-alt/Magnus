import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from assistant.config import HomeAssistantConfig, charger, charger_secrets
from assistant.home_assistant import HomeAssistant
from assistant.main import creer_app
from tests.test_routeur import routeur

CFG_HA = HomeAssistantConfig(url="https://ha.test", jeton_env="JETON_TEST")


def faux_ha(reponse_type="action_done", phrase="Lumière allumée", code="", statut=200, requetes=None):
    def gerer(req: httpx.Request) -> httpx.Response:
        if requetes is not None:
            requetes.append(req)
        if req.url.path == "/api/config":
            return httpx.Response(statut, json={"version": "2026.11.0"})
        corps = {"response": {"response_type": reponse_type,
                              "speech": {"plain": {"speech": phrase}},
                              "data": {"code": code} if code else {}}}
        return httpx.Response(statut, json=corps)
    return HomeAssistant(CFG_HA, jeton="j", transport=httpx.MockTransport(gerer))


def test_conversation_comprise_envoie_langue_et_agent():
    req = []
    r = asyncio.run(faux_ha(requetes=req).conversation("Allume le salon"))
    assert (r.compris, r.texte, r.type) == (True, "Lumière allumée", "action_done")
    corps = json.loads(req[0].content)
    assert corps == {"text": "Allume le salon", "language": "fr", "agent_id": "conversation.home_assistant"}
    assert req[0].headers["authorization"] == "Bearer j"


def test_conversation_non_comprise():
    r = asyncio.run(faux_ha("error", "Désolé, je ne comprends pas", "no_intent_match").conversation("Blabla"))
    assert (r.compris, r.code) == (False, "no_intent_match")


def test_conversation_injoignable_ne_leve_pas():
    ha = HomeAssistant(CFG_HA, jeton="j", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert asyncio.run(ha.conversation("Allume")).compris is False


def test_sans_jeton_non_configure():
    ha = HomeAssistant(CFG_HA, jeton="")
    assert ha.configure is False
    assert asyncio.run(ha.etat())["configure"] is False


def test_etat_jeton_refuse():
    e = asyncio.run(faux_ha(statut=401).etat())
    assert e["connecte"] is False and "refusé" in e["message"]


def _client(ha):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    cfg = cfg.model_copy(update={"home_assistant": CFG_HA})
    return TestClient(creer_app(cfg, r.registre, ha))


def test_api_simple_execute_par_ha():
    rep = _client(faux_ha()).post("/api/chat", json={"message": "Allume le salon"}).json()
    assert rep["fournisseur"] == "home-assistant" and rep["reponse"] == "Lumière allumée"
    assert rep["route"]["home_assistant"]["compris"] is True


def test_api_simple_non_compris_repart_vers_modele():
    ha = faux_ha("error", "?", "no_intent_match")
    rep = _client(ha).post("/api/chat", json={"message": "Allume le truc bizarre"}).json()
    assert rep["fournisseur"] == "rapide"
    assert rep["route"]["home_assistant"]["code"] == "no_intent_match"


def test_api_complexe_ne_passe_pas_par_ha():
    req = []
    rep = _client(faux_ha(requetes=req)).post("/api/chat", json={"message": "Explique la marée"}).json()
    assert rep["fournisseur"] == "claude" and not req


def test_api_flux_ha():
    lignes = [json.loads(l) for l in _client(faux_ha()).post(
        "/api/chat", json={"message": "Allume le salon", "flux": True}).text.splitlines()]
    assert lignes[1] == {"delta": "Lumière allumée"} and lignes[-1]["fournisseur"] == "home-assistant"


def test_secrets_sans_ecraser(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nHA_JETON=abc\nVIDE=\nDEJA=nouveau\n", encoding="utf-8")
    env = {"DEJA": "ancien"}
    assert charger_secrets(f, env) == f
    assert env == {"DEJA": "ancien", "HA_JETON": "abc"}
