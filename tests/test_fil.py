"""Fil partagé : un seul assistant par compte, la même conversation sur tous les appareils."""

import asyncio

from assistant.fil import TOURS_MAX, Fil
from tests.test_memoire import client_machine


def test_conversation_stable_et_contexte(tmp_path):
    f = Fil(tmp_path)
    c = f.conversation("benjamin")
    assert c.startswith("fil-") and f.conversation("benjamin") == c and f.conversation("lea") != c
    assert f.compte_de(c) == "benjamin" and f.compte_de("autre") is None
    f.ajouter("benjamin", "user", "allume le salon", "tel")
    f.ajouter("benjamin", "assistant", "Salon allumé.", "tel")
    f.ajouter("benjamin", "assistant", "", "tel", extra={"erreur": "x"})     # réponse en échec : pas au modèle
    assert f.contexte("benjamin", 20) == [{"role": "user", "content": "allume le salon"},
                                          {"role": "assistant", "content": "Salon allumé."}]
    assert f.contexte("lea", 20) == []
    assert Fil(tmp_path).conversation("benjamin") == c                       # survit au redémarrage


def test_taille_limitee_et_effacer(tmp_path):
    f = Fil(tmp_path)
    for i in range(TOURS_MAX + 5):
        f.ajouter("b", "user", f"m{i}")
    t = f.tours("b", n=2000)
    assert len(t) == TOURS_MAX and t[-1]["texte"] == f"m{TOURS_MAX + 4}"
    assert [x["texte"] for x in f.tours("b", depuis=t[-2]["id"])] == [f"m{TOURS_MAX + 4}"]
    f.effacer("b")
    assert f.tours("b") == []


def test_abonnes_recoivent_en_direct(tmp_path):
    async def scenario():
        f = Fil(tmp_path)
        q1, q2, autre = f.abonner("b"), f.abonner("b"), f.abonner("lea")
        f.ajouter("b", "user", "bonjour", "tel")
        assert (await q1.get())["tour"]["texte"] == "bonjour"
        assert (await q2.get())["tour"]["appareil"] == "tel"
        assert autre.empty() and f.ouverts("b") == 2
        f.desabonner("b", q1)
        f.publier("b", {"type": "delta", "delta": "x"})
        assert q1.empty() and (await q2.get())["delta"] == "x"
    asyncio.run(scenario())


def test_deux_pages_meme_conversation(tmp_path):
    c, modele = client_machine(tmp_path)
    f = c.get("/api/fil").json()
    assert f["actif"] and f["tours"] == []
    conv = f["conversation"]
    assert c.get("/api/fil").json()["conversation"] == conv                    # même compte : même fil
    c.post("/api/chat", json={"message": "je m'appelle Ben", "conversation": conv, "appareil": "pc", "role": "rapide"},
           headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"})
    # l'autre appareil retrouve l'échange, et le modèle reçoit le contexte même si la page n'envoie rien
    tours = c.get("/api/fil").json()["tours"]
    assert [t["role"] for t in tours] == ["user", "assistant"]
    assert tours[0]["appareil"] == "pc" and tours[0]["extra"]["appareil_nom"] == "Windows"
    assert tours[1]["question"] == tours[0]["id"] and tours[1]["texte"]
    c.post("/api/chat", json={"message": "comment je m'appelle ?", "conversation": conv, "appareil": "tel",
                              "role": "rapide", "flux": True})
    vus = [m.content for m in modele.appels[-1][0]]
    assert "je m'appelle Ben" in vus
    tours = c.get("/api/fil").json()["tours"]
    assert len(tours) == 4 and tours[-1]["texte"] and tours[-1]["appareil"] == "tel"
    # une conversation ordinaire (page d'un appareil partagé, ancienne page) n'est pas gardée
    c.post("/api/chat", json={"message": "salut", "conversation": "local-1", "role": "rapide"})
    assert len(c.get("/api/fil").json()["tours"]) == 4
    assert c.delete("/api/fil").json()["ok"] and c.get("/api/fil").json()["tours"] == []


def test_conversation_d_un_autre_compte_refusee(tmp_path):
    c, _ = client_machine(tmp_path)
    autre = c.app.state.fil.conversation("quelqu-un")
    assert c.post("/api/chat", json={"message": "oui", "conversation": autre}).status_code == 403


def test_oui_depuis_un_autre_appareil(tmp_path):
    c, _ = client_machine(tmp_path)
    conv = c.get("/api/fil").json()["conversation"]
    c.post("/api/chat", json={"message": "Retiens que le code du portail est sur le frigo", "conversation": conv,
                              "appareil": "pc"})
    r = c.post("/api/chat", json={"message": "oui", "conversation": conv, "appareil": "tel"}).json()
    assert r["reponse"] == "C'est retenu."
