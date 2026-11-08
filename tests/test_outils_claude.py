"""Outils maison pour Claude : serveur MCP, API interne protégée, arguments du binaire."""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from assistant import mcp_maison
from assistant.config import charger
from assistant.fournisseurs.claude_code import FournisseurClaudeCode
from assistant.main import creer_app
from assistant.maison.alias import AliasAppris
from tests.test_home_assistant import CFG_HA, faux_ha
from tests.test_maison import FauxHA
from tests.test_routeur import routeur


# --- serveur MCP -------------------------------------------------------------------

def test_mcp_initialize_et_liste():
    r = mcp_maison.traiter({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18" and "tools" in r["result"]["capabilities"]
    noms = {o["name"] for o in mcp_maison.traiter({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert noms == {"lister_appareils", "lire_etat", "agir", "annuler", "creer_routine", "lister_routines", "proposer_souvenir"}
    assert mcp_maison.traiter({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_appel_et_erreur(monkeypatch):
    monkeypatch.setattr(mcp_maison, "appeler", lambda nom, args: json.dumps({"ok": nom, **args}))
    r = mcp_maison.traiter({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "agir", "arguments": {"action": "allumer"}}})
    assert not r["result"]["isError"] and "allumer" in r["result"]["content"][0]["text"]

    def boum(nom, args):
        raise ValueError("appareil inconnu")
    monkeypatch.setattr(mcp_maison, "appeler", boum)
    r = mcp_maison.traiter({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "agir"}})
    assert r["result"]["isError"] and "appareil inconnu" in r["result"]["content"][0]["text"]


# --- API interne -------------------------------------------------------------------

def _app(tmp_path):
    r, _ = routeur()
    cfg = charger(env={}, fichier=Path("inexistant.yaml")).model_copy(update={"home_assistant": CFG_HA})
    ha = faux_ha()
    faux = FauxHA()
    ha.ws, ha.appeler_service, ha.etat_entite = faux.ws, faux.appeler_service, faux.etat_entite
    app = creer_app(cfg, r.registre, ha, AliasAppris(tmp_path / "a.json"))
    return app, faux


def _jeton(app):
    # le jeton est dans la fermeture de outils_maison : on le récupère via la config MCP générée
    for route in app.routes:
        pass
    return None


def test_api_interne_refuse_sans_jeton(tmp_path):
    app, _ = _app(tmp_path)
    c = TestClient(app)
    assert c.get("/interne/maison/appareils").status_code == 403
    assert c.post("/interne/maison/agir", json={"entity_ids": ["light.plafonnier"], "action": "allumer"},
                  headers={"X-Jeton-Interne": "faux"}).status_code == 403


def test_api_interne_avec_jeton(tmp_path, monkeypatch):
    import assistant.main as main
    vus = {}
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "jeton-test")
    app, faux = _app(tmp_path)
    c = TestClient(app)
    h = {"X-Jeton-Interne": "jeton-test"}
    appareils = c.get("/interne/maison/appareils", params={"filtre": "lumière salon"}, headers=h).json()
    assert {a["entity_id"] for a in appareils} >= {"light.plafonnier", "light.lampe_pied"}
    assert all(a["type"] != "lock" for a in c.get("/interne/maison/appareils", headers=h).json())
    rep = c.post("/interne/maison/agir", headers=h, json={
        "entity_ids": ["light.plafonnier", "lock.porte"], "action": "allumer", "couleur": "rouge", "conversation": "x"}).json()
    assert faux.appels == [("light", "turn_on", {"entity_id": "light.plafonnier", "color_name": "red"})]
    assert "lock.porte" in rep["resultat"]          # refusée et signalée
    assert "remis" in c.post("/interne/maison/annuler", headers=h, json={"conversation": "x"}).json()["resultat"]
    assert c.get("/interne/maison/etat/lock.porte", headers=h).status_code == 404


# --- arguments du binaire Claude ---------------------------------------------------------

def test_arguments_avec_outils():
    f = FournisseurClaudeCode("claude", "sonnet")
    outils = {"mcp": {"mcpServers": {"maison": {"command": sys.executable}}}, "autoriser": ["mcp__maison"],
              "note": "NOTE OUTILS"}
    a = f._arguments("claude", "Tu es X.", outils)
    assert a[a.index("--tools") + 1] == "WebSearch,WebFetch"   # recherche web, ni fichiers ni commandes
    assert json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["maison"]
    i = a.index("--allowedTools")
    assert a[i + 1:i + 4] == ["WebSearch", "WebFetch", "mcp__maison"]
    assert "NOTE OUTILS" in a[a.index("--system-prompt") + 1]


def test_annuler_defait_toutes_les_actions_d_une_demande(tmp_path, monkeypatch):
    import assistant.main as main
    monkeypatch.setattr(main.secrets, "token_urlsafe", lambda n: "j")
    app, faux = _app(tmp_path)
    c = TestClient(app)
    h = {"X-Jeton-Interne": "j"}
    c.post("/interne/maison/agir", headers=h, json={"entity_ids": ["light.plafonnier"], "action": "regler", "valeur": 100, "conversation": "x"})
    c.post("/interne/maison/agir", headers=h, json={"entity_ids": ["light.lampe_pied"], "action": "allumer", "conversation": "x"})
    c.post("/interne/maison/annuler", headers=h, json={"conversation": "x"})
    restaures = {a[2]["entity_id"] for a in faux.appels[2:]}
    assert restaures == {"light.plafonnier", "light.lampe_pied"}
