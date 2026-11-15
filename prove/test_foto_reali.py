"""Prove facoltative su foto vere. Si attivano solo con la variabile d'ambiente CORNICE_FOTO_PROVA=<cartella>,
che deve contenere (tutte o solo alcune: le prove senza il loro file si saltano) copie di:
  IMG_3612.JPG/.DNG, IMG_3613.JPG, IMG_3541.JPG, IMG_3468.JPG, IMG_3483.JPG  (mosse)
  IMG_3448.JPG, IMG_3495.JPG, IMG_3676.JPG                                    (nitide)
  un RAW non DNG con sensore Bayer qualunque (ARW, NEF, CR2...)               (per il DNG nuovo)
Non si tocca mai la cartella indicata: i file si copiano in una cartella temporanea."""
import glob
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import lingua
import mosso
import togli

lingua.imposta(['en'])
CARTELLA = os.environ.get('CORNICE_FOTO_PROVA', '')
# (nome, esito atteso, lunghezza attesa, angolo atteso)
MOSSE = [('IMG_3612.JPG', 'recuperabile', 25, 64), ('IMG_3612.DNG', 'recuperabile', 25, 64),
         ('IMG_3613.JPG', 'recuperabile', 19, 54), ('IMG_3541.JPG', 'recuperabile', 17.6, 21),
         ('IMG_3468.JPG', 'recuperabile', 11.6, 60), ('IMG_3483.JPG', 'recuperabile', 9.4, 54)]
NITIDE = ['IMG_3448.JPG', 'IMG_3495.JPG', 'IMG_3676.JPG']


def dist_ang(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


@unittest.skipUnless(CARTELLA and os.path.isdir(CARTELLA), 'imposta CORNICE_FOTO_PROVA=<cartella con foto di prova>')
class TestFotoReali(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tmp_reali_', dir=QUI)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def copia(self, nome):
        src = os.path.join(CARTELLA, nome)
        if not os.path.isfile(src):
            self.skipTest('manca ' + src)
        dst = os.path.join(self.tmp, nome)
        shutil.copy(src, dst)
        return dst

    def test_mosse(self):
        for nome, stato, lung, ang in MOSSE:
            with self.subTest(foto=nome):
                e = mosso.stima_file(self.copia(nome))
                print('\n  %s: %s %.1f px a %.1f gradi (%s)' % (nome, e.stato, e.lunghezza, e.angolo, e.spiegazione), end='')
                self.assertEqual(e.stato, stato)
                self.assertAlmostEqual(e.lunghezza, lung, delta=max(2.0, 0.1 * lung))
                self.assertLessEqual(dist_ang(e.angolo, ang), 4)

    def test_nitide(self):
        for nome in NITIDE:
            with self.subTest(foto=nome):
                e = mosso.stima_file(self.copia(nome))
                self.assertEqual(e.stato, 'nitida')
                self.assertEqual(e.lunghezza, 0.0)

    def test_dng_di_chdk(self):
        import rawpy
        p = self.copia('IMG_3612.DNG')
        f = togli.Foto(p)
        self.assertIsNotNone(f.dng)
        out = f.elabora_e_salva(self.tmp, 25, 64, 10)
        self.assertEqual(os.path.getsize(out), os.path.getsize(p))
        with rawpy.imread(p) as a, rawpy.imread(out) as b:
            self.assertFalse(np.array_equal(a.raw_image_visible, b.raw_image_visible))

    def test_raw_bayer_non_dng(self):
        import rawpy
        trovati = [p for e in ('ARW', 'NEF', 'CR2', 'ORF', 'RW2', 'PEF') for p in glob.glob(os.path.join(CARTELLA, '*.' + e))]
        if not trovati:
            self.skipTest('nessun RAW non DNG in ' + CARTELLA)
        p = os.path.join(self.tmp, os.path.basename(trovati[0]))
        shutil.copy(trovati[0], p)
        f = togli.Foto(p)
        self.assertIsNotNone(f.mos)
        out = f.elabora_e_salva(self.tmp, 30, 60, 5)
        self.assertTrue(out.endswith('_nitida.dng'))
        with rawpy.imread(out) as r:
            self.assertEqual(np.array(r.raw_image_visible).shape, f.mos['px'].shape)

    def test_anteprima_incorporata_dei_raw_veri(self):
        import incorporata
        trovati = [p for e in ('ARW', 'NEF', 'CR2', 'ORF', 'RW2', 'PEF', 'CR3', 'RAF') for p in glob.glob(os.path.join(CARTELLA, '*.' + e))]
        if not trovati:
            self.skipTest('nessun RAW in ' + CARTELLA)
        im = incorporata.immagine_incorporata(trovati[0])
        self.assertGreaterEqual(max(im.size), 500)


if __name__ == '__main__':
    unittest.main()
