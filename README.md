# Traukalender-Monitor Wiesbaden

Überwacht den [Traukalender der Landeshauptstadt Wiesbaden](https://traukalender.wiesbaden.de/de/Start-159.html)
und meldet sich per Push aufs Handy, sobald passende Termine freigeschaltet werden.

Beobachtet werden die **Samstage im Juli 2027** im **Kurhaus** und bei der
**Wiesbadener Casino-Gesellschaft**. Reiner Monitor – es wird nichts gebucht.

Reines Python 3 aus der Standardbibliothek, keine Abhängigkeiten, kein Browser.

## Zwei Grenzen des Kalenders

Der Kalender ist zwar offen, aber zwei Dinge begrenzen ihn:

1. **12-Monats-Regel.** Das Portal setzt das Ende des wählbaren Zeitraums auf
   *heute + 12 Monate* („Termine können maximal 12 Monate im Voraus reserviert
   werden"). Juli 2027 liegt seit Juli 2026 vollständig in diesem Fenster – an
   dieser Grenze scheitert es also nicht mehr.
2. **Termine sind noch nicht angelegt.** Die beiden beobachteten Trauorte sind
   noch weit von Juli 2027 entfernt: das Kurhaus reicht bis 06.03.2027, die
   Casino-Gesellschaft bis 09.04.2027 (Stand 02.08.2026). Bis dort Juli-Termine
   erscheinen, bleibt es still – genau darauf wartet der Monitor.

Der Monitor prüft deshalb beides getrennt und meldet **beide** Ereignisse:

| Sicht     | Bedeutung                                                       |
|-----------|-----------------------------------------------------------------|
| `angelegt`     | Die Stadt hat Termine im Zielmonat eingestellt.            |
| `reservierbar` | Der Tag liegt zusätzlich im aktuellen 12-Monats-Fenster und ist jetzt buchbar. |

So kommt die Nachricht schon in dem Moment, in dem die Termine im System
auftauchen – nicht erst, wenn sie auch anklickbar sind.

Wie weit die Trauorte gefüllt sind, unterscheidet sich deutlich (Stand 02.08.2026,
ermittelt mit `--horizont`) – die beiden beobachteten sind **fett**:

| Trauort                           | letzter hinterlegter Tag |
|-----------------------------------|--------------------------|
| Altes Rathaus                     | 31.07.2027               |
| thalhaus Theater Wiesbaden        | 24.07.2027               |
| Nerobergbahn                      | 16.07.2027               |
| **Wiesbadener Casino-Gesellschaft** | **09.04.2027**         |
| **Kurhaus**                       | **06.03.2027**           |
| Kleiner Festsaal im Neuen Rathaus | 16.01.2027               |

Beobachtet werden nur Kurhaus und Casino-Gesellschaft, und dort nur die
Samstage. Mit `--trauorte alle --wochentage alle` lässt sich das jederzeit
aufweiten.

## Schnellstart

```bash
# einmalig prüfen (Samstage im Juli 2027, Kurhaus und Casino)
python3 traukalender_monitor.py

# dauerhaft laufen lassen, alle 15 Minuten
python3 traukalender_monitor.py --watch

# abweichend: anderer Monat, andere Trauorte, alle Wochentage
python3 traukalender_monitor.py --monate 2027-08 --trauorte alle --wochentage alle

# wie weit ist der Kalender aktuell geöffnet?
python3 traukalender_monitor.py --horizont
```

Rückgabewerte: `0` = nichts Neues, `10` = Termine gefunden, `1` = Fehler.

Der Zustand liegt in `state/state.json`. Damit wird jeder Tag nur **einmal**
gemeldet; mit `--force` wird trotzdem benachrichtigt.

## Benachrichtigung einrichten

Alle Kanäle werden über Umgebungsvariablen konfiguriert und können kombiniert
werden. Test mit:

```bash
python3 traukalender_monitor.py --test-notify
```

### ntfy – Push aufs Handy, ohne Anmeldung (empfohlen)

1. App [ntfy](https://ntfy.sh) installieren (iOS/Android).
2. Ein schwer zu erratendes Topic abonnieren, z. B. `traukalender-a7f3k9x`.
3. Monitor starten mit:

```bash
export TK_NTFY_TOPIC=traukalender-a7f3k9x
```

> Ein ntfy-Topic ist öffentlich, wer den Namen kennt, liest mit. Deshalb einen
> zufälligen Namen wählen – in den Nachrichten stehen ohnehin nur Termindaten,
> keine persönlichen Angaben.

### E-Mail

```bash
export TK_SMTP_HOST=smtp.gmail.com
export TK_SMTP_PORT=587
export TK_SMTP_USER=absender@gmail.com
export TK_SMTP_PASS=app-passwort      # bei Gmail: App-Passwort, nicht das Kontopasswort
export TK_MAIL_FROM=absender@gmail.com
export TK_MAIL_TO=empfaenger@example.com
```

### Telegram

```bash
export TK_TELEGRAM_TOKEN=123456:ABC...   # von @BotFather
export TK_TELEGRAM_CHAT=987654321        # eigene Chat-ID
```

### Beliebiger Webhook

```bash
export TK_WEBHOOK_URL=https://...   # bekommt JSON: {title, text}
```

## Dauerbetrieb per GitHub Actions

`.github/workflows/traukalender.yml` prüft alle 15 Minuten (GitHub drosselt das
in der Praxis auf etwa ein- bis zweistündlich), schreibt den Zustand ins
Repository zurück und legt bei einem Treffer zusätzlich ein Issue an.
Push aufs Handy kommt über ntfy, das Issue ist der zusätzliche schriftliche
Nachweis. **Gebucht wird nichts** – der Workflow ist ein reiner Monitor.

**Einrichtung**

1. Der Workflow ist aktiv und wurde getestet: ein Lauf dauert rund 30 Sekunden
   und schreibt den Zustand selbstständig ins Repository zurück. Über
   *Actions → Traukalender-Monitor → Run workflow* lässt sich jederzeit von Hand
   ein Lauf starten.
2. Falls der Schritt „Zustand zurueckschreiben" irgendwann eine Warnung zeigt:
   *Settings → Actions → General → Workflow permissions* auf **Read and write
   permissions** stellen. Ohne Schreibrecht würde der Monitor bei jedem Lauf
   erneut denselben Fund melden.
3. Push aufs Handy: unter *Settings → Secrets and variables → Actions → Secrets*
   ein Secret `TK_NTFY_TOPIC` mit dem selbst gewählten ntfy-Topic anlegen.
   Weitere optionale Secrets: `TK_NTFY_TOKEN`, `TK_SMTP_HOST`, `TK_SMTP_PORT`,
   `TK_SMTP_USER`, `TK_SMTP_PASS`, `TK_MAIL_FROM`, `TK_MAIL_TO`,
   `TK_TELEGRAM_TOKEN`, `TK_TELEGRAM_CHAT`, `TK_WEBHOOK_URL`.
   Was beobachtet wird, lässt sich über *Variables* überschreiben:
   `TK_MONATE` (Standard `2027-07`), `TK_TRAUORTE` (Standard `1210,1345` =
   Kurhaus und Casino-Gesellschaft) und `TK_WOCHENTAGE` (Standard `samstag`,
   `alle` für jeden Tag).
4. *Settings → Notifications* prüfen, damit die Issue-Mails auch ankommen.

**Wenn die Issue-Mail nicht ankommt**

Das Issue wird angelegt, die E-Mail dazu hängt aber an den Kontoeinstellungen.
Der Reihe nach prüfen:

1. <https://github.com/notifications> – steht die Benachrichtigung dort? Dann
   ist nur der Mailversand betroffen, nicht die Meldung selbst.
2. Bei Gmail im Tab **Updates** und im Spam-Ordner nachsehen. GitHub-Mails
   landen dort regelmäßig.
3. *Settings → Notifications*: bei „Subscriptions" muss **Email** angehakt sein.
4. *Settings → Emails*: ist die Adresse **verifiziert**?
5. Auf der Repository-Seite oben rechts *Watch → All Activity* wählen.

Das Issue wird dem Repository-Besitzer zugewiesen und erwähnt ihn im Text –
beides löst auch bei der Standardeinstellung „Participating and @mentions" eine
Benachrichtigung aus. Verlässlicher als jede E-Mail ist trotzdem ntfy: der Push
geht direkt aufs Handy und hängt an keiner Kontoeinstellung.

**Woran man sieht, dass die Überwachung läuft**

* *Actions → Traukalender-Monitor* zeigt die Liste der Läufe. Jeder Lauf schreibt
  eine **Zusammenfassung**, auch wenn nichts gefunden wurde: welcher Monat
  beobachtet wird, bis wann aktuell reserviert werden kann und der Stand je
  Trauort.
* `state/state.json` im Repository enthält den letzten Stand samt Datum der
  letzten Prüfung.

Aufs Handy kommt **nur etwas, wenn wirklich ein Termin gefunden oder reserviert
wurde** – keine Statusmeldungen, keine Lebenszeichen. Meldungen ohne Anlass
stumpfen ab, und genau die eine Nachricht, auf die es ankommt, ginge zwischen
ihnen unter. Ob die Überwachung läuft, steht in der Liste der Actions-Läufe.

Die Push-Nachricht enthält **keinen Link**: ein Tippen darauf soll nicht
ungefragt den Traukalender öffnen. Die Adresse zum Reservieren steht im Issue,
das bei einem Fund entsteht.

**Was beim Dauerbetrieb sonst noch zählt**

* Geplante Workflows laufen nur vom **Standard-Branch** des Repositorys.
* GitHub pausiert geplante Workflows nach **60 Tagen ohne Repository-Aktivität**.
  Deshalb speichert der Monitor Zeitstempel nur tagesgenau: der Zustand ändert
  sich einmal pro Tag, das gibt genau einen Commit am Tag – genug, um das
  Repository aktiv zu halten, ohne die Historie zuzumüllen.
* Geplante Läufe verspäten sich bei hoher Last um einige Minuten. Bei einem
  15-Minuten-Takt ist das unkritisch.
* Doppelte Meldungen sind zweifach abgesichert: über die Zustandsdatei und über
  eine Kennung im Issue-Text. Selbst wenn der Zustand einmal nicht gespeichert
  werden kann, entsteht kein zweites Issue zum selben Fund.
* Läuft der Abruf für alle Trauorte auf einen Fehler, schlägt der Job fehl und
  GitHub schickt eine Fehler-Mail. Einzelne fehlgeschlagene Abfragen werden nur
  protokolliert.
* **Kontingent:** öffentliche Repositories haben unbegrenzte Actions-Minuten,
  deshalb steht der Takt auf `*/15 * * * *` – alle 15 Minuten rund um die Uhr.
  Wird das Repository wieder auf privat gestellt, gilt ein Kontingent von 2.000
  Minuten im Monat; GitHub rechnet jeden Job auf volle Minuten auf, ein Lauf
  kostet also rund eine Minute. Dann passt dieser Takt (~2.900 Minuten) nicht
  mehr und sollte auf etwa `*/30 5-21 * * *` (~1.000 Minuten) zurückgestellt
  werden.
* **Keine persönlichen Daten im Repository.** Der Monitor braucht für die
  Kalenderabfrage weder Namen noch Adressen; Zugangsdaten für die
  Benachrichtigung liegen ausschließlich in den Actions-Secrets. Das sollte so
  bleiben, solange das Repository öffentlich ist.

Alternativ lokal per cron:

```
*/15 * * * * cd /pfad/zum/repo && /usr/bin/python3 traukalender_monitor.py >> monitor.log 2>&1
```

## Trauorte

| ID       | Trauort                          |
|----------|----------------------------------|
| `139598` | Kleiner Festsaal im Neuen Rathaus |
| `1187`   | Altes Rathaus                    |
| `1210`   | Kurhaus                          |
| `1237`   | Nerobergbahn                     |
| `1345`   | Wiesbadener Casino-Gesellschaft  |
| `32243`  | thalhaus Theater Wiesbaden       |

## Wie es funktioniert

Der Monitor spricht denselben Endpunkt an wie der Datepicker der Webseite:

```
GET /common/ajax.php?bereich=portal&modul_id=101&klasse=tko_buergeransicht
    &com=ermitteln_termindaten&trauort=<id>&ortsverwaltung=0
    &monat=<m>&jahr=<j>&min=<JJJJ-MM-TT>&max=<JJJJ-MM-TT>
    &sprache=de&texte=traukalender/texte_buergeransicht
```

Antwort ist JSON mit `appointments` (Tage mit freien Terminen), `holidays`,
`hints` und `tooltips`. Der Parameter `max` wird serverseitig als Filter
angewandt – genau darüber wird die 12-Monats-Grenze abgebildet.

Die konkreten Uhrzeiten eines Tages kommen aus:

```
GET /de/buerger_liste_termine_ajax.html?trauort=<id>&ortsverwaltung=0&tag=<JJJJ-MM-TT>
```

Beide Endpunkte sind ohne Anmeldung und ohne Personendaten abrufbar; das
Formular des Buchungs-Assistenten muss dafür nicht ausgefüllt werden. Der
Monitor legt keine Reservierung an und schickt keine Daten an die Stadt – er
liest nur dieselben Daten, die auch der Browser beim Blättern im Kalender holt.
Abgefragt wird höchstens alle 15 Minuten, das ist weniger Last als ein einzelner
Besucher, der durch den Kalender scrollt.

Kleine Eigenheit: Der Kalender-Endpunkt antwortet mit HTTP 403 und liefert
trotzdem den korrekten JSON-Body. Das Skript wertet den Body deshalb bewusst
auch bei 403 aus.

## Tests

```bash
python3 -m unittest -v
```

Die Tests laufen offline gegen eine Attrappe des Endpunkts.

## Automatisch reservieren

> **Nicht aktiv.** Die automatische Reservierung ist bewusst **nicht** in den
> Workflow eingebunden – der Dauerbetrieb ist ein reiner Monitor. Das Modul
> bleibt als Werkzeug für den Handbetrieb erhalten; von allein läuft es nie.

`traukalender_buchung.py` kann einen Termin auch selbst reservieren. Voreingestellt:
**Samstage im August 2027**, ausschließlich **Kurhaus** oder **Wiesbadener
Casino-Gesellschaft**.

Ausgewählt wird der **frühestmögliche Samstag** und darin die **späteste
Uhrzeit**. Jeder Samstag ist recht, deshalb wird zugegriffen, sobald überhaupt
etwas frei wird, statt auf ein späteres Datum zu warten, das nie kommen muss.
Sind an einem Tag mehrere Zeiten frei, gewinnt die späteste.

> **Eine Reservierung ist verbindlich.** Sie wird beim Standesamt hinterlegt und
> lässt sich per Skript nicht zurücknehmen, sondern nur telefonisch oder per
> E-Mail beim Standesamt Wiesbaden.

### Drei Stufen

| Aufruf | Wirkung |
|---|---|
| `--trocken` (Standard) | sucht und zeigt an, was reserviert würde. Keinerlei Nebenwirkung. |
| `--probe` | kompletter Durchlauf bis zur Bestätigungsseite, dort Abbruch. Blockiert den Termin für die Dauer des Durchlaufs. |
| `--wirklich-buchen` | reserviert verbindlich. Zusätzlich muss `TK_BUCHUNG_AKTIV=1` gesetzt sein. |

```bash
python3 traukalender_buchung.py                       # nur anzeigen
python3 traukalender_buchung.py --monate 2027-08,2027-09
python3 traukalender_buchung.py --wochentage samstag,freitag --trauorte 1210
```

### Sicherungen

* **Kommandozeile bucht nie von allein.** Ohne `--wirklich-buchen` *und*
  `TK_BUCHUNG_AKTIV=1` wird nie reserviert. Beides wird geprüft, bevor
  überhaupt gesucht wird.
* **In Actions ist das Secret die Freigabe.** Der Buchungsschritt läuft nur,
  wenn `TK_PERSONENDATEN` hinterlegt ist – dieses Secret anzulegen *ist* die
  bewusste Scharfschaltung. Notbremse ohne Löschen des Secrets: Variable
  `TK_BUCHUNG_AKTIV` auf `0` setzen.
* **Nur einmal – dreifach abgesichert.** Nach einer erfolgreichen Reservierung
  greifen drei voneinander unabhängige Bremsen:
  1. `state/gebucht.json` liegt im Repository; solange sie existiert, unternimmt
     das Skript nichts mehr.
  2. Das Reservierungs-Issue trägt eine Kennung im Text. Vor jeder Buchung wird
     geprüft, ob es existiert – das wirkt auch dann, wenn der Push der
     Zustandsdatei einmal fehlschlägt.
  3. Der Workflow **schaltet sich selbst ab**. Danach läuft gar nichts mehr,
     bis er unter *Actions → Traukalender-Monitor → Enable workflow* wieder
     eingeschaltet wird.
* **Die Meldung geht immer raus.** Der Zustand wird vor der Benachrichtigung
  geschrieben; schlägt das Schreiben fehl, wird trotzdem benachrichtigt. Eine
  echte Reservierung soll nie unbemerkt bleiben. Zusätzlich zur Push-Nachricht
  entsteht ein Issue mit allen Daten, und die Zusammenfassung des Laufs enthält
  die Bestätigung.
* **Nie doppelt abschicken.** Der Weg bis zur Bestätigungsseite wird bei
  Fehlern wiederholt – das Portal lehnt Formulare gelegentlich grundlos ab. Das
  Absenden selbst wird **nie** wiederholt: Ein fehlgeschlagenes Absenden könnte
  trotzdem angekommen sein.
* **Termin immer freigeben.** Wird ein Termin ausgewählt und der Durchlauf
  bricht danach ab, gibt das Skript ihn wieder frei, damit er nicht für andere
  blockiert bleibt.

### Personendaten hinterlegen

Das Repository ist öffentlich – die Daten gehören deshalb ausschließlich in ein
**Actions-Secret**, nie in eine Datei. Ein einziges Secret namens
`TK_PERSONENDATEN` mit diesem JSON genügt:

```json
{
  "partner1": {
    "anrede": "frau", "vorname": "...", "name": "...",
    "strasse": "... 1", "plz": "12345", "ort": "...",
    "land": "DE", "staat": "deutsch"
  },
  "partner2": { "anrede": "herr", "vorname": "...", "name": "...",
                "strasse": "... 1", "plz": "12345", "ort": "...",
                "land": "DE", "staat": "deutsch" },
  "kontakt": { "email": "...@...", "telefon": "01..." }
}
```

Der Assistent fragt nach Anrede, Vor- und Nachname, Geburtsname (optional),
Anschrift, Land, Staatsangehörigkeit sowie E-Mail und Telefonnummer.
**Geburtsdaten werden nicht abgefragt** – die braucht erst die eigentliche
Anmeldung der Eheschließung beim Standesamt.

Der Workflow ruft dieses Modul **nicht** auf. Wer es wieder scharf schalten
will, muss den entsprechenden Schritt in `.github/workflows/traukalender.yml`
zurückholen und das Secret `TK_PERSONENDATEN` hinterlegen. Solange das nicht
geschieht, wird über GitHub Actions nichts reserviert – auch dann nicht, wenn
das Secret noch aus einer früheren Einrichtung vorhanden ist.

Der Buchungsschritt läuft nur bei **geplanten** Läufen, nicht bei manuell
gestarteten – ein Testlauf von Hand kann also nichts auslösen.

## Reservieren von Hand

Der Monitor benachrichtigt nur – reserviert wird von Hand unter
<https://traukalender.wiesbaden.de/de/Start-159.html>. Der Assistent fragt in
fünf Schritten ab:

1. **Start** – Trauort wählen, Datenschutzhinweis bestätigen.
2. **Personendaten** – je Partner: Anrede, Vorname, Name, Geburtsname (optional),
   Straße & Hausnummer, PLZ, Ort, Land, Staatsangehörigkeit; optional
   Ausweisdokument als PDF/PNG/JPG.
3. **Termine** – Tag im Kalender und Uhrzeit auswählen.
4. **Weitere Daten** – Kontaktdaten (E-Mail, Telefon).
5. **Bestätigung** – Angaben prüfen und Reservierung abschicken.

Es lohnt sich, die Angaben vorher bereitzulegen; beliebte Termine sind schnell weg.
