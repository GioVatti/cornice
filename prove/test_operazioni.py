"""Prove di operazioni.py. Si lavora solo su file finti dentro prove/tmp_*; dal cestino si tolgono solo le voci create qui."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import lingua
import operazioni as op

lingua.imposta(['en'])          # i messaggi si controllano in inglese, qualunque sia la lingua del sistema


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tmp_ops_', dir=QUI)
        self.sorg = os.path.join(self.tmp, 'sorg')
        self.dest = os.path.join(self.tmp, 'dest')
        os.mkdir(self.sorg)
        self.cestino = []              # (percorso originale, nome nel cestino) creati da questo test
        self.addCleanup(self.pulisci)

    def pulisci(self):
        # dal cestino si tolgono solo le voci create dal test
        try:
            for p, n in self.cestino:
                for nome, orig, data, f in op._voci_cestino():
                    if nome == n and orig == p and p.startswith(self.tmp):
                        f.delete(None)
            for nome, orig, data, f in op._voci_cestino():       # anche le voci rinominate da un ripristino fallito
                if orig and orig.startswith(self.tmp + os.sep):
                    f.delete(None)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def crea(self, nome, contenuto=b'x', cartella=None):
        p = os.path.join(cartella or self.sorg, nome)
        with open(p, 'wb') as f:
            f.write(contenuto)
        return p

    def leggi(self, p):
        with open(p, 'rb') as f:
            return f.read()

    def lista(self, c):
        return sorted(os.listdir(c)) if os.path.isdir(c) else None

    def coppia(self, nome='IMG_1'):
        for e, c in (('.JPG', b'jpg-' + nome.encode()), ('.DNG', b'dng-' + nome.encode() * 100),
                     ('.JPG.xmp', b'xmp1'), ('.DNG.xmp', b'xmp2')):
            self.crea(nome + e, c)
        return op.scatto_di(os.path.join(self.sorg, nome + '.JPG'))


class TestRaggruppamento(Base):
    def test_coppia_e_sidecar(self):
        self.coppia('IMG_3612')
        s = op.elenca(self.sorg)
        self.assertEqual(len(s), 1)
        a = s[0]
        self.assertEqual(a.nome, 'IMG_3612')
        self.assertEqual(len(a.file), 4)
        self.assertTrue(a.immagine.endswith('IMG_3612.JPG'))
        self.assertTrue(a.raw.endswith('IMG_3612.DNG'))
        self.assertEqual(a.da_mostrare, a.immagine)

    def test_xmp_senza_estensione_intermedia(self):
        self.crea('IMG_1.jpg')
        self.crea('IMG_1.xmp')
        s = op.elenca(self.sorg)
        self.assertEqual(len(s), 1)
        self.assertEqual(len(s[0].file), 2)

    def test_solo_raw(self):
        self.crea('IMG_5.dng')
        a = op.elenca(self.sorg)[0]
        self.assertIsNone(a.immagine)
        self.assertEqual(a.da_mostrare, a.raw)

    def test_nitida_e_scatto_a_se(self):
        self.crea('IMG_1.JPG')
        self.crea('IMG_1.DNG')
        self.crea('IMG_1_nitida.DNG')
        self.crea('IMG_1_nitida.DNG.xmp')
        s = {x.nome: x for x in op.elenca(self.sorg)}
        self.assertEqual(set(s), {'IMG_1', 'IMG_1_nitida'})
        self.assertEqual(len(s['IMG_1'].file), 2)
        self.assertEqual(len(s['IMG_1_nitida'].file), 2)

    def test_xmp_orfano_nascosti_sottocartelle(self):
        self.crea('IMG_9.JPG.xmp')
        self.crea('.nascosta.jpg')
        self.crea('IMG_2.JPG')
        os.mkdir(os.path.join(self.sorg, 'IMG_3.JPG'))          # una cartella con nome da foto
        os.mkdir(os.path.join(self.sorg, 'sotto'))
        self.crea('IMG_4.JPG', cartella=os.path.join(self.sorg, 'sotto'))
        self.crea('note.txt')
        self.assertEqual([s.nome for s in op.elenca(self.sorg)], ['IMG_2'])

    def test_radice_maiuscole_e_estensioni(self):
        self.crea('img_1.JPG')
        self.crea('IMG_1.jpg')
        self.crea('IMG_1.Dng')
        s = {x.nome: x for x in op.elenca(self.sorg)}
        self.assertEqual(set(s), {'img_1', 'IMG_1'})
        self.assertTrue(s['IMG_1'].raw.endswith('.Dng'))

    def test_jpg_preferito_su_png(self):
        self.crea('A.png')
        self.crea('A.jpeg')
        self.assertTrue(op.elenca(self.sorg)[0].immagine.endswith('.jpeg'))

    def test_ordine_naturale(self):
        for n in (10, 2, 1, 100, 9):
            self.crea('IMG_%d.JPG' % n)
        self.assertEqual([s.nome for s in op.elenca(self.sorg)],
                         ['IMG_1', 'IMG_2', 'IMG_9', 'IMG_10', 'IMG_100'])

    def test_scatto_di(self):
        self.coppia('IMG_7')
        self.coppia('IMG_8')
        for e in ('.DNG', '.JPG.xmp'):
            s = op.scatto_di(os.path.join(self.sorg, 'IMG_7' + e))
            self.assertEqual(s.nome, 'IMG_7')
            self.assertEqual(len(s.file), 4)
        with self.assertRaises(FileNotFoundError):
            op.scatto_di(os.path.join(self.sorg, 'IMG_99.JPG'))
        self.crea('orfano.xmp')
        with self.assertRaises(ValueError):
            op.scatto_di(os.path.join(self.sorg, 'orfano.xmp'))


class TestNomiDiFotocamere(Base):
    """Il raggruppamento in scatti non dipende dal nome Canon (IMG_xxxx): vale per qualunque radice."""

    def test_radici_di_marche_diverse(self):
        nomi = ['DSC_0001.NEF', 'DSC_0001.JPG', 'DSC_0001.NEF.xmp',        # Nikon
                '_DSC1234.ARW', '_DSC1234.JPG',                              # Sony Adobe RGB
                'DSC01234.ARW', 'DSC01234.JPG',                              # Sony
                'P1010001.RW2', 'P1010001.JPG',                              # Panasonic
                'DSCF1234.RAF', 'DSCF1234.JPG',                              # Fujifilm
                'IMG_0001.CR3', 'IMG_0001.JPG', 'IMG_0001.xmp',              # Canon
                '1234567.ORF', 'PA010001.ORF', 'PA010001.JPG',               # Olympus
                'IMGP1234.PEF', 'IMGP1234.DNG', 'IMGP1234.JPG',              # Pentax (due RAW)
                'Mia foto 2026.jpg', 'DJI_0001.JPG']
        for n in nomi:
            self.crea(n)
        s = {x.nome: x for x in op.elenca(self.sorg)}
        self.assertEqual(set(s), {'DSC_0001', '_DSC1234', 'DSC01234', 'P1010001', 'DSCF1234', 'IMG_0001', '1234567',
                                  'PA010001', 'IMGP1234', 'Mia foto 2026', 'DJI_0001'})
        self.assertTrue(s['DSC_0001'].raw.endswith('.NEF') and s['DSC_0001'].immagine.endswith('.JPG'))
        self.assertEqual(len(s['DSC_0001'].file), 3)
        self.assertTrue(s['P1010001'].raw.endswith('.RW2'))
        self.assertTrue(s['DSCF1234'].raw.endswith('.RAF'))
        self.assertTrue(s['IMG_0001'].raw.endswith('.CR3') and len(s['IMG_0001'].file) == 3)
        self.assertIsNone(s['1234567'].immagine)
        self.assertEqual(len(s['IMGP1234'].file), 3)                  # PEF + DNG + JPG
        self.assertIsNone(s['DJI_0001'].raw)

    def test_approva_e_annulla_con_altri_nomi(self):
        for n in ('DSC_0042.NEF', 'DSC_0042.JPG', 'DSC_0042.NEF.xmp'):
            self.crea(n, n.encode())
        sc = op.scatto_di(os.path.join(self.sorg, 'DSC_0042.NEF'))
        az = op.approva(sc, self.dest)
        self.assertEqual(self.lista(self.dest), ['DSC_0042.JPG', 'DSC_0042.NEF', 'DSC_0042.NEF.xmp'])
        op.annulla(az)
        self.assertEqual(self.lista(self.sorg), ['DSC_0042.JPG', 'DSC_0042.NEF', 'DSC_0042.NEF.xmp'])


class TestHeic(unittest.TestCase):
    """HEIC/HEIF solo se pillow_heif c'e': senza, i file .heic non sono foto per Cornice."""

    def _estensioni(self, con_heif):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            if con_heif:
                with open(os.path.join(d, 'pillow_heif.py'), 'w') as f:
                    f.write('def register_heif_opener():\n    pass\n')
            codice = ('import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import operazioni as o; '
                      'print(".heic" in o.IMG_EXT, ".heif" in o.IMG_EXT)' % (os.path.dirname(QUI), d))
            r = subprocess.run([sys.executable, '-c', codice], capture_output=True, text=True)
            return r.stdout.strip(), r.stderr

    def test_senza_pillow_heif(self):
        if __import__('importlib').util.find_spec('pillow_heif'):
            self.skipTest('pillow_heif e\' installato')
        self.assertEqual(self._estensioni(False)[0], 'False False')

    def test_con_pillow_heif(self):
        ris, err = self._estensioni(True)
        self.assertEqual(ris, 'True True', err)


class TestApprova(Base):
    def test_normale_e_annulla(self):
        s = self.coppia()
        mt = os.path.getmtime(s.file[0])
        orig = {f: self.leggi(f) for f in s.file}
        a = op.approva(s, self.dest)
        self.assertEqual(a.tipo, 'approva')
        self.assertEqual(a.descrizione, 'IMG_1 approved')
        self.assertEqual(self.lista(self.sorg), [])
        self.assertEqual(self.lista(self.dest), sorted(os.path.basename(f) for f in s.file))
        for f in s.file:
            d = os.path.join(self.dest, os.path.basename(f))
            self.assertEqual(self.leggi(d), orig[f])
        self.assertAlmostEqual(os.path.getmtime(os.path.join(self.dest, 'IMG_1.JPG')), mt, delta=0.01)
        self.assertEqual(a.nuovo.cartella, self.dest)
        self.assertTrue(a.nuovo.raw.startswith(self.dest))
        self.assertEqual([x for x in os.listdir(self.dest) if x.endswith('.parziale')], [])
        r = op.annulla(a)
        self.assertEqual(r, s)
        self.assertEqual(self.lista(self.dest), [])
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])

    def test_crea_cartella_destinazione(self):
        s = self.coppia()
        d = os.path.join(self.tmp, 'a', 'b', 'c')
        a = op.approva(s, d)
        self.assertEqual(len(os.listdir(d)), 4)
        op.annulla(a)
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_conflitto_non_tocca_nulla(self):
        s = self.coppia()
        os.mkdir(self.dest)
        self.crea('IMG_1.DNG', b'diverso', self.dest)
        self.crea('IMG_1.JPG', self.leggi(s.immagine), self.dest)     # questo e' identico: va bene
        prima = {f: self.leggi(f) for f in s.file}
        with self.assertRaises(op.Conflitto) as c:
            op.approva(s, self.dest)
        self.assertEqual(c.exception.file, [os.path.join(self.dest, 'IMG_1.DNG')])
        self.assertEqual(self.lista(self.dest), ['IMG_1.DNG', 'IMG_1.JPG'])
        self.assertEqual(self.leggi(os.path.join(self.dest, 'IMG_1.DNG')), b'diverso')
        for f in s.file:
            self.assertEqual(self.leggi(f), prima[f])

    def test_file_gia_identico(self):
        s = self.coppia()
        os.mkdir(self.dest)
        self.crea('IMG_1.JPG', self.leggi(s.immagine), self.dest)
        a = op.approva(s, self.dest)
        self.assertEqual(a.gia_presenti, [os.path.join(self.dest, 'IMG_1.JPG')])
        self.assertEqual(len(a.copiati), 3)
        self.assertEqual(self.lista(self.sorg), [])
        op.annulla(a)
        # quello gia' presente resta in destinazione e viene ricopiato indietro
        self.assertEqual(self.lista(self.dest), ['IMG_1.JPG'])
        self.assertEqual(len(os.listdir(self.sorg)), 4)
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_1.DNG')), b'dng-' + b'IMG_1' * 100)

    def test_stessa_cartella(self):
        s = self.coppia()
        with self.assertRaises(ValueError):
            op.approva(s, self.sorg)
        with self.assertRaises(ValueError):
            op.approva(s, self.sorg + os.sep + '.')
        link = os.path.join(self.tmp, 'link')
        os.symlink(self.sorg, link)
        with self.assertRaises(ValueError):
            op.approva(s, link)
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_destinazione_e_un_file(self):
        s = self.coppia()
        f = self.crea('file', cartella=self.tmp)
        with self.assertRaises(OSError):
            op.approva(s, f)
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_sorgente_mancante(self):
        s = self.coppia()
        os.remove(s.raw)
        with self.assertRaises(FileNotFoundError):
            op.approva(s, self.dest)
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(len(os.listdir(self.sorg)), 3)

    def test_errore_a_meta_copia(self):
        s = self.coppia()
        os.mkdir(self.dest)
        vera = op._copia_verificata
        n = [0]

        def finta(a, b):
            n[0] += 1
            if n[0] == 3:
                raise OSError('disco pieno (simulato)')
            return vera(a, b)
        with mock.patch.object(op, '_copia_verificata', finta):
            with self.assertRaises(OSError):
                op.approva(s, self.dest)
        self.assertEqual(self.lista(self.dest), [])
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_errore_a_meta_copia_crea_e_toglie_cartella(self):
        s = self.coppia()
        d = os.path.join(self.tmp, 'nuova', 'sotto')
        with mock.patch.object(op, '_copia_verificata', side_effect=OSError('simulato')):
            with self.assertRaises(OSError):
                op.approva(s, d)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'nuova')))
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_copia_che_non_coincide(self):
        s = self.coppia()
        os.mkdir(self.dest)
        vero = op._uguali

        def finto(a, b):
            if b.endswith('.parziale') and 'DNG' in b and 'xmp' not in b:
                return False
            return vero(a, b)
        with mock.patch.object(op, '_uguali', finto):
            with self.assertRaises(OSError):
                op.approva(s, self.dest)
        self.assertEqual(self.lista(self.dest), [])
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_errore_nella_cancellazione_rimette_a_posto(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        vera = op._cancella
        n = [0]

        def finta(p):
            n[0] += 1
            if n[0] == 3:
                raise OSError('non si cancella (simulato)')
            return vera(p)
        with mock.patch.object(op, '_cancella', finta):
            with self.assertRaises(OSError):
                op.approva(s, self.dest)
        self.assertFalse(os.path.exists(self.dest))      # copie e cartella creata tolte
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])

    def test_non_sovrascrive_nella_corsa(self):
        # un file compare in destinazione dopo il controllo e prima della pubblicazione
        s = self.coppia()
        os.mkdir(self.dest)
        vera = op._copia_verificata
        n = [0]

        def finta(a, b):
            n[0] += 1
            if n[0] == 2:
                self.crea(os.path.basename(b), b'arrivato dopo', self.dest)
            return vera(a, b)
        with mock.patch.object(op, '_copia_verificata', finta):
            with self.assertRaises(op.Conflitto):
                op.approva(s, self.dest)
        self.assertEqual(len(os.listdir(self.sorg)), 4)
        self.assertEqual(len(os.listdir(self.dest)), 1)          # solo quello arrivato dopo, intatto
        self.assertEqual(self.leggi(os.path.join(self.dest, os.listdir(self.dest)[0])), b'arrivato dopo')

    def test_annulla_con_originale_diverso(self):
        s = self.coppia()
        a = op.approva(s, self.dest)
        self.crea('IMG_1.JPG', b'un altro file')
        with self.assertRaises(op.Conflitto):
            op.annulla(a)
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_1.JPG')), b'un altro file')
        self.assertEqual(len(os.listdir(self.dest)), 4)

    def test_annulla_con_file_mancante_in_destinazione(self):
        s = self.coppia()
        a = op.approva(s, self.dest)
        os.remove(os.path.join(self.dest, 'IMG_1.DNG'))
        with self.assertRaises(FileNotFoundError):
            op.annulla(a)
        self.assertEqual(self.lista(self.sorg), [])
        self.assertEqual(len(os.listdir(self.dest)), 3)

    def test_file_grande(self):
        s = self.coppia()
        with open(s.raw, 'wb') as f:
            f.write(os.urandom(5 * 1024 * 1024 + 17))
        dato = self.leggi(s.raw)
        a = op.approva(s, self.dest)
        self.assertEqual(self.leggi(os.path.join(self.dest, 'IMG_1.DNG')), dato)
        op.annulla(a)
        self.assertEqual(self.leggi(s.raw), dato)


class TestCestino(Base):
    def registra(self, azione):
        self.cestino.extend(azione.voci_cestino)

    def test_scarta_e_annulla(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        a = op.scarta(s)
        self.registra(a)
        self.assertEqual(a.descrizione, 'IMG_1 discarded')
        self.assertEqual(self.lista(self.sorg), [])
        self.assertEqual(len(a.voci_cestino), 4)
        self.assertTrue(all(n is not None for p, n in a.voci_cestino))
        r = op.annulla(a)
        self.assertEqual(r, s)
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])
        self.assertEqual(len(op.elenca(self.sorg)), 1)

    def test_due_file_stesso_nome_in_momenti_diversi(self):
        p = os.path.join(self.sorg, 'IMG_5.JPG')
        s = op.Scatto(self.sorg, 'IMG_5', [p], p, None)
        self.crea('IMG_5.JPG', b'uno')
        a1 = op.scarta(s)
        self.registra(a1)
        time.sleep(1.2)
        self.crea('IMG_5.JPG', b'due')
        a2 = op.scarta(s)
        self.registra(a2)
        self.assertNotEqual(a1.voci_cestino[0][1], a2.voci_cestino[0][1])
        # si annulla prima la piu' recente: torna 'due'
        op.annulla(a2)
        self.assertEqual(self.leggi(p), b'due')
        # annullare l'altra ora non deve sovrascrivere
        with self.assertRaises(op.Conflitto):
            op.annulla(a1)
        self.assertEqual(self.leggi(p), b'due')
        os.remove(p)
        op.annulla(a1)
        self.assertEqual(self.leggi(p), b'uno')

    def test_annulla_la_vecchia_per_prima(self):
        p = os.path.join(self.sorg, 'IMG_5.JPG')
        s = op.Scatto(self.sorg, 'IMG_5', [p], p, None)
        self.crea('IMG_5.JPG', b'uno')
        a1 = op.scarta(s)
        self.registra(a1)
        self.crea('IMG_5.JPG', b'due')
        a2 = op.scarta(s)
        self.registra(a2)
        op.annulla(a1)                       # deve tornare 'uno', non il piu' recente
        self.assertEqual(self.leggi(p), b'uno')
        os.remove(p)
        op.annulla(a2)
        self.assertEqual(self.leggi(p), b'due')

    def test_ripristino_senza_identita_usa_la_piu_recente(self):
        p = os.path.join(self.sorg, 'IMG_6.JPG')
        s = op.Scatto(self.sorg, 'IMG_6', [p], p, None)
        self.crea('IMG_6.JPG', b'uno')
        a1 = op.scarta(s)
        self.registra(a1)
        time.sleep(1.2)
        self.crea('IMG_6.JPG', b'due')
        a2 = op.scarta(s)
        self.registra(a2)
        op._ripristina(p, None)
        self.assertEqual(self.leggi(p), b'due')

    def test_annulla_con_file_tolto_dal_cestino(self):
        s = self.coppia()
        a = op.scarta(s)
        self.registra(a)
        nome = a.voci_cestino[1][1]
        for n, o, d, f in op._voci_cestino():
            if n == nome:
                f.delete(None)
        with self.assertRaises(FileNotFoundError):
            op.annulla(a)
        self.assertEqual(self.lista(self.sorg), [])          # nulla ripristinato a meta'

    def test_scarta_fallisce_a_meta_rimette_a_posto(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        vera = op._nel_cestino
        n = [0]

        def finta(p):
            n[0] += 1
            if n[0] == 3:
                raise OSError('errore simulato')
            return vera(p)
        with mock.patch.object(op, '_nel_cestino', finta):
            with self.assertRaises(OSError):
                op.scarta(s)
        # le voci rimesse a posto non sono piu' nel cestino; per sicurezza si toglie l'eventuale resto
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])

    def test_cestino_non_disponibile_non_tocca_nulla(self):
        s = self.coppia()
        Gio, GLib = op._gio()
        err = GLib.Error.new_literal(Gio.io_error_quark(), 'non supportato', int(Gio.IOErrorEnum.NOT_SUPPORTED))
        orig_trash = Gio.File.trash
        chiamate = []

        def finto(self_, cancellable=None):
            chiamate.append(self_.get_path())
            raise err
        with mock.patch.object(Gio.File, 'trash', finto):
            with self.assertRaises(op.CestinoNonDisponibile):
                op.scarta(s)
        self.assertEqual(len(chiamate), 1)
        self.assertEqual(len(os.listdir(self.sorg)), 4)

    def test_elimina_definitivo(self):
        s = self.coppia()
        op.elimina_definitivo(s)
        self.assertEqual(self.lista(self.sorg), [])
        op.elimina_definitivo(s)             # gia' spariti: nessun errore


class TestSostituisci(Base):
    """sostituisci: originali nel cestino, corretti al loro posto; annulla rimette tutto."""

    def corretti(self, s, nuovi=None):
        """Scrive i temporanei accanto agli originali: [(originale, temporaneo, nome finale)]."""
        out = []
        for f in (s.immagine, s.raw):
            if not f:
                continue
            t = op.temporaneo(f)
            with open(t, 'wb') as fh:
                fh.write(b'NUOVO-' + os.path.basename(f).encode())
            nome = os.path.basename(f)
            if (nuovi or {}).get(f):
                nome = nuovi[f]
            out.append((f, t, nome))
        return out

    def registra(self, az):
        self.cestino.extend(az.voci_cestino)

    def test_sostituisci_e_annulla(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        az = op.sostituisci(s, self.corretti(s))
        self.registra(az)
        self.assertEqual(az.tipo, 'sostituisci')
        self.assertEqual(az.descrizione, 'IMG_1 corrected')
        self.assertEqual(self.leggi(s.immagine), b'NUOVO-IMG_1.JPG')
        self.assertEqual(self.leggi(s.raw), b'NUOVO-IMG_1.DNG')
        for x in ('IMG_1.JPG.xmp', 'IMG_1.DNG.xmp'):                     # i sidecar non si toccano
            self.assertEqual(self.leggi(os.path.join(self.sorg, x)), orig[os.path.join(self.sorg, x)])
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))   # niente temporanei
        self.assertEqual(len(az.voci_cestino), 2)
        self.assertTrue(all(n is not None for p, n in az.voci_cestino))
        self.assertEqual(az.nuovo.nome, 'IMG_1')
        r = op.annulla(az)
        self.assertEqual(r, s)
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))

    def test_raw_non_dng_prende_estensione_dng(self):
        for e, c in (('.JPG', b'jpg'), ('.CR2', b'cr2'), ('.CR2.xmp', b'x')):
            self.crea('IMG_2' + e, c)
        s = op.scatto_di(os.path.join(self.sorg, 'IMG_2.CR2'))
        az = op.sostituisci(s, self.corretti(s, {s.raw: 'IMG_2.dng'}))
        self.registra(az)
        self.assertEqual(self.lista(self.sorg), ['IMG_2.CR2.xmp', 'IMG_2.JPG', 'IMG_2.dng'])
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_2.dng')), b'NUOVO-IMG_2.CR2')
        self.assertEqual(az.nuovo.raw, os.path.join(self.sorg, 'IMG_2.dng'))
        op.annulla(az)
        self.assertEqual(self.lista(self.sorg), ['IMG_2.CR2', 'IMG_2.CR2.xmp', 'IMG_2.JPG'])
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_2.CR2')), b'cr2')
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_2.JPG')), b'jpg')

    def test_solo_il_jpg(self):
        s = self.coppia()
        c = [x for x in self.corretti(s) if x[0] == s.immagine]
        az = op.sostituisci(s, c)
        self.registra(az)
        self.assertEqual(self.leggi(s.immagine), b'NUOVO-IMG_1.JPG')
        self.assertEqual(self.leggi(s.raw), b'dng-' + b'IMG_1' * 100)
        self.assertEqual(len(az.voci_cestino), 1)
        op.annulla(az)
        self.assertEqual(self.leggi(s.immagine), b'jpg-IMG_1')

    def test_nome_finale_occupato_non_tocca_nulla(self):
        for e, c in (('.JPG', b'jpg'), ('.CR2', b'cr2')):
            self.crea('IMG_3' + e, c)
        self.crea('IMG_3.dng', b'un altro file')
        s = op.scatto_di(os.path.join(self.sorg, 'IMG_3.CR2'))
        c = self.corretti(s, {s.raw: 'IMG_3.dng'})
        with self.assertRaises(op.Conflitto) as e:
            op.sostituisci(s, c)
        self.assertEqual(e.exception.file, [os.path.join(self.sorg, 'IMG_3.dng')])
        self.assertEqual(self.lista(self.sorg), ['IMG_3.CR2', 'IMG_3.JPG', 'IMG_3.dng'])    # temporanei tolti
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_3.dng')), b'un altro file')
        self.assertEqual(self.leggi(os.path.join(self.sorg, 'IMG_3.CR2')), b'cr2')

    def test_cestino_non_disponibile(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        Gio, GLib = op._gio()
        err = GLib.Error.new_literal(Gio.io_error_quark(), 'non supportato', int(Gio.IOErrorEnum.NOT_SUPPORTED))
        with mock.patch.object(Gio.File, 'trash', side_effect=err):
            with self.assertRaises(op.CestinoNonDisponibile):
                op.sostituisci(s, self.corretti(s))
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])
        # controllo preventivo: il cestino risulta non utilizzabile
        c = self.corretti(s)
        with mock.patch.object(op, 'puo_cestinare', return_value=False):
            with self.assertRaises(op.CestinoNonDisponibile):
                op.sostituisci(s, c)
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))

    def test_errore_a_meta_cestino_rimette_a_posto(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        vera = op._nel_cestino
        n = [0]

        def finta(p):
            n[0] += 1
            if n[0] == 2:
                raise OSError('errore simulato')
            return vera(p)
        with mock.patch.object(op, '_nel_cestino', finta):
            with self.assertRaises(OSError):
                op.sostituisci(s, self.corretti(s))
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])

    def test_errore_nella_pubblicazione_rimette_a_posto(self):
        s = self.coppia()
        orig = {f: self.leggi(f) for f in s.file}
        vera = op._pubblica
        n = [0]

        def finta(t, d):
            n[0] += 1
            if n[0] == 2:
                raise OSError('disco pieno (simulato)')
            return vera(t, d)
        with mock.patch.object(op, '_pubblica', finta):
            with self.assertRaises(OSError):
                op.sostituisci(s, self.corretti(s))
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))   # niente temporanei
        for f in s.file:
            self.assertEqual(self.leggi(f), orig[f])

    def test_annulla_con_corretto_modificato(self):
        s = self.coppia()
        az = op.sostituisci(s, self.corretti(s))
        self.registra(az)
        with open(s.immagine, 'wb') as f:
            f.write(b'ritoccato dopo')
        with self.assertRaises(op.Conflitto):
            op.annulla(az)
        self.assertEqual(self.leggi(s.immagine), b'ritoccato dopo')
        self.assertEqual(self.leggi(s.raw), b'NUOVO-IMG_1.DNG')           # nulla e' cambiato

    def test_annulla_con_originale_tolto_dal_cestino(self):
        s = self.coppia()
        az = op.sostituisci(s, self.corretti(s))
        self.registra(az)
        nome = az.voci_cestino[1][1]
        for n, o, d, f in op._voci_cestino():
            if n == nome:
                f.delete(None)
        with self.assertRaises(FileNotFoundError):
            op.annulla(az)
        self.assertEqual(self.leggi(s.immagine), b'NUOVO-IMG_1.JPG')      # i corretti restano dove sono
        self.assertEqual(self.leggi(s.raw), b'NUOVO-IMG_1.DNG')

    def test_annulla_fallisce_a_meta_riporta_i_corretti(self):
        s = self.coppia()
        az = op.sostituisci(s, self.corretti(s))
        self.registra(az)
        vera = op._ripristina
        n = [0]

        def finta(p, nome):
            n[0] += 1
            if n[0] == 2:
                raise OSError('errore simulato')
            return vera(p, nome)
        with mock.patch.object(op, '_ripristina', finta):
            with self.assertRaises(OSError):
                op.annulla(az)
        self.assertEqual(self.leggi(s.immagine), b'NUOVO-IMG_1.JPG')
        self.assertEqual(self.leggi(s.raw), b'NUOVO-IMG_1.DNG')
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))
        # il primo originale e' rientrato nel cestino con un nome nuovo: ora l'annullamento riesce
        op.annulla(az)
        self.assertEqual(self.leggi(s.immagine), b'jpg-IMG_1')
        self.assertEqual(self.leggi(s.raw), b'dng-' + b'IMG_1' * 100)
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))

    def test_argomenti_sbagliati(self):
        s = self.coppia()
        c = self.corretti(s)
        with self.assertRaises(ValueError):
            op.sostituisci(s, [])
        with self.assertRaises(ValueError):
            op.sostituisci(s, [(c[0][0], c[0][1], '../fuori.jpg')])
        os.remove(c[1][1])                       # il secondo temporaneo non e' stato passato: e' del test
        self.assertEqual(self.lista(self.sorg), sorted(os.path.basename(f) for f in s.file))


if __name__ == '__main__':
    unittest.main()
