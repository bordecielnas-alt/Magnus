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

import difflib
import re
import struct
import unicodedata
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


# Signatures connues dans les journaux : (motif, cause probable en une phrase). Ordre = priorité.
SIGNATURES = [
    (re.compile(r"out of memory|oom[- ]?kill|cannot allocate memory|memoryerror|java\.lang\.OutOfMemoryError", re.I),
     "manque de mémoire : le conteneur a dépassé la RAM disponible ou sa limite"),
    (re.compile(r"no space left on device|disk quota exceeded", re.I),
     "disque plein : plus de place pour écrire (cache, appdata ou image Docker)"),
    (re.compile(r"address already in use|bind: .*in use|port is already allocated", re.I),
     "port déjà pris par un autre conteneur ou service"),
    (re.compile(r"permission denied|operation not permitted|eacces|read-only file system", re.I),
     "problème de droits sur un dossier ou un fichier (PUID/PGID, appdata, montage en lecture seule)"),
    (re.compile(r"connection refused|could not connect|can't connect|connect(ion)? timed out|"
                r"name or service not known|no route to host|temporary failure in name resolution|getaddrinfo", re.I),
     "un service dont il dépend est injoignable (base de données, autre conteneur, réseau ou DNS)"),
    (re.compile(r"database is locked|database disk image is malformed|corrupt", re.I),
     "base de données verrouillée ou corrompue"),
    # moins sûres : cherchées seulement dans les lignes d'erreur
    (re.compile(r"no such file or directory|not found|cannot find|missing", re.I),
     "fichier ou dossier attendu absent (chemin de montage, configuration manquante)"),
    (re.compile(r"invalid|parse error|syntax error|yaml|config(uration)? error", re.I),
     "configuration invalide (fichier de config ou variable d'environnement)"),
    (re.compile(r"segmentation fault|core dumped|sigsegv", re.I),
     "le programme s'est planté tout seul (erreur interne, souvent un bug de la version de l'image)"),
]

SURES = 6          # les 6 premières signatures sont assez précises pour être cherchées dans tout le journal

CODES_SORTIE = {
    137: "tué par le système (SIGKILL) : souvent le manque de mémoire, ou un arrêt forcé",
    139: "plantage interne du programme (erreur de segmentation)",
    143: "arrêté proprement (SIGTERM), par exemple par Unraid ou une mise à jour",
    126: "la commande de démarrage n'est pas exécutable (droits)",
    127: "la commande de démarrage est introuvable (image ou configuration)",
    1: "le programme s'est arrêté sur une erreur (voir les journaux)",
}


def causes_probables(diag: dict) -> list[str]:
    """Causes probables d'un plantage, tirées de l'état et des journaux (de la plus sûre à la moins sûre)."""
    causes: list[str] = []
    if diag.get("oom"):
        causes.append("manque de mémoire : Docker a tué le conteneur (OOMKilled)")
    erreurs = diag.get("erreurs_recentes") or []
    toutes = erreurs + (diag.get("dernieres_lignes") or [])
    for i, (motif, cause) in enumerate(SIGNATURES):
        if i == 0 and diag.get("oom"):
            continue                    # mémoire : déjà dit par OOMKilled
        lignes = toutes if i < SURES else erreurs
        if cause not in causes and any(motif.search(l) for l in lignes):
            causes.append(cause)
    code = diag.get("code_sortie")
    if code is None:
        m = re.search(r"Exited \((\d+)\)", diag.get("statut") or "")
        code = int(m.group(1)) if m else None
    if code and code in CODES_SORTIE and not (code == 137 and diag.get("oom")):
        causes.append(f"code de sortie {code} : {CODES_SORTIE[code]}")
    if (diag.get("redemarrages") or 0) >= 3:
        causes.append(f"il redémarre en boucle ({diag['redemarrages']} redémarrages) : le problème revient à chaque démarrage")
    if diag.get("sante") == "unhealthy":
        causes.append("il tourne mais son contrôle de santé échoue (service bloqué ou trop lent)")
    return causes[:4]


RE_PREFIXE_CONTENEUR = re.compile(r"^(?:(?:le|la|les|l'|du|des|de)\s*)?(?:dockers?|conteneurs?|containers?)\s*", re.I)


def normaliser_nom(nom: str) -> str:
    """« Le docker MiTube » -> « mitube » ; « Dockerplex » -> « plex » : minuscules, sans accents ni ponctuation."""
    t = unicodedata.normalize("NFKD", nom.strip()).encode("ascii", "ignore").decode().lower()
    t = RE_PREFIXE_CONTENEUR.sub("", t)
    return re.sub(r"[^a-z0-9]", "", t)


def choisir_conteneur(nom: str, liste: list[dict]) -> dict | None:
    """Le conteneur désigné : nom exact, puis inclus, puis approchant (transcription vocale : MiTube -> MeTube).
    None si rien ne correspond ou si plusieurs correspondent autant."""
    cible = normaliser_nom(nom)
    if not cible:
        return None
    noms = {c["nom"]: normaliser_nom(c["nom"]) for c in liste}
    exact = [c for c in liste if noms[c["nom"]] == cible]
    if exact:
        return exact[0] if len(exact) == 1 else None
    inclus = [c for c in liste if cible in noms[c["nom"]] or (len(noms[c["nom"]]) >= 4 and noms[c["nom"]] in cible)]
    if inclus:
        return inclus[0] if len(inclus) == 1 else None
    scores = sorted(((difflib.SequenceMatcher(None, cible, noms[c["nom"]]).ratio(), c) for c in liste),
                    key=lambda x: -x[0])
    if scores and scores[0][0] >= 0.75 and (len(scores) == 1 or scores[1][0] < scores[0][0] - 0.05):
        return scores[0][1]
    return None


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
        return choisir_conteneur(nom, await self.conteneurs())

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
        } | ({} if r.status_code == 200 else {"remarque": f"journaux illisibles (HTTP {r.status_code})"})

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
        # facultatif : socket ou docker-socket-proxy en lecture seule, seulement pour lire les journaux
        self.journaux: Docker | None = None

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

    async def _graphql(self, requete: str, variables: dict | None = None, cle: str | None = None) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport,
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
        base = {"nom": c["nom"], "image": c["image"], "etat": c["etat"], "statut": c["statut"], "sante": c["sante"],
                "demarrage_auto": c.get("demarrage_auto")}
        if self.journaux and self.journaux.configure:
            try:
                return {**base, **{k: v for k, v in (await self.journaux.diagnostic(c["nom"], lignes)).items() if v not in ("", None)}}
            except Exception as e:  # noqa: BLE001 - journaux en plus : l'état Unraid suffit pour répondre
                return {**base, "remarque": f"journaux Docker illisibles ({e.__class__.__name__}) ; "
                                            "diagnostic à partir de l'état et du statut."}
        return {**base, "remarque": "API Unraid : pas de journaux disponibles (ajouter docker.url pour les lire) ; "
                                    "diagnostic à partir de l'état et du statut."}


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

    async def serveur(self) -> dict:
        """État de l'array : disques (état, température, erreurs, espace), SMART, parité, alertes."""
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
                if info["utilise_pct"] and info["utilise_pct"] >= 95:
                    alertes.append(f"{nom} : plein à {info['utilise_pct']} %")
        parite = a.get("parityCheckStatus") or {}
        if parite.get("errors"):
            alertes.append(f"parité : {parite['errors']} erreur(s) au dernier contrôle")
        if a.get("state") and a["state"] != "STARTED":
            alertes.append(f"array : {a['state']}")
        cap = ((a.get("capacity") or {}).get("kilobytes") or {})
        to = lambda v: round(int(v) / 1024 / 1024 / 1024, 2) if v not in (None, "") else None  # noqa: E731
        return {"array": a.get("state"), "capacite_to": {"total": to(cap.get("total")), "libre": to(cap.get("free")),
                                                         "utilise": to(cap.get("used"))},
                "parite": parite, "disques": disques, "alertes": alertes}

    # --- démarrer, arrêter, redémarrer un conteneur (2e clé, droits Docker) -----------------------------
    MUTATIONS = {"redemarrer": "restart", "demarrer": "start", "arreter": "stop"}

    async def agir(self, nom: str, action: str, cle_actions: str) -> dict:
        if action not in self.MUTATIONS:
            raise ValueError(f"action inconnue : {action}")
        if not cle_actions:
            raise PermissionError("pas de clé Unraid pour les actions Docker (UNRAID_CLE_ACTIONS)")
        c = await self.trouver(nom)
        if not c:
            raise ValueError(f"conteneur « {nom} » introuvable ou ambigu")

        async def muter(m: str) -> dict:
            data = await self._graphql(f"mutation($id: PrefixedID!) {{ docker {{ {m}(id: $id) {{ id state status }} }} }}",
                                       {"id": c["id"]}, cle=cle_actions)
            return ((data.get("docker") or {}).get(m)) or {}

        m = self.MUTATIONS[action]
        try:
            r = await muter(m)
        except RuntimeError as e:
            # certaines versions de l'API Unraid n'ont pas « restart » : arrêt puis démarrage
            if m != "restart" or not re.search(r"cannot query field|unknown field|restart", str(e), re.I):
                raise
            await muter("stop")
            r = await muter("start")
        return {"nom": c["nom"], "etat": (r.get("state") or "").lower(), "statut": r.get("status", "")}

    async def redemarrer(self, nom: str, cle_actions: str) -> dict:
        return await self.agir(nom, "redemarrer", cle_actions)
