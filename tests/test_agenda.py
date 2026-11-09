"""Agenda CalDAV avec un faux serveur (Radicale imité)."""

import asyncio
from datetime import date, datetime, timedelta

import httpx

from assistant.agenda import LOCAL, Agenda, decrire, ics_evenement, lire_evenements

ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:1@test\r
DTSTART;TZID=Europe/Paris:20261112T143000\r
DTEND;TZID=Europe/Paris:20261112T153000\r
SUMMARY:Dentiste\, Dr Martin\r
LOCATION:Douai\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:2@test\r
DTSTART;VALUE=DATE:20261113\r
SUMMARY:Anniversaire de\r
  Léa\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_lecture_ics():
    e = lire_evenements(ICS)
    assert e[0]["titre"] == "Dentiste, Dr Martin" and e[0]["lieu"] == "Douai"
    assert e[0]["debut"].astimezone(LOCAL).hour in (14, 13)          # Paris, selon le fuseau de la machine
    assert e[1]["journee"] and e[1]["titre"] == "Anniversaire de Léa"   # ligne pliée recollée


def test_creation_ics_avec_rappel():
    uid, ics = ics_evenement("Courses", datetime(2026, 11, 12, 9, 0, tzinfo=LOCAL),
                             datetime(2026, 11, 12, 10, 0, tzinfo=LOCAL), rappel_min=15)
    assert "SUMMARY:Courses" in ics and "TRIGGER:-PT15M" in ics and uid in ics
    assert lire_evenements(ics)[0]["titre"] == "Courses"


def test_decrire():
    j = date(2026, 11, 12)
    e = {"debut": datetime(2026, 11, 12, 14, 30, tzinfo=LOCAL), "titre": "Dentiste", "lieu": "Douai"}
    assert decrire(e, j) == "aujourd'hui à 14h30 : Dentiste (Douai)"
    assert decrire({**e, "debut": e["debut"] + timedelta(days=1)}, j).startswith("demain")


def test_client_caldav():
    vus = []

    def repondre(req: httpx.Request) -> httpx.Response:
        vus.append((req.method, req.url.path, req.headers.get("authorization", "")[:5]))
        if req.method == "REPORT":
            corps = f"<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'><d:response><d:propstat><d:prop><c:calendar-data>{ICS}</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>"
            return httpx.Response(207, text=corps)
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(405)
    a = Agenda("http://radicale:5232/benjamin/agenda", "benjamin", "secret", transport=httpx.MockTransport(repondre))
    evts = asyncio.run(a.evenements(datetime(2026, 11, 12, tzinfo=LOCAL), datetime(2026, 11, 20, tzinfo=LOCAL)))
    assert [e["titre"] for e in evts] == ["Dentiste, Dr Martin", "Anniversaire de Léa"]
    uid = asyncio.run(a.ajouter("Courses", datetime(2026, 11, 14, 9, 0, tzinfo=LOCAL)))
    assert vus[-1][0] == "PUT" and vus[-1][1].startswith("/benjamin/agenda/") and vus[-1][2] == "Basic"
    assert uid.endswith("@assistant")


def test_supprimer_un_evenement():
    vus = []

    def repondre(req: httpx.Request) -> httpx.Response:
        vus.append((req.method, req.url.path))
        if req.method == "REPORT":
            corps = ("<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>"
                     f"<d:response><d:href>/remote.php/dav/calendars/ben/perso/abc.ics</d:href><d:propstat><d:prop>"
                     f"<c:calendar-data>{ICS}</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>")
            return httpx.Response(207, text=corps)
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(405)
    a = Agenda("https://cloud.exemple.fr/remote.php/dav/calendars/ben/perso/", "ben", "x",
               transport=httpx.MockTransport(repondre))
    evts = asyncio.run(a.evenements(datetime(2026, 11, 12, tzinfo=LOCAL), datetime(2026, 11, 20, tzinfo=LOCAL)))
    assert evts[0]["href"] == "/remote.php/dav/calendars/ben/perso/abc.ics"
    asyncio.run(a.supprimer(evts[0]["href"]))
    assert vus[-1] == ("DELETE", "/remote.php/dav/calendars/ben/perso/abc.ics")
    import pytest
    with pytest.raises(PermissionError):                       # jamais hors du calendrier configuré
        asyncio.run(a.supprimer("/remote.php/dav/calendars/autre/agenda/x.ics"))
