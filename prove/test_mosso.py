"""Prove di mosso.py su copie di foto vere (in prove/tmp_*). Stampa i tempi misurati."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import lingua
import mosso
from prove.sintetiche import scrivi_jpg, sintetica, sfoca

lingua.imposta(['en'])

# foto sintetiche: nome -> (seme, lunghezza del mosso, angolo); lunghezza 0 = nitida
FOTO = {'A': (11, 24, 60), 'B': (12, 16, 20), 'C': (13, 18, 100), 'N1': (14, 0, 0), 'N2': (15, 0, 0), 'N3': (16, 0, 0)}
RIFERIMENTI = [('A', 'recuperabile', 24, 60), ('B', 'recuperabile', 16, 20), ('C', 'recuperabile', 18, 100)]
NITIDE = ['N1', 'N2', 'N3']


def dist_ang(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='tmp_mosso_', dir=QUI)
        cls.cartella = os.path.join(cls.tmp, 'foto')
        os.mkdir(cls.cartella)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copia(self, nome, ext='JPG'):
        """Il JPG sintetico `nome` (generato una volta sola per classe, nella cartella di prova)."""
        dst = os.path.join(self.cartella, '%s.%s' % (nome, ext))
        if not os.path.exists(dst):
            seme, lung, ang = FOTO[nome]
            lin = sintetica(seme=seme)
            scrivi_jpg(dst, sfoca(lin, lung, ang) if lung else lin)
        return dst

    def stimatore(self, nome_cache='cache.json'):
        s = mosso.Stimatore(cache=os.path.join(self.tmp, nome_cache))
        self.addCleanup(s.chiudi)
        return s

    def attendi(self, s, percorsi, timeout=120):
        ris, ev = {}, threading.Event()

        def fatto(p, e):
            ris[p] = e
            if len(ris) == len(percorsi):
                ev.set()
        for p in percorsi:
            s.richiedi(p, fatto)
        self.assertTrue(ev.wait(timeout), 'tempo scaduto')
        return ris


class TestStimaFile(Base):
    def test_riferimenti(self):
        for nome, stato, lung, ang in RIFERIMENTI:
            with self.subTest(foto=nome):
                p = self.copia(nome)
                t = time.time()
                e = mosso.stima_file(p)
                dt = time.time() - t
                print('\n  stima_file %s: %s %.1f px a %.1f gradi (%s) in %.1f s'
                      % (nome, e.stato, e.lunghezza, e.angolo, e.spiegazione, dt), end='')
                self.assertEqual(e.stato, stato)
                self.assertAlmostEqual(e.lunghezza, lung, delta=max(2.0, 0.1 * lung))
                self.assertLessEqual(dist_ang(e.angolo, ang), 4)

    def test_nitide(self):
        for nome in NITIDE:
            with self.subTest(foto=nome):
                p = self.copia(nome)
                t = time.time()
                e = mosso.stima_file(p)
                print('\n  stima_file %s: %s (%s) in %.1f s' % (nome, e.stato, e.spiegazione, time.time() - t), end='')
                # le immagini sintetiche possono dare al massimo un dubbio ('incerto'), mai una certezza;
                # con foto vere nitide l'esito e' 'nitida' (vedi test_foto_reali.py)
                self.assertIn(e.stato, ('nitida', 'incerto'))

    def test_errore_file_rovinato(self):
        p = os.path.join(self.cartella, 'rotta.jpg')
        with open(p, 'wb') as f:
            f.write(b'non e una foto')
        e = mosso.stima_file(p)
        self.assertEqual(e.stato, 'errore')
        self.assertTrue(e.spiegazione)

    def test_errore_file_mancante_e_senza_numpy(self):
        self.assertEqual(mosso.stima_file(os.path.join(self.cartella, 'non_esiste.jpg')).stato, 'errore')
        p = self.copia('A')
        with mock.patch.object(mosso, 'togli', None):                # numpy o PIL assenti
            self.assertEqual(mosso.stima_file(p).stato, 'errore')

    def test_esito_serializzabile(self):
        e = mosso.Esito('recuperabile', 25.0, 64.0, 'x')
        self.assertEqual(mosso.Esito.da_dict(json.loads(json.dumps(e.to_dict()))), e)


class TestStimatore(Base):
    def test_calcolo_cache_e_condivisione(self):
        p = self.copia('A')
        s = self.stimatore('c1.json')
        self.assertTrue(s.disponibile)
        self.assertIsNone(s.in_cache(p))
        chiamate = []
        ev = threading.Event()
        t = time.time()
        s.richiedi(p, lambda q, e: chiamate.append(('a', q, e)))
        s.richiedi(p, lambda q, e: (chiamate.append(('b', q, e)), ev.set()))     # richiesta doppia
        self.assertTrue(ev.wait(120))
        dt = time.time() - t
        print('\n  Stimatore, primo calcolo (con avvio del processo figlio): %.1f s' % dt, end='')
        time.sleep(0.2)
        self.assertEqual(sorted(c[0] for c in chiamate), ['a', 'b'])
        self.assertIs(chiamate[0][2], chiamate[1][2])                         # un solo calcolo condiviso
        e = chiamate[0][2]
        self.assertEqual(e.stato, 'recuperabile')
        self.assertEqual(s.in_cache(p), e)
        # file json valido e nuova istanza: legge dalla cache senza calcolare
        with open(os.path.join(self.tmp, 'c1.json')) as f:
            self.assertEqual(len(json.load(f)), 1)
        s2 = self.stimatore('c1.json')
        t = time.time()
        self.assertEqual(s2.in_cache(p), e)
        got = []
        s2.richiedi(p, lambda q, x: got.append(x))
        self.assertEqual(got, [e])                                             # subito, nello stesso thread
        self.assertLess(time.time() - t, 0.5)
        self.assertIsNone(s2._ex)                                              # nessun processo avviato
        # i file temporanei non restano
        self.assertEqual([f for f in os.listdir(self.tmp) if f.startswith('.mosso-')], [])

    def test_cambio_file_invalida_la_cache(self):
        p = self.copia('A')
        q = os.path.join(self.cartella, 'copia_mod.jpg')
        shutil.copy2(p, q)
        s = self.stimatore('c2.json')
        self.attendi(s, [q])
        self.assertIsNotNone(s.in_cache(q))
        os.utime(q, (1000000000, 1000000000))
        self.assertIsNone(s.in_cache(q))

    def test_errori_non_in_cache(self):
        p = os.path.join(self.cartella, 'rotta2.jpg')
        with open(p, 'wb') as f:
            f.write(b'xx')
        s = self.stimatore('c3.json')
        r = self.attendi(s, [p])
        self.assertEqual(r[p].stato, 'errore')
        time.sleep(0.2)
        self.assertIsNone(s.in_cache(p))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'c3.json')))
        # file inesistente: errore subito
        r = self.attendi(s, [os.path.join(self.cartella, 'mai.jpg')], timeout=5)
        self.assertEqual(list(r.values())[0].stato, 'errore')

    def test_solo_questi_annulla_la_coda(self):
        a = self.copia('A')
        b = os.path.join(self.cartella, 'b.jpg')
        c = os.path.join(self.cartella, 'c.jpg')
        shutil.copy2(self.copia('B'), b)
        shutil.copy2(self.copia('C'), c)
        s = self.stimatore('c4.json')
        visti = []
        ev = threading.Event()
        s.richiedi(a, lambda q, e: (visti.append(q), ev.set()))
        s.richiedi(b, lambda q, e: visti.append(q))
        s.richiedi(c, lambda q, e: visti.append(q))
        s.solo_questi([a])
        self.assertTrue(ev.wait(120))
        time.sleep(4)                                       # il tempo che b e c avrebbero impiegato
        self.assertEqual(visti, [a])
        self.assertIsNone(s.in_cache(b))
        self.assertIsNone(s.in_cache(c))
        # e dopo l'annullamento una nuova richiesta funziona
        r = self.attendi(s, [c])
        self.assertEqual(r[c].stato, 'recuperabile')

    def test_ordine_e_tempi_per_foto(self):
        nomi = [('A', 'JPG'), ('B', 'JPG'), ('C', 'JPG'), ('N1', 'JPG')]
        ps = [self.copia(n, e) for n, e in nomi]
        ps = [shutil.copy2(p, os.path.join(self.cartella, 'seq%d.jpg' % i)) for i, p in enumerate(ps)]
        s = self.stimatore('c5.json')
        tempi = {}
        t0 = time.time()
        ev = threading.Event()

        def fatto(p, e):
            tempi[p] = (time.time() - t0, e.stato)
            if len(tempi) == len(ps):
                ev.set()
        for p in ps:
            s.richiedi(p, fatto)
        self.assertTrue(ev.wait(180))
        ordine = sorted(ps, key=lambda p: tempi[p][0])
        self.assertEqual(ordine, ps)                        # una alla volta, in ordine di richiesta
        prec = 0
        for p in ps:
            print('\n  coda: %s pronta a %.1f s (+%.1f s) %s' % (os.path.basename(p), tempi[p][0], tempi[p][0] - prec, tempi[p][1]), end='')
            prec = tempi[p][0]
        self.assertEqual([tempi[p][1] for p in ps][:3], ['recuperabile'] * 3)
        self.assertIn(tempi[ps[3]][1], ('nitida', 'incerto'))

    def test_cache_rovinata_e_non_disponibile(self):
        cc = os.path.join(self.tmp, 'c6.json')
        with open(cc, 'w') as f:
            f.write('{ non json')
        s = mosso.Stimatore(cache=cc)
        self.addCleanup(s.chiudi)
        self.assertIsNone(s.in_cache(self.copia('A')))
        s2 = mosso.Stimatore(cache=os.path.join(self.tmp, 'c7.json'))
        self.assertTrue(s2.disponibile)
        s2.chiudi()
        with mock.patch.object(mosso, 'togli', None):
            s3 = mosso.Stimatore(cache=os.path.join(self.tmp, 'c7.json'))
            self.assertFalse(s3.disponibile)
            got = []
            s3.richiedi(self.copia('A'), lambda q, e: got.append(e.stato))
            self.assertEqual(got, ['errore'])
            s3.chiudi()

    def test_chiudi_mentre_calcola(self):
        p = os.path.join(self.cartella, 'chiusura.jpg')
        shutil.copy2(self.copia('C'), p)
        s = mosso.Stimatore(cache=os.path.join(self.tmp, 'c8.json'))
        s.richiedi(p, lambda q, e: None)
        time.sleep(1.0)
        t = time.time()
        s.chiudi()
        self.assertLess(time.time() - t, 3)

    def test_nessun_gtk(self):
        import subprocess
        r = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, %r); import mosso; print("gi" in sys.modules)'
                            % os.path.dirname(QUI)], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), 'False', r.stderr)


if __name__ == '__main__':
    unittest.main()
