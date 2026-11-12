import pytest


@pytest.fixture(autouse=True)
def _cartes_sans_attente(monkeypatch):
    """Le faux Home Assistant ne change jamais d'état : inutile d'attendre qu'il montre un réglage."""
    monkeypatch.setattr("assistant.maison.cartes.ATTENTE_REGLAGE", 0)
