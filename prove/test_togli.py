"""Prove di togli.py (algoritmo di Togli mosso dentro Cornice) su immagini sintetiche, in cartelle temporanee."""
import hashlib
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

import numpy as np
from PIL import Image

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import lingua
import togli
from prove.sintetiche import mosaico_sintetico, meta_dng, sintetica, sfoca

lingua.imposta(['en'])          # i messaggi si controllano in inglese, qualunque sia la lingua del sistema


def dist_ang(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tmp_togli_', dir=QUI)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def jpg(self, nome='foto.jpg', orient=None, img=None):
        a = sintetica(300, 400) if img is None else img
        im = Image.fromarray((np.clip(a, 0, 1) ** (1 / 2.2) * 255 + 0.5).astype(np.uint8))
        ex = Image.Exif()
        ex[0x010F] = 'ProvaMarca'
        ex[0x0110] = 'ProvaModello'
        if orient:
            ex[0x0112] = orient
        p = os.path.join(self.tmp, nome)
        im.save(p, quality=92, exif=ex.tobytes())
        return p

    def dng(self, nome='m.dng', px=None):
        px = mosaico_sintetico() if px is None else px
        m, meta = meta_dng(*px.shape)
        p = os.path.join(self.tmp, nome)
        togli.scrivi_dng(p, px, m, meta)
        return p, px


class TestLingua(unittest.TestCase):
    def test_messaggi_in_italiano(self):
        lingua.imposta(['it'])
        self.addCleanup(lingua.imposta, ['en'])
        f = togli.Foto.__new__(togli.Foto)
        f.path, f.nome, f.raw, f.dng, f.mos, f.lin = '/x/x.raf', 'x.raf', True, None, None, None
        with self.assertRaises(RuntimeError) as c:
            f.elabora_e_salva('/tmp', 8, 30, 5)
        self.assertIn('Sensore non supportato', str(c.exception))


class TestStima(unittest.TestCase):
    def prova(self, lung, ang, seme):
        lin = sfoca(sintetica(seme=seme), lung, ang)
        esito, l, a, come = togli.stima_completa(lin)
        print('\n  stima %.0f px a %.0f gradi -> %s %.1f px a %.1f gradi (%s)' % (lung, ang, esito, l, a, come), end='')
        self.assertIn(esito, ('sicura', 'zone'))
        self.assertAlmostEqual(l, lung, delta=max(2.0, 0.12 * lung))
        self.assertLessEqual(dist_ang(a, ang), 5)

    def test_mosso_noto(self):
        self.prova(20, 35, 1)

    def test_mosso_noto_verticale_e_orizzontale(self):
        self.prova(14, 90, 3)
        self.prova(25, 0, 4)

    def test_immagine_nitida_non_da_falso_allarme(self):
        esito, l, a, come = togli.stima_completa(sintetica(seme=5))
        self.assertNotIn(esito, ('sicura', 'zone'))     # al massimo un dubbio ('incerta'), mai una certezza

    def test_deconvoluzione_migliora(self):
        orig = sintetica(300, 300, seme=6)
        mosso = sfoca(orig, 12, 40)
        dopo = togli.togli_mosso(mosso, 12, 40, 20)
        err0 = float(np.abs(mosso - orig).mean())
        err1 = float(np.abs(dopo - orig).mean())
        self.assertLess(err1, 0.8 * err0)


class TestAltaRisoluzione(unittest.TestCase):
    def test_fattore_di_riduzione(self):
        r = lambda lato: togli._riduzione(np.zeros((10, lato, 3), np.float32))
        self.assertEqual([r(100), r(6500), r(6501), r(13000), r(13001)], [1, 1, 2, 2, 3])

    def test_lunghezza_massima_del_cursore(self):
        f = togli.lunghezza_massima_cursore
        self.assertEqual([f(1000), f(5000), f(6000), f(9000), f(12000)], [60.0, 60.0, 80.0, 110.0, 150.0])

    def test_riduci_media_a_blocchi(self):
        a = np.arange(4 * 6 * 3, dtype=np.float32).reshape(4, 6, 3)
        r = togli._riduci(a, 2)
        self.assertEqual(r.shape, (2, 3, 3))
        self.assertAlmostEqual(float(r[0, 0, 0]), float(a[:2, :2, 0].mean()))
        self.assertEqual(togli._riduci(np.zeros((5, 7, 3), np.float32), 2).shape, (2, 3, 3))   # bordi scartati

    def test_foto_fino_a_6500_px_ha_lo_stesso_risultato_di_sempre(self):
        lin = sfoca(sintetica(900, 1200, seme=21), 18, 50)
        self.assertEqual(togli.stima_completa(lin), togli._stima_completa(lin))
        self.assertEqual(togli.stima_zona(lin, 600, 450), togli._stima_zona(lin, 600, 450))

    def test_foto_da_9000_px_si_stima_su_copia_ridotta(self):
        lin = sfoca(sintetica(1200, 9000, seme=22), 40, 30)         # 9000 px sul lato lungo: k = 2
        esito, l, a, come = togli.stima_completa(lin)
        print('\n  9000 px: 40 px a 30 gradi -> %s %.1f px a %.1f gradi (%s)' % (esito, l, a, come), end='')
        self.assertIn(esito, ('sicura', 'zone'))
        self.assertAlmostEqual(l, 40, delta=0.15 * 40)
        self.assertLessEqual(dist_ang(a, 30), 5)
        lz, az, voti, prove = togli.stima_zona(lin, 4500, 600)
        self.assertGreaterEqual(voti, togli.VOTI_ZONA)
        self.assertAlmostEqual(lz, 40, delta=0.15 * 40)


class TestJpg(Base):
    def test_salva_jpg_con_exif_e_orientamento(self):
        p = self.jpg(orient=6)
        prima = sha(p)
        f = togli.Foto(p)
        self.assertTrue(f.correggibile())
        self.assertFalse(f.esce_dng())
        self.assertEqual(f.nome_uscita(), 'foto_nitida.jpg')
        self.assertEqual(f.nome_finale(), 'foto.jpg')
        voci = []
        out = f.elabora_e_salva(self.tmp, 8, 30, 10, avanzamento=voci.append)
        self.assertEqual(out, os.path.join(self.tmp, 'foto_nitida.jpg'))
        self.assertEqual(sha(p), prima)                                  # l'originale non cambia
        self.assertTrue(voci and voci[-1] == 1.0)
        with Image.open(out) as im:
            ex = im.getexif()
            self.assertEqual(ex.get(0x010F), 'ProvaMarca')
            self.assertEqual(ex.get(0x0112), 1)                          # la foto e' stata raddrizzata
            self.assertEqual(im.size, (300, 400))                        # 400x300 ruotato di 90 gradi
            self.assertEqual(im.format, 'JPEG')
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith('.')], [])

    def test_destinazione_esplicita_e_nomi_finali(self):
        p = self.jpg('a.JPEG')
        f = togli.Foto(p)
        self.assertEqual(f.nome_finale(), 'a.JPEG')
        dest = os.path.join(self.tmp, '.cornice-a-1234.tmp')            # estensione qualunque: il formato e' esplicito
        self.assertEqual(f.elabora_e_salva(self.tmp, 8, 30, 5, destinazione=dest), dest)
        with Image.open(dest) as im:
            self.assertEqual(im.format, 'JPEG')
        im = Image.new('RGB', (300, 300), (90, 120, 200))
        q = os.path.join(self.tmp, 'b.png')
        im.save(q)
        g = togli.Foto(q)
        self.assertEqual(g.nome_uscita(), 'b_nitida.jpg')
        self.assertEqual(g.nome_finale(), 'b.jpg')

    def test_rifiuta_di_scrivere_sull_originale(self):
        p = self.jpg()
        prima = sha(p)
        f = togli.Foto(p)
        with self.assertRaises(RuntimeError):
            f.elabora_e_salva(self.tmp, 8, 30, 5, destinazione=p)
        collegamento = os.path.join(self.tmp, 'collegamento.jpg')
        os.symlink(p, collegamento)
        with self.assertRaises(RuntimeError):
            f.elabora_e_salva(self.tmp, 8, 30, 5, destinazione=collegamento)
        duro = os.path.join(self.tmp, 'duro.jpg')
        os.link(p, duro)
        with self.assertRaises(RuntimeError):
            f.elabora_e_salva(self.tmp, 8, 30, 5, destinazione=duro)
        self.assertEqual(sha(p), prima)
        self.assertEqual(sorted(os.listdir(self.tmp)), ['collegamento.jpg', 'duro.jpg', 'foto.jpg'])

    def test_sovrascrive_una_copia_esistente(self):
        p = self.jpg()
        f = togli.Foto(p)
        vecchia = os.path.join(self.tmp, 'foto_nitida.jpg')
        with open(vecchia, 'wb') as fh:
            fh.write(b'vecchia')
        f.elabora_e_salva(self.tmp, 8, 30, 5)
        with Image.open(vecchia) as im:
            self.assertEqual(im.format, 'JPEG')


class TestDng(Base):
    def test_scrivi_dng_si_riapre_con_rawpy(self):
        import rawpy
        p, px = self.dng()
        with rawpy.imread(p) as r:
            vis = np.array(r.raw_image_visible)
            self.assertEqual(r.raw_type, rawpy.RawType.Flat)
            self.assertEqual(r.color_desc, b'RGBG')
        h, w = px.shape
        self.assertEqual(vis.shape, (h, w))
        self.assertTrue(np.array_equal(vis, px))                        # il mosaico torna identico
        with open(p, 'rb') as f:
            self.assertIn(b'Cornice\0', f.read(4096))                   # tag Software

    def foto_bayer(self, p, px):
        """Una Foto con mosaico Bayer come quella che rawpy descrive per un RAW qualunque (ARW, CR2...)."""
        f = togli.Foto.__new__(togli.Foto)
        f.path, f.nome, f.raw, f.dng, f.ruotata = p, os.path.basename(p), True, None, False
        m, meta = meta_dng(*px.shape)
        m['px'] = px
        f.mos = m
        f.lin = np.zeros((px.shape[0] // 2, px.shape[1] // 2, 3), np.float32)
        return f

    def test_dng_nuovo_round_trip(self):
        import rawpy
        p, px = self.dng()
        f = self.foto_bayer(p, px)
        self.assertTrue(f.esce_dng() and f.correggibile())
        self.assertEqual(f.nome_uscita(), 'm_nitida.dng')
        out = f.elabora_e_salva(self.tmp, 6, 20, 8, freno=0.3)
        self.assertEqual(os.path.basename(out), 'm_nitida.dng')
        with rawpy.imread(out) as r:
            vis = np.array(r.raw_image_visible)
        self.assertEqual(vis.shape, px.shape)
        self.assertFalse(np.array_equal(vis, px))                       # il mosaico e' stato davvero elaborato
        self.assertLess(abs(float(vis.mean()) - float(px.mean())), 0.05 * float(px.mean()))
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith('.')], [])

    def test_raw_non_correggibile_da_errore_chiaro(self):
        f = togli.Foto.__new__(togli.Foto)                              # un RAW senza mosaico Bayer, senza DNG di CHDK
        f.path = os.path.join(self.tmp, 'x.raf')
        f.nome, f.raw, f.dng, f.mos = 'x.raf', True, None, None
        f.lin = np.zeros((10, 10, 3), np.float32)
        self.assertFalse(f.esce_dng())
        self.assertFalse(f.correggibile())
        with self.assertRaises(RuntimeError) as c:
            f.elabora_e_salva(self.tmp, 8, 30, 5)
        self.assertIn('Sensor not supported', str(c.exception))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_senza_rawpy_errore_chiaro(self):
        p, px = self.dng()
        from unittest import mock
        with mock.patch.object(togli, 'rawpy', None):
            with self.assertRaises(RuntimeError) as c:
                togli.Foto(p)
        self.assertIn('rawpy', str(c.exception))

    def test_nome_raw_non_dng_diventa_dng(self):
        p, px = self.dng('IMG_7.dng')
        f = self.foto_bayer(p, px)
        self.assertEqual(f.nome_finale(), 'IMG_7.dng')
        f.nome = 'IMG_7.CR2'
        self.assertEqual(f.nome_finale(), 'IMG_7.dng')
        self.assertEqual(f.nome_uscita(), 'IMG_7_nitida.dng')


class TestInterruzione(Base):
    def test_ferma_gia_acceso(self):
        p = self.jpg()
        f = togli.Foto(p)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(togli.Interrotto):
            f.elabora_e_salva(self.tmp, 8, 30, 10, ferma=ev)
        self.assertEqual(os.listdir(self.tmp), ['foto.jpg'])           # niente file a meta'
        self.assertIsNone(togli.togli_mosso(sintetica(100, 100), 8, 30, 10, ferma=ev))
        with self.assertRaises(togli.Interrotto):
            togli.mosaico_nitido(mosaico_sintetico(), [64] * 4, 4095, 4095, 6, 20, 10, ferma=ev)

    def test_ferma_durante_il_calcolo(self):
        a = sintetica(900, 1200, seme=7)
        im = Image.fromarray((a ** (1 / 2.2) * 255).astype(np.uint8))
        p = os.path.join(self.tmp, 'grande.jpg')
        im.save(p)
        f = togli.Foto(p)
        ev = threading.Event()
        esito = []

        def lavoro():
            try:
                f.elabora_e_salva(self.tmp, 15, 40, 60, ferma=ev)
                esito.append('finito')
            except togli.Interrotto:
                esito.append('interrotto')
        t0 = time.time()
        th = threading.Thread(target=lavoro)
        th.start()
        time.sleep(0.4)
        ev.set()
        th.join(30)
        self.assertFalse(th.is_alive())
        self.assertEqual(esito, ['interrotto'])
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(sorted(os.listdir(self.tmp)), ['grande.jpg'])

    def test_senza_ferma_funziona_come_prima(self):
        p = self.jpg()
        out = togli.Foto(p).elabora_e_salva(self.tmp, 8, 30, 5, ferma=threading.Event())
        self.assertTrue(os.path.isfile(out))


class TestEstensioni(unittest.TestCase):
    def test_una_sola_fonte(self):
        import operazioni
        self.assertIs(togli.RAW_EXT, operazioni.RAW_EXT)
        self.assertIs(togli.IMG_EXT, operazioni.IMG_EXT)

    def test_nessun_gtk_ne_darktable(self):
        import subprocess
        r = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, %r); import togli; '
                            'print("gi" in sys.modules, hasattr(togli, "trova_darktable"))' % os.path.dirname(QUI)],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), 'False False', r.stderr)


if __name__ == '__main__':
    unittest.main()
