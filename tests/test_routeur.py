import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.config import charger
from assistant.eval_routeur import charger_phrases
from assistant.fournisseurs import Fournisseur, FournisseurIndisponible, Registre
from assistant.main import creer_app
from assistant.routeur import Routeur

CFG = charger(env={}, fichier=Path("inexistant.yaml"))


class Classeur(Fournisseur):
    """Faux modèle : renvoie une réponse fixe, ou attend, ou tombe en panne."""

    def __init__(self, ident, reponse="SIMPLE", attente=0.0, panne=False):
        super().__init__(ident, ident)
        self.reponse, self.attente, self.panne = reponse, attente, panne
        self.appels = []

    async def repondre(self, messages, options=None):
        self.appels.append((messages, options))
        if self.panne:
            raise FournisseurIndisponible(self.ident)
        await asyncio.sleep(self.attente)
        return self.reponse

    async def flux(self, messages):
        yield await self.repondre(messages)

    async def disponible(self):
        return not self.panne


def routeur(**kw):
    rapide = Classeur("rapide", **kw)
    reg = Registre({"r": rapide, "c": Classeur("claude", "Réponse longue")},
                   {"rapide": "r", "raisonnement": "c", "secours": "r"})
    return Routeur(CFG.routeur, reg), rapide


def decider(r, texte, precedent=""):
    return asyncio.run(r.decider(texte, precedent))


@pytest.mark.parametrize("texte", [
    "Allume le salon", "éteins la cuisine", "Peux-tu ferme les volets",
    "Mets un minuteur de 5 minutes", "Quelle heure est-il ?", "Merci !", "bonne nuit",
])
def test_regles_simples(texte):
    r, classeur = routeur(reponse="COMPLEXE")
    d = decider(r, texte)
    assert (d.categorie, d.role) == ("simple", "rapide")
    assert d.raison.startswith("règle") and not classeur.appels


@pytest.mark.parametrize("texte", [
    "Explique-moi la photosynthèse", "Pourquoi le ciel est bleu ?", "Résume cet article",
    "Aide-moi à préparer un discours", "Quelle est la différence entre ZFS et Btrfs ?",
])
def test_regles_complexes(texte):
    r, classeur = routeur(reponse="SIMPLE")
    d = decider(r, texte)
    assert (d.categorie, d.role) == ("complexe", "raisonnement") and not classeur.appels


def test_message_long_complexe():
    r, _ = routeur(reponse="SIMPLE")
    assert decider(r, "x " * 200).raison == "longueur"


def test_classeur_quand_aucune_regle():
    r, classeur = routeur(reponse="COMPLEXE\n")
    r.cfg = r.cfg.model_copy(update={"classeur": "rapide"})     # désactivé par défaut, réactivable
    d = decider(r, "Qu'offrir à un ami randonneur ?", precedent="avant")
    assert (d.categorie, d.raison) == ("complexe", "classeur")
    msgs, options = classeur.appels[0]
    assert options == {"temperature": 0, "max_tokens": 4}
    assert "Demande précédente : avant" in msgs[1].content


def test_classeur_en_panne_donne_defaut():
    r, _ = routeur(panne=True)
    d = decider(r, "Qu'offrir à un ami randonneur ?")
    assert d.categorie == "complexe" and d.raison.startswith("défaut")


def test_classeur_indecis_donne_defaut():
    r, _ = routeur(reponse="peut-être")
    assert decider(r, "Qu'offrir à un ami randonneur ?").raison.startswith("défaut")


def test_classeur_trop_lent_donne_defaut():
    r, _ = routeur(attente=5)
    r.cfg = r.cfg.model_copy(update={"delai_classeur_ms": 100})
    assert decider(r, "Qu'offrir à un ami randonneur ?").raison.startswith("défaut")


def test_jeu_d_evaluation_regles_jamais_fausses():
    """Sur le jeu d'évaluation, une règle qui s'applique ne doit jamais se tromper."""
    r, _ = routeur()
    for texte, attendu in charger_phrases():
        res = r.par_regles(texte)
        if res:
            assert res[0] == attendu, texte


def test_config_regle_invalide(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("routeur:\n  regles_simples: ['(']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="règle invalide"):
        charger(env={}, fichier=f)


def test_api_auto_route_et_flux():
    r, _ = routeur()
    app = creer_app(CFG, r.registre)
    c = TestClient(app)
    lignes = [json.loads(l) for l in c.post("/api/chat", json={"message": "Explique la marée", "flux": True}).text.splitlines()]
    assert lignes[0]["route"]["categorie"] == "complexe"
    assert lignes[-1]["fournisseur"] == "claude"
    assert c.post("/api/route", json={"message": "Allume le salon"}).json()["role"] == "rapide"


def test_sans_classeur_par_defaut_tout_va_a_claude():
    r, local = routeur(reponse="SIMPLE\n")
    d = decider(r, "Quelle est la capitale de l'Afrique ?")
    assert d.categorie == "complexe" and not local.appels
    assert decider(r, "Bonjour").categorie == "simple"                 # salutations : modèle local
