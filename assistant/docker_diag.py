"""Diagnostic des conteneurs Docker (Unraid), en lecture seule.

Deux façons de joindre Docker :
- le socket monté dans le conteneur (url « unix:///var/run/docker.sock ») ;
- ou un docker-socket-proxy en lecture seule (url http://…:2375).
Dans les deux cas, ce module n'envoie que des GET sur trois chemins autorisés (liste, détail, journaux) :
il ne peut rien démarrer, arrêter ni supprimer. Attention : avec le socket monté, cette limite est dans
le code de l'assistant, pas dans Docker.
Les correctifs sont proposés (par Claude), jamais appliqués.
"""

from __future__ import annotations

import re
import struct
import httpx

CHEMINS_AUTORISES = re.compile(r"/containers/json|/containers/[0-9a-f]{12,64}/(json|logs)")
RE_ERREUR = re.compile(r"\b(error|erreur|exception|fatal|panic|traceback|failed|échec|denied|refused|"
                       r"out of memory|oom|no space left|permission)\b", re.I)


def demultiplexer(brut: bytes) -> str:
    """Journaux Docker sans TTY : trames [flux, 0, 0, 0, taille sur 4 octets] + contenu."""
    if len(brut) < 8 or brut[0] not in (0, 1, 2) or brut[1:4] != b"\0\0\0":
        return brut.decode("utf-8", "replace")
    morceaux, i = [], 0
    while i + 8 <= len(brut):
        taille = struct.unpack(">I", brut[i + 4:i + 8])[0]
        morceaux.append(brut[i + 8:i + 8 + taille])
        i += 8 + taille
    return b"".join(morceaux).decode("utf-8", "replace")


class Docker:
    def __init__(self, url: str, timeout: float = 8, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    @property
    def configure(self) -> bool:
        return bool(self.url)

    def _client(self) -> httpx.AsyncClient:
        transport, base = self._transport, self.url
        if self.url.startswith("unix://"):
            transport = transport or httpx.AsyncHTTPTransport(uds=self.url[len("unix://"):])
            base = "http://docker"
        return httpx.AsyncClient(base_url=base, timeout=self.timeout, transport=transport,
                                 event_hooks={"request": [self._lecture_seule]})

    @staticmethod
    async def _lecture_seule(req: httpx.Request) -> None:
        """Garde-fou : seulement des lectures, sur la liste, le détail et les journaux des conteneurs."""
        chemin = req.url.path
        if req.method != "GET" or not CHEMINS_AUTORISES.fullmatch(chemin):
            raise PermissionError(f"appel Docker refusé (lecture seule) : {req.method} {chemin}")

    async def conteneurs(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/containers/json", params={"all": "1"})
            r.raise_for_status()
        sortie = []
        for x in r.json():
            nom = (x.get("Names") or ["?"])[0].lstrip("/")
            statut = x.get("Status", "")
            sante = "unhealthy" if "(unhealthy)" in statut else "healthy" if "(healthy)" in statut else ""
            sortie.append({"nom": nom, "image": x.get("Image", ""), "etat": x.get("State", ""),
                           "statut": statut, "sante": sante, "id": x.get("Id", "")[:12]})
        return sorted(sortie, key=lambda c: c["nom"].lower())

    async def trouver(self, nom: str) -> dict | None:
        cible = nom.strip().lower()
        liste = await self.conteneurs()
        exact = [c for c in liste if c["nom"].lower() == cible]
        proches = exact or [c for c in liste if cible in c["nom"].lower()]
        return proches[0] if len(proches) == 1 else None

    async def diagnostic(self, nom: str, lignes: int = 120) -> dict:
        """État, santé, redémarrages, code de sortie et lignes d'erreur récentes d'un conteneur."""
        c = await self.trouver(nom)
        if not c:
            noms = [x["nom"] for x in await self.conteneurs() if nom.lower() in x["nom"].lower()]
            raise ValueError(f"conteneur « {nom} » introuvable ou ambigu" + (f" : {', '.join(noms)}" if noms else ""))
        async with self._client() as cl:
            d = (await cl.get(f"/containers/{c['id']}/json")).json()
            r = await cl.get(f"/containers/{c['id']}/logs",
                             params={"stdout": "1", "stderr": "1", "tail": str(lignes), "timestamps": "1"})
        etat = d.get("State") or {}
        journaux = demultiplexer(r.content).splitlines() if r.status_code == 200 else []
        erreurs = [l for l in journaux if RE_ERREUR.search(l)][-15:]
        sante = (etat.get("Health") or {})
        return {
            "nom": c["nom"], "image": c["image"], "etat": etat.get("Status", c["etat"]),
            "demarre": etat.get("StartedAt", ""), "arrete": etat.get("FinishedAt", ""),
            "code_sortie": etat.get("ExitCode"), "oom": etat.get("OOMKilled", False),
            "redemarrages": d.get("RestartCount", 0), "politique": ((d.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name", ""),
            "sante": sante.get("Status", ""), "sante_derniers": [h.get("Output", "")[:200] for h in (sante.get("Log") or [])[-3:]],
            "erreurs_recentes": [l[:300] for l in erreurs],
            "dernieres_lignes": [l[:300] for l in journaux[-20:]],
        }

    async def problemes(self) -> list[dict]:
        """Conteneurs arrêtés anormalement ou en mauvaise santé (pour le mode proactif)."""
        return [c for c in await self.conteneurs()
                if c["sante"] == "unhealthy" or c["etat"] in ("restarting", "dead")
                or (c["etat"] == "exited" and not re.search(r"Exited \(0\)", c["statut"]))]


class UnraidDocker(Docker):
    """Conteneurs lus par l'API officielle d'Unraid (7.2+, GraphQL), avec une clé à droits limités.

    C'est Unraid qui applique les droits de la clé (rôle lecture seule). Pas de journaux de conteneur
    dans l'API : le diagnostic se limite à l'état, au statut et au démarrage automatique.
    """

    REQUETE = "query { dockerContainers { id names state status autoStart image } }"
    REQUETE_MINIMALE = "query { dockerContainers { id names state status autoStart } }"

    def __init__(self, url: str, cle: str, timeout: float = 8, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(url, timeout, transport)
        self.cle = cle

    @property
    def configure(self) -> bool:
        return bool(self.url and self.cle)

    def _verifier_certificat(self) -> bool:
        """Unraid en HTTPS sur le réseau local a souvent un certificat auto-signé : accepté seulement pour
        une adresse IP privée (le trafic ne sort pas de la maison)."""
        import ipaddress
        from urllib.parse import urlparse
        try:
            return not ipaddress.ip_address(urlparse(self.url).hostname or "").is_private
        except ValueError:
            return True

    async def _graphql(self, requete: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport,
                                     verify=self._verifier_certificat()) as c:
            r = await c.post(self.url, json={"query": requete},
                             headers={"x-api-key": self.cle, "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            raise PermissionError("API Unraid : clé refusée ou droits insuffisants")
        r.raise_for_status()
        d = r.json()
        if d.get("errors"):
            raise RuntimeError(d["errors"][0].get("message", "erreur GraphQL"))
        return d.get("data") or {}

    async def conteneurs(self) -> list[dict]:
        try:
            data = await self._graphql(self.REQUETE)
        except RuntimeError:            # champ « image » absent de cette version : requête documentée
            data = await self._graphql(self.REQUETE_MINIMALE)
        sortie = []
        for x in data.get("dockerContainers") or []:
            nom = ((x.get("names") or ["?"])[0] or "?").lstrip("/")
            statut = x.get("status") or ""
            sante = "unhealthy" if "(unhealthy)" in statut else "healthy" if "(healthy)" in statut else ""
            sortie.append({"nom": nom, "image": x.get("image") or "", "etat": (x.get("state") or "").lower(),
                           "statut": statut, "sante": sante, "id": str(x.get("id", "")),
                           "demarrage_auto": x.get("autoStart")})
        return sorted(sortie, key=lambda c: c["nom"].lower())

    async def diagnostic(self, nom: str, lignes: int = 120) -> dict:
        c = await self.trouver(nom)
        if not c:
            noms = [x["nom"] for x in await self.conteneurs() if nom.lower() in x["nom"].lower()]
            raise ValueError(f"conteneur « {nom} » introuvable ou ambigu" + (f" : {', '.join(noms)}" if noms else ""))
        return {"nom": c["nom"], "image": c["image"], "etat": c["etat"], "statut": c["statut"], "sante": c["sante"],
                "demarrage_auto": c.get("demarrage_auto"),
                "remarque": "API Unraid : pas de journaux disponibles ; diagnostic à partir de l'état et du statut."}
