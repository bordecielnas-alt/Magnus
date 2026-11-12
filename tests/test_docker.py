"""Diagnostic Docker en lecture seule, avec un faux docker-socket-proxy."""

import asyncio
import struct

import httpx
import pytest

from assistant.docker_diag import Docker, demultiplexer, statut_lisible

CONTENEURS = [
    {"Id": "a" * 64, "Names": ["/plex"], "Image": "plexinc/pms-docker", "State": "running", "Status": "Up 2 hours (healthy)"},
    {"Id": "b" * 64, "Names": ["/radarr"], "Image": "linuxserver/radarr", "State": "exited", "Status": "Exited (137) 5 minutes ago"},
    {"Id": "c" * 64, "Names": ["/sauvegarde"], "Image": "duplicati", "State": "exited", "Status": "Exited (0) 1 day ago"},
    {"Id": "d" * 64, "Names": ["/nginx-proxy-manager"], "Image": "jc21/npm", "State": "running", "Status": "Up 3 days (unhealthy)"},
]


def trame(texte, flux=1):
    b = texte.encode()
    return bytes([flux, 0, 0, 0]) + struct.pack(">I", len(b)) + b


def faux_proxy(requetes):
    def repondre(req: httpx.Request) -> httpx.Response:
        requetes.append((req.method, req.url.path))
        if req.method != "GET":
            return httpx.Response(403)            # proxy en lecture seule
        if req.url.path == "/containers/json":
            return httpx.Response(200, json=CONTENEURS)
        if req.url.path.endswith("/json"):
            return httpx.Response(200, json={"State": {"Status": "exited", "ExitCode": 137, "OOMKilled": True},
                                             "RestartCount": 4, "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}}})
        if req.url.path.endswith("/logs"):
            return httpx.Response(200, content=trame("démarrage ok\n") + trame("ERROR: out of memory\n", 2))
        return httpx.Response(404)
    return httpx.MockTransport(repondre)


def test_demultiplexer():
    assert demultiplexer(trame("a\n") + trame("b\n", 2)) == "a\nb\n"
    assert demultiplexer(b"texte brut (TTY)") == "texte brut (TTY)"


def test_liste_et_problemes():
    d = Docker("http://proxy:2375", transport=faux_proxy([]))
    noms = [c["nom"] for c in asyncio.run(d.conteneurs())]
    assert noms == ["nginx-proxy-manager", "plex", "radarr", "sauvegarde"]
    # sortie normale (code 0) : pas un problème ; tué (137) et unhealthy : problèmes
    assert {c["nom"] for c in asyncio.run(d.problemes())} == {"radarr", "nginx-proxy-manager"}


def test_diagnostic_et_lecture_seule():
    requetes = []
    d = Docker("http://proxy:2375", transport=faux_proxy(requetes))
    r = asyncio.run(d.diagnostic("Radarr"))
    assert r["oom"] is True and r["redemarrages"] == 4 and r["code_sortie"] == 137
    assert r["erreurs_recentes"] == ["ERROR: out of memory"]
    assert all(m == "GET" for m, _ in requetes)
    with pytest.raises(ValueError):
        asyncio.run(d.diagnostic("inconnu"))


def test_api_docker_non_configure(tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_securite import app
    a, _ = app(tmp_path)
    assert TestClient(a, client=("127.0.0.1", 1)).get("/api/docker").json()["configure"] is False


def test_garde_fou_lecture_seule():
    import asyncio

    d = Docker("http://proxy:2375", transport=faux_proxy([]))

    async def ecrire():
        async with d._client() as c:
            await c.post("/containers/" + "a" * 64 + "/restart")
    with pytest.raises(PermissionError):
        asyncio.run(ecrire())

    async def autre():
        async with d._client() as c:
            await c.get("/images/json")
    with pytest.raises(PermissionError):
        asyncio.run(autre())


def test_socket_unix():
    d = Docker("unix:///var/run/docker.sock", transport=faux_proxy([]))
    assert d.configure and asyncio.run(d.conteneurs())[0]["nom"] == "nginx-proxy-manager"


def test_api_unraid():
    from assistant.docker_diag import UnraidDocker
    vus = []

    def repondre(req: httpx.Request) -> httpx.Response:
        import json
        q = json.loads(req.content)["query"]
        vus.append((req.headers.get("x-api-key"), q))
        if "image" in q:                       # version d'Unraid sans ce champ
            return httpx.Response(200, json={"errors": [{"message": "Cannot query field image"}]})
        return httpx.Response(200, json={"data": {"dockerContainers": [
            {"id": "1", "names": ["/plex"], "state": "RUNNING", "status": "Up 2 hours (healthy)", "autoStart": True},
            {"id": "2", "names": ["/radarr"], "state": "EXITED", "status": "Exited (137) 3 minutes ago", "autoStart": True},
        ]}})
    d = UnraidDocker("http://tour/graphql", "cle-lecture", transport=httpx.MockTransport(repondre))
    assert d.configure and [c["etat"] for c in asyncio.run(d.conteneurs())] == ["running", "exited"]
    assert vus[0][0] == "cle-lecture"
    assert [c["nom"] for c in asyncio.run(d.problemes())] == ["radarr"]
    diag = asyncio.run(d.diagnostic("radarr"))
    assert diag["statut"].startswith("Exited (137)") and "pas de journaux" in diag["remarque"]
    refus = UnraidDocker("http://tour/graphql", "x", transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(PermissionError):
        asyncio.run(refus.conteneurs())


def test_certificat_auto_signe_accepte_seulement_en_local():
    from assistant.docker_diag import UnraidDocker
    assert UnraidDocker("https://192.168.1.50:4436/graphql", "k")._verifier_certificat() is False
    assert UnraidDocker("https://tour.exemple.fr/graphql", "k")._verifier_certificat() is True


def test_api_unraid_schema_recent_et_erreur_400():
    """Unraid 7.3 : « docker { containers } » ; l'ancienne requête renvoie 400 avec la raison en JSON."""
    from assistant.docker_diag import UnraidDocker
    vues = []

    def repondre(req: httpx.Request) -> httpx.Response:
        import json
        q = json.loads(req.content)["query"]
        vues.append(q)
        if "docker {" in q and "image" not in q:
            return httpx.Response(200, json={"data": {"docker": {"containers": [
                {"id": "1", "names": ["/plex"], "state": "RUNNING", "status": "Up 3 hours", "autoStart": True}]}}})
        return httpx.Response(400, json={"errors": [{"message": "Cannot query field on type"}]})
    d = UnraidDocker("https://192.168.1.50:4436/graphql", "k", transport=httpx.MockTransport(repondre))
    assert [c["nom"] for c in asyncio.run(d.conteneurs())] == ["plex"]
    n = len(vues)
    asyncio.run(d.conteneurs())
    assert len(vues) == n + 1                       # la bonne requête est retenue

    rien = UnraidDocker("http://x/graphql", "k", transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"errors": [{"message": "Cannot query field dockerContainers"}]})))
    with pytest.raises(RuntimeError, match="schéma"):
        asyncio.run(rien.conteneurs())
    droits = UnraidDocker("http://x/graphql", "k", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"errors": [{"message": "Forbidden resource"}]})))
    with pytest.raises(PermissionError):
        asyncio.run(droits.conteneurs())


def test_conteneurs_arretes_expres_ne_sont_pas_des_problemes():
    from assistant.docker_diag import UnraidDocker

    def repondre(req):
        return httpx.Response(200, json={"data": {"docker": {"containers": [
            {"id": "1", "names": ["/adminer"], "state": "EXITED", "status": "Created", "autoStart": False},
            {"id": "2", "names": ["/vieux"], "state": "EXITED", "status": "Exited (137) 2 days ago", "autoStart": False},
            {"id": "3", "names": ["/plex"], "state": "EXITED", "status": "Exited (1) 3 minutes ago", "autoStart": True}]}}})
    d = UnraidDocker("http://x/graphql", "k", transport=httpx.MockTransport(repondre))
    assert [c["nom"] for c in asyncio.run(d.problemes())] == ["plex"]


def test_causes_probables():
    from assistant.docker_diag import causes_probables
    oom = causes_probables({"oom": True, "code_sortie": 137, "erreurs_recentes": ["ERROR: out of memory"]})
    assert oom[0].startswith("manque de mémoire") and len([c for c in oom if "mémoire" in c]) == 1
    assert not any("137" in c for c in oom)            # 137 déjà expliqué par OOMKilled
    port = causes_probables({"code_sortie": 1, "redemarrages": 5,
                             "dernieres_lignes": ["listen tcp 0.0.0.0:8080: bind: address already in use"]})
    assert "port déjà pris" in port[0] and any("code de sortie 1" in c for c in port) and any("boucle" in c for c in port)
    # signature peu sûre : ignorée hors des lignes d'erreur (ex. un 404 banal dans le journal)
    assert causes_probables({"dernieres_lignes": ["GET /favicon.ico 404 not found"]}) == []
    # API Unraid sans journaux : le code de sortie vient du statut
    assert causes_probables({"statut": "Exited (139) 2 minutes ago"})[0].startswith("code de sortie 139")


def test_unraid_avec_journaux_docker():
    from assistant.docker_diag import UnraidDocker

    def repondre(req):
        return httpx.Response(200, json={"data": {"docker": {"containers": [
            {"id": "1", "names": ["/radarr"], "state": "EXITED", "status": "Exited (137) 3 minutes ago", "autoStart": True}]}}})
    d = UnraidDocker("http://x/graphql", "k", transport=httpx.MockTransport(repondre))
    requetes = []
    d.journaux = Docker("http://proxy:2375", transport=faux_proxy(requetes))
    diag = asyncio.run(d.diagnostic("radarr"))
    assert diag["erreurs_recentes"] == ["ERROR: out of memory"] and diag["oom"] is True
    assert diag["demarrage_auto"] is True and "remarque" not in diag
    assert all(m == "GET" for m, _ in requetes)
    d.journaux = Docker("http://proxy:2375", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert "journaux Docker illisibles" in asyncio.run(d.diagnostic("radarr"))["remarque"]


@pytest.mark.parametrize("brut, etat, attendu", [
    ("Up Less than a second", "running", "démarré à l'instant"),
    ("Up 3 seconds", "running", "démarré à l'instant"),
    ("Up About a minute", "running", "en marche depuis environ une minute"),
    ("Up 2 hours (healthy)", "running", "en marche depuis 2 heures"),
    ("Up 1 day", "running", "en marche depuis un jour"),
    ("Up 4 months", "running", "en marche depuis 4 mois"),
    ("Up 3 days (unhealthy)", "running", "en marche depuis 3 jours, mais en mauvaise santé"),
    ("Up 2 hours (Paused)", "paused", "en pause"),
    ("Exited (0) 1 day ago", "exited", "arrêté normalement depuis un jour"),
    ("Exited (137) 5 minutes ago", "exited", "arrêté de force depuis 5 minutes"),
    ("Exited (1) About an hour ago", "exited", "arrêté depuis environ une heure après une erreur (code 1)"),
    ("Restarting (1) 10 seconds ago", "restarting", "en train de redémarrer après une erreur (code 1)"),
    ("Created", "created", "créé mais jamais démarré"),
    ("Up", "running", "en marche"),
    ("", "exited", "arrêté"),
    ("Something new", "", "dans un état inconnu"),
])
def test_statut_lisible(brut, etat, attendu):
    assert statut_lisible(brut, etat) == attendu
