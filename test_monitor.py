"""Offline-Tests fuer den Traukalender-Monitor: python3 -m unittest -v"""

import datetime as dt
import json
import os
import tempfile
import unittest
import urllib.parse

import traukalender_monitor as tm


class DatumsHelfer(unittest.TestCase):
    def test_plus_monate_jahreswechsel(self):
        self.assertEqual(tm.plus_monate(dt.date(2026, 8, 2), 12), dt.date(2027, 8, 2))
        self.assertEqual(tm.plus_monate(dt.date(2026, 12, 15), 1), dt.date(2027, 1, 15))

    def test_plus_monate_monatsende(self):
        self.assertEqual(tm.plus_monate(dt.date(2027, 1, 31), 1), dt.date(2027, 2, 28))
        self.assertEqual(tm.plus_monate(dt.date(2027, 3, 31), 1), dt.date(2027, 4, 30))
        # 2028 ist ein Schaltjahr
        self.assertEqual(tm.plus_monate(dt.date(2028, 1, 31), 1), dt.date(2028, 2, 29))

    def test_monate_parsen(self):
        self.assertEqual(tm.monate_parsen(["2027-08", "2027-9"]), [(2027, 8), (2027, 9)])
        with self.assertRaises(SystemExit):
            tm.monate_parsen(["August 2027"])
        with self.assertRaises(SystemExit):
            tm.monate_parsen(["2027-13"])

    def test_deutsches_datum(self):
        self.assertEqual(tm.deutsches_datum("2027-08-14"), "Samstag, 14.08.2027")

    def test_trauorte_parsen(self):
        self.assertEqual(tm.trauorte_parsen("alle"), list(tm.TRAUORTE))
        self.assertEqual(tm.trauorte_parsen("1187, 1210"), [1187, 1210])
        with self.assertRaises(SystemExit):
            tm.trauorte_parsen("Altes Rathaus")


class ZustandUndDedupe(unittest.TestCase):
    def test_neue_treffer_und_merken(self):
        zustand = {"gemeldet": {}}
        treffer = [
            tm.Treffer(1187, "2027-08-14", True, ["10:00 - 10:30"]),
            tm.Treffer(1210, "2027-08-21", False, []),
        ]
        self.assertEqual(len(tm.neue_treffer(zustand, treffer)), 2)
        tm.als_gemeldet_merken(zustand, treffer)
        self.assertEqual(tm.neue_treffer(zustand, treffer), [])

        nachzuegler = tm.Treffer(1187, "2027-08-28", True, [])
        neu = tm.neue_treffer(zustand, treffer + [nachzuegler])
        self.assertEqual([t.datum for t in neu], ["2027-08-28"])

    def test_speichern_und_laden(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, "unter", "state.json")
            tm.zustand_speichern(pfad, {"gemeldet": {"1187": ["2027-08-14"]}})
            self.assertEqual(tm.zustand_laden(pfad)["gemeldet"]["1187"], ["2027-08-14"])

    def test_kaputte_datei_wird_toleriert(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, "state.json")
            with open(pfad, "w", encoding="utf-8") as fh:
                fh.write("{kein json")
            self.assertEqual(tm.zustand_laden(pfad), {"gemeldet": {}})


class Marker(unittest.TestCase):
    def test_stabil_und_reihenfolgeunabhaengig(self):
        a = tm.Treffer(1187, "2027-08-14", True, [])
        b = tm.Treffer(1210, "2027-08-21", False, [])
        self.assertEqual(tm.marker_bauen([a, b]), tm.marker_bauen([b, a]))

    def test_unterscheidet_verschiedene_funde(self):
        a = tm.Treffer(1187, "2027-08-14", True, [])
        c = tm.Treffer(1187, "2027-08-15", True, [])
        self.assertNotEqual(tm.marker_bauen([a]), tm.marker_bauen([c]))

    def test_unabhaengig_von_uhrzeiten(self):
        """Uhrzeiten aendern sich, wenn andere reservieren - das darf kein
        zweites Issue fuer denselben Tag ausloesen."""
        a = tm.Treffer(1187, "2027-08-14", True, ["10:00 - 10:30"])
        b = tm.Treffer(1187, "2027-08-14", True, ["11:00 - 11:30", "12:00 - 12:30"])
        self.assertEqual(tm.marker_bauen([a]), tm.marker_bauen([b]))


class Nachricht(unittest.TestCase):
    def test_titel_unterscheidet_buchbar(self):
        diagnose = {"zielmonate": ["2027-08"], "reservierbar_bis": "2027-08-02"}
        titel, text = tm.nachricht_bauen(
            [tm.Treffer(1187, "2027-08-14", True, ["10:00 - 10:30"])], diagnose
        )
        self.assertIn("frei!", titel)
        self.assertIn("JETZT reservierbar", text)
        self.assertIn("Altes Rathaus", text)
        self.assertIn("10:00 - 10:30", text)

        titel, text = tm.nachricht_bauen(
            [tm.Treffer(1187, "2027-08-14", False, [])], diagnose
        )
        self.assertIn("angelegt", titel)
        self.assertIn("ausserhalb der 12-Monats-Frist", text)


class ApiMitAttrappe(unittest.TestCase):
    """pruefen() gegen eine Attrappe des AJAX-Endpunkts."""

    def setUp(self):
        self.original = tm.http_get
        self.aufrufe = []

    def tearDown(self):
        tm.http_get = self.original

    def attrappe(self, angelegt, uhrzeiten=("08:30 - 09:00",)):
        def _get(url, timeout=30, retries=3):
            self.aufrufe.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if "buerger_liste_termine_ajax" in url:
                tag = query["tag"][0]
                return "".join(
                    f'<div class="termin" title="Termin am {tag} um {z} Uhr"></div>'
                    for z in uhrzeiten
                )
            max_datum = query["max"][0]
            tage = [t for t in angelegt if t <= max_datum]
            return json.dumps(
                {"appointments": tage, "hints": [], "holidays": [], "tooltips": []}
            )
        return _get

    def test_findet_nur_angelegte_tage(self):
        tm.http_get = self.attrappe(["2027-08-14", "2027-08-28"])
        treffer, diagnose, fehler = tm.pruefen(
            [1187], [(2027, 8)], dt.date(2026, 8, 2)
        )
        self.assertEqual(fehler, [])
        self.assertEqual([t.datum for t in treffer], ["2027-08-14", "2027-08-28"])
        # Portal-Fenster endet am 2027-08-02, also ist noch nichts reservierbar.
        self.assertEqual([t.buchbar for t in treffer], [False, False])
        self.assertEqual(diagnose["reservierbar_bis"], "2027-08-02")
        self.assertEqual(treffer[0].uhrzeiten, ["08:30 - 09:00"])

    def test_buchbar_wenn_im_zwoelfmonatsfenster(self):
        tm.http_get = self.attrappe(["2027-08-14"])
        treffer, _, _ = tm.pruefen([1187], [(2027, 8)], dt.date(2026, 8, 20))
        self.assertTrue(treffer[0].buchbar)

    def test_leerer_monat_gibt_keine_treffer(self):
        tm.http_get = self.attrappe([])
        treffer, _, fehler = tm.pruefen([1187], [(2027, 8)], dt.date(2026, 8, 2))
        self.assertEqual(treffer, [])
        self.assertEqual(fehler, [])

    def test_netzfehler_wird_gemeldet_statt_zu_stuerzen(self):
        def kaputt(url, timeout=30, retries=3):
            raise RuntimeError("Netzwerk weg")
        tm.http_get = kaputt
        treffer, _, fehler = tm.pruefen([1187], [(2027, 8)], dt.date(2026, 8, 2))
        self.assertEqual(treffer, [])
        self.assertEqual(len(fehler), 1)

    def test_403_mit_json_body_wird_akzeptiert(self):
        """Der echte Endpunkt antwortet mit HTTP 403 und gueltigem JSON."""
        import io
        import urllib.error

        rufe = []

        def fake_urlopen(req, timeout=30):
            rufe.append(req.full_url)
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden", {},
                io.BytesIO(b'{"appointments": ["2027-08-14"]}'),
            )

        original = tm.urllib.request.urlopen
        tm.urllib.request.urlopen = fake_urlopen
        try:
            daten = tm.monat_abfragen(1187, 2027, 8, "2026-08-02", "2029-08-02")
        finally:
            tm.urllib.request.urlopen = original
        self.assertEqual(daten["appointments"], ["2027-08-14"])
        self.assertEqual(len(rufe), 1)


if __name__ == "__main__":
    unittest.main()
