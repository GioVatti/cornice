"""Immagini sintetiche per le prove: nessuna foto vera serve. Si può anche lanciare:

    python3 prove/sintetiche.py CARTELLA     # scrive qualche foto di prova (due mosse, due nitide) in CARTELLA
"""
import os
import sys

import numpy as np
from PIL import Image

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
import togli  # noqa: E402


def sintetica(h=900, w=1200, seme=1):
    """Immagine lineare "naturale": rumore con spettro 1/f (dettaglio a tutte le scale) e qualche bordo netto."""
    rng = np.random.default_rng(seme)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    filtro = 1.0 / np.maximum(np.hypot(fy, fx), 1.0 / max(h, w)) ** 1.2
    canali = []
    for c in range(3):
        r = np.fft.irfft2(np.fft.rfft2(rng.normal(0, 1, (h, w))) * filtro, s=(h, w))
        r = (r - r.mean()) / (r.std() + 1e-9)
        canali.append(0.3 + 0.12 * r)
    a = np.stack(canali, -1).astype(np.float32)
    for _ in range(40):                                  # bordi netti (scritte, spigoli)
        y, x = int(rng.integers(0, h - 60)), int(rng.integers(0, w - 60))
        a[y:y + int(rng.integers(8, 60)), x:x + int(rng.integers(8, 60))] += float(rng.normal(0, 0.15))
    return np.clip(a, 0.01, 1).astype(np.float32)


def sfoca(img, lung, ang):
    """Sfocatura da mosso con la stessa funzione di sfocatura del programma (in frequenza, ai bordi si avvolge)."""
    h, w = img.shape[:2]
    psf = togli._psf(lung, ang)
    n = psf.shape[0]
    P = np.zeros((h, w), np.float32)
    P[:n, :n] = psf
    P = np.roll(P, (-(n // 2), -(n // 2)), (0, 1))
    F = np.fft.rfft2(P)
    return np.stack([np.fft.irfft2(np.fft.rfft2(img[..., c]) * F, s=(h, w)) for c in range(3)], -1).astype(np.float32)


def mosaico_sintetico(h=192, w=256, seme=2):
    rng = np.random.default_rng(seme)
    px = np.full((h, w), 600, np.float32)
    for _ in range(120):
        y, x = int(rng.integers(0, h - 12)), int(rng.integers(0, w - 12))
        px[y:y + int(rng.integers(3, 12)), x:x + int(rng.integers(3, 12))] = rng.integers(200, 3800)
    px += rng.normal(0, 8, px.shape)
    return np.clip(px, 0, 4095).astype(np.uint16)


def meta_dng(h, w):
    m = dict(cfa=[0, 1, 1, 2], neri=[64, 64, 64, 64], bianco=4095, orient=1,
             matrice=[0.6, -0.1, -0.05, -0.3, 1.1, 0.2, -0.05, 0.15, 0.6],
             neutro=[0.5, 1.0, 0.6])
    meta = dict(bo='<', marca='Prova', modello='Camera di prova', data=None, exif={})
    return m, meta


def scrivi_jpg(percorso, lin, data='2026:11:15 10:30:00', tempo=0.04, orient=None):
    """JPG da un'immagine lineare, con un po' di EXIF (marca, data, tempo di posa, diaframma, ISO)."""
    im = Image.fromarray((np.clip(lin, 0, 1) ** (1 / 2.2) * 255 + 0.5).astype(np.uint8))
    ex = Image.Exif()
    ex[0x010F] = 'ProvaMarca'
    ex[0x0110] = 'ProvaModello'
    ex[0x0132] = data
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = data
    sub[0x829A] = tempo
    sub[0x829D] = 3.2
    sub[0x8827] = 160
    if orient:
        ex[0x0112] = orient
    im.save(percorso, quality=92, exif=ex.tobytes())


def scrivi_dng_di_prova(percorso, seme=2):
    """Un DNG finto (mosaico casuale): rawpy lo apre, ma non ha la matrice dei colori, quindi Cornice lo
    mostra e non lo considera correggibile."""
    px = mosaico_sintetico(seme=seme)
    m, meta = meta_dng(*px.shape)
    togli.scrivi_dng(percorso, px, m, meta)
    return px


def _jpeg_bytes(im, qualita=90):
    import io
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=qualita)
    return b.getvalue()


def scrivi_raw_finto(percorso, im, orient=1):
    """Un file "RAW" che non e' un RAW vero ma ha la struttura TIFF di un NEF/ARW con un JPEG grande in un SubIFD
    (tag 330 -> 513/514) e una miniatura nella IFD1. rawpy non lo apre, l'anteprima incorporata si'.
    `im` e' l'immagine PIL dell'anteprima grande, tale e quale (senza EXIF): l'orientamento sta nella IFD0."""
    import struct
    grande = _jpeg_bytes(im)
    mini = _jpeg_bytes(im.resize((max(im.width // 8, 32), max(im.height // 8, 32))))

    def ifd(voci, succ):
        out = struct.pack('<H', len(voci))
        for tag, tipo, val in voci:
            out += struct.pack('<HHII', tag, tipo, 1, val) if tipo == 4 else struct.pack('<HHIHH', tag, tipo, 1, val, 0)
        return out + struct.pack('<I', succ)
    off1 = 8 + 30
    off_sub = off1 + 30
    off_mini = off_sub + 30
    off_grande = off_mini + len(mini)
    ifd0 = ifd([(274, 3, orient), (330, 4, off_sub)], off1)
    ifd1 = ifd([(513, 4, off_mini), (514, 4, len(mini))], 0)
    sub = ifd([(513, 4, off_grande), (514, 4, len(grande))], 0)
    with open(percorso, 'wb') as f:
        f.write(b'II*\0' + struct.pack('<I', 8) + ifd0 + ifd1 + sub + mini + grande)


def scrivi_raw_finto_cr3(percorso, im):
    """Come sopra ma senza struttura TIFF (come un CR3): solo JPEG sparsi in un contenitore."""
    import os
    with open(percorso, 'wb') as f:
        f.write(b'\0\0\0\x18ftypcrx \0\0\0\1crx isom' + os.urandom(300))
        f.write(_jpeg_bytes(im.resize((max(im.width // 6, 32), max(im.height // 6, 32)))))
        f.write(os.urandom(500))
        f.write(_jpeg_bytes(im))
        f.write(os.urandom(200))


def genera_cartella(cartella):
    """Foto di prova: IMG_0005.NEF (RAW finto con anteprima), IMG_0001 (JPG + DNG finto + .xmp) e IMG_0002 mosse, IMG_0003 e IMG_0004 nitide."""
    os.makedirs(cartella, exist_ok=True)
    scrivi_jpg(os.path.join(cartella, 'IMG_0001.JPG'), sfoca(sintetica(seme=11), 24, 60))
    scrivi_dng_di_prova(os.path.join(cartella, 'IMG_0001.DNG'))
    with open(os.path.join(cartella, 'IMG_0001.JPG.xmp'), 'w') as f:
        f.write('<x:xmpmeta xmlns:x="adobe:ns:meta/"/>\n')
    scrivi_jpg(os.path.join(cartella, 'IMG_0002.JPG'), sfoca(sintetica(seme=12), 16, 20), data='2026:11:15 10:31:00')
    scrivi_jpg(os.path.join(cartella, 'IMG_0003.JPG'), sintetica(seme=13), data='2026:11:15 10:32:00', tempo=0.008)
    scrivi_jpg(os.path.join(cartella, 'IMG_0004.JPG'), sintetica(seme=14), data='2026:11:15 10:33:00', tempo=0.008)
    # un "RAW" che rawpy non apre ma con anteprima JPEG incorporata (orientamento 6: foto verticale)
    scrivi_raw_finto(os.path.join(cartella, 'IMG_0005.NEF'),
                     Image.fromarray((np.clip(sintetica(600, 900, seme=15), 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)),
                     orient=6)
    return cartella


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    print(genera_cartella(sys.argv[1]))
