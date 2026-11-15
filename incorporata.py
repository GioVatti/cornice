# SPDX-License-Identifier: GPL-3.0-or-later
"""Anteprima JPEG incorporata nei file RAW, per quando rawpy manca o non sa aprire il file.

Prima si percorrono le cartelle (IFD) TIFF del RAW (tag 513/514 JPEGInterchangeFormat, strisce JPEG, SubIFD
330), che copre DNG, ARW, NEF, CR2, ORF, PEF, RW2, SRW...; poi, se non basta (CR3, RAF e altri formati non TIFF),
si cerca nel file ogni inizio di JPEG (FF D8 FF) e si prova ad aprirlo. Vince l'anteprima più grande.
Niente GTK; serve solo Pillow.
"""
import io
import re
import struct

LATO_BASTA = 1500           # se l'anteprima trovata nelle IFD ha almeno questo lato lungo, non si cerca oltre
MAX_PROVE = 80              # inizi di JPEG provati nella ricerca nel file

_DIM = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TRASFORMAZIONI = {2: 'FLIP_LEFT_RIGHT', 3: 'ROTATE_180', 4: 'FLIP_TOP_BOTTOM', 5: 'TRANSPOSE',
                   6: 'ROTATE_270', 7: 'TRANSVERSE', 8: 'ROTATE_90'}


class NessunaAnteprima(Exception):
    """Nel file non c'è nessun JPEG utilizzabile."""


class _Sezione(io.RawIOBase):
    """File virtuale su una parte di un buffer, senza copiarla."""

    def __init__(self, buf, inizio):
        self._m = memoryview(buf)[inizio:]
        self._p = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._p

    def seek(self, pos, whence=0):
        self._p = pos if whence == 0 else self._p + pos if whence == 1 else len(self._m) + pos
        self._p = max(self._p, 0)
        return self._p

    def readinto(self, b):
        chunk = self._m[self._p:self._p + len(b)]
        b[:len(chunk)] = chunk
        self._p += len(chunk)
        return len(chunk)


def _ifd(b, off, bo):
    """Una IFD: ({tag: (tipo, quanti, valori | None)}, offset della successiva). I valori sono numeri
    (BYTE, SHORT, LONG), gli altri tipi si saltano."""
    if off < 8 or off + 2 > len(b):
        return {}, 0
    n = struct.unpack_from(bo + 'H', b, off)[0]
    tag = {}
    for i in range(min(n, 500)):
        p = off + 2 + i * 12
        if p + 12 > len(b):
            break
        t, ty, c = struct.unpack_from(bo + 'HHI', b, p)
        if ty not in (1, 3, 4) or c > 4096:
            continue
        lung = c * _DIM[ty]
        q = struct.unpack_from(bo + 'I', b, p + 8)[0] if lung > 4 else p + 8
        if q + lung > len(b):
            continue
        tag[t] = (ty, c, struct.unpack_from(bo + {1: 'B', 3: 'H', 4: 'I'}[ty] * c, b, q))
    fine = off + 2 + n * 12
    succ = struct.unpack_from(bo + 'I', b, fine)[0] if fine + 4 <= len(b) else 0
    return tag, succ


def _candidati_tiff(b):
    """([(inizio, lunghezza)], orientamento della IFD0 o 1)."""
    if len(b) < 16 or b[:2] not in (b'II', b'MM'):
        return [], 1
    bo = '<' if b[:2] == b'II' else '>'
    prime = [struct.unpack_from(bo + 'I', b, 4)[0]]
    viste, cand, orient = set(), [], 1
    prima = True
    while prime and len(viste) < 40:
        off = prime.pop(0)
        if off in viste:
            continue
        viste.add(off)
        tag, succ = _ifd(b, off, bo)
        if prima:
            orient = (tag.get(274, (0, 0, (1,)))[2] or (1,))[0]
            prima = False
        if 513 in tag and 514 in tag:
            cand.append((tag[513][2][0], tag[514][2][0]))
        if tag.get(259, (0, 0, (0,)))[2][0] in (6, 7) and 273 in tag and 279 in tag \
                and len(tag[273][2]) == 1 and len(tag[279][2]) == 1:
            cand.append((tag[273][2][0], tag[279][2][0]))
        if 330 in tag:
            prime.extend(tag[330][2])
        if succ:
            prime.append(succ)
    return cand, orient if orient in range(1, 9) else 1


def _apri(buf, inizio, lunghezza=None):
    """Immagine PIL (non ancora decodificata) a partire da `inizio`, o None."""
    from PIL import Image
    try:
        if buf[inizio:inizio + 3] != b'\xff\xd8\xff':
            return None
        if lunghezza:
            im = Image.open(io.BytesIO(buf[inizio:inizio + lunghezza]))
        else:
            im = Image.open(io.BufferedReader(_Sezione(buf, inizio)))
        if im.format != 'JPEG' or im.size[0] < 32 or im.size[1] < 32:
            return None
        return im
    except Exception:  # noqa: BLE001 - un falso inizio di JPEG non è un errore
        return None


def _per_grandezza(trovate):
    return sorted(trovate, key=lambda t: t[0].size[0] * t[0].size[1], reverse=True)


def immagine_incorporata(percorso):
    """La più grande anteprima JPEG del RAW, già raddrizzata, come immagine PIL RGB.
    Solleva NessunaAnteprima se non ce n'è."""
    from PIL import Image
    with open(percorso, 'rb') as f:
        b = f.read()
    cand, orient = _candidati_tiff(b)
    trovate = []
    for inizio, lung in cand:
        im = _apri(b, inizio, lung)
        if im is not None:
            trovate.append((im, inizio, lung))
    ordine = _per_grandezza(trovate)
    if not ordine or max(ordine[0][0].size) < LATO_BASTA:
        visti = set(inizio for im, inizio, lung in trovate)
        n = 0
        for m in re.finditer(b'\xff\xd8\xff', b):
            if m.start() in visti:
                continue
            n += 1
            if n > MAX_PROVE:
                break
            im = _apri(b, m.start())
            if im is not None:
                trovate.append((im, m.start(), None))
        ordine = _per_grandezza(trovate)
    for im, inizio, lung in ordine:                 # la più grande che si decodifica per intero
        try:
            o = im.getexif().get(274) or orient
            im.load()
            im = im.convert('RGB')
        except Exception:  # noqa: BLE001 - JPEG troncato o rovinato: si passa alla successiva
            continue
        nome = _TRASFORMAZIONI.get(o)
        if nome:
            im = im.transpose(getattr(Image.Transpose, nome))
        return im
    raise NessunaAnteprima('Nessun JPEG incorporato in %s' % percorso)
