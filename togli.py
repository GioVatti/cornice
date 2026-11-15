# SPDX-License-Identifier: GPL-3.0-or-later
"""Togli mosso dentro Cornice: algoritmo senza interfaccia (derivato da Togli mosso, dello stesso autore).

Corregge il mosso rettilineo (colpo sul pulsante, tremolio) di una foto.
Formati: JPG/PNG/TIFF/WebP a 8 bit (esce un JPG) e RAW con sensore Bayer (esce un DNG vero: il mosso
viene tolto dai dati del sensore, prima della ricostruzione dei colori). I RAW si leggono con rawpy.
Gli originali non vengono mai modificati qui: si scrive sempre un file nuovo.
Niente GTK. Un calcolo si puo' fermare con un threading.Event (`ferma`): `togli_mosso` rende None,
`mosaico_nitido` e `Foto.elabora_e_salva` sollevano `Interrotto`.
"""
import os, struct, uuid, math
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image, ImageOps

from operazioni import IMG_EXT, RAW_EXT     # un'unica fonte per le estensioni
from lingua import _

try:
    import rawpy                       # lettura dei RAW
except Exception:
    rawpy = None

SUFFISSO = '_nitida'
LAMBDA_MAX = 0.02     # regolarizzazione al massimo del cursore "Freno rumore"


class Interrotto(Exception):
    """Il calcolo e' stato fermato dall'utente."""


# ----------------------------------------------------------------------------- TIFF e DNG a basso livello

_DIM = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
# dati di scatto copiati dal RAW al DNG (tempo, diaframma, ISO, date, focale, obiettivo...)
EXIF_DA_COPIARE = {33434, 33437, 34850, 34855, 34864, 34866, 36864, 36867, 36868, 37377, 37378, 37379, 37380,
                   37381, 37383, 37384, 37385, 37386, 37520, 37521, 37522, 41985, 41986, 41987, 41988, 41989,
                   41990, 42034, 42035, 42036, 42037}
_MARCHE = {'SONY': 'Sony', 'NIKON CORPORATION': 'Nikon', 'NIKON': 'Nikon', 'CANON': 'Canon',
           'FUJIFILM': 'Fujifilm', 'OLYMPUS CORPORATION': 'Olympus', 'OLYMPUS IMAGING CORP.': 'Olympus',
           'OM DIGITAL SOLUTIONS': 'OM System', 'PANASONIC': 'Panasonic', 'PENTAX': 'Pentax',
           'PENTAX CORPORATION': 'Pentax', 'RICOH IMAGING COMPANY, LTD.': 'Ricoh', 'SAMSUNG': 'Samsung',
           'LEICA CAMERA AG': 'Leica'}


def _leggi_ifd(b, off, bo):
    """Una cartella (IFD) di un file TIFF: {tag: (tipo, quanti, byte del valore)}."""
    n = struct.unpack(bo + 'H', b[off:off + 2])[0]
    tag = {}
    for i in range(n):
        p = off + 2 + i * 12
        t, ty, c = struct.unpack(bo + 'HHI', b[p:p + 8])
        if ty not in _DIM:
            continue
        lung = c * _DIM[ty]
        q = struct.unpack(bo + 'I', b[p + 8:p + 12])[0] if lung > 4 else p + 8
        if lung > 1 << 24 or q + lung > len(b):
            continue
        tag[t] = (ty, c, bytes(b[q:q + lung]))
    return tag


def _valori(v, bo):
    """Numeri di una voce di tipo BYTE, SHORT o LONG."""
    return struct.unpack(bo + {1: 'B', 3: 'H', 4: 'I'}[v[0]] * v[1], v[2])


def meta_sorgente(path):
    """Marca, modello, data e dati di scatto letti dal RAW, quando e' un file di tipo TIFF (ARW, NEF, CR2,
    PEF, DNG...). Per gli altri formati i campi restano vuoti."""
    m = dict(bo='<', marca='', modello='', data=None, exif={})
    try:
        with open(path, 'rb') as f:
            b = f.read()
        if b[:2] not in (b'II', b'MM'):
            return m
        bo = '<' if b[:2] == b'II' else '>'
        if struct.unpack(bo + 'H', b[2:4])[0] != 42:
            return m
        t0 = _leggi_ifd(b, struct.unpack(bo + 'I', b[4:8])[0], bo)

        def testo(v):
            return v[2].split(b'\0')[0].decode('latin-1').strip()
        m['bo'] = bo
        if 271 in t0:
            m['marca'] = testo(t0[271])
        if 272 in t0:
            m['modello'] = testo(t0[272])
        if 306 in t0 and t0[306][0] == 2:
            m['data'] = t0[306]
        if 34665 in t0:
            ex = _leggi_ifd(b, _valori(t0[34665], bo)[0], bo)
            m['exif'] = {t: v for t, v in ex.items() if t in EXIF_DA_COPIARE and len(v[2]) <= 4096}
    except Exception:
        pass
    return m


def _nome_fotocamera(marca, modello):
    """Nome nel formato usato da Adobe ("Sony ILCE-7M3"), da cui dipende la scelta del profilo colore."""
    if not marca and not modello:
        return 'Fotocamera sconosciuta'
    pulita = _MARCHE.get(marca.upper(), marca.split()[0].capitalize() if marca else '')
    mod = modello
    for prefisso in (marca, pulita):
        if prefisso and mod.upper().startswith(prefisso.upper() + ' '):
            mod = mod[len(prefisso) + 1:]
    return (pulita + ' ' + mod).strip()


def _ifd_in_byte(voci, inizio, bo):
    """Scrive una cartella TIFF che comincia all'indirizzo `inizio`, seguita dai valori lunghi."""
    voci = sorted(voci.items())
    base = inizio + 2 + len(voci) * 12 + 4
    corpo, extra = b'', b''
    for t, (ty, c, d) in voci:
        if len(d) <= 4:
            campo = d + b'\0' * (4 - len(d))
        else:
            campo = struct.pack(bo + 'I', base + len(extra))
            extra += d + (b'\0' if len(d) % 2 else b'')
        corpo += struct.pack(bo + 'HHI', t, ty, c) + campo
    return struct.pack(bo + 'H', len(voci)) + corpo + struct.pack(bo + 'I', 0) + extra


def scrivi_dng(dest, px, m, meta):
    """Scrive un DNG non compresso a 16 bit con il mosaico px (uint16) e i dati di colore in m."""
    bo = meta['bo']
    h, w = px.shape

    def S(*v):
        return (3, len(v), struct.pack(bo + 'H' * len(v), *v))

    def L(*v):
        return (4, len(v), struct.pack(bo + 'I' * len(v), *v))

    def B(*v):
        return (1, len(v), bytes(v))

    def A(s):
        d = s.encode('ascii', 'replace') + b'\0'
        return (2, len(d), d)

    def R(v, den, segno=False):
        f = bo + ('ii' if segno else 'II')
        return (10 if segno else 5, len(v), b''.join(struct.pack(f, int(round(x * den)), den) for x in v))
    voci = {254: L(0), 256: L(w), 257: L(h), 258: S(16), 259: S(1), 262: S(32803), 273: L(0),
            274: S(m['orient']), 277: S(1), 278: L(h), 279: L(w * h * 2), 284: S(1), 305: A('Cornice'),
            33421: S(2, 2), 33422: B(*m['cfa']),
            50706: B(1, 4, 0, 0), 50707: B(1, 1, 0, 0),
            50708: A(_nome_fotocamera(meta['marca'], meta['modello'])),
            50710: B(0, 1, 2), 50711: S(1), 50713: S(2, 2), 50714: L(*m['neri']), 50717: L(m['bianco']),
            50719: L(4, 4), 50720: L(w - 8, h - 8),
            50721: R(m['matrice'], 10000, True), 50728: R(m['neutro'], 1000000), 50778: S(21)}
    if meta['marca']:
        voci[271] = A(meta['marca'])
    if meta['modello']:
        voci[272] = A(meta['modello'])
    if meta['data']:
        voci[306] = meta['data']
    if meta['exif']:
        voci[34665] = L(0)
    # le lunghezze non dipendono dai valori: prima si misurano i blocchi, poi si scrivono gli indirizzi veri
    lung0 = len(_ifd_in_byte(voci, 8, bo))
    inizio_exif = 8 + lung0 + lung0 % 2
    exif = _ifd_in_byte(meta['exif'], inizio_exif, bo) if meta['exif'] else b''
    inizio_dati = inizio_exif + len(exif) + len(exif) % 2
    voci[273] = L(inizio_dati)
    if meta['exif']:
        voci[34665] = L(inizio_exif)
    testa = (b'II' if bo == '<' else b'MM') + struct.pack(bo + 'HI', 42, 8)
    blocco = testa + _ifd_in_byte(voci, 8, bo)
    blocco += b'\0' * (inizio_exif - len(blocco)) + exif
    blocco += b'\0' * (inizio_dati - len(blocco))
    with open(dest, 'wb') as f:
        f.write(blocco)
        f.write(px.astype(bo + 'u2').tobytes())


def mosaico_nitido(px, neri, bianco, massimo, lung, ang, forza, freno=0.0, sfoc=0.0, avanzamento=None, ferma=None):
    """Toglie il mosso da un mosaico Bayer (uint16). I quattro piani di colore sono immagini a meta'
    risoluzione, quindi il mosso e' lungo la meta'. neri: i quattro livelli del nero, nell'ordine del mosaico."""
    h, w = px.shape[0] - px.shape[0] % 2, px.shape[1] - px.shape[1] % 2
    posti = [(i, j) for i in (0, 1) for j in (0, 1)]
    scale = [float(bianco) - float(n) for n in neri]
    piani = np.stack([np.clip(px[i:h:2, j:w:2].astype(np.float32) - np.float32(neri[k]), 0, None) / scale[k]
                      for k, (i, j) in enumerate(posti)], -1)
    ris = togli_mosso(piani, lung / 2.0, ang, forza, avanzamento=avanzamento, ferma=ferma, sigma=0.5,
                      freno=freno, sfoc=sfoc / 2.0)
    if ris is None:
        raise Interrotto()
    out = px.copy()
    for k, (i, j) in enumerate(posti):
        v = np.clip(ris[..., k] * scale[k] + float(neri[k]) + 0.5, 0, massimo).astype(np.uint16)
        orig = px[i:h:2, j:w:2]
        bruciato = orig >= 0.98 * bianco               # le zone bruciate restano bruciate (niente aloni colorati)
        v[bruciato] = orig[bruciato]
        out[i:h:2, j:w:2] = v
    return out


# ----------------------------------------------------------------------------- lettura/scrittura

class Foto:
    """Una foto caricata: dati lineari float32 (H, W, 3) e quanto serve per salvarla."""

    def __init__(self, path):
        self.path = path
        self.nome = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        self.raw = ext in RAW_EXT
        self.dng = None                    # dati per riscrivere sul posto un DNG di CHDK
        self.mos = None                    # mosaico del sensore (rawpy), per scrivere un DNG nuovo
        if self.raw:
            self._carica_raw()
        else:
            self._carica_immagine()

    # --- immagini normali
    def _carica_immagine(self):
        im = Image.open(self.path)
        self.icc = im.info.get('icc_profile')
        try:
            self.exif = im.getexif()
        except Exception:
            self.exif = None
        im = ImageOps.exif_transpose(im).convert('RGB')
        if self.exif is not None and 0x0112 in self.exif:
            self.exif[0x0112] = 1          # l'immagine ora e' gia' dritta
        a = np.asarray(im, np.float32) / 255.0
        self.lin = a ** 2.2

    def _salva_immagine(self, lin, dest):
        o = (np.clip(lin, 0, 1) ** (1 / 2.2) * 255.0 + 0.5).astype(np.uint8)
        kw = dict(format='JPEG', quality=95, subsampling=0)
        if self.icc:
            kw['icc_profile'] = self.icc
        if self.exif is not None and len(self.exif):
            kw['exif'] = self.exif.tobytes()
        Image.fromarray(o).save(dest, **kw)

    # --- RAW: lettura con rawpy
    def _carica_raw(self):
        self.ruotata = False               # la foto mostrata e' ruotata di 90 gradi rispetto al sensore
        if rawpy is None:
            raise RuntimeError(_('RAW files need the rawpy library, which is not installed here.'))
        try:
            self._carica_rawpy()
        except Exception as e:
            raise RuntimeError(_('Cannot read this RAW: %s') % e)
        try:
            self.dng = self._leggi_dng()
        except Exception:
            self.dng = None                # non e' un DNG riscrivibile sul posto

    def _carica_rawpy(self):
        with rawpy.imread(self.path) as r:
            im = r.postprocess(gamma=(1, 1), no_auto_bright=True, output_bps=16, use_camera_wb=True,
                               output_color=rawpy.ColorSpace.sRGB)
            self.lin = im.astype(np.float32) / 65535.0
            self.ruotata = r.sizes.flip in (5, 6)
            try:
                self.mos = self._descrivi_mosaico(r)
            except Exception:
                self.mos = None            # sensore non Bayer o dati mancanti: non si puo' correggere

    @staticmethod
    def _descrivi_mosaico(r):
        """Quanto serve per riscrivere il mosaico in un DNG, o None se il sensore non e' un Bayer RGB."""
        if r.raw_type != rawpy.RawType.Flat or r.num_colors != 3:
            return None
        pat = np.asarray(r.raw_pattern)
        if pat.shape != (2, 2):
            return None
        desc = r.color_desc.decode()
        cfa = [{'R': 0, 'G': 1, 'B': 2}[desc[k]] for k in pat.ravel()]
        neri_c = list(r.black_level_per_channel)
        neri = [int(round(neri_c[k])) for k in pat.ravel()]
        bianco = int(r.white_level)
        cw = r.camera_white_level_per_channel
        if cw is not None and min(cw[:3]) > max(neri):
            bianco = int(min(cw[:3]))
        mat = np.asarray(r.rgb_xyz_matrix, float)[:3]
        if not np.any(mat):
            return None
        wb = [float(x) for x in r.camera_whitebalance]
        if not (wb[0] > 0 and wb[1] > 0 and wb[2] > 0):
            wb = [float(x) for x in r.daylight_whitebalance]
        return dict(px=np.array(r.raw_image_visible, np.uint16), cfa=cfa, neri=neri, bianco=bianco,
                    matrice=mat.ravel().tolist(), neutro=[wb[1] / wb[0], 1.0, wb[1] / wb[2]],
                    orient={0: 1, 3: 3, 5: 8, 6: 6}.get(r.sizes.flip, 1))

    # --- DNG non compresso a 12 bit (CHDK): i dati del sensore si riscrivono in una copia del file
    def _leggi_dng(self):
        """Rende la descrizione dei dati grezzi, o solleva un errore se il file non e' del tipo atteso."""
        with open(self.path, 'rb') as f:
            b = f.read()
        if b[:2] != b'II' and b[:2] != b'MM':
            raise ValueError('non TIFF')
        bo = '<' if b[:2] == b'II' else '>'

        def ifd(off):
            return {k: _valori(v, bo) for k, v in _leggi_ifd(b, off, bo).items() if v[0] in (1, 3, 4) and v[1] <= 64}
        t0 = ifd(struct.unpack(bo + 'I', b[4:8])[0])
        t = None
        for cand in [t0] + [ifd(o) for o in t0.get(330, ())]:
            if cand.get(262, (0,))[0] == 32803:          # mosaico di colori (CFA)
                t = cand
        if t is None:
            raise ValueError('nessun mosaico')
        w, h = t[256][0], t[257][0]
        if t.get(259, (1,))[0] != 1 or t[258][0] != 12 or t.get(33421) != (2, 2) \
                or len(t[273]) != 1 or t[279][0] != w * h * 3 // 2 or w % 2 or h % 2:
            raise ValueError('formato non gestito')
        nero = t.get(50714, (0,))
        if len(set(nero)) != 1:
            raise ValueError('livelli del nero diversi')
        area = t.get(50829, (0, 0, h, w))                # alto, sinistra, basso, destra
        y0, x0 = area[0] + area[0] % 2, area[1] + area[1] % 2
        y1, x1 = area[2] - (area[2] - y0) % 2, area[3] - (area[3] - x0) % 2
        return dict(off=t[273][0], w=w, h=h, nero=float(nero[0]), bianco=float(t.get(50717, (4095,))[0]),
                    area=(y0, x0, y1, x1), ruotata=t0.get(274, (1,))[0] in (5, 6, 7, 8))

    def _salva_dng_copia(self, dest, lung, ang, forza, avanzamento=None, freno=0.0, sfoc=0.0, ferma=None):
        d = self.dng
        with open(self.path, 'rb') as f:
            b = bytearray(f.read())
        w, h, off = d['w'], d['h'], d['off']
        nb = w * h * 3 // 2
        tre = np.frombuffer(bytes(b[off:off + nb]), np.uint8).reshape(h, w // 2, 3).astype(np.uint16)
        px = np.empty((h, w), np.uint16)                 # 12 bit impacchettati: due pixel ogni tre byte
        px[:, 0::2] = (tre[..., 0] << 4) | (tre[..., 1] >> 4)
        px[:, 1::2] = ((tre[..., 1] & 15) << 8) | tre[..., 2]
        y0, x0, y1, x1 = d['area']
        px[y0:y1, x0:x1] = mosaico_nitido(px[y0:y1, x0:x1], [d['nero']] * 4, d['bianco'], d['bianco'], lung,
                                          (ang + 90) % 180 if d['ruotata'] else ang, forza, freno, sfoc,
                                          avanzamento, ferma)
        tre = np.empty((h, w // 2, 3), np.uint8)
        p, q = px[:, 0::2], px[:, 1::2]
        tre[..., 0] = p >> 4
        tre[..., 1] = ((p & 15) << 4) | (q >> 8)
        tre[..., 2] = q & 255
        b[off:off + nb] = tre.tobytes()
        with open(dest, 'wb') as f:
            f.write(b)

    # --- qualsiasi altro RAW con sensore Bayer: si scrive un DNG nuovo con il mosaico corretto
    def _salva_dng_nuovo(self, dest, lung, ang, forza, avanzamento=None, freno=0.0, sfoc=0.0, ferma=None):
        m = self.mos
        px = mosaico_nitido(m['px'], m['neri'], m['bianco'], 65535, lung,
                            (ang + 90) % 180 if self.ruotata else ang, forza, freno, sfoc, avanzamento, ferma)
        scrivi_dng(dest, px, m, meta_sorgente(self.path))

    # --- comune
    def esce_dng(self):
        """Un RAW si puo' correggere solo se e' un DNG di CHDK o un sensore Bayer letto da rawpy."""
        return self.raw and (self.dng is not None or self.mos is not None)

    def correggibile(self):
        return (not self.raw) or self.esce_dng()

    def nome_uscita(self):
        """Nome della copia: <radice>_nitida.<ext>."""
        base, ext = self._base_ext()
        return base + SUFFISSO + ext

    def nome_finale(self):
        """Nome che il file corretto prende quando sostituisce l'originale."""
        base, ext = self._base_ext()
        return base + ext

    def _base_ext(self):
        base, ext = os.path.splitext(self.nome)
        if not self.raw:
            if ext.lower() in ('.jpg', '.jpeg'):
                return base, ext
            return base, '.jpg'
        if self.dng is not None:
            return base, ext
        return base, '.dng'

    def elabora_e_salva(self, cartella, lung, ang, forza, avanzamento=None, freno=0.0, sfoc=0.0,
                        ferma=None, destinazione=None):
        """Toglie il mosso dalla foto intera e la salva (di norma in `cartella`, come <radice>_nitida.<ext>;
        con `destinazione` in quel percorso preciso). Si scrive su un file temporaneo e lo si rinomina alla
        fine: se qualcosa va storto non resta un file a meta'. Rende il percorso scritto.
        Solleva Interrotto se `ferma` scatta, RuntimeError se il file non si puo' correggere."""
        if self.raw and not self.esce_dng():
            raise RuntimeError(_('Sensor not supported: only a RAW with a Bayer sensor (or a CHDK DNG) can be corrected.'))
        dest = os.path.abspath(destinazione or os.path.join(cartella, self.nome_uscita()))
        if dest == os.path.abspath(self.path) or \
                (os.path.exists(dest) and os.path.samefile(dest, self.path)):
            raise RuntimeError('Il file di uscita coinciderebbe con l\'originale.')
        tmp = os.path.join(os.path.dirname(dest), '.cornice-%s.parziale' % uuid.uuid4().hex[:10])
        try:
            if self.raw:
                salva = self._salva_dng_copia if self.dng is not None else self._salva_dng_nuovo
                salva(tmp, lung, ang, forza, avanzamento, freno, sfoc, ferma)
            else:
                r = togli_mosso(self.lin, lung, ang, forza, avanzamento=avanzamento, ferma=ferma,
                                freno=freno, sfoc=sfoc)
                if r is None:
                    raise Interrotto()
                self._salva_immagine(r, tmp)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return dest

    def anteprima8(self, lin):
        """Da lineare a 8 bit per lo schermo (i RAW vengono schiariti)."""
        a = lin
        if self.raw:
            a = np.clip(a, 0, None) * self.guadagno()
        return (np.clip(a, 0, 1) ** (1 / 2.2) * 255.0 + 0.5).astype(np.uint8)

    def guadagno(self):
        if not hasattr(self, '_g'):
            med = float(np.median(self.lin[::16, ::16, 1]))
            self._g = float(np.clip(0.18 / max(med, 1e-4), 1.0, 12.0))
        return self._g


# ----------------------------------------------------------------------------- stima del mosso

def stima_mosso(lin, rmax=60):
    """Stima lunghezza (px) e angolo (gradi, 0 = orizzontale, 90 = verticale, antiorario a schermo)
    di un mosso rettilineo dal cepstrum. Rende (lunghezza, angolo, affidabilita')."""
    y = lin[..., 0] + lin[..., 1] + lin[..., 2]
    h, w = y.shape
    n = 1024
    while n > min(h, w):
        n //= 2
    if n < 128:
        return 0.0, 0.0, 0.0
    fin = np.outer(np.hanning(n), np.hanning(n)).astype(np.float32)
    acc = np.zeros((n, n))
    passo = max(n // 2, 1)
    k = 0
    for cy in range(0, h - n + 1, passo):
        for cx in range(0, w - n + 1, passo):
            t = y[cy:cy + n, cx:cx + n]
            t = (t - t.mean()) * fin
            acc += np.log(np.abs(np.fft.fft2(t)) + 1e-3)
            k += 1
    c = np.fft.fftshift(np.real(np.fft.ifft2(acc / k)))
    m = n // 2
    R = min(rmax, m - 1)
    sub = c[m - R:m + R + 1, m - R:m + R + 1].copy()
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    r = np.hypot(xx, yy)
    valido = (r >= 4) & (r <= R)
    sub[~valido] = 0
    i = int(np.argmin(sub))
    dy, dx = divmod(i, 2 * R + 1)
    dy -= R
    dx -= R
    # affidabilita': quanto il minimo sporge rispetto agli altri valori alla stessa distanza dal centro
    anello = sub[(np.abs(r - math.hypot(dx, dy)) <= 2.5) & valido]
    med = float(np.median(anello))
    mad = float(np.median(np.abs(anello - med))) * 1.4826
    aff = float((med - sub.ravel()[i]) / max(mad, 1e-9))
    lung = float(math.hypot(dx, dy))
    ang = math.degrees(math.atan2(-dy, dx)) % 180.0
    return lung, ang, aff


def _stima_zona(lin, x, y):
    """Stima il mosso attorno al punto (x, y): prova zone di grandezza e posizione diverse e tiene
    il valore su cui concordano di piu'. Rende (lunghezza, angolo, voti a favore, zone provate)."""
    h, w = lin.shape[:2]
    stime = []
    for z in (300, 400, 550):
        for dx, dy in ((0, 0), (-z // 2, 0), (z // 2, 0), (0, -z // 2), (0, z // 2)):
            cx, cy = x + dx, y + dy
            p = lin[max(cy - z, 0):min(cy + z, h), max(cx - z, 0):min(cx + z, w)]
            if min(p.shape[:2]) >= 256:
                l, a, _aff = stima_mosso(p)
                stime.append((l, a))

    vicine = _vicine
    meglio = []
    for q in stime:
        gruppo = [p for p in stime if vicine(p, q)]
        if len(gruppo) > len(meglio):
            meglio = gruppo
    if not meglio:
        return 0.0, 0.0, 0, len(stime)
    lung = float(np.median([p[0] for p in meglio]))
    rif = meglio[0][1]                                # media degli angoli attenta al salto 180 -> 0
    ang = float(np.median([rif + ((p[1] - rif + 90) % 180 - 90) for p in meglio]) % 180)
    return lung, ang, len(meglio), len(stime)


LATO_STIMA_MAX = 6500   # oltre questo lato lungo la stima si fa su una copia ridotta (vedi _riduzione)
SOGLIA = 6.0        # affidabilita' minima della stima su tutta la foto
VOTI_ZONA = 7       # stima in una zona: quante delle 15 prove devono concordare


def _vicine(p, q):
    """Due stime (lunghezza, angolo) dicono lo stesso mosso?"""
    da = abs(p[1] - q[1]) % 180
    return abs(p[0] - q[0]) <= max(3.0, 0.12 * q[0]) and min(da, 180 - da) <= 10


def stima_griglia(lin, avanzamento=None):
    """Stima il mosso in nove zone della foto e cerca il valore su cui concordano. Ogni zona pesa
    per quanto dettaglio contiene: cielo e muri lisci non dicono niente sul mosso e non votano.
    Rende (lunghezza, angolo, peso a favore da 0 a 1, zone a favore, zone con dettaglio)."""
    h, w = lin.shape[:2]
    y = np.sqrt(np.clip(lin[..., 0] + lin[..., 1] + lin[..., 2], 0, None))
    zone = []
    for fy in (0.25, 0.5, 0.75):
        for fx in (0.25, 0.5, 0.75):
            cx, cy = int(w * fx), int(h * fy)
            t = y[max(cy - 400, 0):cy + 400, max(cx - 400, 0):cx + 400]
            dett = float(np.abs(np.diff(t, axis=0)).mean() + np.abs(np.diff(t, axis=1)).mean())
            zone.append([cx, cy, dett])
    massimo = max(z[2] for z in zone) or 1.0
    zone = [z for z in zone if z[2] >= 0.25 * massimo]
    fatte = [0]

    def una(z):
        r = _stima_zona(lin, z[0], z[1])
        fatte[0] += 1
        if avanzamento:
            avanzamento(fatte[0] / len(zone))
        return r
    with ThreadPoolExecutor(3) as ex:
        ris = list(ex.map(una, zone))
    totale = sum(z[2] for z in zone)
    buone = [(r[0], r[1], z[2]) for z, r in zip(zone, ris) if r[2] >= VOTI_ZONA and r[0] >= 4]
    meglio, peso = [], 0.0
    for q in buone:
        gruppo = [p for p in buone if _vicine(p, q)]
        pg = sum(p[2] for p in gruppo)
        if pg > peso:
            meglio, peso = gruppo, pg
    if not meglio:
        return 0.0, 0.0, 0.0, 0, len(zone)
    lung = float(np.median([p[0] for p in meglio]))
    rif = meglio[0][1]
    ang = float(np.median([rif + ((p[1] - rif + 90) % 180 - 90) for p in meglio]) % 180)
    return lung, ang, peso / totale, len(meglio), len(zone)


def _stima_completa(lin, avanzamento=None):
    lung, ang, aff = stima_mosso(lin)
    if aff >= SOGLIA and lung >= 4:
        return 'sicura', lung, ang, _('reliability %.1f') % aff
    gl, ga, peso, n, tot = stima_griglia(lin, avanzamento)
    quante = _('%d zones out of %d') % (n, tot)
    if n >= 3 and peso >= 0.5:
        return 'zone', gl, ga, quante
    if n >= 2:
        return 'incerta', gl, ga, quante
    if aff >= 4 and lung >= 4:
        return 'incerta', lung, ang, _('reliability %.1f, %s') % (aff, quante)
    return 'niente', 0.0, 0.0, quante


def _riduzione(lin):
    """Fattore intero k per ridurre la stima su foto molto grandi: 1 fino a LATO_STIMA_MAX px sul lato lungo."""
    lato = max(lin.shape[:2])
    return 1 if lato <= LATO_STIMA_MAX else int(math.ceil(lato / LATO_STIMA_MAX))


def lunghezza_massima_cursore(lato):
    """Massimo del cursore "Lunghezza" (pixel): 60 fino a 5000 px sul lato lungo, poi cresce con la risoluzione
    (60 per lato/5000, arrotondato per eccesso a 10)."""
    return float(max(60, int(math.ceil(60 * max(1.0, lato / 5000.0) / 10.0)) * 10))


def _riduci(lin, k):
    """Media a blocchi k x k (i bordi che non completano un blocco si scartano)."""
    h, w = lin.shape[0] // k * k, lin.shape[1] // k * k
    a = lin[:h, :w].astype(np.float32, copy=False)
    return a.reshape(h // k, k, w // k, k, lin.shape[2]).mean(axis=(1, 3), dtype=np.float32)


def stima_zona(lin, x, y):
    """Come `_stima_zona`, ma sulle foto molto grandi lavora su una copia ridotta e rende la lunghezza
    in pixel della foto vera. Rende (lunghezza, angolo, voti a favore, zone provate)."""
    k = _riduzione(lin)
    if k == 1:
        return _stima_zona(lin, x, y)
    lung, ang, voti, prove = _stima_zona(_riduci(lin, k), x // k, y // k)
    return lung * k, ang, voti, prove


def stima_completa(lin, avanzamento=None):
    """Stima su tutta la foto e, se e' incerta, conferma con le zone.
    Rende (esito, lunghezza, angolo, spiegazione); esito: 'sicura', 'zone', 'incerta', 'niente'.
    Fino a 6500 px sul lato lungo si lavora sulla foto intera; oltre, su una copia ridotta di un fattore intero
    k (media a blocchi) e la lunghezza trovata si moltiplica per k."""
    k = _riduzione(lin)
    if k == 1:
        return _stima_completa(lin, avanzamento)
    esito, lung, ang, come = _stima_completa(_riduci(lin, k), avanzamento)
    return esito, lung * k, ang, come


# ----------------------------------------------------------------------------- deconvoluzione

def _psf(lung, ang, sigma=0.7):
    lung = max(float(lung), 0.01)
    dx = lung * math.cos(math.radians(ang))
    dy = -lung * math.sin(math.radians(ang))
    c = int(lung / 2 + 3 * sigma) + 3
    n = 2 * c + 1
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    k = np.zeros((n, n), np.float32)
    for t in np.linspace(-0.5, 0.5, int(lung * 6) + 2):
        k += np.exp(-((xx - c - t * dx) ** 2 + (yy - c - t * dy) ** 2) / (2 * sigma ** 2))
    return k / k.sum()


def _buona(n):
    """Prima dimensione >= n con soli fattori 2, 3, 5 (FFT veloce)."""
    while True:
        m = n
        for f in (2, 3, 5):
            while m % f == 0:
                m //= f
        if m == 1:
            return n
        n += 1


def _divergenza_tv(x, piccolo):
    """Divergenza del gradiente normalizzato: positiva nei picchi isolati (rumore), circa zero sui bordi netti."""
    gx = np.zeros_like(x)
    gy = np.zeros_like(x)
    gx[:, :-1] = x[:, 1:] - x[:, :-1]
    gy[:-1, :] = x[1:, :] - x[:-1, :]
    norma = np.sqrt(gx * gx + gy * gy + piccolo)
    gx /= norma
    gy /= norma
    d = gx.copy()
    d[:, 1:] -= gx[:, :-1]
    d[1:, :] += gy[1:, :] - gy[:-1, :]
    d[0, :] += gy[0, :]
    return d


def togli_mosso(lin, lung, ang, forza=20, avanzamento=None, ferma=None, sigma=0.7, freno=0.0, sfoc=0.0):
    """Richardson-Lucy con un mosso rettilineo. lin: float32 (H, W, canali) lineare.
    freno (0..1): regolarizzazione a variazione totale, che trattiene rumore e trame senza
    ammorbidire i bordi netti.
    sfoc: sfocatura uniforme in piu' (fuori fuoco leggero), come raggio gaussiano in pixel."""
    if (lung < 0.25 and sfoc < 0.3) or forza < 1:
        return lin.copy()
    psf = _psf(lung, ang, math.hypot(sigma, sfoc))
    n = psf.shape[0]
    c = n // 2
    bordo = n + 8
    h, w = lin.shape[:2]
    H, W = _buona(h + 2 * bordo), _buona(w + 2 * bordo)
    a = np.pad(lin, ((bordo, H - h - bordo), (bordo, W - w - bordo), (0, 0)), mode='reflect') \
        if min(h, w) > H - h and min(h, w) > W - w else \
        np.pad(lin, ((bordo, H - h - bordo), (bordo, W - w - bordo), (0, 0)), mode='edge')
    P = np.zeros((H, W), np.float32)
    P[:n, :n] = psf
    P = np.roll(P, (-c, -c), (0, 1))
    F = np.fft.rfft2(P)
    Fc = np.conj(F)
    eps = np.float32(1e-5)
    lam = np.float32(min(max(freno, 0.0), 1.0) * LAMBDA_MAX)

    def canale(ch):
        img = a[..., ch] + eps
        x = img.copy()
        for i in range(forza):
            if ferma is not None and ferma.is_set():
                return None
            st = np.fft.irfft2(np.fft.rfft2(x) * F, s=(H, W))
            np.maximum(st, eps, out=st)
            corr = np.fft.irfft2(np.fft.rfft2(img / st) * Fc, s=(H, W))
            if lam > 0:
                corr /= np.maximum(1 - lam * _divergenza_tv(x, np.float32(1e-12)), np.float32(0.2))
            x *= corr
            np.clip(x, 0, 4, out=x)
            if ch == 0 and avanzamento:
                avanzamento((i + 1) / forza)
        return x[bordo:bordo + h, bordo:bordo + w]

    nc = lin.shape[2]
    with ThreadPoolExecutor(nc) as ex:
        ris = list(ex.map(canale, range(nc)))
    if any(r is None for r in ris):
        return None
    return np.stack(ris, -1).astype(np.float32)

