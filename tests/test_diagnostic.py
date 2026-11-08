from assistant.diagnostic import Diagnostic, masquer
from tests.test_securite import LAN, MDP, app, client


def test_masquer_secrets():
    t = masquer('jeton=abc123 "password": "hunter2" Authorization: Bearer xyz.987 HA_JETON=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefgh')
    assert "abc123" not in t and "hunter2" not in t and "xyz.987" not in t and "eyJ" not in t


def test_evenements_et_rapport(tmp_path):
    d = Diagnostic(tmp_path)
    d.evenements("tab1", "p1", [{"ms": 120, "heure": "2099-01-01T10:00:01.000Z", "texte": "micro ouvert"},
                                {"ms": 900, "heure": "2099-01-01T10:00:03.000Z", "texte": "ligne\nsur deux"}],
                 contexte="Firefox Android 128", compte="principal", ip=LAN)
    d.serveur("INFO POST /api/voix/transcrire -> 200 en 812 ms")
    r = d.rapport(entete="Diagnostic test")
    assert "[tab1/p1]  +120 ms  micro ouvert" in r and "CONTEXTE" in r and "Firefox Android" in r
    assert "[serveur]" in r and "\n    sur deux" in r
    assert r.index("[serveur]") < r.index("micro ouvert")       # trié par heure (2099 en dernier)


def test_appareil_nettoye(tmp_path):
    a, _ = app(tmp_path)
    c = client(a, "127.0.0.1")
    assert c.post("/api/diagnostic/evenements", json={"appareil": "../../x", "page": "p", "lignes": [{"texte": "a"}]}).status_code == 200
    assert (tmp_path / "diagnostic" / "x.log").is_file()                 # nettoyé : pas de sortie du dossier
    assert c.post("/api/diagnostic/evenements", json={"appareil": "", "lignes": [{"texte": "b"}]}).status_code == 200
    assert (tmp_path / "diagnostic" / "inconnu.log").is_file()
    assert c.post("/api/diagnostic/evenements", json={"appareil": "a", "lignes": "pas une liste"}).status_code == 422
    assert c.post("/api/diagnostic/evenements", content=b"x" * 70000).status_code == 413


def test_api_diagnostic(tmp_path):
    a, sec = app(tmp_path)
    machine = client(a, "127.0.0.1")
    r = machine.post("/api/diagnostic/evenements", content=b'{"appareil":"ab12","page":"z9","contexte":"UA test",'
                     b'"lignes":[{"ms":5,"heure":"2026-11-07T10:00:00Z","texte":"mot_de_passe=secret42 envoi"}]}',
                     headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    machine.post("/api/voix/endormir", json={"conversation": "c1"})
    t = machine.get("/diagnostic?brut=1&jours=7").text
    assert "UA test" in t and "secret42" not in t and "POST /api/voix/endormir -> 200" in t
    assert "Tout copier" in machine.get("/diagnostic").text
    # derrière la connexion, et réservé aux administrateurs
    sec.creer_premier(MDP)
    assert client(a, LAN).get("/diagnostic").status_code == 303
