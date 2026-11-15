"""Anteprima JPEG incorporata nei RAW (incorporata.py) e Cornice senza rawpy."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import incorporata
import lingua
import togli
from prove.sintetiche import scrivi_raw_finto, scrivi_raw_finto_cr3, sintetica

lingua.imposta(['en'])


def immagine(w=900, h=600, seme=1):
    return Image.fromarray((np.clip(sintetica(h, w, seme=seme), 0, 1) ** (1 / 2.2) * 255).astype(np.uint8))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tmp_incorp_', dir=QUI)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def p(self, nome):
        return os.path.join(self.tmp, nome)


class TestIncorporata(Base):
    def test_raw_tiff_prende_la_piu_grande_e_la_raddrizza(self):
        scrivi_raw_finto(self.p('a.nef'), immagine(900, 600), orient=6)
        im = incorporata.immagine_incorporata(self.p('a.nef'))
        self.assertEqual(im.size, (600, 900))               # 900x600 ruotato di 90 gradi, non la miniatura 112x75
        self.assertEqual(im.mode, 'RGB')

    def test_orientamenti(self):
        for orient, atteso in ((1, (900, 600)), (3, (900, 600)), (6, (600, 900)), (8, (600, 900))):
            scrivi_raw_finto(self.p('o.nef'), immagine(900, 600), orient=orient)
            self.assertEqual(incorporata.immagine_incorporata(self.p('o.nef')).size, atteso, orient)

    def test_la_rotazione_e_giusta(self):
        base = Image.new('RGB', (900, 600), (0, 0, 0))
        base.paste((255, 0, 0), (0, 0, 100, 100))             # un riquadro rosso in alto a sinistra
        scrivi_raw_finto(self.p('r.nef'), base, orient=6)       # 6 = ruotare di 90 gradi in senso orario
        im = incorporata.immagine_incorporata(self.p('r.nef'))
        r, g, b = im.getpixel((590, 10))                        # dopo la rotazione il riquadro e' in alto a destra
        self.assertGreater(r, 150)
        self.assertLess(g, 80)

    def test_senza_struttura_tiff_si_cerca_nel_file(self):
        scrivi_raw_finto_cr3(self.p('b.cr3'), immagine(1200, 800))
        im = incorporata.immagine_incorporata(self.p('b.cr3'))
        self.assertEqual(im.size, (1200, 800))

    def test_nessun_jpeg(self):
        with open(self.p('c.raf'), 'wb') as f:
            f.write(os.urandom(5000))
        with self.assertRaises(incorporata.NessunaAnteprima):
            incorporata.immagine_incorporata(self.p('c.raf'))

    def test_file_troncato_o_rovinato_non_solleva_altro(self):
        scrivi_raw_finto(self.p('d.nef'), immagine())
        with open(self.p('d.nef'), 'rb') as f:
            b = f.read()
        for taglio in (4, 10, 40, 80, len(b) // 2):
            with open(self.p('t.nef'), 'wb') as f:
                f.write(b[:taglio])
            try:
                incorporata.immagine_incorporata(self.p('t.nef'))
            except incorporata.NessunaAnteprima:
                pass


class TestSenzaRawpy(Base):
    """Cornice senza rawpy: il RAW si vede con l'anteprima incorporata; il Togli mosso dice chiaro che manca."""

    def test_carica_foto_ripiega_sull_anteprima(self):
        try:
            import cornice
        except Exception as e:  # noqa: BLE001
            self.skipTest('GTK non disponibile: %s' % e)
        scrivi_raw_finto(self.p('IMG_9.NEF'), immagine(900, 600), orient=6)
        with mock.patch.dict(sys.modules, {'rawpy': None}):
            r = cornice.carica_foto(self.p('IMG_9.NEF'))
        self.assertTrue(r.incorporata)
        self.assertEqual((r.w, r.h), (600, 900))

    def test_carica_foto_con_rawpy_che_non_apre_il_file(self):
        try:
            import cornice
            import rawpy  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.skipTest('GTK o rawpy non disponibili: %s' % e)
        scrivi_raw_finto(self.p('IMG_8.NEF'), immagine(900, 600))      # rawpy non sa aprirlo
        r = cornice.carica_foto(self.p('IMG_8.NEF'))
        self.assertTrue(r.incorporata)

    def test_togli_mosso_su_questo_raw_da_un_messaggio_chiaro(self):
        scrivi_raw_finto(self.p('IMG_7.NEF'), immagine())
        with mock.patch.object(togli, 'rawpy', None):
            with self.assertRaises(RuntimeError) as c:
                togli.Foto(self.p('IMG_7.NEF'))
        self.assertIn('rawpy', str(c.exception))
        self.assertIn('not installed', str(c.exception))

    def test_il_jpg_della_coppia_si_puo_ancora_correggere(self):
        im = immagine()
        im.save(self.p('IMG_6.JPG'))
        scrivi_raw_finto(self.p('IMG_6.NEF'), im)
        with mock.patch.object(togli, 'rawpy', None):
            f = togli.Foto(self.p('IMG_6.JPG'))
            self.assertTrue(f.correggibile())
            out = f.elabora_e_salva(self.tmp, 8, 30, 5)
        self.assertTrue(os.path.isfile(out))

    def test_stima_del_mosso_senza_rawpy_da_errore_e_non_si_blocca(self):
        import mosso
        scrivi_raw_finto(self.p('IMG_5.NEF'), immagine())
        with mock.patch.object(togli, 'rawpy', None):
            e = mosso.stima_file(self.p('IMG_5.NEF'))
        self.assertEqual(e.stato, 'errore')


if __name__ == '__main__':
    unittest.main()
