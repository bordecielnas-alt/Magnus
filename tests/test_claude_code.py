"""Fournisseur Claude Code, testé avec un faux binaire qui imite `claude -p --output-format stream-json`."""

import asyncio
import json
import sys
import textwrap

import pytest

from assistant.config import charger
from assistant.fournisseurs import FournisseurIndisponible, Message
from assistant.fournisseurs import claude_code as cc

FAUX = textwrap.dedent('''
    import json, sys, time
    mode = sys.argv[1]
    invite = sys.stdin.read()
    def ecrire(o): print(json.dumps(o), flush=True)
    ecrire({"type": "system", "subtype": "init"})
    if mode == "lent":
        time.sleep(5)
    if mode == "ok":
        for mot in ["Bon", "jour ", invite[:5]]:
            ecrire({"type": "stream_event", "event": {"type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": mot}}})
        ecrire({"type": "result", "is_error": False, "result": "Bonjour"})
    elif mode == "sans-flux":
        ecrire({"type": "result", "is_error": False, "result": "Réponse entière"})
    elif mode == "erreur":
        ecrire({"type": "result", "is_error": True, "result": "Not logged in"})
    # mode "muet" : se termine sans résultat
''')


class Faux(cc.FournisseurClaudeCode):
    def __init__(self, tmp_path, mode, timeout=10):
        super().__init__("claude", "sonnet", commande="inutile", timeout=timeout)
        self.script = tmp_path / "faux_claude.py"
        self.script.write_text(FAUX, encoding="utf-8")
        self.mode = mode
        self.args_vus = None

    def _arguments(self, binaire, systeme, outils=None):
        self.args_vus = super()._arguments(binaire, systeme, outils)
        return [sys.executable, str(self.script), self.mode]


@pytest.fixture(autouse=True)
def binaire_present(monkeypatch):
    monkeypatch.setattr(cc, "resoudre_binaire", lambda c: "claude")


MSGS = [Message("system", "Tu es Akatosh."), Message("user", "salut")]


def lire(f):
    async def go():
        return [m async for m in f.flux(MSGS)]
    return asyncio.run(go())


def test_flux_texte(tmp_path):
    f = Faux(tmp_path, "ok")
    assert "".join(lire(f)) == "Bonjour salut"


def test_arguments_sans_outils_et_consigne(tmp_path):
    f = Faux(tmp_path, "ok")
    lire(f)
    a = f.args_vus
    assert a[a.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in a and "--no-session-persistence" in a
    consigne = a[a.index("--system-prompt") + 1]
    assert consigne.startswith("Tu es Akatosh.") and cc.NOTE_SANS_OUTILS in consigne
    assert a[a.index("--model") + 1] == "sonnet"


def test_resultat_sans_flux(tmp_path):
    assert lire(Faux(tmp_path, "sans-flux")) == ["Réponse entière"]


def test_erreur_devient_indisponible(tmp_path):
    with pytest.raises(FournisseurIndisponible, match="Not logged in"):
        lire(Faux(tmp_path, "erreur"))


def test_sans_resultat(tmp_path):
    with pytest.raises(FournisseurIndisponible):
        lire(Faux(tmp_path, "muet"))


def test_delai_depasse(tmp_path):
    with pytest.raises(FournisseurIndisponible, match="pas de réponse"):
        lire(Faux(tmp_path, "lent", timeout=1))


def test_binaire_absent(monkeypatch):
    monkeypatch.setattr(cc, "resoudre_binaire", lambda c: None)
    f = cc.FournisseurClaudeCode("claude", "sonnet")
    with pytest.raises(FournisseurIndisponible, match="introuvable"):
        lire(f)
    assert asyncio.run(f.disponible()) is False


def test_invite_avec_historique():
    systeme, invite = cc.construire_invite([
        Message("system", "S"), Message("user", "a"), Message("assistant", "b"), Message("user", "c"),
    ])
    assert systeme == "S"
    assert "Utilisateur : a" in invite and "Assistant : b" in invite and invite.endswith("c")


def test_env_sans_cle_api_ni_session():
    env = cc.env_enfant({"PATH": "x", "ANTHROPIC_API_KEY": "k", "CLAUDECODE": "1",
                         "CLAUDE_CODE_ENTRYPOINT": "y", "CLAUDE_CONFIG_DIR": "garde"})
    assert env == {"PATH": "x", "CLAUDE_CONFIG_DIR": "garde"}


def test_config_par_defaut_raisonnement_sur_claude():
    from pathlib import Path
    cfg = charger(env={}, fichier=Path("inexistant.yaml"))
    assert cfg.fournisseurs[cfg.roles["raisonnement"]].type == "claude_code"
    assert cfg.fournisseurs[cfg.roles["secours"]].type == "openai"


def test_config_openai_sans_url_refusee(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("fournisseurs:\n  x:\n    type: openai\n    modele: m\n", encoding="utf-8")
    with pytest.raises(ValueError):
        charger(env={}, fichier=f)
