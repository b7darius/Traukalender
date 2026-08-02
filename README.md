# Traukalender-Monitor Wiesbaden

Überwacht den [Traukalender der Landeshauptstadt Wiesbaden](https://traukalender.wiesbaden.de/de/Start-159.html)
und meldet sich sofort, sobald Termine im Zielmonat – standardmäßig **August 2027** –
freigeschaltet werden.

Reines Python 3 aus der Standardbibliothek, keine Abhängigkeiten, kein Browser.

## Warum August 2027 heute noch nicht wählbar ist

Der Kalender ist zwar offen, aber zwei Dinge begrenzen ihn:

1. **12-Monats-Regel.** Das Portal setzt das Ende des wählbaren Zeitraums auf
   *heute + 12 Monate* („Termine können maximal 12 Monate im Voraus reserviert
   werden"). Am 02.08.2026 endete der Zeitraum entsprechend am 02.08.2027.
2. **Termine sind noch nicht angelegt.** Auch wenn man die Grenze umgeht, liefert
   der Kalender für August 2027 an **keinem** der sechs Trauorte Termine. Der
   späteste derzeit hinterlegte Tag ist der 31.07.2027 (Altes Rathaus).

Der Monitor prüft deshalb beides getrennt und meldet **beide** Ereignisse:

| Sicht     | Bedeutung                                                       |
|-----------|-----------------------------------------------------------------|
| `angelegt`     | Die Stadt hat Termine im Zielmonat eingestellt.            |
| `reservierbar` | Der Tag liegt zusätzlich im aktuellen 12-Monats-Fenster und ist jetzt buchbar. |

So kommt die Nachricht schon in dem Moment, in dem die Termine im System
auftauchen – nicht erst, wenn sie auch anklickbar sind.

Wie weit die Trauorte gefüllt sind, unterscheidet sich deutlich (Stand 02.08.2026,
ermittelt mit `--horizont`):

| Trauort                           | letzter hinterlegter Tag |
|-----------------------------------|--------------------------|
| Altes Rathaus                     | 31.07.2027               |
| thalhaus Theater Wiesbaden        | 24.07.2027               |
| Nerobergbahn                      | 16.07.2027               |
| Wiesbadener Casino-Gesellschaft   | 09.04.2027               |
| Kurhaus                           | 06.03.2027               |
| Kleiner Festsaal im Neuen Rathaus | 16.01.2027               |

Der Monitor beobachtet standardmäßig alle sechs Trauorte gleichzeitig.

## Schnellstart

```bash
# einmalig prüfen
python3 traukalender_monitor.py

# dauerhaft laufen lassen, alle 15 Minuten
python3 traukalender_monitor.py --watch

# anderer Zielmonat / nur bestimmte Trauorte
python3 traukalender_monitor.py --monate 2027-08,2027-09 --trauorte 1187,1210

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
export TK_MAIL_TO=maike240300@gmail.com
```

### Telegram

```bash
export TK_TELEGRAM_TOKEN=123456:ABC...   # von @BotFather
export TK_TELEGRAM_CHAT=987654321        # eigene Chat-ID
```

### Beliebiger Webhook

```bash
export TK_WEBHOOK_URL=https://...   # bekommt JSON: {title, text, url}
```

## Dauerbetrieb per GitHub Actions

`.github/workflows/traukalender.yml` prüft halbstündlich zwischen 07:00 und
23:00 deutscher Zeit, schreibt den Zustand ins Repository zurück und legt bei
einem Treffer zusätzlich ein Issue an.
Der Workflow läuft ohne jede weitere Konfiguration – die Issue-Mail von GitHub
ist der Meldeweg, der immer funktioniert. Push aufs Handy kommt über ntfy dazu.

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
   Der Zielmonat lässt sich als *Variable* `TK_MONATE` überschreiben
   (Standard `2027-08`).
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
* **Montags um 11:05 deutscher Zeit** kommt ein Lebenszeichen aufs Handy
  („Monitor läuft – 2027-08 noch ohne Termine"). Bleibt es zweimal aus, stimmt
  etwas nicht.
* `state/state.json` im Repository enthält den letzten Stand samt Datum der
  letzten Prüfung.

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
* **Kontingent:** private Repositories haben 2.000 Actions-Minuten im Monat,
  öffentliche unbegrenzt viele. GitHub rechnet jeden Job auf volle Minuten auf,
  ein Lauf kostet also rund eine Minute. Der eingestellte Takt
  (`*/30 5-21 * * *`, rund 34 Läufe am Tag) verbraucht etwa 1.000 Minuten im
  Monat und passt damit dauerhaft ins Kontingent eines privaten Repositorys.
  Ein Takt von 15 Minuten rund um die Uhr wären ~2.900 Minuten – das reicht
  nicht. Wer schneller prüfen will, macht das Repository öffentlich (es liegen
  keine persönlichen Daten darin) und setzt den Takt auf `*/15 * * * *`.

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

## Reservieren, wenn die Meldung kommt

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
