"""Traduzioni: ogni stringa del codice è nel .pot, ha la traduzione italiana con gli stessi segnaposto,
e il .mo compilato è aggiornato."""
import gettext
import os
import sys
import unittest

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)
sys.path.insert(0, RADICE)
sys.path.insert(0, os.path.join(RADICE, 'tools'))
import i18n  # noqa: E402
import lingua  # noqa: E402


class TestTraduzioni(unittest.TestCase):
    def test_il_pot_e_aggiornato(self):
        """Se fallisce: python3 tools/i18n.py extract (poi tradurre le nuove stringhe e `compile`)."""
        _, pot = i18n.leggi_po(os.path.join(RADICE, 'po', 'cornice.pot'))
        nel_pot = {(v['id'], v['plurale']) for v in pot}
        nel_codice = {(i, p) for i, p, pos in i18n.stringhe_nel_codice()}
        self.assertEqual(nel_codice - nel_pot, set(), 'stringhe nel codice ma non nel .pot')
        self.assertEqual(nel_pot - nel_codice, set(), 'stringhe nel .pot ma non piu\' nel codice')

    def test_ogni_stringa_ha_la_traduzione_italiana(self):
        _, pot = i18n.leggi_po(os.path.join(RADICE, 'po', 'cornice.pot'))
        _, it = i18n.leggi_po(os.path.join(RADICE, 'po', 'it.po'))
        tradotte = {(v['id'], v['plurale']): v for v in it}
        for v in pot:
            t = tradotte.get((v['id'], v['plurale']))
            self.assertIsNotNone(t, 'manca in it.po: %r' % v['id'])
            self.assertFalse(t['fuzzy'], 'fuzzy: %r' % v['id'])
            self.assertTrue(all(t['tr']) if v['plurale'] is not None else t['tr'][0], 'senza traduzione: %r' % v['id'])

    def test_segnaposto_uguali(self):
        _, it = i18n.leggi_po(os.path.join(RADICE, 'po', 'it.po'))
        for v in it:
            for t in v['tr']:
                self.assertEqual(i18n.segnaposto(t), i18n.segnaposto(v['id']), 'segnaposto diversi in %r' % v['id'])

    def test_il_controllo_dello_strumento_non_trova_problemi(self):
        self.assertEqual(i18n.controlla(), [])

    def test_il_mo_compilato_e_aggiornato(self):
        _, it = i18n.leggi_po(os.path.join(RADICE, 'po', 'it.po'))
        t = gettext.translation('cornice', lingua.CARTELLA_LOCALE, languages=['it'])
        for v in it:
            if v['plurale'] is None:
                self.assertEqual(t.gettext(v['id']), v['tr'][0], 'il .mo non e\' aggiornato: %r' % v['id'])
            else:
                self.assertEqual(t.ngettext(v['id'], v['plurale'], 1), v['tr'][0])
                self.assertEqual(t.ngettext(v['id'], v['plurale'], 2), v['tr'][1])

    def test_lingue(self):
        try:
            lingua.imposta(['it'])
            self.assertEqual(lingua.lingua_attiva(), 'it')
            self.assertEqual(lingua._('Save copy'), 'Salva copia')
            self.assertEqual(lingua.ngettext('%d photo left', '%d photos left', 1) % 1, '1 foto resta')
            self.assertEqual(lingua.ngettext('%d photo left', '%d photos left', 5) % 5, '5 foto restano')
            lingua.imposta(['en'])
            self.assertEqual(lingua._('Save copy'), 'Save copy')
            self.assertEqual(lingua.ngettext('%d photo left', '%d photos left', 1) % 1, '1 photo left')
            lingua.imposta(['de'])                       # lingua senza traduzione: si resta in inglese
            self.assertEqual(lingua._('Save copy'), 'Save copy')
        finally:
            lingua.imposta(['en'])

    def test_separatore_decimale_e_data_per_lingua(self):
        try:
            import cornice
        except Exception as e:  # noqa: BLE001
            self.skipTest('GTK non disponibile: %s' % e)
        try:
            lingua.imposta(['it'])
            self.assertEqual(cornice.dec(3.5, 1), '3,5')
            self.assertEqual(cornice.fmt_tempo(2.5), '2,5 s')
            self.assertEqual(cornice.fmt_ev(-0.7), '-0,7 EV')
            self.assertEqual(cornice.fmt_data('2026:11:15 10:30:00'), '15/11/2026 10:30')
            lingua.imposta(['en'])
            self.assertEqual(cornice.dec(3.5, 1), '3.5')
            self.assertEqual(cornice.fmt_tempo(2.5), '2.5 s')
            self.assertEqual(cornice.fmt_ev(-0.7), '-0.7 EV')
            self.assertEqual(cornice.fmt_data('2026:11:15 10:30:00'), '2026-11-15 10:30')
        finally:
            lingua.imposta(['en'])

    def test_estrazione_e_compilazione_sono_ripetibili(self):
        """extract non cambia nulla se il codice non e' cambiato (le traduzioni non si perdono)."""
        _, voci = i18n.leggi_po(os.path.join(RADICE, 'po', 'it.po'))
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'x.po')
            i18n.scrivi_po(out, i18n.intestazione_per('it'), voci)
            _, di_nuovo = i18n.leggi_po(out)
        self.assertEqual([(v['id'], v['plurale'], v['tr']) for v in voci],
                         [(v['id'], v['plurale'], v['tr']) for v in di_nuovo])


if __name__ == '__main__':
    unittest.main()
