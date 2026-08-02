"""Offline-Tests fuer die automatische Reservierung: python3 -m unittest -v

Kein Test spricht mit dem Portal. Der Weg durch den Assistenten wird gegen
eine Attrappe geprueft - besonders die Zusicherung, dass nach dem Absenden
nie ein zweites Mal abgeschickt wird.
"""

import datetime as dt
import json
import os
import unittest
from unittest import mock

import traukalender_buchung as tb


class Wochentage(unittest.TestCase):
    def test_namen_und_zahlen(self):
        self.assertEqual(tb.wochentage_parsen("samstag"), {5})
        self.assertEqual(tb.wochentage_parsen("Samstag, Sonntag"), {5, 6})
        self.assertEqual(tb.wochentage_parsen("5"), {5})

    def test_unbekannt_bricht_ab(self):
        with self.assertRaises(SystemExit):
            tb.wochentage_parsen("caturday")
        with self.assertRaises(SystemExit):
            tb.wochentage_parsen("")


class Personendaten(unittest.TestCase):
    def test_json_aus_einem_secret(self):
        daten = {
            "partner1": {"anrede": "frau", "vorname": "A", "name": "B",
                         "strasse": "Weg 1", "plz": "12345", "ort": "Ort"},
            "partner2": {"anrede": "herr", "vorname": "C", "name": "D",
                         "strasse": "Weg 1", "plz": "12345", "ort": "Ort"},
            "kontakt": {"email": "a@b.de", "telefon": "0123"},
        }
        with mock.patch.dict(os.environ, {"TK_PERSONENDATEN": json.dumps(daten)}, clear=True):
            p1, p2, k = tb.personendaten_aus_umgebung()
        self.assertEqual(p1.vorname, "A")
        self.assertEqual(p2.name, "D")
        self.assertEqual(k.email, "a@b.de")
        # Vorgabewerte greifen, wenn nichts angegeben ist
        self.assertEqual(p1.land, "DE")
        self.assertEqual(p1.staatsangehoerigkeit, "deutsch")
        self.assertEqual(p1.fehlend(), [])

    def test_einzelvariablen_als_rueckfallebene(self):
        umgebung = {
            "TK_P1_VORNAME": "A", "TK_P1_NAME": "B", "TK_P1_STRASSE": "Weg 1",
            "TK_P1_PLZ": "12345", "TK_P1_ORT": "Ort",
            "TK_KONTAKT_EMAIL": "a@b.de", "TK_KONTAKT_TELEFON": "0123",
        }
        with mock.patch.dict(os.environ, umgebung, clear=True):
            p1, p2, k = tb.personendaten_aus_umgebung()
        self.assertEqual(p1.vorname, "A")
        self.assertEqual(k.telefon, "0123")
        self.assertEqual(p2.fehlend(), ["vorname", "name", "strasse_hausnummer", "plz", "ort"])

    def test_kaputtes_json_bricht_ab(self):
        with mock.patch.dict(os.environ, {"TK_PERSONENDATEN": "{kaputt"}, clear=True):
            with self.assertRaises(SystemExit):
                tb.personendaten_aus_umgebung()


class Formularfelder(unittest.TestCase):
    def test_spamfallen_bleiben_leer(self):
        f = ('<form><input type="text" name="email" value="">'
             '<input type="text" name="url" value="">'
             '<input type="hidden" name="date" value="123">'
             '<input type="checkbox" name="ok" value="1"></form>')
        daten = tb.formularfelder(f)
        self.assertEqual(daten["email"], "")
        self.assertEqual(daten["url"], "")
        self.assertEqual(daten["ok"], "1")
        self.assertEqual(daten["date"], "123")

    def test_attribute_die_nur_name_enthalten_werden_ignoriert(self):
        f = '<form><input data-option-fieldname="#webid" type="text" name="echt" value="x"></form>'
        self.assertEqual(list(tb.formularfelder(f)), ["echt"])


class DatenPruefung(unittest.TestCase):
    VOLLSTAENDIG = {
        "partner1": {"anrede": "frau", "vorname": "A", "name": "B",
                     "strasse": "W 1", "plz": "1", "ort": "O"},
        "partner2": {"anrede": "herr", "vorname": "C", "name": "D",
                     "strasse": "W 1", "plz": "1", "ort": "O"},
        "kontakt": {"email": "a@b.de", "telefon": "0123"},
    }

    def test_ohne_secret_fehler(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tb.daten_pruefen(), tb.EXIT_ERROR)

    def test_unvollstaendig_fehler(self):
        roh = {"partner1": {"vorname": "A"}, "partner2": {}, "kontakt": {}}
        with mock.patch.dict(os.environ, {"TK_PERSONENDATEN": json.dumps(roh)}, clear=True):
            self.assertEqual(tb.daten_pruefen(), tb.EXIT_ERROR)

    def test_vollstaendig_ok(self):
        with mock.patch.dict(os.environ,
                             {"TK_PERSONENDATEN": json.dumps(self.VOLLSTAENDIG)}, clear=True):
            self.assertEqual(tb.daten_pruefen(), tb.EXIT_OK)

    def test_gibt_keine_inhalte_preis(self):
        """Die Protokolle eines oeffentlichen Repositorys sind fuer jeden
        lesbar - es darf kein Name und keine Adresse darin auftauchen."""
        import contextlib
        import io
        puffer = io.StringIO()
        with mock.patch.dict(os.environ,
                             {"TK_PERSONENDATEN": json.dumps(self.VOLLSTAENDIG)}, clear=True):
            with contextlib.redirect_stdout(puffer):
                tb.daten_pruefen()
        ausgabe = puffer.getvalue()
        for geheim in ("a@b.de", "0123", "W 1"):
            self.assertNotIn(geheim, ausgabe)

    def test_buchung_bricht_bei_luecken_vor_der_suche_ab(self):
        umgebung = {"TK_BUCHUNG_AKTIV": "1",
                    "TK_PERSONENDATEN": json.dumps({"partner1": {"vorname": "A"}})}
        with mock.patch.dict(os.environ, umgebung, clear=True), \
             mock.patch.object(tb, "kandidaten") as kandidaten:
            code = tb.main(["--wirklich-buchen", "--state", "/nonexistent/gebucht.json"])
        self.assertEqual(code, tb.EXIT_ERROR)
        kandidaten.assert_not_called()


class SlotSortierung(unittest.TestCase):
    def test_fruehester_tag_darin_spaeteste_uhrzeit(self):
        s = [
            tb.Slot(1210, "2027-08-07", "10:00", "10:30"),
            tb.Slot(1345, "2027-08-07", "16:00", "16:45"),
            tb.Slot(1210, "2027-08-28", "17:00", "17:45"),
        ]
        s.sort(key=lambda x: (x.datum, tb._zeit_absteigend(x.von)))
        self.assertEqual(
            [(x.datum, x.von) for x in s],
            [("2027-08-07", "16:00"), ("2027-08-07", "10:00"), ("2027-08-28", "17:00")],
        )

    def test_uhrzeiten_werden_numerisch_verglichen(self):
        # Textvergleich wuerde "9:30" hinter "17:00" einsortieren.
        self.assertLess(tb._zeit_absteigend("17:00"), tb._zeit_absteigend("9:30"))
        self.assertLess(tb._zeit_absteigend("17:45"), tb._zeit_absteigend("17:00"))

    def test_beschreibung_ist_lesbar(self):
        s = tb.Slot(1210, "2027-08-28", "17:00", "17:45")
        self.assertEqual(s.beschreibung(), "Samstag, 28.08.2027, 17:00 - 17:45 Uhr, Kurhaus")


class BuchenAblauf(unittest.TestCase):
    """buchen() gegen eine Attrappe der Schritte."""

    def setUp(self):
        self.slot = tb.Slot(1210, "2027-08-28", "17:00", "17:45", termin_id="42")
        self.p1 = tb.Person(vorname="A", name="B", strasse_hausnummer="W 1",
                            plz="1", ort="O")
        self.p2 = tb.Person(vorname="C", name="D", strasse_hausnummer="W 1",
                            plz="1", ort="O")
        self.k = tb.Kontakt(email="a@b.de", telefon="0123")

    def _attrappe(self, bis_scheitern=0, schritt5_fehler=False):
        zaehler = {"bis": 0, "submit": 0, "freigaben": 0}

        def bis_bestaetigung(sitzung, slot, p1, p2, kontakt, pause):
            zaehler["bis"] += 1
            if zaehler["bis"] <= bis_scheitern:
                sitzung.termin_gehalten = True
                raise tb.BuchungsFehler("Formular abgelehnt")
            sitzung.termin_gehalten = True
            return "<form name='buchung-bestaetigen'></form>"

        def schritt5(sitzung, html):
            zaehler["submit"] += 1
            if schritt5_fehler:
                raise tb.BuchungsFehler("Reservierung abgelehnt")
            sitzung.termin_gehalten = False
            return "<title>Bestaetigung</title> Vorgangsnummer: ABC-123"

        def freigeben(self_):
            if self_.termin_gehalten:
                zaehler["freigaben"] += 1
                self_.termin_gehalten = False

        return bis_bestaetigung, schritt5, freigeben, zaehler

    def test_erfolgreiche_buchung(self):
        bis, s5, frei, z = self._attrappe()
        with mock.patch.object(tb, "_bis_bestaetigungsseite", bis), \
             mock.patch.object(tb, "_schritt5", s5), \
             mock.patch.object(tb.Sitzung, "termin_freigeben", frei), \
             mock.patch.object(tb.time, "sleep", lambda *_: None):
            ergebnis = tb.buchen(self.slot, self.p1, self.p2, self.k, wirklich=True)
        self.assertTrue(ergebnis["gebucht"])
        self.assertEqual(ergebnis["bestaetigung"]["vorgangsnummer"], "ABC-123")
        self.assertEqual(z["submit"], 1)

    def test_wiederholung_vor_dem_absenden(self):
        bis, s5, frei, z = self._attrappe(bis_scheitern=2)
        with mock.patch.object(tb, "_bis_bestaetigungsseite", bis), \
             mock.patch.object(tb, "_schritt5", s5), \
             mock.patch.object(tb.Sitzung, "termin_freigeben", frei), \
             mock.patch.object(tb.time, "sleep", lambda *_: None):
            ergebnis = tb.buchen(self.slot, self.p1, self.p2, self.k, wirklich=True, versuche=3)
        self.assertTrue(ergebnis["gebucht"])
        self.assertEqual(z["bis"], 3)
        self.assertEqual(z["submit"], 1)
        # Jeder gescheiterte Versuch muss den Termin wieder freigeben.
        self.assertEqual(z["freigaben"], 2)

    def test_kein_zweiter_versuch_nach_fehler_beim_absenden(self):
        """Das ist die wichtigste Zusicherung: ein fehlgeschlagenes Absenden
        koennte trotzdem angekommen sein - nie erneut abschicken."""
        bis, s5, frei, z = self._attrappe(schritt5_fehler=True)
        with mock.patch.object(tb, "_bis_bestaetigungsseite", bis), \
             mock.patch.object(tb, "_schritt5", s5), \
             mock.patch.object(tb.Sitzung, "termin_freigeben", frei), \
             mock.patch.object(tb.time, "sleep", lambda *_: None):
            with self.assertRaises(tb.BuchungsFehler):
                tb.buchen(self.slot, self.p1, self.p2, self.k, wirklich=True, versuche=3)
        self.assertEqual(z["submit"], 1)

    def test_probe_schickt_nie_ab(self):
        bis, s5, frei, z = self._attrappe()
        with mock.patch.object(tb, "_bis_bestaetigungsseite", bis), \
             mock.patch.object(tb, "_schritt5", s5), \
             mock.patch.object(tb.Sitzung, "termin_freigeben", frei), \
             mock.patch.object(tb.time, "sleep", lambda *_: None):
            ergebnis = tb.buchen(self.slot, self.p1, self.p2, self.k, wirklich=False)
        self.assertFalse(ergebnis["gebucht"])
        self.assertEqual(z["submit"], 0)
        self.assertEqual(z["freigaben"], 1)


class Sicherungen(unittest.TestCase):
    def test_wirklich_buchen_braucht_freischaltung(self):
        """Ohne Freischaltung muss abgebrochen werden, bevor ueberhaupt
        gesucht wird."""
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(tb, "kandidaten") as kandidaten:
            code = tb.main(["--wirklich-buchen", "--monate", "2027-08",
                            "--trauorte", "1210", "--heute", "2026-08-02",
                            "--state", "/nonexistent/gebucht.json"])
        self.assertEqual(code, tb.EXIT_ERROR)
        kandidaten.assert_not_called()

    def _erfolgreiche_buchung_simulieren(self, tmp, speichern_faellt_aus=False):
        slot = tb.Slot(1210, "2027-08-07", "16:00", "16:45", termin_id="7")
        gesendet = []
        umgebung = {
            "TK_BUCHUNG_AKTIV": "1",
            "TK_PERSONENDATEN": json.dumps({
                "partner1": {"vorname": "A", "name": "B", "strasse": "W 1",
                             "plz": "1", "ort": "O"},
                "partner2": {"vorname": "C", "name": "D", "strasse": "W 1",
                             "plz": "1", "ort": "O"},
                "kontakt": {"email": "a@b.de", "telefon": "0123"},
            }),
        }
        with mock.patch.dict(os.environ, umgebung, clear=True), \
             mock.patch.object(tb, "kandidaten", return_value=[slot]), \
             mock.patch.object(tb, "buchen", return_value={
                 "gebucht": True, "slot": slot.als_dict(),
                 "bestaetigung": {"titel": "Bestaetigung", "vorgangsnummer": "X-1"}}), \
             mock.patch.object(tb, "benachrichtigen",
                               side_effect=lambda t, x, c: gesendet.append(t) or ["ntfy"]), \
             mock.patch.object(
                 tb, "buchungszustand_speichern",
                 side_effect=(OSError("Platte voll") if speichern_faellt_aus else None)):
            code = tb.main(["--wirklich-buchen", "--state",
                            os.path.join(tmp, "gebucht.json")])
        return code, gesendet

    def test_erfolg_meldet_und_endet_mit_code_20(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            code, gesendet = self._erfolgreiche_buchung_simulieren(tmp)
        self.assertEqual(code, tb.EXIT_GEBUCHT)
        self.assertEqual(len(gesendet), 1)
        self.assertIn("reserviert", gesendet[0])

    def test_meldung_kommt_auch_wenn_zustand_nicht_schreibbar_ist(self):
        """Eine echte Reservierung darf nie unbemerkt bleiben, nur weil die
        Zustandsdatei nicht geschrieben werden konnte."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            code, gesendet = self._erfolgreiche_buchung_simulieren(
                tmp, speichern_faellt_aus=True)
        self.assertEqual(code, tb.EXIT_GEBUCHT)
        self.assertEqual(len(gesendet), 1)

    def test_bereits_gebucht_unternimmt_nichts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, "gebucht.json")
            tb.buchungszustand_speichern(pfad, {"gebucht": True, "beschreibung": "x"})
            with mock.patch.object(tb, "kandidaten") as kandidaten:
                code = tb.main(["--state", pfad, "--wirklich-buchen"])
            self.assertEqual(code, tb.EXIT_OK)
            kandidaten.assert_not_called()


if __name__ == "__main__":
    unittest.main()
