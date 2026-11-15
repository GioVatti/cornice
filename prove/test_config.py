"""Configurazione, cartelle XDG e destinazione predefinita (serve GTK per GLib; si salta se manca)."""
import json
import os
import sys
import tempfile
import unittest

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import lingua

lingua.imposta(['en'])
try:
    import cornice
except Exception as e:  # noqa: BLE001
    cornice = None
    _ERRORE = str(e)


@unittest.skipIf(cornice is None, 'GTK non disponibile')
class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tmp_cfg_', dir=QUI)
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.p = os.path.join(self.tmp, 'config.json')

    def scrivi(self, dati):
        with open(self.p, 'w', encoding='utf-8') as f:
            f.write(dati if isinstance(dati, str) else json.dumps(dati))

    def test_senza_file_si_deve_scegliere_la_destinazione(self):
        c = cornice.carica_config(self.p)
        self.assertFalse(c['destinazione_scelta'])
        self.assertEqual(c['destinazione'], os.path.join(cornice.cartella_immagini(), 'Approved'))

    def test_file_di_una_versione_precedente_vale_come_scelta(self):
        self.scrivi({'destinazione': '/qualche/cartella', 'spessore': 9, 'cornice': True, 'mosso_attivo': False,
                     'togli_mosso': '/vecchio/percorso/togli_mosso.py', 'finestra': [1200, 800]})
        c = cornice.carica_config(self.p)
        self.assertTrue(c['destinazione_scelta'])
        self.assertEqual(c['destinazione'], '/qualche/cartella')
        self.assertEqual((c['spessore'], c['cornice'], c['mosso_attivo'], c['finestra']), (9, True, False, [1200, 800]))
        self.assertNotIn('togli_mosso', c)              # la chiave tolta si ignora

    def test_scelta_esplicita_non_fatta(self):
        self.scrivi({'destinazione': '/x', 'destinazione_scelta': False})
        self.assertFalse(cornice.carica_config(self.p)['destinazione_scelta'])

    def test_file_rovinato_o_strano(self):
        for contenuto in ('{ non json', '[1, 2]', '{"spessore": "tanto", "finestra": [1, 2], "destinazione": 5}', ''):
            self.scrivi(contenuto)
            c = cornice.carica_config(self.p)
            self.assertFalse(c['destinazione_scelta'], contenuto)
            self.assertEqual(c['spessore'], 6)
            self.assertEqual(c['finestra'], [1400, 900])

    def test_salvataggio_e_rilettura(self):
        c = cornice.carica_config(self.p)
        c['destinazione'], c['destinazione_scelta'], c['spessore'] = '/dove/voglio', True, 12
        cornice.salva_config(c, self.p)
        c2 = cornice.carica_config(self.p)
        self.assertEqual((c2['destinazione'], c2['destinazione_scelta'], c2['spessore']), ('/dove/voglio', True, 12))
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith('.tmp')], [])

    def test_percorsi_xdg(self):
        from gi.repository import GLib
        self.assertEqual(cornice.CONFIG_PATH, os.path.join(GLib.get_user_config_dir(), 'cornice', 'config.json'))
        import mosso
        cache = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
        self.assertEqual(mosso.CACHE, os.path.join(cache, 'cornice', 'mosso.json'))

    def test_destinazione_predefinita_tradotta(self):
        try:
            lingua.imposta(['it'])
            self.assertTrue(cornice.dest_predefinita().endswith('Approvate'))
            lingua.imposta(['en'])
            self.assertTrue(cornice.dest_predefinita().endswith('Approved'))
        finally:
            lingua.imposta(['en'])

    def test_cartella_immagini_ripiega_su_pictures(self):
        from unittest import mock
        with mock.patch.object(cornice.GLib, 'get_user_special_dir', return_value=None):
            self.assertEqual(cornice.cartella_immagini(), os.path.expanduser('~/Pictures'))

    def test_versione_e_riga_di_comando(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(QUI), 'cornice.py'), '--version'],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), 'Cornice 1.0.0')
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(QUI), 'cornice.py'), '--help'],
                           capture_output=True, text=True, env=dict(os.environ, LANGUAGE='en'))
        self.assertIn('Usage: cornice.py', r.stdout)
        self.assertEqual(cornice.APP_ID, 'io.github.giovatti.Cornice')


if __name__ == '__main__':
    unittest.main()
