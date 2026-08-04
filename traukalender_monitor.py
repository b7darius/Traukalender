#!/usr/bin/env python3
"""Monitor fuer den Traukalender der Landeshauptstadt Wiesbaden.

Prueft regelmaessig, ob im Traukalender (https://traukalender.wiesbaden.de)
Termine an bestimmten Tagen freigeschaltet werden, und benachrichtigt sofort,
sobald das passiert. Voreingestellt sind die Samstage im Juli 2027: geprueft
werden alle Trauorte, benachrichtigt wird nur fuer das Kurhaus und die
Wiesbadener Casino-Gesellschaft.

Der Kalender liefert seine Daten ueber einen oeffentlichen AJAX-Endpunkt,
den auch die Webseite selbst benutzt:

    /common/ajax.php?bereich=portal&modul_id=101&klasse=tko_buergeransicht
        &com=ermitteln_termindaten&trauort=<id>&ortsverwaltung=0
        &monat=<m>&jahr=<j>&min=<YYYY-MM-DD>&max=<YYYY-MM-DD>
        &sprache=de&texte=traukalender/texte_buergeransicht

Antwort (JSON):
    appointments -> Liste der Tage mit buchbaren Terminen
    holidays     -> Feiertage
    hints        -> Tage mit Hinweis (z.B. nur telefonisch)
    tooltips     -> Text pro Tag

Wichtig: der Parameter ``max`` wird serverseitig als Filter angewandt. Die
Buergeransicht setzt ihn auf "heute + 12 Monate" (Reservierung maximal 12
Monate im Voraus). Deshalb fragt dieses Skript zwei Sichten ab:

  * "backend"  - max weit in der Zukunft: gibt es die Termine ueberhaupt schon?
  * "buchbar"  - max wie im Portal: sind sie schon jetzt reservierbar?

Nur Standardbibliothek, keine Abhaengigkeiten.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from html import unescape
from typing import Any, Iterable

BASE = "https://traukalender.wiesbaden.de"
AJAX = BASE + "/common/ajax.php"
START_URL = BASE + "/de/Start-159.html"
LANDING_URL = BASE + "/de/Traukalender-Stadt-Wiesbaden-50.html"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Trauorte laut https://traukalender.wiesbaden.de/de/Traukalender-Stadt-Wiesbaden-50.html
TRAUORTE: dict[int, str] = {
    139598: "Kleiner Festsaal im Neuen Rathaus",
    1187: "Altes Rathaus",
    1210: "Kurhaus",
    1237: "Nerobergbahn",
    1345: "Wiesbadener Casino-Gesellschaft",
    32243: "thalhaus Theater Wiesbaden",
}

# Reservierung ist laut Portal maximal so weit im Voraus moeglich.
VORLAUF_MONATE = 12

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FOUND = 10


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def http_get(url: str, timeout: int = 30, retries: int = 3) -> str:
    """GET mit Retries. Der Kalender-Endpunkt antwortet mit HTTP 403 und
    trotzdem gueltigem JSON-Body - dieser Fall wird bewusst akzeptiert."""
    last: Exception | None = None
    for versuch in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Accept-Language": "de-DE,de;q=0.9",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")
            if body.strip():
                return body
            last = err
        except Exception as err:  # Netzwerkfehler, Timeouts, DNS
            last = err
        if versuch < retries - 1:
            time.sleep(2 ** versuch)
    raise RuntimeError(f"Abruf fehlgeschlagen: {url} ({last})")


# --------------------------------------------------------------------------
# Kalender-API
# --------------------------------------------------------------------------


def monat_abfragen(
    trauort: int,
    jahr: int,
    monat: int,
    min_datum: str,
    max_datum: str,
    timeout: int = 30,
) -> dict[str, Any]:
    params = {
        "bereich": "portal",
        "modul_id": "101",
        "klasse": "tko_buergeransicht",
        "com": "ermitteln_termindaten",
        "trauort": str(trauort),
        "ortsverwaltung": "0",
        "monat": str(monat),
        "jahr": str(jahr),
        "min": min_datum,
        "max": max_datum,
        "sprache": "de",
        "texte": "traukalender/texte_buergeransicht",
    }
    roh = http_get(AJAX + "?" + urllib.parse.urlencode(params), timeout=timeout)
    try:
        daten = json.loads(roh)
    except json.JSONDecodeError as err:
        raise RuntimeError(
            f"Unerwartete Antwort fuer {jahr}-{monat:02d} (Trauort {trauort}): "
            f"{roh[:200]!r}"
        ) from err
    if not isinstance(daten, dict):
        raise RuntimeError(f"Unerwartetes JSON fuer {jahr}-{monat:02d}: {daten!r}")
    return daten


def uhrzeiten_abfragen(trauort: int, tag: str, timeout: int = 30) -> list[str]:
    """Konkrete Zeitfenster eines Tages (z.B. '08:30 - 09:00')."""
    params = {"trauort": str(trauort), "ortsverwaltung": "0", "tag": tag}
    url = f"{BASE}/de/buerger_liste_termine_ajax.html?" + urllib.parse.urlencode(params)
    try:
        html = http_get(url, timeout=timeout, retries=2)
    except RuntimeError:
        return []
    treffer = re.findall(r'title="Termin am [^"]*?um ([^"]*?) Uhr"', html)
    gesehen: list[str] = []
    for zeit in treffer:
        zeit = unescape(zeit).strip()
        if zeit not in gesehen:
            gesehen.append(zeit)
    return gesehen


# --------------------------------------------------------------------------
# Datums-Helfer
# --------------------------------------------------------------------------


def monate_parsen(werte: Iterable[str]) -> list[tuple[int, int]]:
    monate: list[tuple[int, int]] = []
    for wert in werte:
        wert = wert.strip()
        if not wert:
            continue
        treffer = re.fullmatch(r"(\d{4})-(\d{1,2})", wert)
        if not treffer:
            raise SystemExit(f"Ungueltiger Monat {wert!r}, erwartet wird JJJJ-MM.")
        jahr, monat = int(treffer.group(1)), int(treffer.group(2))
        if not 1 <= monat <= 12:
            raise SystemExit(f"Ungueltiger Monat {wert!r}.")
        if (jahr, monat) not in monate:
            monate.append((jahr, monat))
    if not monate:
        raise SystemExit("Es wurde kein Zielmonat angegeben.")
    return monate


def plus_monate(datum: dt.date, monate: int) -> dt.date:
    monat_gesamt = datum.month - 1 + monate
    jahr = datum.year + monat_gesamt // 12
    monat = monat_gesamt % 12 + 1
    tag = min(datum.day, [31, 29 if jahr % 4 == 0 and (jahr % 100 != 0 or jahr % 400 == 0)
                          else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][monat - 1])
    return dt.date(jahr, monat, tag)


WOCHENTAGE = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}


def wochentage_parsen(wert: str) -> set[int] | None:
    """Wochentage aus einer Angabe wie 'samstag' oder 'samstag,sonntag'.

    'alle' oder eine leere Angabe bedeutet: keine Einschraenkung (None).
    """
    wert = (wert or "").strip().lower()
    if not wert or wert == "alle":
        return None
    tage: set[int] = set()
    for teil in wert.split(","):
        teil = teil.strip()
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


def wochentage_namen(wochentage: set[int] | None) -> str:
    if not wochentage:
        return "alle Tage"
    umgekehrt = {nummer: name for name, nummer in WOCHENTAGE.items()}
    return ", ".join(umgekehrt[n].capitalize() for n in sorted(wochentage))


def passt_auf_wochentag(iso: str, wochentage: set[int] | None) -> bool:
    if not wochentage:
        return True
    try:
        return dt.date.fromisoformat(iso).weekday() in wochentage
    except ValueError:
        return False


def deutsches_datum(iso: str) -> str:
    try:
        datum = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    wochentage = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                  "Freitag", "Samstag", "Sonntag"]
    return f"{wochentage[datum.weekday()]}, {datum.day:02d}.{datum.month:02d}.{datum.year}"


# --------------------------------------------------------------------------
# Pruefung
# --------------------------------------------------------------------------


class Treffer:
    def __init__(self, trauort: int, datum: str, buchbar: bool, uhrzeiten: list[str]):
        self.trauort = trauort
        self.datum = datum
        self.buchbar = buchbar
        self.uhrzeiten = uhrzeiten

    @property
    def trauort_name(self) -> str:
        return TRAUORTE.get(self.trauort, f"Trauort {self.trauort}")

    def als_dict(self) -> dict[str, Any]:
        return {
            "trauort": self.trauort,
            "trauort_name": self.trauort_name,
            "datum": self.datum,
            "buchbar": self.buchbar,
            "uhrzeiten": self.uhrzeiten,
        }


def pruefen(
    trauorte: list[int],
    monate: list[tuple[int, int]],
    heute: dt.date,
    timeout: int = 30,
    mit_uhrzeiten: bool = True,
    wochentage: set[int] | None = None,
    melde_trauorte: set[int] | None = None,
) -> tuple[list[Treffer], dict[str, Any], list[str]]:
    """Fragt alle Trauorte fuer alle Zielmonate ab.

    wochentage begrenzt auf bestimmte Wochentage (0 = Montag). None bedeutet
    alle Tage.

    melde_trauorte trennt Beobachten von Melden: geprueft und in der
    Zusammenfassung ausgewiesen werden alle uebergebenen Trauorte, ein Treffer
    entsteht aber nur fuer diese Auswahl. None heisst: alle melden.

    Rueckgabe: (Treffer, Diagnose, Fehler)
    """
    # "max" weit in der Zukunft -> zeigt, ob die Termine ueberhaupt angelegt sind.
    max_backend = (heute + dt.timedelta(days=365 * 3)).isoformat()
    # "max" wie im Portal -> zeigt, ob sie schon reservierbar sind.
    max_portal = plus_monate(heute, VORLAUF_MONATE).isoformat()
    min_datum = heute.isoformat()

    treffer: list[Treffer] = []
    fehler: list[str] = []
    diagnose: dict[str, Any] = {
        "geprueft_am": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "reservierbar_bis": max_portal,
        "zielmonate": [f"{j}-{m:02d}" for j, m in monate],
        "wochentage": wochentage_namen(wochentage),
        "melde_trauorte": sorted(
            TRAUORTE.get(t, str(t)) for t in (melde_trauorte or trauorte)
        ),
        "trauorte": {},
    }

    for trauort in trauorte:
        name = TRAUORTE.get(trauort, f"Trauort {trauort}")
        meldet = melde_trauorte is None or trauort in melde_trauorte
        info: dict[str, Any] = {"name": name, "meldet": meldet, "monate": {}}
        for jahr, monat in monate:
            schluessel = f"{jahr}-{monat:02d}"
            try:
                backend = monat_abfragen(
                    trauort, jahr, monat, min_datum, max_backend, timeout
                )
                portal = monat_abfragen(
                    trauort, jahr, monat, min_datum, max_portal, timeout
                )
            except RuntimeError as err:
                fehler.append(f"{name} {schluessel}: {err}")
                continue

            angelegt = [t for t in sorted(backend.get("appointments") or [])
                        if passt_auf_wochentag(t, wochentage)]
            reservierbar = {t for t in (portal.get("appointments") or [])
                            if passt_auf_wochentag(t, wochentage)}
            info["monate"][schluessel] = {
                "angelegt": angelegt,
                "reservierbar": sorted(reservierbar),
            }
            if not meldet:
                # Nur beobachtet: taucht in der Zusammenfassung auf, loest aber
                # keine Meldung aus. Die Uhrzeiten spart das gleich mit ein.
                continue
            for datum in angelegt:
                zeiten = uhrzeiten_abfragen(trauort, datum, timeout) if mit_uhrzeiten else []
                treffer.append(Treffer(trauort, datum, datum in reservierbar, zeiten))
        diagnose["trauorte"][str(trauort)] = info

    treffer.sort(key=lambda t: (t.datum, t.trauort))
    return treffer, diagnose, fehler


def horizont_ermitteln(
    trauorte: list[int], heute: dt.date, monate_voraus: int = 14, timeout: int = 30
) -> dict[str, str]:
    """Letzter aktuell buchbarer Tag je Trauort - zeigt, wie weit der
    Kalender inzwischen geoeffnet ist."""
    max_backend = (heute + dt.timedelta(days=365 * 3)).isoformat()
    horizont: dict[str, str] = {}
    for trauort in trauorte:
        letzter = ""
        zeiger = heute.replace(day=1)
        for _ in range(monate_voraus):
            try:
                daten = monat_abfragen(
                    trauort, zeiger.year, zeiger.month,
                    heute.isoformat(), max_backend, timeout,
                )
            except RuntimeError:
                daten = {}
            for datum in daten.get("appointments") or []:
                if datum > letzter:
                    letzter = datum
            zeiger = plus_monate(zeiger, 1)
        horizont[str(trauort)] = letzter
    return horizont


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------


def zustand_laden(pfad: str) -> dict[str, Any]:
    if not pfad or not os.path.exists(pfad):
        return {"gemeldet": {}}
    try:
        with open(pfad, encoding="utf-8") as fh:
            zustand = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"gemeldet": {}}
    zustand.setdefault("gemeldet", {})
    return zustand


def zustand_speichern(pfad: str, zustand: dict[str, Any]) -> None:
    if not pfad:
        return
    verzeichnis = os.path.dirname(os.path.abspath(pfad))
    os.makedirs(verzeichnis, exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as fh:
        json.dump(zustand, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def neue_treffer(zustand: dict[str, Any], treffer: list[Treffer]) -> list[Treffer]:
    gemeldet = zustand.get("gemeldet", {})
    neu = []
    for eintrag in treffer:
        bekannt = gemeldet.get(str(eintrag.trauort), [])
        if eintrag.datum not in bekannt:
            neu.append(eintrag)
    return neu


def als_gemeldet_merken(zustand: dict[str, Any], treffer: list[Treffer]) -> None:
    gemeldet = zustand.setdefault("gemeldet", {})
    for eintrag in treffer:
        liste = gemeldet.setdefault(str(eintrag.trauort), [])
        if eintrag.datum not in liste:
            liste.append(eintrag.datum)
        liste.sort()


# --------------------------------------------------------------------------
# Benachrichtigungen
# --------------------------------------------------------------------------


def nachricht_bauen(treffer: list[Treffer], diagnose: dict[str, Any]) -> tuple[str, str]:
    monate = ", ".join(diagnose.get("zielmonate", []))
    sofort = [t for t in treffer if t.buchbar]
    if sofort:
        titel = f"Traukalender Wiesbaden: {len(sofort)} Termin(e) im Zeitraum {monate} frei!"
    else:
        titel = f"Traukalender Wiesbaden: Termine fuer {monate} sind angelegt"

    zeilen = []
    for eintrag in treffer:
        status = "JETZT reservierbar" if eintrag.buchbar else (
            "angelegt, aber noch ausserhalb der 12-Monats-Frist"
        )
        zeilen.append(f"* {deutsches_datum(eintrag.datum)} - {eintrag.trauort_name} ({status})")
        if eintrag.uhrzeiten:
            zeilen.append(f"    Uhrzeiten: {', '.join(eintrag.uhrzeiten)}")

    text = "\n".join(
        [
            f"Neue Termine im Traukalender Wiesbaden ({monate}):",
            "",
            *zeilen,
            "",
            f"Reservierbar aktuell bis: {diagnose.get('reservierbar_bis', '?')}",
            "",
            "Hinweis: Termine sind maximal 12 Monate im Voraus reservierbar.",
            "Sind Tage 'angelegt, aber noch ausserhalb der Frist', werden sie",
            "an dem Tag freigeschaltet, an dem sie genau 12 Monate entfernt sind.",
        ]
    )
    return titel, text


def benachrichtigen(titel: str, text: str, cfg: dict[str, str]) -> list[str]:
    """Verschickt die Benachrichtigung ueber alle konfigurierten Kanaele.

    Gibt die Liste der Kanaele zurueck, die erfolgreich waren."""
    erfolgreich: list[str] = []

    topic = cfg.get("ntfy_topic")
    if topic:
        server = cfg.get("ntfy_server") or "https://ntfy.sh"
        try:
            req = urllib.request.Request(
                f"{server.rstrip('/')}/{topic}",
                data=text.encode("utf-8"),
                # Bewusst ohne "Click"-Kopfzeile: ein Tippen auf die Meldung
                # soll nicht ungefragt den Traukalender oeffnen.
                headers={
                    "Title": titel.encode("ascii", "replace").decode(),
                    "Priority": "urgent",
                    "Tags": "wedding,bell",
                },
            )
            if cfg.get("ntfy_token"):
                req.add_header("Authorization", f"Bearer {cfg['ntfy_token']}")
            with urllib.request.urlopen(req, timeout=30):
                erfolgreich.append("ntfy")
        except Exception as err:
            print(f"[warn] ntfy fehlgeschlagen: {err}", file=sys.stderr)

    if cfg.get("smtp_host") and cfg.get("mail_to"):
        try:
            nachricht = EmailMessage()
            nachricht["Subject"] = titel
            nachricht["From"] = cfg.get("mail_from") or cfg.get("smtp_user") or "traukalender-monitor"
            nachricht["To"] = cfg["mail_to"]
            nachricht.set_content(text)
            port = int(cfg.get("smtp_port") or 587)
            if port == 465:
                server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30)
            else:
                server = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
            with server:
                if port != 465:
                    server.starttls()
                if cfg.get("smtp_user"):
                    server.login(cfg["smtp_user"], cfg.get("smtp_pass", ""))
                server.send_message(nachricht)
            erfolgreich.append("email")
        except Exception as err:
            print(f"[warn] E-Mail fehlgeschlagen: {err}", file=sys.stderr)

    if cfg.get("telegram_token") and cfg.get("telegram_chat"):
        try:
            nutzlast = urllib.parse.urlencode(
                {
                    "chat_id": cfg["telegram_chat"],
                    "text": f"{titel}\n\n{text}",
                    "disable_web_page_preview": "true",
                }
            ).encode()
            url = f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage"
            with urllib.request.urlopen(urllib.request.Request(url, data=nutzlast), timeout=30):
                erfolgreich.append("telegram")
        except Exception as err:
            print(f"[warn] Telegram fehlgeschlagen: {err}", file=sys.stderr)

    if cfg.get("webhook_url"):
        try:
            nutzlast = json.dumps(
                {"title": titel, "text": text}, ensure_ascii=False
            ).encode("utf-8")
            req = urllib.request.Request(
                cfg["webhook_url"], data=nutzlast,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30):
                erfolgreich.append("webhook")
        except Exception as err:
            print(f"[warn] Webhook fehlgeschlagen: {err}", file=sys.stderr)

    return erfolgreich


def config_aus_umgebung() -> dict[str, str]:
    schluessel = {
        "ntfy_server": "TK_NTFY_SERVER",
        "ntfy_topic": "TK_NTFY_TOPIC",
        "ntfy_token": "TK_NTFY_TOKEN",
        "smtp_host": "TK_SMTP_HOST",
        "smtp_port": "TK_SMTP_PORT",
        "smtp_user": "TK_SMTP_USER",
        "smtp_pass": "TK_SMTP_PASS",
        "mail_from": "TK_MAIL_FROM",
        "mail_to": "TK_MAIL_TO",
        "telegram_token": "TK_TELEGRAM_TOKEN",
        "telegram_chat": "TK_TELEGRAM_CHAT",
        "webhook_url": "TK_WEBHOOK_URL",
    }
    return {name: os.environ[var] for name, var in schluessel.items() if os.environ.get(var)}


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------


def zusammenfassung_drucken(
    treffer: list[Treffer], diagnose: dict[str, Any], fehler: list[str]
) -> None:
    stempel = diagnose.get("geprueft_am", "")
    monate = ", ".join(diagnose.get("zielmonate", []))
    print(f"[{stempel}] Zielmonate: {monate} | {diagnose.get('wochentage', 'alle Tage')} "
          f"| reservierbar bis {diagnose.get('reservierbar_bis')}")
    print(f"  Benachrichtigung nur fuer: {', '.join(diagnose.get('melde_trauorte', []))}")
    for trauort_id, info in diagnose.get("trauorte", {}).items():
        teile = []
        for monat, werte in info.get("monate", {}).items():
            teile.append(
                f"{monat}: {len(werte['angelegt'])} Tag(e) angelegt, "
                f"{len(werte['reservierbar'])} reservierbar"
            )
        # ">" markiert die Trauorte, die eine Meldung ausloesen koennen.
        marke = ">" if info.get("meldet", True) else " "
        print(f"  {marke} {info.get('name', trauort_id):34s} " + " | ".join(teile))
    for meldung in fehler:
        print(f"  [fehler] {meldung}", file=sys.stderr)
    if treffer:
        print(f"  -> {len(treffer)} meldepflichtige(r) Tag(e) gefunden.")
    else:
        print("  -> nichts zu melden (an den meldenden Trauorten keine Termine).")


def bericht_schreiben(pfad: str, titel: str, text: str) -> None:
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write(f"# {titel}\n\n```\n{text}\n```\n")


def statusbericht(diagnose: dict[str, Any], fehler: list[str]) -> tuple[str, str]:
    """Kurzer Statustext fuer Laeufe ohne Fund - beantwortet die Frage
    'laeuft die Ueberwachung ueberhaupt noch und worauf?'."""
    monate = ", ".join(diagnose.get("zielmonate", []))
    titel = f"Traukalender-Monitor laeuft - {monate} noch ohne Termine"
    zeilen = [
        f"Beobachtet: {monate}, {diagnose.get('wochentage', 'alle Tage')}",
        f"Meldung nur fuer: {', '.join(diagnose.get('melde_trauorte', []))}",
        f"Geprueft am: {diagnose.get('geprueft_am', '?')}",
        f"Reservierbar aktuell bis: {diagnose.get('reservierbar_bis', '?')}",
        "",
        "Stand je Trauort (* = meldet):",
    ]
    for trauort_id, info in diagnose.get("trauorte", {}).items():
        teile = [
            f"{monat}: {len(werte['angelegt'])} Tag(e)"
            for monat, werte in info.get("monate", {}).items()
        ]
        marke = "*" if info.get("meldet", True) else "-"
        zeilen.append(f"{marke} {info.get('name', trauort_id)} - {', '.join(teile)}")
    if fehler:
        zeilen += ["", "Fehler bei einzelnen Abfragen:"] + [f"* {f}" for f in fehler]
    return titel, "\n".join(zeilen)


def marker_bauen(treffer: list[Treffer]) -> str:
    """Stabile Kennung der gemeldeten Tage.

    Damit erkennt der GitHub-Workflow, ob zu genau diesem Fund schon ein Issue
    offen ist - auch dann, wenn die Zustandsdatei mal nicht gespeichert werden
    konnte."""
    schluessel = ";".join(sorted(f"{t.trauort}:{t.datum}" for t in treffer))
    return hashlib.sha256(schluessel.encode("utf-8")).hexdigest()[:16]


def github_output_setzen(gefunden: bool, titel: str, marker: str = "") -> None:
    pfad = os.environ.get("GITHUB_OUTPUT")
    if not pfad:
        return
    with open(pfad, "a", encoding="utf-8") as fh:
        fh.write(f"gefunden={'true' if gefunden else 'false'}\n")
        fh.write(f"titel={titel}\n")
        fh.write(f"marker={marker}\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def durchlauf(args: argparse.Namespace, cfg: dict[str, str]) -> int:
    heute = dt.date.today() if not args.heute else dt.date.fromisoformat(args.heute)
    monate = monate_parsen(args.monate.split(","))
    trauorte = trauorte_parsen(args.trauorte)
    melde_trauorte = set(trauorte_parsen(args.melde_trauorte))
    wochentage = wochentage_parsen(args.wochentage)

    try:
        treffer, diagnose, fehler = pruefen(
            trauorte, monate, heute, timeout=args.timeout,
            mit_uhrzeiten=not args.ohne_uhrzeiten, wochentage=wochentage,
            melde_trauorte=melde_trauorte,
        )
    except Exception as err:
        print(f"[fehler] Pruefung abgebrochen: {err}", file=sys.stderr)
        return EXIT_ERROR

    if args.horizont:
        diagnose["horizont"] = horizont_ermitteln(trauorte, heute, timeout=args.timeout)
        for trauort_id, datum in diagnose["horizont"].items():
            name = TRAUORTE.get(int(trauort_id), trauort_id)
            print(f"  Horizont {name:34s} letzter buchbarer Tag: {datum or '-'}")

    zusammenfassung_drucken(treffer, diagnose, fehler)

    if fehler and not treffer:
        # Alle Abfragen fehlgeschlagen -> Zustand nicht anfassen.
        if len(fehler) >= len(trauorte) * len(monate):
            return EXIT_ERROR

    zustand = zustand_laden(args.state)
    zu_melden = treffer if args.force else neue_treffer(zustand, treffer)

    if zu_melden:
        titel, text = nachricht_bauen(zu_melden, diagnose)
        print("\n" + titel + "\n" + text)
        kanaele = benachrichtigen(titel, text, cfg)
        print(f"[info] benachrichtigt ueber: {', '.join(kanaele) or 'keinen Kanal'}")
        if args.bericht:
            bericht_schreiben(args.bericht, titel, text)
        github_output_setzen(True, titel, marker_bauen(zu_melden))
        if not args.force:
            als_gemeldet_merken(zustand, zu_melden)
    else:
        # Ohne Fund wird nur der Bericht geschrieben, der in der Zusammenfassung
        # des Laufs landet. Benachrichtigt wird ausschliesslich bei einem
        # echten Fund - Meldungen ohne Anlass wuerden nur abstumpfen.
        titel, text = statusbericht(diagnose, fehler)
        if args.bericht:
            bericht_schreiben(args.bericht, titel, text)
        github_output_setzen(False, "")

    # Bewusst nur tagesgenau: die Zustandsdatei wird im GitHub-Workflow ins
    # Repository zurueckgeschrieben. Mit Uhrzeit gaebe es bei jedem Lauf einen
    # Commit (alle 15 Minuten), tagesgenau bleibt es bei einem pro Tag - genug,
    # damit GitHub die geplanten Laeufe nicht wegen Inaktivitaet pausiert.
    persistierbar = {k: v for k, v in diagnose.items() if k != "geprueft_am"}
    zustand["letzte_pruefung"] = diagnose["geprueft_am"][:10]
    zustand["diagnose"] = persistierbar
    if fehler:
        zustand["letzte_fehler"] = fehler
    else:
        zustand.pop("letzte_fehler", None)
    zustand_speichern(args.state, zustand)

    return EXIT_FOUND if zu_melden else EXIT_OK


def trauorte_parsen(wert: str) -> list[int]:
    wert = (wert or "").strip()
    if not wert or wert.lower() == "alle":
        return list(TRAUORTE)
    ids: list[int] = []
    for teil in wert.split(","):
        teil = teil.strip()
        if not teil:
            continue
        if not teil.isdigit():
            raise SystemExit(f"Ungueltige Trauort-ID: {teil!r}")
        ids.append(int(teil))
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Monitor fuer den Traukalender der Stadt Wiesbaden.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Beispiele:\n"
            "  python traukalender_monitor.py                     # einmalig pruefen\n"
            "  python traukalender_monitor.py --watch             # dauerhaft alle 15 Minuten\n"
            "  python traukalender_monitor.py --test-notify       # Benachrichtigung testen\n"
            "  python traukalender_monitor.py --monate 2027-08,2027-09 --horizont\n"
        ),
    )
    parser.add_argument("--monate", default=os.environ.get("TK_MONATE", "2027-07"),
                        help="Zielmonate als JJJJ-MM, kommagetrennt (Standard: 2027-07)")
    parser.add_argument("--trauorte", default=os.environ.get("TK_TRAUORTE", "alle"),
                        help="welche Trauorte geprueft werden: IDs kommagetrennt "
                             "oder 'alle' (Standard: alle)")
    parser.add_argument("--melde-trauorte", dest="melde_trauorte",
                        default=os.environ.get("TK_MELDE_TRAUORTE", "1210,1345"),
                        help="welche Trauorte eine Benachrichtigung ausloesen: IDs "
                             "kommagetrennt oder 'alle' (Standard: 1210,1345 = "
                             "Kurhaus und Casino-Gesellschaft)")
    parser.add_argument("--wochentage", default=os.environ.get("TK_WOCHENTAGE", "samstag"),
                        help="nur diese Wochentage melden, z.B. 'samstag' oder "
                             "'alle' (Standard: samstag)")
    parser.add_argument("--state", default=os.environ.get("TK_STATE", "state/state.json"),
                        help="Datei fuer den Zustand (verhindert doppelte Meldungen)")
    parser.add_argument("--watch", action="store_true",
                        help="dauerhaft laufen und im Intervall pruefen")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("TK_INTERVAL", "900")),
                        help="Intervall in Sekunden fuer --watch (Standard: 900)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP-Timeout in Sekunden")
    parser.add_argument("--force", action="store_true",
                        help="immer benachrichtigen, auch wenn schon gemeldet")
    parser.add_argument("--horizont", action="store_true",
                        help="zusaetzlich den letzten buchbaren Tag je Trauort ermitteln "
                             "(fragt 14 Monate je Trauort ab, dauert ein bis zwei Minuten)")
    parser.add_argument("--ohne-uhrzeiten", action="store_true",
                        help="keine Uhrzeiten je Tag nachladen (schneller)")
    parser.add_argument("--bericht", default=os.environ.get("TK_BERICHT", ""),
                        help="Markdown-Bericht in diese Datei schreiben (bei Fund die Meldung, "
                             "sonst den aktuellen Stand)")
    parser.add_argument("--test-notify", action="store_true",
                        help="Testnachricht ueber alle konfigurierten Kanaele senden")
    parser.add_argument("--heute", default="", help="Datum ueberschreiben (JJJJ-MM-TT), fuer Tests")
    args = parser.parse_args(argv)

    cfg = config_aus_umgebung()

    if args.test_notify:
        kanaele = benachrichtigen(
            "Traukalender-Monitor: Test",
            "Das ist eine Testnachricht. Der Monitor ist eingerichtet und laeuft.",
            cfg,
        )
        if kanaele:
            print(f"[ok] Testnachricht verschickt ueber: {', '.join(kanaele)}")
            return EXIT_OK
        print("[fehler] Kein Kanal konfiguriert oder alle fehlgeschlagen.", file=sys.stderr)
        return EXIT_ERROR

    if not cfg:
        print("[warn] Kein Benachrichtigungskanal konfiguriert - Ausgabe nur im Terminal.",
              file=sys.stderr)

    if not args.watch:
        return durchlauf(args, cfg)

    print(f"[info] Dauerbetrieb, Intervall {args.interval}s. Abbruch mit Strg+C.")
    while True:
        try:
            durchlauf(args, cfg)
        except KeyboardInterrupt:
            print("\n[info] beendet.")
            return EXIT_OK
        except Exception as err:  # Monitor darf nie sterben
            print(f"[fehler] {err}", file=sys.stderr)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[info] beendet.")
            return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
