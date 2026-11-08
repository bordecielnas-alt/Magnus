"""Diagnostic Docker en lecture seule, avec un faux docker-socket-proxy."""

import asyncio
import struct

import httpx
import pytest

from assistant.docker_diag import Docker, demultiplexer

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
