#!/usr/bin/env python3
"""Automatische Reservierung eines Trautermins im Traukalender Wiesbaden.

Sucht in den konfigurierten Monaten nach Terminen an den gewuenschten
Wochentagen in den gewuenschten Trauorten und reserviert den passendsten
davon - standardmaessig den spaetestmoeglichen.

ACHTUNG: Eine Reservierung ist verbindlich. Sie wird beim Standesamt
Wiesbaden hinterlegt und laesst sich nicht per Skript zuruecknehmen, sondern
nur telefonisch oder per E-Mail beim Standesamt.

Deshalb drei Stufen, aufsteigend nach Eingriffstiefe:

  --trocken (Standard)  Nur suchen und anzeigen. Keinerlei Nebenwirkung.
  --probe               Kompletter Durchlauf bis zur Bestaetigungsseite, dort
                        Abbruch und Freigabe des Termins. Haelt den Termin fuer
                        wenige Sekunden.
  --wirklich-buchen     Reserviert verbindlich. Zusaetzlich muss die
                        Umgebungsvariable TK_BUCHUNG_AKTIV auf 1 stehen.

Personenbezogene Daten kommen ausschliesslich aus Umgebungsvariablen, damit
sie nicht im Repository landen. Siehe .env.example.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from typing import Any

from traukalender_monitor import (
    BASE,
    TRAUORTE,
    VORLAUF_MONATE,
    benachrichtigen,
    config_aus_umgebung,
    deutsches_datum,
    monat_abfragen,
    monate_parsen,
    plus_monate,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

WOCHENTAGE = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GEBUCHT = 20


# --------------------------------------------------------------------------
# Daten
# --------------------------------------------------------------------------


@dataclass
class Person:
    anrede: str = ""
    vorname: str = ""
    name: str = ""
    geburtsname: str = ""
    strasse_hausnummer: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "DE"
    staatsangehoerigkeit: str = "deutsch"

    def fehlend(self) -> list[str]:
        pflicht = ["vorname", "name", "strasse_hausnummer", "plz", "ort",
                   "land", "staatsangehoerigkeit"]
        return [f for f in pflicht if not getattr(self, f).strip()]


@dataclass
class Kontakt:
    email: str = ""
    telefon: str = ""

    def fehlend(self) -> list[str]:
        return [f for f in ("email", "telefon") if not getattr(self, f).strip()]


@dataclass
class Slot:
    trauort: int
    datum: str
    von: str
    bis: str
    sts: str = ""
    ets: str = ""
    termin_id: str = ""
    kosten: str = ""

    @property
    def trauort_name(self) -> str:
        return TRAUORTE.get(self.trauort, f"Trauort {self.trauort}")

    @property
    def kennung(self) -> str:
        return f"{self.trauort}|{self.datum}|{self.von}"

    def beschreibung(self) -> str:
        return (f"{deutsches_datum(self.datum)}, {self.von} - {self.bis} Uhr, "
                f"{self.trauort_name}")

    def als_dict(self) -> dict[str, Any]:
        return {
            "trauort": self.trauort, "trauort_name": self.trauort_name,
            "datum": self.datum, "von": self.von, "bis": self.bis,
            "sts": self.sts, "ets": self.ets, "termin_id": self.termin_id,
        }


def person_aus_dict(roh: dict[str, Any]) -> Person:
    hole = lambda *namen: next(
        (str(roh[n]).strip() for n in namen if roh.get(n) not in (None, "")), ""
    )
    return Person(
        anrede=hole("anrede"),
        vorname=hole("vorname"),
        name=hole("name", "nachname"),
        geburtsname=hole("geburtsname"),
        strasse_hausnummer=hole("strasse", "strasse_hausnummer"),
        plz=hole("plz"),
        ort=hole("ort"),
        land=hole("land") or "DE",
        staatsangehoerigkeit=hole("staat", "staatsangehoerigkeit") or "deutsch",
    )


def personendaten_aus_umgebung() -> tuple[Person, Person, Kontakt]:
    """Personendaten aus der Umgebung lesen.

    Bevorzugt wird ein einzelnes JSON in TK_PERSONENDATEN - das ist als
    GitHub-Secret deutlich handlicher als zwei Dutzend Einzelwerte. Als
    Rueckfallebene funktionieren weiterhin TK_P1_*, TK_P2_* und TK_KONTAKT_*.
    """
    roh = os.environ.get("TK_PERSONENDATEN", "").strip()
    if roh:
        try:
            daten = json.loads(roh)
        except json.JSONDecodeError as err:
            raise SystemExit(f"TK_PERSONENDATEN ist kein gueltiges JSON: {err}")
        kontakt_roh = daten.get("kontakt", {})
        return (
            person_aus_dict(daten.get("partner1", {})),
            person_aus_dict(daten.get("partner2", {})),
            Kontakt(
                email=str(kontakt_roh.get("email", "")).strip(),
                telefon=str(kontakt_roh.get("telefon", "")).strip(),
            ),
        )

    def person(praefix: str) -> Person:
        holen = lambda feld, standard="": os.environ.get(f"{praefix}_{feld}", standard).strip()
        return Person(
            anrede=holen("ANREDE"),
            vorname=holen("VORNAME"),
            name=holen("NAME"),
            geburtsname=holen("GEBURTSNAME"),
            strasse_hausnummer=holen("STRASSE"),
            plz=holen("PLZ"),
            ort=holen("ORT"),
            land=holen("LAND", "DE"),
            staatsangehoerigkeit=holen("STAAT", "deutsch"),
        )
    kontakt = Kontakt(
        email=os.environ.get("TK_KONTAKT_EMAIL", "").strip(),
        telefon=os.environ.get("TK_KONTAKT_TELEFON", "").strip(),
    )
    return person("TK_P1"), person("TK_P2"), kontakt


# --------------------------------------------------------------------------
# HTTP-Sitzung
# --------------------------------------------------------------------------


class Sitzung:
    """Eine Browsersitzung durch den Assistenten, inklusive Cookies."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.op.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept-Language", "de-DE,de;q=0.9"),
        ]
        self.termin_gehalten = False

    def _oeffnen(self, req: urllib.request.Request) -> str:
        try:
            with self.op.open(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            # Der Kalender antwortet teils mit 403 und trotzdem gueltigem Inhalt.
            koerper = err.read().decode("utf-8", "replace")
            if koerper.strip():
                return koerper
            raise

    def get(self, url: str, ajax: bool = False) -> str:
        kopf = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
        return self._oeffnen(urllib.request.Request(url, headers=kopf))

    def post(self, url: str, daten: dict[str, Any]) -> str:
        koerper = urllib.parse.urlencode(daten, doseq=True).encode()
        return self._oeffnen(urllib.request.Request(
            url, data=koerper,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ))

    def ajax(self, com: str, **params: Any) -> str:
        query = {
            "bereich": "portal", "modul_id": "101",
            "klasse": "tko_buergeransicht", "com": com,
        }
        query.update({k: str(v) for k, v in params.items()})
        return self.get(f"{BASE}/common/ajax.php?" + urllib.parse.urlencode(query), ajax=True)

    def termin_freigeben(self) -> None:
        """Haelt das Skript einen Termin, muss er in jedem Fehlerfall zurueck."""
        if not self.termin_gehalten:
            return
        try:
            self.ajax("termin_abwaehlen")
            self.termin_gehalten = False
        except Exception as err:
            print(f"[warn] Termin konnte nicht freigegeben werden: {err}", file=sys.stderr)


# --------------------------------------------------------------------------
# HTML-Formulare
# --------------------------------------------------------------------------


def formular(html: str, name: str | None = None) -> str | None:
    for treffer in re.finditer(r"<form[^>]*>.*?</form>", html, re.S):
        if name is None or f'name="{name}"' in treffer.group(0):
            return treffer.group(0)
    return None


def formularfelder(f: str) -> dict[str, str]:
    """Alle absendbaren Felder mit ihrem Vorgabewert.

    Wichtig: die Felder 'email' und 'url' ohne Praefix sind Spamfallen und
    muessen leer bleiben - sie werden hier bewusst mit uebernommen.
    """
    daten: dict[str, str] = {}
    for treffer in re.finditer(r"<input\b[^>]*>", f):
        tag = treffer.group(0)
        name = re.search(r'(?:^|\s)name="([^"]*)"', tag)
        if not name:
            continue
        typ = (re.search(r'type="([^"]*)"', tag) or [None, "text"])[1]
        wert = (re.search(r'value="([^"]*)"', tag) or [None, ""])[1]
        daten[name.group(1)] = "1" if typ == "checkbox" else unescape(wert)
    for treffer in re.finditer(r'<select\b[^>]*(?:^|\s)name="([^"]*)"[^>]*>(.*?)</select>',
                               f, re.S):
        gewaehlt = re.findall(r'<option[^>]*value="([^"]*)"[^>]*selected', treffer.group(2))
        daten[treffer.group(1)] = gewaehlt[0] if gewaehlt else ""
    for treffer in re.finditer(r'<textarea\b[^>]*(?:^|\s)name="([^"]*)"', f):
        daten.setdefault(treffer.group(1), "")
    return daten


def fehlermeldungen(html: str) -> list[str]:
    roh = re.findall(r'container-fehler">([^<]+)', html)
    return [unescape(x).strip() for x in roh if x.strip()]


# --------------------------------------------------------------------------
# Termine finden
# --------------------------------------------------------------------------


def tagesslots(sitzung: Sitzung, trauort: int, tag: str) -> list[Slot]:
    url = (f"{BASE}/de/buerger_liste_termine_ajax.html?"
           + urllib.parse.urlencode({"trauort": trauort, "ortsverwaltung": 0, "tag": tag}))
    html = sitzung.get(url, ajax=True)
    slots: list[Slot] = []
    for treffer in re.finditer(r'<div class="[^"]*button--termin[^"]*"([^>]*)>', html):
        attribute = dict(re.findall(r'data-(\w+)="([^"]*)"', treffer.group(1)))
        titel = re.search(r'title="([^"]*)"', treffer.group(1))
        zeiten = re.search(r"um (\d{2}:\d{2}) - (\d{2}:\d{2})", unescape(titel.group(1))) if titel else None
        if not zeiten:
            continue
        slots.append(Slot(
            trauort=int(attribute.get("trauort") or trauort),
            datum=tag,
            von=zeiten.group(1),
            bis=zeiten.group(2),
            sts=attribute.get("sts", ""),
            ets=attribute.get("ets", ""),
            termin_id=attribute.get("termin", ""),
        ))
    slots.sort(key=lambda s: s.von)
    return slots


def kandidaten(
    sitzung: Sitzung,
    trauorte: list[int],
    monate: list[tuple[int, int]],
    wochentage: set[int],
    heute: dt.date,
    nur_reservierbar: bool = True,
) -> list[Slot]:
    """Alle passenden Termine, spaetester zuerst."""
    max_portal = plus_monate(heute, VORLAUF_MONATE).isoformat()
    gefunden: list[Slot] = []
    for trauort in trauorte:
        for jahr, monat in monate:
            try:
                daten = monat_abfragen(trauort, jahr, monat, heute.isoformat(), max_portal)
            except RuntimeError as err:
                print(f"[warn] {TRAUORTE.get(trauort, trauort)} {jahr}-{monat:02d}: {err}",
                      file=sys.stderr)
                continue
            for tag in sorted(daten.get("appointments") or []):
                try:
                    datum = dt.date.fromisoformat(tag)
                except ValueError:
                    continue
                if datum.weekday() not in wochentage:
                    continue
                if nur_reservierbar and tag > max_portal:
                    continue
                gefunden += tagesslots(sitzung, trauort, tag)
    # Spaetester Termin zuerst: erst Datum, dann Uhrzeit.
    gefunden.sort(key=lambda s: (s.datum, s.von), reverse=True)
    return gefunden


# --------------------------------------------------------------------------
# Buchung
# --------------------------------------------------------------------------


class BuchungsFehler(RuntimeError):
    pass


def _schritt1(sitzung: Sitzung, trauort: int, pause: float = 1.5) -> None:
    html = sitzung.get(f"{BASE}/de/Start-159.html?trauort={trauort}")
    f = formular(html, "start")
    if not f:
        raise BuchungsFehler("Startseite ohne Formular")
    daten = formularfelder(f)
    # Jedes Formular traegt im versteckten Feld "date" den Zeitpunkt seiner
    # Ausgabe. Wird sofort abgeschickt, lehnt das Portal als Bot-Verdacht ab.
    time.sleep(pause)
    daten["101_tko_buergeransicht_start"] = "1"
    html = sitzung.post(f"{BASE}/de/Start-159.html", daten)
    if not formular(html, "formular-informationen"):
        raise BuchungsFehler(f"Schritt 1 abgelehnt: {fehlermeldungen(html)}")
    sitzung._schritt2_html = html  # type: ignore[attr-defined]


def _schritt2(sitzung: Sitzung, p1: Person, p2: Person) -> str:
    html = sitzung._schritt2_html  # type: ignore[attr-defined]
    f = formular(html, "formular-informationen")
    daten = formularfelder(f)
    for praefix, person in (("partner1", p1), ("partner2", p2)):
        for feld in ("anrede", "vorname", "name", "geburtsname",
                     "strasse_hausnummer", "plz", "ort", "land"):
            daten[f"tko_brautpaare_{praefix}_{feld}"] = getattr(person, feld)
        daten[f"tko_brautpaare_{praefix}_staatsangehoerigkeit"] = person.staatsangehoerigkeit
        daten[f"tko_brautpaare_{praefix}_staatsangehoerigkeit[]"] = person.staatsangehoerigkeit
    daten["101_tko_brautpaare_formular_informationen"] = "1"
    html = sitzung.post(f"{BASE}/de/Personendaten-66.html", daten)
    if "datumsauswahl" not in html:
        raise BuchungsFehler(f"Personendaten abgelehnt: {fehlermeldungen(html)}")
    return html


def _termin_waehlen(sitzung: Sitzung, slot: Slot, listen_html: str) -> str:
    attribute = re.search(r'<div id="liste-termine"[^>]*>', listen_html)
    vorlage = dict(re.findall(r'(data-[\w-]+)="([^"]*)"', attribute.group(0))) if attribute else {}
    antwort = sitzung.ajax(
        "termin_auswaehlen",
        trauort=slot.trauort,
        termin=slot.termin_id,
        sts=slot.sts,
        ets=slot.ets,
        modul_template=vorlage.get("data-template", ""),
        modul_texte=vorlage.get("data-text", "traukalender/texte_buergeransicht"),
        sprache_kuerzel=vorlage.get("data-language", "de"),
    )
    try:
        ergebnis = json.loads(antwort)
    except json.JSONDecodeError:
        raise BuchungsFehler(f"Terminauswahl lieferte kein JSON: {antwort[:200]!r}")
    if not ergebnis.get("selectable") or not ergebnis.get("href"):
        raise BuchungsFehler("Termin ist nicht mehr waehlbar - vermutlich vergeben")
    sitzung.termin_gehalten = True
    return ergebnis["href"]


def _schritt4(sitzung: Sitzung, weiter_url: str, kontakt: Kontakt, pause: float = 1.5) -> str:
    html = sitzung.get(weiter_url)
    f = formular(html, "formular-brautpaar")
    if not f:
        raise BuchungsFehler(f"Schritt 'Weitere Daten' nicht erreicht: {fehlermeldungen(html)}")
    daten = formularfelder(f)
    time.sleep(pause)  # siehe _schritt1: Mindestabstand zur Formularausgabe
    daten["tko_brautpaare_email"] = kontakt.email
    daten["tko_brautpaare_email_wiederholen"] = kontakt.email
    daten["tko_brautpaare_telefon"] = kontakt.telefon
    daten["101_tko_brautpaare_formular_brautpaar"] = "1"
    html = sitzung.post(f"{BASE}/de/Weitere-Daten-73.html", daten)
    if not formular(html, "buchung-bestaetigen"):
        raise BuchungsFehler(f"Bestaetigungsseite nicht erreicht: {fehlermeldungen(html)}")
    return html


def _schritt5(sitzung: Sitzung, bestaetigung_html: str) -> str:
    f = formular(bestaetigung_html, "buchung-bestaetigen")
    daten = formularfelder(f)
    daten["101_tko_buergeransicht_buchung_bestaetigen"] = "1"
    html = sitzung.post(f"{BASE}/de/Bestaetigung-5.html", daten)
    if fehlermeldungen(html):
        raise BuchungsFehler(f"Reservierung abgelehnt: {fehlermeldungen(html)}")
    sitzung.termin_gehalten = False
    return html


def bestaetigung_auswerten(html: str) -> dict[str, str]:
    text = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()
    ergebnis: dict[str, str] = {"titel": ""}
    titel = re.search(r"<title>([^<]*)", html)
    if titel:
        ergebnis["titel"] = unescape(titel.group(1)).strip()
    nummer = re.search(r"(?:Vorgangsnummer|Reservierungsnummer)[:\s]*([A-Za-z0-9-]+)", text)
    if nummer:
        ergebnis["vorgangsnummer"] = nummer.group(1)
    ergebnis["auszug"] = text[:600]
    return ergebnis


def _bis_bestaetigungsseite(
    sitzung: Sitzung, slot: Slot, p1: Person, p2: Person, kontakt: Kontakt, pause: float
) -> str:
    _schritt1(sitzung, slot.trauort, pause)
    time.sleep(pause)
    listen_html = _schritt2(sitzung, p1, p2)
    time.sleep(pause)
    aktuelle = tagesslots(sitzung, slot.trauort, slot.datum)
    passend = [s for s in aktuelle if s.von == slot.von]
    if not passend:
        raise BuchungsFehler(f"Termin {slot.beschreibung()} ist nicht mehr in der Liste")
    weiter_url = _termin_waehlen(sitzung, passend[0], listen_html)
    return _schritt4(sitzung, weiter_url, kontakt, pause)


def buchen(
    slot: Slot,
    p1: Person,
    p2: Person,
    kontakt: Kontakt,
    wirklich: bool = False,
    timeout: int = 30,
    pause: float = 1.5,
    versuche: int = 3,
) -> dict[str, Any]:
    """Durchlaeuft den Assistenten fuer einen Termin.

    Der Weg bis zur Bestaetigungsseite wird bei Fehlern wiederholt - das
    Portal lehnt Formulare gelegentlich grundlos ab. Das Absenden selbst
    wird NIE wiederholt: schlaegt es fehl, koennte die Reservierung trotzdem
    angekommen sein, und ein zweiter Versuch wuerde doppelt buchen.
    """
    letzter: Exception | None = None
    for versuch in range(1, versuche + 1):
        sitzung = Sitzung(timeout=timeout)
        try:
            bestaetigung_html = _bis_bestaetigungsseite(sitzung, slot, p1, p2, kontakt, pause)
        except BuchungsFehler as err:
            sitzung.termin_freigeben()
            letzter = err
            print(f"[warn] Versuch {versuch}/{versuche} fehlgeschlagen: {err}", file=sys.stderr)
            if versuch < versuche:
                time.sleep(2 ** versuch)
            continue

        # Ab hier keine Wiederholung mehr.
        try:
            if not wirklich:
                return {"gebucht": False, "probe": True, "slot": slot.als_dict(),
                        "versuche": versuch,
                        "hinweis": "Bestaetigungsseite erreicht, nicht abgeschickt"}
            time.sleep(pause)
            ergebnis_html = _schritt5(sitzung, bestaetigung_html)
            return {"gebucht": True, "slot": slot.als_dict(), "versuche": versuch,
                    "bestaetigung": bestaetigung_auswerten(ergebnis_html)}
        finally:
            sitzung.termin_freigeben()

    raise BuchungsFehler(f"Nach {versuche} Versuchen gescheitert: {letzter}")


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------


def buchungszustand_laden(pfad: str) -> dict[str, Any]:
    if not pfad or not os.path.exists(pfad):
        return {}
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def buchungszustand_speichern(pfad: str, zustand: dict[str, Any]) -> None:
    if not pfad:
        return
    os.makedirs(os.path.dirname(os.path.abspath(pfad)), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as fh:
        json.dump(zustand, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def wochentage_parsen(wert: str) -> set[int]:
    tage: set[int] = set()
    for teil in (wert or "").split(","):
        teil = teil.strip().lower()
        if not teil:
            continue
        if teil.isdigit():
            tage.add(int(teil) % 7)
        elif teil in WOCHENTAGE:
            tage.add(WOCHENTAGE[teil])
        else:
            raise SystemExit(f"Unbekannter Wochentag: {teil!r}")
    if not tage:
        raise SystemExit("Es wurde kein Wochentag angegeben.")
    return tage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reserviert automatisch einen Trautermin in Wiesbaden.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Stufen:\n"
            "  --trocken          nur suchen (Standard, keine Nebenwirkung)\n"
            "  --probe            Durchlauf bis zur Bestaetigung, dann Abbruch\n"
            "  --wirklich-buchen  verbindlich reservieren (braucht TK_BUCHUNG_AKTIV=1)\n"
        ),
    )
    parser.add_argument("--monate", default=os.environ.get("TK_BUCHUNG_MONATE", "2027-08"))
    parser.add_argument("--trauorte", default=os.environ.get("TK_BUCHUNG_TRAUORTE", "1210,1345"),
                        help="Trauort-IDs, Standard: Kurhaus und Casino-Gesellschaft")
    parser.add_argument("--wochentage", default=os.environ.get("TK_BUCHUNG_WOCHENTAGE", "samstag"))
    parser.add_argument("--state", default=os.environ.get("TK_BUCHUNG_STATE", "state/gebucht.json"))
    parser.add_argument("--trocken", action="store_true", default=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--wirklich-buchen", dest="wirklich", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--versuche", type=int, default=3,
                        help="Wiederholungen auf dem Weg zur Bestaetigungsseite")
    parser.add_argument("--heute", default="")
    args = parser.parse_args(argv)

    # Endzustand zuerst: ist bereits reserviert, wird nichts mehr unternommen,
    # egal wie der Rest konfiguriert ist.
    zustand = buchungszustand_laden(args.state)
    if zustand.get("gebucht"):
        print(f"[info] Es ist bereits reserviert: {zustand.get('beschreibung', '?')}. "
              "Es wird nichts weiter unternommen.")
        return EXIT_OK

    # Danach die Freischaltung: lieber sofort abbrechen, als erst zu suchen und
    # dann am Ende festzustellen, dass gar nicht gebucht werden darf.
    if args.wirklich and os.environ.get("TK_BUCHUNG_AKTIV") != "1":
        print("[fehler] --wirklich-buchen verlangt TK_BUCHUNG_AKTIV=1. Abbruch.",
              file=sys.stderr)
        return EXIT_ERROR

    heute = dt.date.fromisoformat(args.heute) if args.heute else dt.date.today()
    monate = monate_parsen(args.monate.split(","))
    trauorte = [int(t) for t in args.trauorte.split(",") if t.strip()]
    tage = wochentage_parsen(args.wochentage)
    cfg = config_aus_umgebung()

    sitzung = Sitzung(timeout=args.timeout)
    treffer = kandidaten(sitzung, trauorte, monate, tage, heute)

    namen = ", ".join(TRAUORTE.get(t, str(t)) for t in trauorte)
    print(f"Gesucht: {args.monate} | {args.wochentage} | {namen}")
    if not treffer:
        print("Keine passenden Termine frei.")
        return EXIT_OK

    print(f"{len(treffer)} passende(r) Termin(e), spaetester zuerst:")
    for slot in treffer[:10]:
        print(f"  {slot.beschreibung()}")

    ziel = treffer[0]
    if not (args.probe or args.wirklich):
        print(f"\nWuerde reservieren: {ziel.beschreibung()}")
        print("(Trockenlauf - nichts unternommen)")
        return EXIT_OK

    p1, p2, kontakt = personendaten_aus_umgebung()
    fehlt = (["Person 1: " + f for f in p1.fehlend()]
             + ["Person 2: " + f for f in p2.fehlend()]
             + ["Kontakt: " + f for f in kontakt.fehlend()])
    if fehlt:
        print(f"[fehler] Unvollstaendige Daten: {', '.join(fehlt)}", file=sys.stderr)
        return EXIT_ERROR

    print(f"\nZiel: {ziel.beschreibung()}")
    try:
        ergebnis = buchen(ziel, p1, p2, kontakt, wirklich=args.wirklich,
                          timeout=args.timeout, versuche=args.versuche)
    except BuchungsFehler as err:
        print(f"[fehler] {err}", file=sys.stderr)
        return EXIT_ERROR

    if not ergebnis.get("gebucht"):
        print(f"[ok] Probelauf erfolgreich: {ergebnis.get('hinweis')}")
        return EXIT_OK

    zustand = {
        "gebucht": True,
        "beschreibung": ziel.beschreibung(),
        "slot": ziel.als_dict(),
        "zeitpunkt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "bestaetigung": ergebnis.get("bestaetigung", {}),
    }
    buchungszustand_speichern(args.state, zustand)

    titel = f"Trautermin reserviert: {ziel.beschreibung()}"
    text = "\n".join([
        "Der Trautermin wurde automatisch reserviert:",
        "",
        f"  {ziel.beschreibung()}",
        "",
        f"Bestaetigung: {ergebnis.get('bestaetigung', {}).get('titel', '')}",
        ergebnis.get("bestaetigung", {}).get("vorgangsnummer", ""),
        "",
        "Die Bestaetigung des Standesamts kommt per E-Mail.",
        "Aenderungen oder Absage nur direkt beim Standesamt Wiesbaden.",
    ])
    print("\n" + titel + "\n" + text)
    kanaele = benachrichtigen(titel, text, cfg)
    print(f"[info] benachrichtigt ueber: {', '.join(kanaele) or 'keinen Kanal'}")
    return EXIT_GEBUCHT


if __name__ == "__main__":
    sys.exit(main())
