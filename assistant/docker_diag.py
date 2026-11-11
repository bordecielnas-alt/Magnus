"""Diagnostic des conteneurs Docker (Unraid), en lecture seule.

Deux façons de joindre Docker :
- le socket monté dans le conteneur (url « unix:///var/run/docker.sock ») ;
- ou un docker-socket-proxy en lecture seule (url http://…:2375).
Dans les deux cas, ce module n'envoie que des GET sur trois chemins autorisés (liste, détail, journaux) :
il ne peut rien démarrer, arrêter ni supprimer. Attention : avec le socket monté, cette limite est dans
le code de l'assistant, pas dans Docker.
Les correctifs sont proposés (par Claude), jamais appliqués. Seule exception, avec l'API Unraid et une 2e clé
(droits Docker) : redémarrer ou mettre à jour un conteneur, toujours après un oui ou un bouton de la personne.
"""

from __future__ import annotations

import re
import struct
from datetime import datetime, timezone

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


def age_jours(date: str | None, maintenant: datetime | None = None) -> int | None:
    """Jours écoulés depuis une date ISO (« 2026-11-01 » ou « 2026-11-01T03:00:00Z ») ; None si illisible."""
    if not date:
        return None
    try:
        d = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0, ((maintenant or datetime.now(timezone.utc)) - d).days)


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
        def tombe(c: dict) -> bool:
            if c["etat"] != "exited" or re.search(r"Exited \(0\)|^Created", c["statut"]):
                return False            # arrêt normal, ou jamais démarré (« Created »)
            return c.get("demarrage_auto") is not False   # arrêté exprès : pas de démarrage automatique
        return [c for c in await self.conteneurs()
                if c["sante"] == "unhealthy" or c["etat"] in ("restarting", "dead") or tombe(c)]


class UnraidDocker(Docker):
    """Conteneurs lus par l'API officielle d'Unraid (7.2+, GraphQL), avec une clé à droits limités.

    C'est Unraid qui applique les droits de la clé (rôle lecture seule). Pas de journaux de conteneur
    dans l'API : le diagnostic se limite à l'état, au statut et au démarrage automatique.
    """

    # Le schéma a changé selon les versions d'Unraid : on essaie dans l'ordre, la première qui passe est gardée.
    REQUETES = [
        ("docker.containers", "query { docker { containers { id names state status autoStart image } } }"),
        ("docker.containers", "query { docker { containers { id names state status autoStart } } }"),
        ("dockerContainers", "query { dockerContainers { id names state status autoStart image } }"),
        ("dockerContainers", "query { dockerContainers { id names state status autoStart } }"),
    ]

    def __init__(self, url: str, cle: str, timeout: float = 8, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(url, timeout, transport)
        self.cle = cle
        self._requete: tuple[str, str] | None = None

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

    async def _graphql(self, requete: str, variables: dict | None = None, cle: str | None = None,
                       timeout: float | None = None) -> dict:
        async with httpx.AsyncClient(timeout=timeout or self.timeout, transport=self._transport,
                                     verify=self._verifier_certificat()) as c:
            r = await c.post(self.url, json={"query": requete, **({"variables": variables} if variables else {})},
                             headers={"x-api-key": cle or self.cle, "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            raise PermissionError("API Unraid : clé refusée ou droits insuffisants")
        try:
            d = r.json()
        except ValueError:
            r.raise_for_status()
            raise RuntimeError(f"réponse illisible ({r.status_code})") from None
        if d.get("errors"):                 # GraphQL : requête refusée (souvent 400), avec la raison
            message = d["errors"][0].get("message", "erreur GraphQL")
            if re.search(r"forbidden|unauthori[sz]ed|permission|not allowed", message, re.I):
                raise PermissionError(f"API Unraid : droits insuffisants pour cette clé ({message})")
            raise RuntimeError(message)
        r.raise_for_status()
        return d.get("data") or {}

    async def conteneurs(self) -> list[dict]:
        erreurs, liste = [], None
        for chemin, requete in ([self._requete] if self._requete else self.REQUETES):
            try:
                data = await self._graphql(requete)
            except RuntimeError as e:
                erreurs.append(str(e))
                continue
            liste = data
            for cle in chemin.split("."):
                liste = (liste or {}).get(cle)
            if liste is None:
                erreurs.append(f"pas de {chemin} dans la réponse")
                continue
            self._requete = (chemin, requete)      # retenue pour les fois suivantes
            break
        if liste is None:
            raise RuntimeError("schéma de l'API Unraid non reconnu : " + " | ".join(erreurs[-2:]))
        sortie = []
        for x in liste or []:
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


    # --- état du serveur (array, disques, parité) ------------------------------------------------------
    REQUETE_SERVEUR = """query {
      array {
        state
        capacity { kilobytes { free used total } }
        parityCheckStatus { status errors progress running date }
        parities { name device status temp numErrors warning critical isSpinning type }
        disks { name device status temp numErrors warning critical isSpinning type fsSize fsFree fsUsed }
        caches { name device status temp numErrors warning critical isSpinning type fsSize fsFree fsUsed }
      }
      disks { name device smartStatus temperature }
    }"""

    async def serveur(self, plein_pct: int = 95, libre_min_pct: int = 10, parite_max_jours: int = 45) -> dict:
        """État de l'array : disques (état, température, erreurs, espace), SMART, parité, alertes.

        `plein_pct` : un disque utilisé à ce pourcentage ou plus est signalé ; `libre_min_pct` : alerte si l'array
        entier a moins de ce pourcentage libre ; `parite_max_jours` : alerte si le dernier contrôle de parité
        est plus vieux (0 = jamais)."""
        data = await self._graphql(self.REQUETE_SERVEUR)
        a = data.get("array") or {}
        smart = {(d.get("device") or "").split("/")[-1]: d for d in data.get("disks") or []}
        disques, alertes = [], []
        for groupe in ("parities", "disks", "caches"):
            for d in a.get(groupe) or []:
                if (d.get("status") or "") == "DISK_NP":       # emplacement vide
                    continue
                s_ = smart.get((d.get("device") or "").split("/")[-1], {})
                temp = d.get("temp")
                libre = d.get("fsFree")
                info = {"nom": d.get("name"), "type": (d.get("type") or "").lower(), "etat": d.get("status"),
                        "temperature": temp, "erreurs": int(d.get("numErrors") or 0),
                        "smart": s_.get("smartStatus"), "en_rotation": d.get("isSpinning"),
                        "libre_go": round(int(libre) / 1024 / 1024, 1) if libre not in (None, "") else None,
                        "utilise_pct": round(100 * int(d["fsUsed"]) / int(d["fsSize"])) if d.get("fsSize") and d.get("fsUsed") else None}
                disques.append(info)
                nom = info["nom"] or d.get("device")
                if info["etat"] not in ("DISK_OK", None):
                    alertes.append(f"{nom} : état {info['etat']}")
                if temp and d.get("critical") and temp >= d["critical"]:
                    alertes.append(f"{nom} : {temp} °C (critique)")
                elif temp and d.get("warning") and temp >= d["warning"]:
                    alertes.append(f"{nom} : {temp} °C (chaud)")
                if info["erreurs"]:
                    alertes.append(f"{nom} : {info['erreurs']} erreur(s) de lecture/écriture")
                if info["smart"] and info["smart"] != "OK":
                    alertes.append(f"{nom} : SMART {info['smart']}")
                if plein_pct and info["utilise_pct"] and info["utilise_pct"] >= plein_pct:
                    alertes.append(f"{nom} : plein à {info['utilise_pct']} %")
        parite = a.get("parityCheckStatus") or {}
        if parite.get("errors"):
            alertes.append(f"parité : {parite['errors']} erreur(s) au dernier contrôle")
        age = age_jours(parite.get("date"))
        parite["age_jours"] = age
        if parite_max_jours and age is not None and age > parite_max_jours and not parite.get("running") \
                and any(d["type"] == "parity" for d in disques):
            alertes.append(f"parité : dernier contrôle il y a {age} jours")
        if a.get("state") and a["state"] != "STARTED":
            alertes.append(f"array : {a['state']}")
        cap = ((a.get("capacity") or {}).get("kilobytes") or {})
        to = lambda v: round(int(v) / 1024 / 1024 / 1024, 2) if v not in (None, "") else None  # noqa: E731
        capacite = {"total": to(cap.get("total")), "libre": to(cap.get("free")), "utilise": to(cap.get("used"))}
        if capacite["total"] and capacite["libre"] is not None:
            capacite["libre_pct"] = round(100 * capacite["libre"] / capacite["total"])
            if libre_min_pct and capacite["libre_pct"] < libre_min_pct:
                alertes.append(f"array : plus que {capacite['libre_pct']} % d'espace libre ({capacite['libre']} To)")
        return {"array": a.get("state"), "capacite_to": capacite, "parite": parite, "disques": disques, "alertes": alertes}

    # --- mises à jour des images Docker -----------------------------------------------------------------
    # Unraid garde l'état des mises à jour (le contrôle qu'il fait lui-même, page Docker) : on le lit seulement.
    REQUETES_MAJ = [
        ("statuts", "query { docker { containerUpdateStatuses { name updateStatus } } }"),
        ("conteneurs", "query { docker { containers { names isUpdateAvailable } } }"),
    ]

    async def mises_a_jour(self) -> list[str]:
        """Noms des conteneurs dont l'image a une mise à jour disponible (triés)."""
        erreurs = []
        for forme, requete in self.REQUETES_MAJ:
            try:
                docker = (await self._graphql(requete)).get("docker") or {}
            except RuntimeError as e:
                erreurs.append(str(e))
                continue
            if forme == "statuts" and docker.get("containerUpdateStatuses") is not None:
                noms = [x.get("name") for x in docker["containerUpdateStatuses"]
                        if (x.get("updateStatus") or "").upper() == "UPDATE_AVAILABLE"]
            elif forme == "conteneurs" and docker.get("containers") is not None:
                noms = [(x.get("names") or [""])[0] for x in docker["containers"] if x.get("isUpdateAvailable")]
            else:
                erreurs.append(f"pas de {forme} dans la réponse")
                continue
            return sorted({(n or "").lstrip("/") for n in noms if n}, key=str.lower)
        raise RuntimeError("mises à jour Docker illisibles (Unraid 7.2+ requis) : " + " | ".join(erreurs[-2:]))

    async def mettre_a_jour(self, nom: str, cle_actions: str, timeout: float = 900) -> dict:
        """Télécharge la nouvelle image et recrée le conteneur (comme « update » dans Unraid). 2e clé, droits Docker."""
        if not cle_actions:
            raise PermissionError("pas de clé Unraid pour les actions Docker (UNRAID_CLE_ACTIONS)")
        c = await self.trouver(nom)
        if not c:
            raise ValueError(f"conteneur « {nom} » introuvable ou ambigu")
        data = await self._graphql("mutation($id: PrefixedID!) { docker { updateContainer(id: $id) { id state status } } }",
                                   {"id": c["id"]}, cle=cle_actions, timeout=timeout)
        r = ((data.get("docker") or {}).get("updateContainer")) or {}
        return {"nom": c["nom"], "etat": (r.get("state") or "").lower(), "statut": r.get("status", "")}

    # --- redémarrer un conteneur (2e clé, droits Docker) --------------------------------------------------
    async def redemarrer(self, nom: str, cle_actions: str) -> dict:
        if not cle_actions:
            raise PermissionError("pas de clé Unraid pour les actions Docker (UNRAID_CLE_ACTIONS)")
        c = await self.trouver(nom)
        if not c:
            raise ValueError(f"conteneur « {nom} » introuvable ou ambigu")
        data = await self._graphql("mutation($id: PrefixedID!) { docker { restart(id: $id) { id state status } } }",
                                   {"id": c["id"]}, cle=cle_actions)
        r = ((data.get("docker") or {}).get("restart")) or {}
        return {"nom": c["nom"], "etat": (r.get("state") or "").lower(), "statut": r.get("status", "")}
