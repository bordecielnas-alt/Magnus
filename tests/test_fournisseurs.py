import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.config import charger
from assistant.fournisseurs import Fournisseur, FournisseurIndisponible, Message, Registre
from assistant.fournisseurs.openai_compat import FournisseurOpenAI
from assistant.main import creer_app

MSGS = [Message("user", "bonjour")]


# --- Fournisseur compatible OpenAI, sur un faux serveur HTTP ---------------

def _serveur(requetes):
    def gerer(req: httpx.Request) -> httpx.Response:
        requetes.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        corps = json.loads(req.content)
        if corps["stream"]:
            sse = "".join(
                f'data: {json.dumps({"choices": [{"delta": {"content": m}}]})}\n\n'
                for m in ["Bon", "jour"]
            ) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Bonjour"}}]})
    return httpx.MockTransport(gerer)


def _openai(requetes, cle=""):
    return FournisseurOpenAI("test", "http://x/v1/", "mini", cle=cle,
                             options={"temperature": 0.1}, transport=_serveur(requetes))


def test_openai_repondre_envoie_modele_et_options():
    req = []
    assert asyncio.run(_openai(req).repondre(MSGS)) == "Bonjour"
    corps = json.loads(req[0].content)
    assert req[0].url.path == "/v1/chat/completions"
    assert corps["model"] == "mini" and corps["temperature"] == 0.1
    assert corps["messages"] == [{"role": "user", "content": "bonjour"}]
    assert "authorization" not in req[0].headers


def test_openai_cle_dans_entete():
    req = []
    asyncio.run(_openai(req, cle="sk-test").repondre(MSGS))
    assert req[0].headers["authorization"] == "Bearer sk-test"


def test_openai_flux():
    async def lire():
        return [m async for m in _openai([]).flux(MSGS)]
    assert asyncio.run(lire()) == ["Bon", "jour"]


def test_openai_erreur_devient_indisponible():
    f = FournisseurOpenAI("ko", "http://x/v1", "m",
                          transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(FournisseurIndisponible):
        asyncio.run(f.repondre(MSGS))
    assert asyncio.run(f.disponible()) is False


# --- Registre et secours, avec de faux fournisseurs ------------------------

class Faux(Fournisseur):
    def __init__(self, ident, texte=None):
        super().__init__(ident, f"modele-{ident}")
        self.texte = texte  # None = en panne
        self.recu = None

    async def repondre(self, messages):
        self.recu = messages
        if self.texte is None:
            raise FournisseurIndisponible(self.ident)
        return self.texte

    async def flux(self, messages):
        self.recu = messages
        if self.texte is None:
            raise FournisseurIndisponible(self.ident)
        for mot in self.texte.split(" "):
            yield mot + " "

    async def disponible(self):
        return self.texte is not None


def _registre(principal="ok", secours="secours"):
    f = {"a": Faux("a", principal), "b": Faux("b", secours)}
    return Registre(f, {"rapide": "a", "secours": "b"}), f


def test_registre_principal():
    reg, _ = _registre()
    r = asyncio.run(reg.repondre("rapide", MSGS))
    assert (r.texte, r.fournisseur, r.secours) == ("ok", "a", False)


def test_registre_bascule_sur_secours():
    reg, _ = _registre(principal=None)
    r = asyncio.run(reg.repondre("rapide", MSGS))
    assert (r.texte, r.fournisseur, r.secours) == ("secours", "b", True)


def test_registre_tout_en_panne():
    reg, _ = _registre(principal=None, secours=None)
    with pytest.raises(FournisseurIndisponible):
        asyncio.run(reg.repondre("rapide", MSGS))


def test_config_refuse_role_vers_fournisseur_inconnu(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("roles:\n  rapide: nexiste-pas\n", encoding="utf-8")
    with pytest.raises(ValueError):
        charger(env={}, fichier=f)


# --- API -------------------------------------------------------------------

def _client(**kw):
    reg, faux = _registre(**kw)
    cfg = charger(env={"ASSISTANT_NOM": "Akatosh"}, fichier=Path("inexistant.yaml"))
    return TestClient(creer_app(cfg, reg)), faux


def test_api_chat_injecte_consigne_avec_nom():
    c, faux = _client(principal="Salut")
    r = c.post("/api/chat", json={"message": "bonjour", "role": "rapide",
                                  "historique": [{"role": "user", "content": "avant"}]}).json()
    assert r["reponse"] == "Salut" and r["fournisseur"] == "a" and r["secours"] is False
    recu = faux["a"].recu
    assert recu[0].role == "system" and "Akatosh" in recu[0].content
    assert [m.content for m in recu[1:]] == ["avant", "bonjour"]


def test_api_chat_flux():
    c, _ = _client(principal="Il fait beau")
    lignes = [json.loads(l) for l in c.post("/api/chat", json={"message": "météo ?", "flux": True, "role": "rapide"}).text.splitlines()]
    assert lignes[0]["route"]["raison"] == "choisi à la main"
    assert "".join(l.get("delta", "") for l in lignes) == "Il fait beau "
    assert lignes[-1]["fin"] is True and lignes[-1]["fournisseur"] == "a"


def test_api_chat_503_si_tout_en_panne():
    c, _ = _client(principal=None, secours=None)
    assert c.post("/api/chat", json={"message": "x", "role": "rapide"}).status_code == 503


def test_api_role_inconnu():
    c, _ = _client()
    assert c.post("/api/chat", json={"message": "x", "role": "magie"}).status_code == 400


def test_api_fournisseurs():
    c, _ = _client(principal=None)
    e = c.get("/api/fournisseurs").json()
    dispo = {f["id"]: f["disponible"] for f in e["fournisseurs"]}
    assert dispo == {"a": False, "b": True}
