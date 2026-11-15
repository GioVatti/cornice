#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cornice: visualizzatore di foto per GNOME con cornice bianca, cernita e indicatore di mosso.

Uso: python3 cornice.py [file o cartella ...]   (--help per le opzioni)
"""
import json
import math
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

import incorporata  # noqa: E402
import lingua  # noqa: E402
import operazioni as op  # noqa: E402
from lingua import _, ngettext  # noqa: E402

try:  # senza numpy o Pillow Togli mosso non c'è
    import numpy as np
    import togli
except Exception:  # noqa: BLE001
    np = togli = None

try:  # senza mosso.py l'indicatore resta spento
    import mosso
except Exception:  # noqa: BLE001
    mosso = None

APP_ID = 'io.github.giovatti.Cornice'
VERSIONE = '1.0.0'
SITO = 'https://github.com/GioVatti/cornice'
CONFIG_PATH = os.path.join(GLib.get_user_config_dir(), 'cornice', 'config.json')
CONFIG_PREDEFINITA = {
    'destinazione': '',           # vuoto = la cartella predefinita (vedi dest_predefinita)
    'destinazione_scelta': False,  # l'utente ha scelto la destinazione (primo avvio fatto)
    'spessore': 6,
    'cornice': False,
    'mosso_attivo': True,
    'finestra': [1400, 900],
}
CACHE_TEXTURE = 5

CSS = """
.vista-foto { background-color: #141414; }
.chip-mosso {
  background-color: alpha(black, 0.65); color: white;
  border-radius: 14px; padding: 4px 6px 4px 12px;
}
.chip-mosso label { color: white; }
.chip-errore {
  background-color: alpha(black, 0.65); color: white;
  border-radius: 14px; padding: 8px 14px;
}
.barra-info { padding: 4px 12px; }
.barra-cernita { padding: 6px 12px; }
"""


# ---------------------------------------------------------------- funzioni pure

def dec(v, n=1):
    """Numero con n decimali e il separatore decimale della lingua (virgola in italiano)."""
    return ('%.*f' % (n, v)).replace('.', _('.'))


def cartella_immagini():
    """La cartella Immagini dell'utente (XDG), o ~/Pictures."""
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return d or os.path.expanduser('~/Pictures')


def dest_predefinita():
    """Destinazione proposta per le foto approvate: <Immagini>/Approved (o Approvate)."""
    return os.path.join(cartella_immagini(), _('Approved'))


def carica_config(percorso=CONFIG_PATH):
    """Legge la configurazione; valori mancanti o strani tornano ai predefiniti.
    Un file di una versione precedente che ha già una destinazione conta come "destinazione scelta"."""
    cfg = dict(CONFIG_PREDEFINITA)
    dati = {}
    try:
        with open(percorso, encoding='utf-8') as f:
            dati = json.load(f)
        if isinstance(dati, dict):
            for k, v in dati.items():
                if k in cfg and type(v) is type(cfg[k]):
                    cfg[k] = v
        else:
            dati = {}
    except (OSError, ValueError):
        dati = {}
    if 'destinazione_scelta' not in dati:
        cfg['destinazione_scelta'] = bool(isinstance(dati.get('destinazione'), str) and dati.get('destinazione'))
    if not cfg['destinazione']:
        cfg['destinazione'] = dest_predefinita()
    cfg['destinazione'] = os.path.expanduser(cfg['destinazione'])
    cfg['spessore'] = max(1, min(25, int(cfg['spessore'])))
    f = cfg['finestra']
    if not (isinstance(f, list) and len(f) == 2 and all(isinstance(x, int) and x > 200 for x in f)):
        cfg['finestra'] = list(CONFIG_PREDEFINITA['finestra'])
    return cfg


def salva_config(cfg, percorso=CONFIG_PATH):
    """Scrittura atomica del json di configurazione."""
    try:
        os.makedirs(os.path.dirname(percorso), exist_ok=True)
        tmp = percorso + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, percorso)
    except OSError as e:
        print(f'Cornice: could not save the settings ({e})', file=sys.stderr)


def fmt_data(testo):
    try:
        return datetime.strptime(str(testo)[:19], '%Y:%m:%d %H:%M:%S').strftime(_('%Y-%m-%d %H:%M'))
    except ValueError:
        return ''


def fmt_tempo(t):
    if not t or t <= 0:
        return ''
    if t < 1:
        return f'1/{round(1 / t)} s'
    return ('%g s' % t).replace('.', _('.'))


def fmt_ev(v):
    v = round(float(v), 1)
    if v == 0:
        return '0 EV'
    return ('%+.1f EV' % v).replace('.', _('.'))


def tipi_scatto(scatto):
    """'JPG + DNG' dalle estensioni dei file mostrabili dello scatto."""
    nomi = [p for p in (scatto.immagine, scatto.raw) if p]
    return ' + '.join(os.path.splitext(p)[1].lstrip('.').upper() for p in nomi)


def leggi_exif(percorso):
    """Dati di scatto al meglio possibile: dict con data, tempo, diaframma, iso, ev (testi)."""
    info = {}
    try:
        from PIL import Image
        with Image.open(percorso) as im:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769)
        data = sub.get(0x9003) or ex.get(0x0132)
        if data:
            info['data'] = fmt_data(data)
        if sub.get(0x829A):
            info['tempo'] = fmt_tempo(float(sub[0x829A]))
        if sub.get(0x829D):
            info['diaframma'] = 'f/' + dec(float(sub[0x829D]), 1).removesuffix(_('.') + '0')
        iso = sub.get(0x8827) or sub.get(0x8832)
        if isinstance(iso, (tuple, list)) and iso:
            iso = iso[0]
        if iso:
            info['iso'] = f'ISO {int(iso)}'
        if sub.get(0x9204) is not None:
            info['ev'] = fmt_ev(float(sub[0x9204]))
    except Exception:  # noqa: BLE001 - l'EXIF è facoltativo
        pass
    if not info.get('data'):
        try:
            info['data'] = datetime.fromtimestamp(os.path.getmtime(percorso)).strftime(_('%Y-%m-%d %H:%M'))
        except OSError:
            pass
    return info


class Risultato:
    """Una foto caricata: texture, dimensioni e dati EXIF."""

    def __init__(self, texture, larghezza, altezza, info, incorporata=False):
        self.texture = texture
        self.w = larghezza
        self.h = altezza
        self.info = info
        self.incorporata = incorporata     # RAW mostrato con l'anteprima JPEG che contiene


def carica_foto(percorso):
    """Carica e decodifica (da thread): PIL oppure rawpy per i RAW. Restituisce Risultato."""
    ext = os.path.splitext(percorso)[1].lower()
    incorp = False
    if ext in op.RAW_EXT:
        try:
            import rawpy
            with rawpy.imread(percorso) as r:
                arr = r.postprocess(half_size=True, use_camera_wb=True)
            h, w = arr.shape[:2]
            dati = arr.tobytes()
        except Exception:  # noqa: BLE001 - senza rawpy, o RAW che rawpy non sa aprire: anteprima incorporata
            im = incorporata.immagine_incorporata(percorso)
            w, h = im.size
            dati = im.tobytes()
            incorp = True
        fmt, stride = Gdk.MemoryFormat.R8G8B8, w * 3
    else:
        from PIL import Image, ImageOps
        with Image.open(percorso) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            if 'A' in im.getbands() or (im.mode == 'P' and 'transparency' in im.info):
                im = im.convert('RGBA')
                fmt, mult = Gdk.MemoryFormat.R8G8B8A8, 4
            else:
                im = im.convert('RGB')
                fmt, mult = Gdk.MemoryFormat.R8G8B8, 3
            w, h = im.size
            dati = im.tobytes()
            stride = w * mult
    tex = Gdk.MemoryTexture.new(w, h, fmt, GLib.Bytes.new(dati), stride)
    return Risultato(tex, w, h, leggi_exif(percorso), incorp)


# ---------------------------------------------------------------- vista

def scala_schermo(widget):
    """Fattore di scala della superficie (anche frazionario): pixel fisici per pixel logico."""
    try:
        return widget.get_native().get_surface().get_scale() or 1.0
    except AttributeError:
        return float(widget.get_scale_factor() or 1)


class VistaFoto(Gtk.Widget):
    """Disegna una texture adattata alla finestra o con zoom, con cornice bianca opzionale."""
    __gtype_name__ = 'CorniceVista'

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class('vista-foto')
        self.texture = None
        self.tw = self.th = 0
        self.cornice = False
        self.spessore = 6          # percentuale del lato corto
        self.zoom = None           # None = adatta; altrimenti scala in px logici per px texture
        self.ox = self.oy = 0.0    # spostamento rispetto al centro
        self.puntatore = None
        self.su_cambio = None      # callback senza argomenti
        self._base = (0.0, 0.0)

        sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        sc.connect('scroll', self._scroll)
        self.add_controller(sc)
        mo = Gtk.EventControllerMotion()
        mo.connect('motion', lambda c, x, y: setattr(self, 'puntatore', (x, y)))
        mo.connect('leave', lambda c: setattr(self, 'puntatore', None))
        self.add_controller(mo)
        dr = Gtk.GestureDrag()
        dr.connect('drag-begin', lambda g, x, y: setattr(self, '_base', (self.ox, self.oy)))
        dr.connect('drag-update', self._trascina)
        self.add_controller(dr)
        cl = Gtk.GestureClick()
        cl.connect('pressed', self._clic)
        self.add_controller(cl)

    # -- geometria
    def _scala_schermo(self):
        return scala_schermo(self)

    def _bordo(self):
        if not self.cornice or not self.texture:
            return 0.0
        return round(self.spessore / 100 * min(self.tw, self.th))

    def _contenuto(self):
        b = self._bordo()
        return self.tw + 2 * b, self.th + 2 * b

    def scala_adatta(self):
        cw, ch = self._contenuto()
        if not cw or not ch:
            return 1.0
        k = 0.96 if self.cornice else 1.0
        return min(self.get_width() * k / cw, self.get_height() * k / ch)

    def scala(self):
        return self.zoom if self.zoom is not None else self.scala_adatta()

    def percentuale(self):
        return self.scala() * self._scala_schermo() * 100

    def _limita(self):
        s = self.scala()
        cw, ch = self._contenuto()
        mx = max(0.0, (cw * s - self.get_width()) / 2)
        my = max(0.0, (ch * s - self.get_height()) / 2)
        self.ox = max(-mx, min(mx, self.ox))
        self.oy = max(-my, min(my, self.oy))

    def _cambiato(self):
        self._limita()
        self.set_cursor_from_name('grab' if self.zoom is not None else None)
        self.queue_draw()
        if self.su_cambio:
            self.su_cambio()

    # -- interfaccia
    def imposta_texture(self, texture, w=0, h=0):
        self.texture, self.tw, self.th = texture, w, h
        self.zoom, self.ox, self.oy = None, 0.0, 0.0
        self._cambiato()

    def imposta_cornice(self, accesa, spessore):
        self.cornice, self.spessore = accesa, spessore
        self._cambiato()

    def zoom_a(self, nuova, cx=None, cy=None):
        """Zoom a scala `nuova` mantenendo fermo il punto (cx, cy) in coordinate del widget."""
        if not self.texture or self.get_width() < 2 or self.get_height() < 2:
            return
        vw, vh = self.get_width(), self.get_height()
        if cx is None:
            cx, cy = vw / 2, vh / 2
        adatta = self.scala_adatta()
        massimo = max(16.0 / self._scala_schermo(), adatta)
        nuova = min(max(nuova, adatta), massimo)
        if nuova <= adatta * 1.0001:
            self.zoom, self.ox, self.oy = None, 0.0, 0.0
        else:
            s = self.scala()
            px, py = cx - vw / 2, cy - vh / 2
            qx, qy = (px - self.ox) / s, (py - self.oy) / s
            self.zoom = nuova
            self.ox, self.oy = px - qx * nuova, py - qy * nuova
        self._cambiato()

    def zoom_fattore(self, f, cx=None, cy=None):
        self.zoom_a(self.scala() * f, cx, cy)

    def adatta(self):
        self.zoom, self.ox, self.oy = None, 0.0, 0.0
        self._cambiato()

    def cento(self, cx=None, cy=None):
        self.zoom_a(1.0 / self._scala_schermo(), cx, cy)

    def alterna(self, cx=None, cy=None):
        if self.zoom is None:
            self.cento(cx, cy)
        else:
            self.adatta()

    # -- eventi
    def _scroll(self, ctrl, dx, dy):
        if not dy:
            return False
        p = self.puntatore
        self.zoom_fattore(1.1 ** (-dy), *(p if p else (None, None)))
        return True

    def _trascina(self, g, dx, dy):
        if self.zoom is None:
            return
        self.ox, self.oy = self._base[0] + dx, self._base[1] + dy
        self._limita()
        self.queue_draw()

    def _clic(self, g, n, x, y):
        if n == 2:
            self.alterna(x, y)

    def do_size_allocate(self, w, h, baseline):
        self._cambiato()

    def do_snapshot(self, snapshot):
        if not self.texture:
            return
        vw, vh = self.get_width(), self.get_height()
        s = self.scala()
        cw, ch = self._contenuto()
        b = self._bordo()
        k = self._scala_schermo()
        x = round(((vw - cw * s) / 2 + self.ox) * k) / k
        y = round(((vh - ch * s) / 2 + self.oy) * k) / k
        r = Graphene.Rect()
        if b > 0:
            bianco = Gdk.RGBA()
            bianco.red = bianco.green = bianco.blue = bianco.alpha = 1.0
            snapshot.append_color(bianco, r.init(x, y, cw * s, ch * s))
        if s * k >= 2:
            filtro = Gsk.ScalingFilter.NEAREST
        elif s * k >= 1:
            filtro = Gsk.ScalingFilter.LINEAR
        else:
            filtro = Gsk.ScalingFilter.TRILINEAR
        snapshot.append_scaled_texture(self.texture, filtro,
                                       r.init(x + b * s, y + b * s, self.tw * s, self.th * s))


# ---------------------------------------------------------------- Togli mosso (modalità dentro la finestra)

RIT_MAX = 800          # lato massimo dei ritagli in pixel fisici: oltre, l'anteprima diventa lenta
RIT_MIN = 120
MARGINE_ANTEPRIMA = 80  # pixel in più attorno al ritaglio, per non avere artefatti ai bordi
ALTEZZA_PAN_MAX = 230   # altezza massima della panoramica (logica)
GIALLO = (1.0, 0.93, 0.27, 1.0)


def colore(r, g, b, a=1.0):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


def texture_da_rgb(arr):
    """Gdk.Texture da un array uint8 (H, W, 3)."""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(arr.tobytes()), w * 3)


class RitaglioVista(Gtk.Widget):
    """Un ritaglio a grandezza reale: un pixel della foto è un pixel fisico dello schermo."""
    __gtype_name__ = 'CorniceRitaglio'

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_size_request(200, 200)
        self.add_css_class('vista-foto')
        self.texture = None
        self.tw = self.th = 0
        self.velo = False
        self.su_misura = None      # callback senza argomenti: la dimensione è cambiata

    def lato_fisico(self):
        """Lato del quadrato più grande che ci sta, in pixel fisici."""
        return int(min(self.get_width(), self.get_height()) * scala_schermo(self))

    def imposta(self, texture, w=0, h=0):
        self.texture, self.tw, self.th = texture, w, h
        self.queue_draw()

    def imposta_velo(self, si):
        self.velo = si
        self.queue_draw()

    def do_size_allocate(self, w, h, baseline):
        if self.su_misura:
            self.su_misura()

    def do_snapshot(self, snapshot):
        if not self.texture:
            return
        vw, vh = self.get_width(), self.get_height()
        k = scala_schermo(self)
        lw, lh = self.tw / k, self.th / k
        x = round((vw - lw) / 2 * k) / k
        y = round((vh - lh) / 2 * k) / k
        r = Graphene.Rect().init(x, y, lw, lh)
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.NEAREST, r)
        if self.velo:                      # velo sul ritaglio che non corrisponde più ai cursori
            snapshot.append_color(colore(0, 0, 0, 0.55), r)


class Panoramica(Gtk.Widget):
    """La foto intera ridotta, con il riquadro del ritaglio e una linea che mostra la direzione del mosso."""
    __gtype_name__ = 'CorniceTogliPanoramica'

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class('vista-foto')
        self.texture = None
        self.fw = self.fh = 0          # dimensioni vere della foto
        self.zona = None               # (x0, y0, x1, y1) nella foto
        self.lung = 0.0
        self.lung_max = 60.0
        self.ang = 0.0
        self.su_scelta = None          # callback (x, y) in coordinate della foto
        self._inizio = (0.0, 0.0)
        dr = Gtk.GestureDrag()
        dr.connect('drag-begin', self._inizia)
        dr.connect('drag-update', self._trascina)
        self.add_controller(dr)
        self.set_cursor_from_name('crosshair')

    def imposta_foto(self, texture, w, h):
        self.texture, self.fw, self.fh = texture, w, h
        self.queue_resize()
        self.queue_draw()

    def imposta_zona(self, zona, lung, ang):
        self.zona, self.lung, self.ang = zona, lung, ang
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 100, 300, -1, -1
        if self.fw and for_size > 0:
            h = int(max(60, min(for_size * self.fh / self.fw, ALTEZZA_PAN_MAX)))
            return h, h, -1, -1
        return 100, 160, -1, -1

    def _geometria(self):
        W, H = self.get_width(), self.get_height()
        s = min(W / self.fw, H / self.fh) if self.fw and self.fh else 1.0
        return s, (W - self.fw * s) / 2, (H - self.fh * s) / 2

    def _scegli(self, x, y):
        if not self.fw or not self.su_scelta:
            return
        s, ox, oy = self._geometria()
        fx = int(min(max((x - ox) / s, 0), self.fw - 1))
        fy = int(min(max((y - oy) / s, 0), self.fh - 1))
        self.su_scelta(fx, fy)

    def _inizia(self, g, x, y):
        self._inizio = (x, y)
        self._scegli(x, y)

    def _trascina(self, g, dx, dy):
        self._scegli(self._inizio[0] + dx, self._inizio[1] + dy)

    def do_snapshot(self, snapshot):
        if not self.texture:
            return
        s, ox, oy = self._geometria()
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init(ox, oy, self.fw * s, self.fh * s))
        if not self.zona:
            return
        x0, y0, x1, y1 = self.zona
        rect = Graphene.Rect().init(ox + x0 * s, oy + y0 * s, (x1 - x0) * s, (y1 - y0) * s)
        pb = Gsk.PathBuilder.new()
        pb.add_rect(rect)
        a = math.radians(self.ang)
        mezza = 6 + min(self.lung, self.lung_max) / self.lung_max * 26
        cx, cy = ox + (x0 + x1) / 2 * s, oy + (y0 + y1) / 2 * s
        pb.move_to(cx - mezza * math.cos(a), cy + mezza * math.sin(a))
        pb.line_to(cx + mezza * math.cos(a), cy - mezza * math.sin(a))
        path = pb.to_path()
        snapshot.append_stroke(path, Gsk.Stroke.new(4.0), colore(0, 0, 0, 0.7))
        snapshot.append_stroke(path, Gsk.Stroke.new(2.0), colore(*GIALLO))


class PannelloTogli(Gtk.Box):
    """La modalità "Togli mosso": prima e dopo al 100 % affiancati e, a destra, panoramica, stime, cursori
    e salvataggio. Il lavoro pesante gira in thread; la finestra GTK si tocca solo dal thread principale
    (i risultati arrivano con GLib.idle_add)."""

    def __init__(self, win, scatto, esito):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.win = win
        self.scatto = scatto
        self.esito0 = esito            # stima già nota dall'indicatore (o None)
        self.fotos = []                # Foto correggibili: la prima fa da anteprima
        self.base = None
        self.centro = None
        self.rit = RIT_MAX             # lato attuale dei ritagli, in pixel fisici
        self.incerta = None            # (lunghezza, angolo) proposto ma non applicato
        self.lung_max = 60.0           # massimo del cursore Lunghezza (cresce con la risoluzione)
        self.occupato = False          # caricamento, stima o salvataggio in corso
        self.salvando = False
        self.chiuso = False
        self.caricato = False
        self.aggiornato = False        # il "Dopo" corrisponde ai cursori
        self.giro = 0
        self.ferma_ante = None
        self.ferma_lavoro = threading.Event()
        self._timer = None
        self._timer_misura = None
        self._pulsa = None
        self._blocca = False
        self._costruisci()

    # ---------------------------------------------------------- costruzione
    def _costruisci(self):
        # area centrale: i due ritagli (o il messaggio di caricamento / errore)
        self.pagine = Gtk.Stack(hexpand=True, vexpand=True)
        self.stato_pagina = Adw.StatusPage(icon_name='image-x-generic-symbolic')
        self.spinner = Gtk.Spinner(spinning=True, width_request=48, height_request=48, halign=Gtk.Align.CENTER)
        self.stato_pagina.set_child(self.spinner)
        self.pagine.add_named(self.stato_pagina, 'stato')
        righe = Gtk.Box(spacing=12, homogeneous=True, margin_top=12, margin_bottom=12, margin_start=12,
                        margin_end=12)
        self.v_prima, self.v_dopo = RitaglioVista(), RitaglioVista()
        self.l_prima = Gtk.Label(label=_('Before (100%)'))
        self.l_dopo = Gtk.Label(label=_('After (100%)'))
        for lbl, v in ((self.l_prima, self.v_prima), (self.l_dopo, self.v_dopo)):
            lbl.add_css_class('heading')
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            col.append(lbl)
            col.append(v)
            righe.append(col)
        self.v_prima.su_misura = self._misura_cambiata
        self.pagine.add_named(righe, 'ritagli')
        self.append(self.pagine)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # barra laterale
        lat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=340)
        lat.set_hexpand(False)
        cont = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        cont.append(Gtk.Label(label=_('Whole photo (click or drag to choose the area)'), xalign=0, wrap=True,
                              css_classes=['heading']))
        self.pan = Panoramica()
        self.pan.su_scelta = self._zona_scelta
        cont.append(self.pan)
        self.lbl_stato = Gtk.Label(xalign=0, wrap=True, wrap_mode=2, selectable=False)
        cont.append(self.lbl_stato)
        self.barra = Gtk.ProgressBar(visible=False)
        cont.append(self.barra)
        self.b_stima = Gtk.Button(label=_('Estimate on the whole photo'))
        self.b_stima.connect('clicked', lambda b: self.stima())
        self.b_zona = Gtk.Button(label=_('Estimate in the chosen area'))
        self.b_zona.connect('clicked', lambda b: self.stima(True))
        self.b_prova = Gtk.Button(label=_('Try the uncertain value'), visible=False)
        self.b_prova.connect('clicked', lambda b: self.prova_incerta())
        for b in (self.b_stima, self.b_zona, self.b_prova):
            cont.append(b)
        self.s_lung, self.v_lung = self._cursore(cont, _('Length'), 0, 60, 0.1, 0.0,
                                                 lambda v: _('%s px') % dec(v, 1),
                                                 _('Length of the blur, in pixels of the photo'))
        self.s_ang, self.v_ang = self._cursore(cont, _('Direction'), 0, 180, 1, 0, lambda v: '%.0f°' % v,
                                               _('0° horizontal, 90° vertical'))
        self.s_forza, self.v_forza = self._cursore(cont, _('Strength'), 5, 60, 1, 20, lambda v: '%d' % v,
                                                   _('More sharpness, but more noise'))
        self.s_freno, self.v_freno = self._cursore(cont, _('Noise brake'), 0, 100, 1, 30, lambda v: '%d' % v,
                                                   _('Cleaner, but smoother'))
        self.s_sfoc, self.v_sfoc = self._cursore(cont, _('Slight defocus'), 0, 8, 0.1, 0.0,
                                                 lambda v: _('%s px') % dec(v, 1),
                                                 _('Removes an extra uniform blur'))
        # una riga per etichetta: con alcuni caratteri GTK 4 non disegna i trattini bassi dalla seconda riga in giù
        self.box_file = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cont.append(self.box_file)
        self.scorri = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=cont)
        lat.append(self.scorri)

        # salvataggio, sempre in vista in fondo
        basso = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=12,
                        margin_start=12, margin_end=12)
        self.barra_salva = Gtk.ProgressBar(visible=False)
        self.lbl_salva = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=['caption', 'dim-label'])
        self.b_copia = Gtk.Button(label=_('Save copy'), tooltip_text=_('Writes <name>_nitida next to the originals'))
        self.b_copia.add_css_class('suggested-action')
        self.b_copia.connect('clicked', lambda b: self.salva_copia())
        self.b_sost = Gtk.Button(label=_('Replace the original'),
                                 tooltip_text=_('The originals go to the trash; Ctrl+Z undoes'))
        self.b_sost.add_css_class('destructive-action')
        self.b_sost.connect('clicked', lambda b: self.sostituisci())
        for w in (Gtk.Separator(), self.lbl_salva, self.barra_salva, self.b_copia, self.b_sost):
            basso.append(w)
        lat.append(basso)
        self.append(lat)
        self._abilita()

    def _cursore(self, cont, titolo, a, b, passo, valore, formato, suggerimento):
        riga = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, tooltip_text=suggerimento)
        testa = Gtk.Box()
        testa.append(Gtk.Label(label=titolo, xalign=0, hexpand=True))
        val = Gtk.Label(xalign=1, css_classes=['numeric', 'dim-label'])
        testa.append(val)
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, a, b, passo)
        sc.set_draw_value(False)
        sc.set_increments(passo, passo * 10)
        sc.set_value(valore)
        val.set_label(formato(valore))

        def cambio(s):
            val.set_label(formato(s.get_value()))
            if not self._blocca:
                self._zona_e_direzione()
                self.prenota()
        sc.connect('value-changed', cambio)
        riga.append(testa)
        riga.append(sc)
        cont.append(riga)
        return sc, val

    # ---------------------------------------------------------- valori
    def lung(self):
        return round(self.s_lung.get_value(), 1)

    def ang(self):
        return self.s_ang.get_value()

    def forza(self):
        return int(round(self.s_forza.get_value()))

    def freno(self):
        return self.s_freno.get_value() / 100.0

    def sfoc(self):
        return round(self.s_sfoc.get_value(), 1)

    def imposta_valori(self, lung=None, ang=None):
        self._blocca = True
        if lung is not None:
            self.s_lung.set_value(round(lung, 1))
        if ang is not None:
            self.s_ang.set_value(round(ang))
        self._blocca = False
        self._zona_e_direzione()

    def larghezza(self, percorso):
        """Larghezza (nell'orientamento mostrato) di una delle foto caricate, o None."""
        for f in self.fotos:
            if os.path.abspath(f.path) == os.path.abspath(percorso):
                return f.lin.shape[1]
        return None

    def _scalate(self, lung):
        """[(Foto, lunghezza)]: il mosso si scala con il rapporto tra le larghezze dei due file."""
        wb = self.base.lin.shape[1]
        return [(f, lung * f.lin.shape[1] / wb) for f in self.fotos]

    # ---------------------------------------------------------- stato dei comandi e messaggi
    def _abilita(self):
        ok = self.caricato and not self.occupato
        for b in (self.b_stima, self.b_zona, self.b_copia, self.b_sost):
            b.set_sensitive(ok)
        self.b_prova.set_sensitive(ok and bool(self.incerta))
        self.b_prova.set_visible(bool(self.incerta))
        self.scorri.set_sensitive(not self.salvando)
        self.win.b_fine.set_sensitive(not self.salvando)

    def stato(self, testo):
        self.lbl_stato.set_label(testo)

    def _barra(self, mostra, frazione=None, barra=None):
        barra = barra or self.barra
        barra.set_visible(mostra)
        if mostra and frazione is not None:
            barra.set_fraction(max(0.0, min(1.0, frazione)))
        self._ferma_pulsazione()
        if mostra and frazione is None:
            self._pulsa = GLib.timeout_add(120, lambda: (barra.pulse(), GLib.SOURCE_CONTINUE)[1])

    def _ferma_pulsazione(self):
        if self._pulsa is not None:
            GLib.source_remove(self._pulsa)
            self._pulsa = None

    def _progresso(self, p, barra=None):
        if not self.chiuso:
            barra = barra or self.barra
            self._ferma_pulsazione()
            barra.set_visible(True)
            barra.set_fraction(max(0.0, min(1.0, p)))
        return GLib.SOURCE_REMOVE

    def _lavora(self, funzione, fine, messaggio):
        """Esegue funzione() in un thread; fine(risultato, errore) torna nel thread principale."""
        if self.chiuso:
            return
        self.occupato = True
        self.ferma_lavoro.clear()
        self._abilita()
        self.stato(messaggio)
        self._barra(True)

        def corpo():
            try:
                r, e = funzione(), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            GLib.idle_add(self._lavoro_finito, fine, r, e)
        threading.Thread(target=corpo, daemon=True).start()

    def _lavoro_finito(self, fine, r, e):
        if self.chiuso:
            return GLib.SOURCE_REMOVE
        self.occupato = False
        self._barra(False)
        self._abilita()
        fine(r, e)
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------- caricamento
    def avvia(self):
        raw, img = self.scatto.raw, self.scatto.immagine
        percorsi = [p for p in (raw, img) if p]          # il RAW per primo: ha la precedenza per l'anteprima
        self.pagine.set_visible_child_name('stato')
        self.stato_pagina.set_title(_('Loading %s…') % ', '.join(os.path.basename(p) for p in percorsi))
        self.stato_pagina.set_description('')
        self.stato(_('Loading the photos…'))

        def lavoro():
            usabili, note = [], []
            for p in percorsi:
                try:
                    f = togli.Foto(p)
                except Exception as e:  # noqa: BLE001
                    note.append((p, str(e)))
                    continue
                if not f.correggibile():
                    note.append((p, _('sensor not supported: the blur of this RAW cannot be corrected')))
                    continue
                usabili.append(f)
            pan = None
            if usabili:
                b = usabili[0]
                b.guadagno()                              # calcolato qui, non nei thread dell'anteprima
                h, w = b.lin.shape[:2]
                passo = max(max(h, w) // 1600, 1)
                pan = b.anteprima8(b.lin[::passo, ::passo])
            return usabili, note, pan
        self._lavora(lavoro, self._caricato, _('Loading the photos…'))

    def _caricato(self, r, e):
        if e is not None:
            self._errore_caricamento(str(e))
            return
        usabili, note, pan = r
        if not usabili:
            self._errore_caricamento('\n'.join('%s: %s' % (os.path.basename(p), m) for p, m in note))
            return
        self.fotos = usabili
        self.base = usabili[0]
        self.caricato = True
        h, w = self.base.lin.shape[:2]
        self.lung_max = togli.lunghezza_massima_cursore(max(h, w))
        self.s_lung.set_range(0, self.lung_max)       # foto molto grandi: il mosso in pixel può essere più lungo
        self.pan.lung_max = self.lung_max
        self.centro = (w // 2, h // 2)
        self.pan.imposta_foto(texture_da_rgb(pan), w, h)
        self.pagine.set_visible_child_name('ritagli')
        nomi = ['%s → %s' % (f.nome, f.nome_uscita()) for f in usabili]
        self.nota = ''
        if note:
            self.nota = ' '.join(_('%s cannot be corrected (%s): only %s is corrected.')
                                 % (os.path.basename(p), m, ', '.join(f.nome for f in usabili)) for p, m in note)
        while (figlio := self.box_file.get_first_child()) is not None:
            self.box_file.remove(figlio)
        for riga in nomi + ([''] + [self.nota] if self.nota else []):
            self.box_file.append(Gtk.Label(label=riga, xalign=0, wrap=True, wrap_mode=2,
                                           css_classes=['caption', 'dim-label']))
        self._abilita()
        e0 = self.esito0
        if e0 is not None and e0.stato in ('recuperabile', 'incerto'):
            wf = self.larghezza(self.scatto.da_mostrare) or w
            l = e0.lunghezza * w / wf
            if e0.stato == 'recuperabile':
                self.imposta_valori(l, e0.angolo)
                self.stato(_('Estimated blur: %s pixels at %s°. Check the "After" crop and, if needed, '
                             'adjust the sliders; then save.') % (dec(l, 1), dec(e0.angolo, 0)))
            else:
                self.incerta = (l, e0.angolo)
                self.imposta_valori(0.0, e0.angolo)
                self.stato(_('Not sure: maybe a blur of %s pixels at %s°. Press "Try the uncertain value" '
                             'and judge from the "After" crop; if it gets worse, set the length back to zero.')
                           % (dec(l, 1), dec(e0.angolo, 0)))
            self._abilita()
            self.aggiorna()
        else:
            self.stima()

    def _errore_caricamento(self, testo):
        self.spinner.set_visible(False)
        self.stato_pagina.set_icon_name('dialog-error-symbolic')
        self.stato_pagina.set_title(_('Cannot be corrected'))
        self.stato_pagina.set_description(testo)
        self.pagine.set_visible_child_name('stato')
        self.stato(testo)
        self._abilita()

    # ---------------------------------------------------------- stima
    def stima(self, zona=False):
        if not self.base or self.occupato:
            return
        f = self.base
        if zona:                        # solo attorno al punto scelto: per un soggetto che si muove, o quando il
            x, y = self.centro          # resto della foto è fuori fuoco
            self._lavora(lambda: togli.stima_zona(f.lin, x, y), lambda r, e: self._stimato(r, e, True),
                         _('Estimating the blur in the chosen area…'))
        else:                           # se è incerta, la conferma arriva dalle zone
            self._lavora(lambda: togli.stima_completa(f.lin, lambda p: GLib.idle_add(self._progresso, p)),
                         lambda r, e: self._stimato(r, e, False), _('Estimating the blur…'))

    def _stimato(self, r, e, zona):
        self.incerta = None
        if e is not None:
            self.stato(_('Estimation error: %s') % e)
            self._abilita()
            return
        if zona:
            if not (r[2] >= togli.VOTI_ZONA and r[0] >= 4):
                self.stato(_('I cannot find an obvious straight-line blur in this area. Try clicking on a '
                             'part of the subject with more detail (lettering, sharp edges), or set the '
                             'sliders by hand.'))
                self._abilita()
                return
            esito, lung, ang, come = 'sicura', r[0], r[1], ''
        else:
            esito, lung, ang, come = r
        if esito in ('sicura', 'zone'):
            self.imposta_valori(lung, ang)
            da = _(' (confirmed by %s)') % come if esito == 'zone' else ''
            self.stato(_('Estimated blur: %s pixels at %s°%s. Check the "After" crop and, if needed, '
                         'adjust the sliders; then save.') % (dec(lung, 1), dec(ang, 0), da))
        elif esito == 'incerta':
            self.imposta_valori(0.0, None)
            self.incerta = (lung, ang)
            self.stato(_('Not sure: maybe a blur of %s pixels at %s° (%s). Press "Try the uncertain value" '
                         'and judge from the "After" crop; if it gets worse, set the length back to zero.')
                       % (dec(lung, 1), dec(ang, 0), come))
        else:
            self.imposta_valori(0.0, None)
            self.stato(_('I cannot find an obvious straight-line blur, either on the whole photo or in its '
                         'areas. If only the subject is blurred, click on it in the whole photo and press '
                         '"Estimate in the chosen area". Otherwise the photo is sharp or out of focus: for a '
                         'slight defocus try the last slider.'))
        self._abilita()
        self.aggiorna()

    def prova_incerta(self):
        if not self.incerta:
            return
        lung, ang = self.incerta
        self.imposta_valori(lung, ang)
        self.stato(_('Trying %s pixels at %s°: look at the "After" crop. If the edges become double or '
                     'streaks appear, the value is wrong: set the length back to zero.') % (dec(lung, 1), dec(ang, 0)))
        self.aggiorna()

    # ---------------------------------------------------------- ritagli e anteprima
    def _zona_e_direzione(self):
        if self.base:
            self.pan.imposta_zona(self.riquadro(), self.lung(), self.ang())

    def _zona_scelta(self, x, y):
        if self.salvando or not self.caricato:
            return
        self.centro = (x, y)
        self._zona_e_direzione()
        self.prenota(120)

    def riquadro(self):
        h, w = self.base.lin.shape[:2]
        lw, lh = min(self.rit, w), min(self.rit, h)
        x0 = int(min(max(self.centro[0] - lw // 2, 0), w - lw))
        y0 = int(min(max(self.centro[1] - lh // 2, 0), h - lh))
        return x0, y0, x0 + lw, y0 + lh

    def _misura_cambiata(self, subito=False):
        """La finestra è cambiata: il lato dei ritagli segue lo spazio, fino a RIT_MAX pixel fisici."""
        if self._timer_misura is not None:
            GLib.source_remove(self._timer_misura)
            self._timer_misura = None
        if subito:
            self._adatta()
        else:
            self._timer_misura = GLib.timeout_add(120, self._adatta)

    def _adatta(self):
        self._timer_misura = None
        lato = self.v_prima.lato_fisico()
        if lato <= 0:                                  # non ancora disposto
            return GLib.SOURCE_REMOVE
        rit = int(min(max(lato, RIT_MIN), RIT_MAX))
        if rit != self.rit and self.caricato:
            self.rit = rit
            self.prenota(0)
        elif not self.caricato:
            self.rit = rit
        return GLib.SOURCE_REMOVE

    def prenota(self, ritardo=200):
        """Rimanda l'aggiornamento dell'anteprima: durante il ritocco dei cursori si calcola solo alla fine."""
        self.aggiornato = False
        if self._timer is not None:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(ritardo, self._scatta)

    def _scatta(self):
        self._timer = None
        self.aggiorna()
        return GLib.SOURCE_REMOVE

    def aggiorna(self):
        if not self.base or self.chiuso:
            return
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        f = self.base
        lung, ang, forza = self.lung(), self.ang(), self.forza()
        freno, sfoc = self.freno(), self.sfoc()
        x0, y0, x1, y1 = self.riquadro()
        self.pan.imposta_zona((x0, y0, x1, y1), lung, ang)
        h, w = f.lin.shape[:2]
        m = MARGINE_ANTEPRIMA
        X0, Y0, X1, Y1 = max(x0 - m, 0), max(y0 - m, 0), min(x1 + m, w), min(y1 + m, h)
        pezzo = f.lin[Y0:Y1, X0:X1]
        ta = (slice(y0 - Y0, y1 - Y0), slice(x0 - X0, x1 - X0))
        self.v_prima.imposta(texture_da_rgb(f.anteprima8(pezzo[ta])), x1 - x0, y1 - y0)
        # il "Dopo" si calcola in un thread a parte: un nuovo ritocco abbandona il calcolo superato
        if self.ferma_ante is not None:
            self.ferma_ante.set()
        self.ferma_ante = ferma = threading.Event()
        self.giro += 1
        giro = self.giro
        self.aggiornato = False
        self.l_dopo.set_label(_('After (100%): updating…'))
        self.v_dopo.imposta_velo(True)
        if self.v_dopo.texture is None:
            self.v_dopo.imposta(self.v_prima.texture, x1 - x0, y1 - y0)

        def calcola():
            try:
                dopo = togli.togli_mosso(pezzo, lung, ang, forza, ferma=ferma, freno=freno, sfoc=sfoc)
                img = None if dopo is None else f.anteprima8(dopo[ta])
            except Exception:  # noqa: BLE001 - un'anteprima mancata non deve fermare il programma
                img = None
            GLib.idle_add(self._dopo_pronto, giro, img)
        threading.Thread(target=calcola, daemon=True).start()

    def _dopo_pronto(self, giro, img):
        if self.chiuso or giro != self.giro:          # risultato di una richiesta già superata
            return GLib.SOURCE_REMOVE
        self.l_dopo.set_label(_('After (100%)'))
        self.v_dopo.imposta_velo(False)
        if img is not None:
            self.v_dopo.imposta(texture_da_rgb(img), img.shape[1], img.shape[0])
        self.aggiornato = True
        return GLib.SOURCE_REMOVE

    @property
    def pronto(self):
        """Caricata, nessun calcolo in corso, "Dopo" aggiornato (lo usa la prova automatica)."""
        return (self.caricato and not self.occupato and self.aggiornato and self._timer is None
                and self._timer_misura is None)

    # ---------------------------------------------------------- salvataggio
    def _niente_da_correggere(self):
        if self.lung() < 0.5 and self.sfoc() < 0.3:
            self.win.dialogo(_('Nothing to correct'), _('Blur and defocus are both zero: there is nothing to correct.'))
            return True
        return False

    def salva_copia(self):
        if not self.caricato or self.occupato or self._niente_da_correggere():
            return
        esistono = [f.nome_uscita() for f in self.fotos
                    if os.path.exists(os.path.join(os.path.dirname(f.path), f.nome_uscita()))]
        if esistono:
            self.win.dialogo(_('Files already there'),
                             _('These already exist:\n%s\n\nShall I overwrite them?')
                             % '\n'.join('• ' + n for n in esistono),
                             [('no', _("Don't overwrite")), ('si', _('Overwrite'))], predefinita='no',
                             distruttiva='si', esito=lambda r: self._salva(False) if r == 'si' else None)
        else:
            self._salva(False)

    def sostituisci(self, confermato=False):
        if not self.caricato or self.occupato or self._niente_da_correggere():
            return
        originali = {os.path.abspath(f.path) for f in self.fotos}
        occupati = []
        for f in self.fotos:
            fin = os.path.join(os.path.dirname(os.path.abspath(f.path)), f.nome_finale())
            if os.path.lexists(fin) and fin not in originali:
                occupati.append(os.path.basename(fin))
        if occupati:
            self.win.dialogo(_('Cannot replace'),
                             _('Another file already has the name the corrected version would take:\n%s\n\n'
                               'Nothing was touched. Use "Save copy".') % '\n'.join('• ' + n for n in occupati))
            return
        if not op.puo_cestinare(self.fotos[0].path):
            self._senza_cestino()
            return
        if confermato:
            self._salva(True)
            return
        elenco = '\n'.join('• ' + f.nome + (' → ' + f.nome_finale() if f.nome_finale() != f.nome else '')
                           for f in self.fotos)
        self.win.dialogo(_('Replace the original?'),
                         _('The corrected versions take the place of:\n%s\n\nThe originals go to the trash: '
                           'you can recover them, and Ctrl+Z undoes the replacement. A JPG that is saved again '
                           'loses a little quality.') % elenco,
                         [('no', _('Cancel')), ('si', _('Replace'))], predefinita='no', distruttiva='si',
                         esito=lambda r: self._salva(True) if r == 'si' else None)

    def _senza_cestino(self):
        self.win.dialogo(_('The trash is not available'),
                         _('For these files (for example on an SD card) I cannot send the originals to the '
                           'trash, so I do not replace them. Use "Save copy".'))

    def _salva(self, sostituzione):
        if self.occupato:
            return
        lung, ang, forza, freno, sfoc = self.lung(), self.ang(), self.forza(), self.freno(), self.sfoc()
        scalate = self._scalate(lung)
        n = len(scalate)
        if self.ferma_ante is not None:
            self.ferma_ante.set()                    # l'anteprima non serve più: tutto il processore al salvataggio
        ferma = self.ferma_lavoro
        scatto = self.scatto

        def prog(i):
            return lambda p: GLib.idle_add(self._progresso, (i + p) / n, self.barra_salva)

        def lavoro():
            if not sostituzione:
                return [f.elabora_e_salva(os.path.dirname(f.path), l, ang, forza, avanzamento=prog(i),
                                          freno=freno, sfoc=sfoc, ferma=ferma)
                        for i, (f, l) in enumerate(scalate)]
            piano = []
            try:
                for i, (f, l) in enumerate(scalate):
                    tmp = op.temporaneo(os.path.abspath(f.path))
                    piano.append((os.path.abspath(f.path), tmp, f.nome_finale()))
                    f.elabora_e_salva(os.path.dirname(tmp), l, ang, forza, avanzamento=prog(i), freno=freno,
                                      sfoc=sfoc, ferma=ferma, destinazione=tmp)
                return op.sostituisci(scatto, piano)     # trasferisce e, se qualcosa va storto, rimette tutto a posto
            except BaseException:
                for o, t, nome in piano:
                    try:
                        os.remove(t)
                    except OSError:
                        pass
                raise

        self.salvando = True
        self.lbl_salva.set_label(_('Replacing…') if sostituzione else _('Saving the copy…'))
        self.lbl_salva.set_visible(True)
        self.barra_salva.set_visible(True)
        self.barra_salva.set_fraction(0)
        self._lavora(lavoro, lambda r, e: self._salvato(r, e, sostituzione),
                     _('Removing the blur from the whole photo (10-15 seconds per file)…'))
        self._barra(False)                           # il progresso del salvataggio ha la sua barra

    def _salvato(self, r, e, sostituzione):
        self.salvando = False
        self.barra_salva.set_visible(False)
        self.lbl_salva.set_visible(False)
        self._abilita()
        if e is not None:
            if isinstance(e, op.CestinoNonDisponibile):
                self.stato(_('Nothing was replaced: the trash is not available.'))
                self._senza_cestino()
            elif isinstance(e, op.Conflitto):
                self.stato(_('Nothing was replaced: a file with that name already exists.'))
                self.win.dialogo(_('Cannot replace'), _('Other files with these names already exist:\n%s\n\n'
                                 'Nothing was touched.') % '\n'.join('• ' + os.path.basename(f) for f in e.file))
            elif isinstance(e, togli.Interrotto):
                self.stato(_('Saving interrupted: nothing was written.'))
            else:
                self.stato(_('Saving failed: %s') % e)
                self.win.dialogo(_('Saving failed'), _('%s\n\nThe originals were not touched.') % e)
            return
        if sostituzione:
            self.win.togli_sostituito(r)
        else:
            self.win.togli_copiato(r)

    # ---------------------------------------------------------- chiusura
    def chiudi(self):
        """Ferma ogni calcolo in corso e ignora i risultati che arriveranno."""
        self.chiuso = True
        self.ferma_lavoro.set()
        if self.ferma_ante is not None:
            self.ferma_ante.set()
        for t in (self._timer, self._timer_misura):
            if t is not None:
                GLib.source_remove(t)
        self._timer = self._timer_misura = None
        self._ferma_pulsazione()


# ---------------------------------------------------------------- finestra

class Finestra(Adw.ApplicationWindow):
    def __init__(self, app, cfg, salva=True, destinazione=None, cache_mosso=None, config_path=CONFIG_PATH):
        super().__init__(application=app)
        self.cfg = cfg
        self._config_path = config_path
        self._salva_ok = salva
        self._dest_forzata = destinazione
        self.set_default_size(*cfg['finestra'])
        self.set_title('Cornice')

        self.scatti = []
        self.indice = 0
        self.pila = []             # (azione, indice)
        self.occupato = False
        self.toast = None
        self._ignora = False
        self._corrente = None      # percorso mostrato (o in attesa)
        self._mostrata = None      # percorso la cui texture è sulla vista
        self.cache = OrderedDict()  # percorso -> Risultato
        self.in_corso = {}
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.esiti = {}            # percorso -> Esito
        self.stim = None
        self.pannello = None       # la modalità Togli mosso, se aperta
        self._cache_mosso = cache_mosso
        self._crea_stimatore()

        self._costruisci()
        self._azioni()
        self._tasti()
        self._trascinamento()
        self.vista.su_cambio = self._aggiorna_zoom
        self.connect('close-request', self._chiudendo)
        self.connect('notify::fullscreened', self._cambio_schermo_intero)
        self._aggiorna_tutto()

    # ---------------------------------------------------------- costruzione
    def _costruisci(self):
        self.vista = VistaFoto()
        self.vista.imposta_cornice(self.cfg['cornice'], self.cfg['spessore'])

        # intestazione
        self.header = Adw.HeaderBar()
        self.titolo = Adw.WindowTitle(title='Cornice', subtitle='')
        self.header.set_title_widget(self.titolo)
        self.b_apri = Gtk.Button(icon_name='folder-open-symbolic', tooltip_text=_('Open folder (Ctrl+O)'))
        self.b_apri.connect('clicked', lambda b: self.apri_dialogo())
        self.header.pack_start(self.b_apri)
        self.b_fine = Gtk.Button(label=_('Done'), tooltip_text=_('Back to the photos (Esc)'), visible=False)
        self.b_fine.add_css_class('suggested-action')
        self.b_fine.connect('clicked', lambda b: self.esci_togli())
        self.header.pack_start(self.b_fine)
        self.t_cernita = Gtk.ToggleButton(label=_('Cull'), tooltip_text=_('Discard or approve photos'))
        self.t_cernita.connect('toggled', self._toggle_cernita)
        self.header.pack_start(self.t_cernita)

        menu = Gio.Menu()
        menu.append(_('Preferences'), 'win.preferenze')
        menu.append(_('Keyboard shortcuts'), 'win.scorciatoie')
        menu.append(_('About Cornice'), 'win.informazioni')
        self.header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu,
                                            primary=True, tooltip_text=_('Main menu')))
        gruppo = self.gruppo_cornice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        gruppo.add_css_class('linked')
        self.t_cornice = Gtk.ToggleButton(label=_('Frame'), tooltip_text=_('White frame (C)'))
        self.t_cornice.set_active(self.cfg['cornice'])
        self.t_cornice.connect('toggled', self._toggle_cornice)
        gruppo.append(self.t_cornice)
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Label(label=_('Frame thickness'), xalign=0, css_classes=['heading']))
        self.scala_sp = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 25, 1)
        self.scala_sp.set_value(self.cfg['spessore'])
        self.scala_sp.set_draw_value(True)
        self.scala_sp.set_value_pos(Gtk.PositionType.RIGHT)
        self.scala_sp.set_size_request(240, -1)
        self.scala_sp.connect('value-changed', self._cambio_spessore)
        box.append(self.scala_sp)
        box.append(Gtk.Label(label=_("percentage of the photo's short side"), xalign=0,
                             css_classes=['dim-label', 'caption']))
        pop.set_child(box)
        gruppo.append(Gtk.MenuButton(popover=pop, tooltip_text=_('Frame thickness')))
        self.header.pack_end(gruppo)

        # pagine: foto / stato
        self.pila_pagine = Gtk.Stack()
        self.stato = Adw.StatusPage(icon_name='folder-pictures-symbolic', title=_('No photo open'),
                                    description=_('Open a folder or drop a photo here.'))
        b = Gtk.Button(label=_('Open folder'), halign=Gtk.Align.CENTER)
        b.add_css_class('suggested-action')
        b.add_css_class('pill')
        b.connect('clicked', lambda x: self.apri_dialogo())
        self.stato.set_child(b)
        self.pila_pagine.add_named(self.stato, 'stato')

        ov = Gtk.Overlay()
        ov.set_child(self.vista)
        self.chip = Gtk.Box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.START,
                            margin_top=12, margin_start=12)
        self.chip.add_css_class('chip-mosso')
        self.lbl_mosso = Gtk.Label()
        self.chip.append(self.lbl_mosso)
        self.b_togli = Gtk.Button(label=_('Deblur'))
        self.b_togli.add_css_class('flat')
        self.b_togli.add_css_class('pill')
        self.b_togli.connect('clicked', lambda b: self.entra_togli())
        self.chip.append(self.b_togli)
        ov.add_overlay(self.chip)
        self.lbl_errore = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, wrap=True)
        self.lbl_errore.add_css_class('chip-errore')
        ov.add_overlay(self.lbl_errore)
        self.b_prec = Gtk.Button(icon_name='go-previous-symbolic', halign=Gtk.Align.START,
                                 valign=Gtk.Align.CENTER, margin_start=12, tooltip_text=_('Previous (←)'))
        self.b_succ = Gtk.Button(icon_name='go-next-symbolic', halign=Gtk.Align.END,
                                 valign=Gtk.Align.CENTER, margin_end=12, tooltip_text=_('Next (→)'))
        for bt, d in ((self.b_prec, -1), (self.b_succ, 1)):
            bt.add_css_class('osd')
            bt.add_css_class('circular')
            bt.connect('clicked', lambda x, d=d: self.vai(d))
            ov.add_overlay(bt)
        self.pila_pagine.add_named(ov, 'foto')

        self.overlay_toast = Adw.ToastOverlay()
        self.overlay_toast.set_child(self.pila_pagine)

        # barra informazioni
        self.barra_info = Gtk.Box(spacing=12)
        self.barra_info.add_css_class('barra-info')
        self.lbl_info = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.lbl_info.add_css_class('caption')
        self.lbl_info.add_css_class('numeric')
        self.lbl_zoom = Gtk.Label(xalign=1)
        self.lbl_zoom.add_css_class('caption')
        self.lbl_zoom.add_css_class('numeric')
        self.barra_info.append(self.lbl_info)
        self.barra_info.append(self.lbl_zoom)

        # barra cernita
        self.barra_cernita = Gtk.Box(spacing=12)
        self.barra_cernita.add_css_class('barra-cernita')
        self.b_scarta = Gtk.Button(label=_('Discard'), tooltip_text=_('Move to the trash (X, Del)'))
        self.b_scarta.add_css_class('destructive-action')
        self.b_scarta.connect('clicked', lambda b: self.scarta())
        self.b_approva = Gtk.Button(label=_('Approve'), tooltip_text=_('Move to the destination (A, Enter)'))
        self.b_approva.add_css_class('suggested-action')
        self.b_approva.connect('clicked', lambda b: self.approva())
        self.lbl_dest = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.lbl_dest.add_css_class('dim-label')
        self.lbl_restano = Gtk.Label(xalign=1)
        self.lbl_restano.add_css_class('dim-label')
        for w in (self.b_scarta, self.b_approva, self.lbl_dest, self.lbl_restano):
            self.barra_cernita.append(w)
        self.barra_cernita.set_visible(False)

        self.tv = Adw.ToolbarView()
        self.tv.add_top_bar(self.header)
        self.tv.add_bottom_bar(self.barra_info)
        self.tv.add_bottom_bar(self.barra_cernita)
        self.tv.set_content(self.overlay_toast)
        self.set_content(self.tv)

        mo = Gtk.EventControllerMotion()
        mo.connect('motion', self._mouse_alto)
        self.tv.add_controller(mo)

    def _azioni(self):
        for nome, fn in (('preferenze', self.preferenze), ('scorciatoie', self.scorciatoie),
                         ('informazioni', self.informazioni)):
            a = Gio.SimpleAction.new(nome, None)
            a.connect('activate', lambda act, par, fn=fn: fn())
            self.add_action(a)

    def _tasti(self):
        k = Gtk.EventControllerKey()
        k.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        k.connect('key-pressed', self._tasto)
        self.add_controller(k)

    def _trascinamento(self):
        dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        dt.connect('drop', self._rilasciato)
        self.add_controller(dt)

    # ---------------------------------------------------------- tastiera
    def _tasto(self, ctrl, keyval, keycode, stato):
        if self.get_visible_dialog() is not None:
            return False
        ctrl_premuto = bool(stato & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(stato & Gdk.ModifierType.ALT_MASK)
        if alt:
            return False
        K = Gdk
        if self.pannello is not None:              # in Togli mosso: solo Esc; il resto va ai cursori e ai pulsanti
            if not ctrl_premuto and Gdk.keyval_to_lower(keyval) == K.KEY_Escape:
                self.esci_togli()
                return True
            return False
        if ctrl_premuto:
            c = Gdk.keyval_to_lower(keyval)
            if c == K.KEY_z:
                self.annulla_ultima()
            elif c == K.KEY_o:
                self.apri_dialogo()
            elif c == K.KEY_comma:
                self.preferenze()
            else:
                return False
            return True
        k = Gdk.keyval_to_lower(keyval)
        if k in (K.KEY_Left, K.KEY_Page_Up):
            self.vai(-1)
        elif k in (K.KEY_Right, K.KEY_Page_Down):
            self.vai(1)
        elif k == K.KEY_Home:
            self.vai_a(0)
        elif k == K.KEY_End:
            self.vai_a(len(self.scatti) - 1)
        elif k in (K.KEY_plus, K.KEY_equal, K.KEY_KP_Add):
            self.vista.zoom_fattore(1.25)
        elif k in (K.KEY_minus, K.KEY_KP_Subtract):
            self.vista.zoom_fattore(1 / 1.25)
        elif k in (K.KEY_0, K.KEY_KP_0):
            self.vista.adatta()
        elif k in (K.KEY_1, K.KEY_KP_1):
            self.vista.cento()
        elif k == K.KEY_m:
            self.entra_togli()
        elif k == K.KEY_c:
            self.t_cornice.set_active(not self.t_cornice.get_active())
        elif k in (K.KEY_f, K.KEY_F11):
            self.set_fullscreen_(not self.is_fullscreen())
        elif k == K.KEY_Escape:
            if not self.is_fullscreen():
                return False
            self.set_fullscreen_(False)
        elif k in (K.KEY_x, K.KEY_Delete, K.KEY_KP_Delete):
            if not self.t_cernita.get_active():
                return False
            self.scarta()
        elif k in (K.KEY_a, K.KEY_Return, K.KEY_KP_Enter):
            if not self.t_cernita.get_active():
                return False
            self.approva()
        else:
            return False
        return True

    # ---------------------------------------------------------- schermo intero
    def set_fullscreen_(self, si):
        if si:
            self.fullscreen()
        else:
            self.unfullscreen()

    def _cambio_schermo_intero(self, *a):
        fs = self.is_fullscreen()
        self.tv.set_extend_content_to_top_edge(fs)
        self.tv.set_reveal_top_bars(not fs)

    def _mouse_alto(self, ctrl, x, y):
        if self.is_fullscreen():
            self.tv.set_reveal_top_bars(y < 8 or (self.tv.get_reveal_top_bars() and y < 70))

    # ---------------------------------------------------------- apertura
    def apri_dialogo(self):
        dlg = Gtk.FileDialog(title=_('Open folder'))
        cur = self.scatti[self.indice].cartella if self.scatti else cartella_immagini()
        if os.path.isdir(cur):
            dlg.set_initial_folder(Gio.File.new_for_path(cur))

        def fine(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.apri_percorsi([f.get_path()])
        dlg.select_folder(self, None, fine)

    def _rilasciato(self, target, valore, x, y):
        if self.pannello is not None:
            return False
        percorsi = [f.get_path() for f in valore.get_files() if f.get_path()]
        if percorsi:
            self.apri_percorsi(percorsi)
            return True
        return False

    def apri_percorsi(self, percorsi, dopo=None):
        """Elenca gli scatti in un thread e mostra il primo (o quello del file indicato)."""
        percorsi = [os.path.abspath(p) for p in percorsi]

        def lavoro():
            if len(percorsi) == 1:
                p = percorsi[0]
                if os.path.isdir(p):
                    return op.elenca(p), 0
                lista = op.elenca(os.path.dirname(p))
                for i, s in enumerate(lista):
                    if p in s.file or any(os.path.samefile(p, f) for f in s.file if os.path.exists(f)):
                        return lista, i
                return lista, 0
            lista, viste = [], set()
            for p in percorsi:
                scelti = op.elenca(p) if os.path.isdir(p) else [op.scatto_di(p)]
                for s in scelti:
                    if (s.cartella, s.nome) not in viste:
                        viste.add((s.cartella, s.nome))
                        lista.append(s)
            return lista, 0

        def fine(ris, err):
            if err:
                self.avviso(_('Cannot open: %s') % err)
                return
            self.scatti, i = ris
            self.pila.clear()
            self.esiti.clear()
            if not self.scatti:
                self.mostra_stato(_('No photos'), _('There are no photos in this folder that Cornice can open.'))
            else:
                self.vai_a(i)
            self._aggiorna_cernita()
            if dopo:
                dopo()
        self._in_thread(lavoro, fine)

    def mostra_stato(self, titolo, descrizione, icona='folder-pictures-symbolic'):
        self.stato.set_title(titolo)
        self.stato.set_description(descrizione)
        self.stato.set_icon_name(icona)
        self.pila_pagine.set_visible_child_name('stato')
        self._corrente = self._mostrata = None
        self.vista.imposta_texture(None)
        self.titolo.set_title('Cornice')
        self.titolo.set_subtitle('')
        self._aggiorna_tutto()

    # ---------------------------------------------------------- navigazione
    def vai(self, d):
        self.vai_a(self.indice + d)

    def vai_a(self, i):
        if not self.scatti:
            return
        i = max(0, min(len(self.scatti) - 1, i))
        self.pila_pagine.set_visible_child_name('foto')
        self.indice = i
        s = self.scatti[i]
        p = s.da_mostrare
        self._corrente = p
        self.lbl_errore.set_visible(False)
        ris = self.cache.get(p)
        if ris is not None:
            self.cache.move_to_end(p)
            self._metti(p, ris)
        else:
            self._carica(p)
        if i + 1 < len(self.scatti):
            self._carica(self.scatti[i + 1].da_mostrare)
        self._chiedi_mosso()
        self._aggiorna_tutto()

    def _metti(self, p, ris):
        self._mostrata = p
        self.lbl_errore.set_visible(False)
        self.vista.imposta_texture(ris.texture, ris.w, ris.h)
        self._aggiorna_tutto()

    def _carica(self, p):
        if p in self.cache or p in self.in_corso:
            return
        self.in_corso[p] = True

        def lavoro():
            try:
                r, e = carica_foto(p), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            GLib.idle_add(self._arrivata, p, r, e)
        self.pool.submit(lavoro)

    def _arrivata(self, p, ris, err):
        self.in_corso.pop(p, None)
        if ris is not None:
            self.cache[p] = ris
            while len(self.cache) > CACHE_TEXTURE:
                self.cache.popitem(last=False)
            if p == self._corrente:
                self._metti(p, ris)
        elif p == self._corrente:
            self._mostrata = None
            self.vista.imposta_texture(None)
            self.lbl_errore.set_label(_('Cannot open %s') % os.path.basename(p))
            self.lbl_errore.set_visible(True)
            self._aggiorna_tutto()
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------- aggiornamento interfaccia
    def _scatto(self):
        return self.scatti[self.indice] if self.scatti else None

    def _aggiorna_tutto(self):
        if self.pannello is not None:              # in Togli mosso la finestra non cambia con le foto
            return
        s = self._scatto()
        self.b_prec.set_sensitive(bool(s) and self.indice > 0)
        self.b_succ.set_sensitive(bool(s) and self.indice < len(self.scatti) - 1)
        mostra_nav = len(self.scatti) > 1
        self.b_prec.set_visible(mostra_nav)
        self.b_succ.set_visible(mostra_nav)
        if s:
            self.titolo.set_title(s.nome)
            self.titolo.set_subtitle(f'{self.indice + 1} / {len(self.scatti)}')
        parti = []
        if s:
            nome = s.nome
            tipi = tipi_scatto(s)
            parti.append(f'{nome} ({tipi})' if tipi else nome)
            parti.append(f'{self.indice + 1} / {len(self.scatti)}')
            ris = self.cache.get(s.da_mostrare)
            if ris:
                for k in ('data', 'tempo', 'diaframma', 'iso', 'ev'):
                    if ris.info.get(k):
                        parti.append(ris.info[k])
                if ris.incorporata:
                    parti.append(_('embedded preview'))
        self.lbl_info.set_label('   ·   '.join(parti))
        self.barra_info.set_visible(bool(s))
        self._aggiorna_zoom()
        self._aggiorna_cernita()
        self._aggiorna_chip()

    def _aggiorna_zoom(self):
        self.lbl_zoom.set_label(f'{self.vista.percentuale():.0f}%' if self.vista.texture else '')

    def _dest(self):
        return self._dest_forzata or self.cfg['destinazione']

    def _approva_possibile(self):
        s = self._scatto()
        if not s:
            return False
        try:
            return os.path.realpath(s.cartella) != os.path.realpath(self._dest())
        except OSError:
            return False

    def _aggiorna_cernita(self):
        if self.pannello is not None:
            return
        attiva = self.t_cernita.get_active()
        self.barra_cernita.set_visible(attiva)
        s = self._scatto()
        self.b_scarta.set_sensitive(bool(s) and not self.occupato)
        ok = self._approva_possibile()
        self.b_approva.set_sensitive(ok and not self.occupato)
        self.b_approva.set_tooltip_text(_('Move to the destination (A, Enter)') if ok or not s
                                        else _('The current folder is already the destination'))
        d = self._dest()
        self.lbl_dest.set_label(_('Destination: %s') % (os.path.basename(d.rstrip('/')) or d))
        self.lbl_dest.set_tooltip_text(d)
        n = len(self.scatti)
        self.lbl_restano.set_label(ngettext('%d photo left', '%d photos left', n) % n)

    # ---------------------------------------------------------- cornice
    def _toggle_cornice(self, b):
        if self._ignora:
            return
        self.cfg['cornice'] = b.get_active()
        self.vista.imposta_cornice(b.get_active(), self.cfg['spessore'])
        self.salva()

    def _cambio_spessore(self, scala):
        if self._ignora:
            return
        self.cfg['spessore'] = int(round(scala.get_value()))
        self.vista.imposta_cornice(self.cfg['cornice'], self.cfg['spessore'])
        self.salva()

    def imposta_spessore(self, v):
        self._ignora = True
        self.scala_sp.set_value(v)
        self._ignora = False
        self._cambio_spessore(self.scala_sp)

    def _toggle_cernita(self, b):
        self._aggiorna_cernita()

    # ---------------------------------------------------------- operazioni in thread
    def _in_thread(self, lavoro, fine):
        def corpo():
            try:
                r, e = lavoro(), None
            except Exception as ex:  # noqa: BLE001
                r, e = None, ex
            GLib.idle_add(lambda: (fine(r, e), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=corpo, daemon=True).start()

    def _imposta_occupato(self, v):
        self.occupato = v
        self._aggiorna_cernita()

    def avviso(self, testo, azione=None):
        """Toast con messaggio (e opzionale tasto Annulla)."""
        if self.toast:
            self.toast.dismiss()
        t = Adw.Toast(title=testo, timeout=6)
        if azione:
            t.set_button_label(_('Undo'))
            t.connect('button-clicked', lambda x: self.annulla_ultima())
        self.toast = t
        self.overlay_toast.add_toast(t)

    def dialogo(self, titolo, testo, risposte=None, predefinita='ok', distruttiva=None, esito=None):
        """Adw.AlertDialog; `esito(id_risposta)` alla chiusura."""
        d = Adw.AlertDialog(heading=titolo, body=testo)
        for rid, etichetta in (risposte or [('ok', _('OK'))]):
            d.add_response(rid, etichetta)
        d.set_default_response(predefinita)
        d.set_close_response(risposte[0][0] if risposte else 'ok')
        if distruttiva:
            d.set_response_appearance(distruttiva, Adw.ResponseAppearance.DESTRUCTIVE)

        def fine(dlg, res):
            r = dlg.choose_finish(res)
            if esito:
                esito(r)
        d.choose(self, None, fine)

    def _puo_agire(self):
        return bool(self.scatti) and not self.occupato and self.pila_pagine.get_visible_child_name() == 'foto'

    def _togli_dall_elenco(self, s):
        """Toglie lo scatto dall'elenco e mostra il successivo (o il precedente se era l'ultimo)."""
        i = next((k for k, x in enumerate(self.scatti) if x.cartella == s.cartella and x.nome == s.nome),
                 None)
        if i is None:
            return None
        del self.scatti[i]
        for f in s.file:
            self.cache.pop(f, None)
        if not self.scatti:
            self.mostra_stato(_('Culling finished'), _('There are no more photos in this folder.'),
                              'object-select-symbolic')
        else:
            self.vai_a(min(i, len(self.scatti) - 1))
        return i

    def scarta(self):
        if not self.t_cernita.get_active() or not self._puo_agire():
            return
        s = self._scatto()
        self._imposta_occupato(True)

        def lavoro():
            return op.scarta(s)

        def fine(az, err):
            if isinstance(err, op.CestinoNonDisponibile):
                self._imposta_occupato(False)
                if self._autoprova_attiva:
                    self.errori_autoprova.append('trash not available')
                    return
                self.dialogo(_('The trash is not available'),
                             _('%s cannot be moved to the trash. Do you want to delete it permanently? '
                               'This cannot be undone.') % s.nome,
                             [('no', _("Don't delete")), ('si', _('Delete permanently'))],
                             predefinita='no', distruttiva='si',
                             esito=lambda r: self._elimina(s) if r == 'si' else None)
                return
            if err:
                self._imposta_occupato(False)
                self.avviso(_('Discard failed: %s') % err)
                return
            self._dopo_azione(az, s)
        self._in_thread(lavoro, fine)

    def _elimina(self, s):
        self._imposta_occupato(True)

        def fine(r, err):
            self._imposta_occupato(False)
            if err:
                self.avviso(_('Deletion failed: %s') % err)
                return
            self._togli_dall_elenco(s)
            self.avviso(_('%s deleted permanently') % s.nome)
        self._in_thread(lambda: op.elimina_definitivo(s), fine)

    def approva(self):
        if not self.t_cernita.get_active() or not self._puo_agire() or not self._approva_possibile():
            return
        s = self._scatto()
        dest = self._dest()
        self._imposta_occupato(True)

        def fine(az, err):
            if isinstance(err, op.Conflitto):
                self._imposta_occupato(False)
                nomi = '\n'.join(f'• {os.path.basename(f)}' for f in getattr(err, 'file', []))
                self.dialogo(_('Files already there and different'),
                             _('These files already exist in the destination with different content:\n%s\n\n'
                               'Nothing was moved.') % nomi)
                return
            if err:
                self._imposta_occupato(False)
                self.avviso(_('Approval failed: %s') % err)
                return
            self._dopo_azione(az, s)
        self._in_thread(lambda: op.approva(s, dest), fine)

    def _dopo_azione(self, az, s):
        i = self._togli_dall_elenco(s)
        self.pila.append((az, i if i is not None else 0))
        self._imposta_occupato(False)
        self.avviso(az.descrizione, azione=True)

    def annulla_ultima(self):
        if self.occupato or not self.pila or self.pannello is not None:
            return
        az, i = self.pila.pop()
        self._imposta_occupato(True)

        def fine(sc, err):
            self._imposta_occupato(False)
            if err:
                self.pila.append((az, i))
                self.avviso(_('Undo failed: %s') % err)
                return
            if az.tipo == 'sostituisci':             # lo scatto c'è ancora nell'elenco: si rimette com'era
                k = self._indice_scatto(sc)
                if k is None:
                    k = min(i, len(self.scatti))
                    self.scatti.insert(k, sc)
                else:
                    self.scatti[k] = sc
                self._dimentica(sc, az.nuovo)
                self.vai_a(k)
                self.avviso(_('%s: original restored') % sc.nome)
                return
            self.scatti.insert(min(i, len(self.scatti)), sc)
            self.vai_a(min(i, len(self.scatti) - 1))
            self.avviso(_('%s restored') % sc.nome)
        self._in_thread(lambda: op.annulla(az), fine)

    # ---------------------------------------------------------- mosso
    def _crea_stimatore(self):
        if self.stim is not None:
            try:
                self.stim.chiudi()
            except Exception:  # noqa: BLE001
                pass
        self.stim = None
        self.esiti.clear()
        if mosso is None:
            return
        try:
            kw = {'cache': self._cache_mosso} if self._cache_mosso else {}
            st = mosso.Stimatore(**kw)
            if st.disponibile:
                self.stim = st
        except Exception as e:  # noqa: BLE001
            print(f'Cornice: blur indicator not available ({e})', file=sys.stderr)

    def _mosso_attivo(self):
        return self.stim is not None and self.cfg['mosso_attivo']

    def _chiedi_mosso(self):
        if not self._mosso_attivo() or not self.scatti:
            return
        try:
            vicini = [s.da_mostrare for s in self.scatti[self.indice:self.indice + 3]]
            self.stim.solo_questi(vicini)
            for p in vicini:
                if p in self.esiti:
                    continue
                e = self.stim.in_cache(p)
                if e is not None:
                    self.esiti[p] = e
                else:
                    self.stim.richiedi(p, self._mosso_fatto)
        except Exception as e:  # noqa: BLE001
            print(f'Cornice: blur estimation failed ({e})', file=sys.stderr)

    def _mosso_fatto(self, percorso, esito):
        # chiamata da un thread qualsiasi
        GLib.idle_add(self._mosso_pronto, percorso, esito)

    def _mosso_pronto(self, percorso, esito):
        self.esiti[percorso] = esito
        self._aggiorna_chip()
        return GLib.SOURCE_REMOVE

    def testo_mosso(self, p):
        """(testo, con_tasto) per il file p; testo vuoto = nessun indicatore."""
        e = self.esiti.get(p)
        if e is None:
            return '…', False
        st = e.stato
        if st == 'nitida':
            return _('Sharp'), False
        if st == 'recuperabile':
            return _('Recoverable blur · %.0f px at %.0f°') % (e.lunghezza, e.angolo), True
        if st == 'incerto':
            return _('Uncertain blur · %.0f px at %.0f°') % (e.lunghezza, e.angolo), True
        return '', False

    def _aggiorna_chip(self):
        s = self._scatto()
        if not (s and self._mosso_attivo()) or self.pila_pagine.get_visible_child_name() != 'foto':
            self.chip.set_visible(False)
            return
        testo, tasto = self.testo_mosso(s.da_mostrare)
        self.chip.set_visible(bool(testo))
        self.lbl_mosso.set_label(testo)
        self.b_togli.set_visible(tasto)
        self.chip.set_margin_end(6 if tasto else 12)

    # ---------------------------------------------------------- Togli mosso (modalità)
    def entra_togli(self):
        if self.pannello is not None or not self._puo_agire():
            return
        if togli is None:
            self.avviso(_('Deblur is not available: numpy or Pillow is missing'))
            return
        s = self._scatto()
        esito = self.esiti.get(s.da_mostrare)
        if esito is None and self.stim is not None:
            try:
                esito = self.stim.in_cache(s.da_mostrare)
            except Exception:  # noqa: BLE001
                esito = None
        self.pannello = PannelloTogli(self, s, esito)
        self.pila_pagine.add_named(self.pannello, 'togli')
        self.pila_pagine.set_visible_child_name('togli')
        self.b_apri.set_visible(False)
        self.t_cernita.set_visible(False)
        self.gruppo_cornice.set_visible(False)
        self.b_fine.set_visible(True)
        self.b_fine.set_sensitive(True)
        self.barra_info.set_visible(False)
        self.barra_cernita.set_visible(False)
        self.titolo.set_title('Cornice')
        self.titolo.set_subtitle(_('Deblur · %s') % s.nome)
        self.pannello.avvia()

    def esci_togli(self):
        p = self.pannello
        if p is None or p.salvando:
            return
        p.chiudi()
        self.pannello = None
        self.pila_pagine.set_visible_child_name('foto')
        self.pila_pagine.remove(p)
        self.b_fine.set_visible(False)
        self.b_apri.set_visible(True)
        self.t_cernita.set_visible(True)
        self.gruppo_cornice.set_visible(True)
        self._aggiorna_tutto()
        self._chiedi_mosso()

    def _indice_scatto(self, s):
        return next((k for k, x in enumerate(self.scatti) if x.cartella == s.cartella and x.nome == s.nome), None)

    def _dimentica(self, *scatti):
        """Toglie dalla cache delle immagini e dalle stime i file di questi scatti (sono cambiati)."""
        for s in scatti:
            for f in (s.file if s else []):
                self.cache.pop(f, None)
                self.esiti.pop(f, None)

    def togli_copiato(self, percorsi):
        """Dopo "Salva copia": si esce dalla modalità e si mostra lo scatto nuovo, subito dopo l'originale."""
        self.esci_togli()

        def fine(nuovo, err):
            if err:
                self.avviso(_('Saved, but I cannot show it: %s') % err)
                return
            k = self._indice_scatto(nuovo)
            if k is None:
                k = min(self.indice + 1, len(self.scatti))
                self.scatti.insert(k, nuovo)
            else:
                self.scatti[k] = nuovo
            self._dimentica(nuovo)
            self.vai_a(k)
            self.avviso(_('Saved: %s') % ', '.join(os.path.basename(p) for p in percorsi))
        self._in_thread(lambda: op.scatto_di(percorsi[0]), fine)

    def togli_sostituito(self, az):
        """Dopo "Sostituisci l'originale": l'azione va nella pila di Ctrl+Z e si mostra il file corretto."""
        self.esci_togli()
        nuovo = az.nuovo or op.scatto_di(az.corretti[0][0])
        k = self._indice_scatto(az.scatto)
        if k is None:
            k = min(self.indice, len(self.scatti))
            self.scatti.insert(k, nuovo)
        else:
            self.scatti[k] = nuovo
        self._dimentica(az.scatto, nuovo)
        self.pila.append((az, k))
        self.vai_a(k)
        self.avviso(_('Saved: %s') % ', '.join(os.path.basename(f) for f, firma in az.corretti), azione=True)

    # ---------------------------------------------------------- finestre di dialogo
    def preferenze(self):
        dlg = Adw.PreferencesDialog(title=_('Preferences'))
        pag = Adw.PreferencesPage()

        g1 = Adw.PreferencesGroup(title=_('Cull'))
        riga = Adw.ActionRow(title=_('Destination folder'), subtitle=self.cfg['destinazione'])
        riga.set_subtitle_selectable(True)
        scegli = Gtk.Button(label=_('Choose…'), valign=Gtk.Align.CENTER)

        def scelta(b):
            fd = Gtk.FileDialog(title=_('Destination folder'))
            if os.path.isdir(self.cfg['destinazione']):
                fd.set_initial_folder(Gio.File.new_for_path(self.cfg['destinazione']))

            def fine(d, res):
                try:
                    f = d.select_folder_finish(res)
                except GLib.Error:
                    return
                if f and f.get_path():
                    self.cfg['destinazione'] = f.get_path()
                    self.cfg['destinazione_scelta'] = True
                    riga.set_subtitle(f.get_path())
                    self.salva()
                    self._aggiorna_cernita()
            fd.select_folder(self, None, fine)
        scegli.connect('clicked', scelta)
        riga.add_suffix(scegli)
        g1.add(riga)
        pag.add(g1)

        g2 = Adw.PreferencesGroup(title=_('White frame'))
        spin = Adw.SpinRow.new_with_range(1, 25, 1)
        spin.set_title(_('Thickness'))
        spin.set_subtitle(_("Percentage of the photo's short side"))
        spin.set_value(self.cfg['spessore'])
        spin.connect('notify::value', lambda s, p: self.imposta_spessore(int(round(s.get_value()))))
        g2.add(spin)
        pag.add(g2)

        g3 = Adw.PreferencesGroup(title=_('Blur indicator'))
        sw = Adw.SwitchRow(title=_('Show the blur indicator'),
                           subtitle=_('Estimated in a separate process, with a cache') if self.stim or mosso
                           else _('Not available: mosso.py module missing'))
        sw.set_active(self.cfg['mosso_attivo'])
        sw.connect('notify::active', lambda s, p: self._cambia_mosso(s.get_active()))
        g3.add(sw)
        pag.add(g3)
        dlg.add(pag)
        dlg.present(self)

    def _cambia_mosso(self, on):
        self.cfg['mosso_attivo'] = on
        self.salva()
        self._chiedi_mosso()
        self._aggiorna_chip()

    def scorciatoie(self):
        elenco = [
            (_('Navigation'), [(_('Previous photo'), 'Left'), (_('Next photo'), 'Right'),
                               (_('First photo'), 'Home'), (_('Last photo'), 'End'),
                               (_('Open folder'), '<Control>o')]),
            (_('View'), [(_('Zoom in'), 'plus'), (_('Zoom out'), 'minus'), (_('Fit to window'), '0'),
                         (_('Actual size (100%)'), '1'), (_('White frame'), 'c'),
                         (_('Full screen'), 'f'), (_('Leave full screen'), 'Escape')]),
            (_('Deblur'), [(_('Enter Deblur mode (any photo)'), 'm'), (_('Leave Deblur mode'), 'Escape')]),
            (_('Cull'), [(_('Move to the trash'), 'x'), (_('Approve (move to the destination)'), 'a'),
                         (_('Undo the last action'), '<Control>z')]),
            (_('General'), [(_('Preferences'), '<Control>comma')]),
        ]
        if not hasattr(Adw, 'ShortcutsDialog'):       # libadwaita anteriore alla 1.8: un semplice elenco
            righe = []
            for titolo, voci in elenco:
                righe.append(titolo)
                for t, acc in voci:
                    ok, k, m = Gtk.accelerator_parse(acc)
                    righe.append('    %s: %s' % (Gtk.accelerator_get_label(k, m), t))
                righe.append('')
            d = Adw.AlertDialog(heading=_('Keyboard shortcuts'), body='\n'.join(righe).strip())
            d.add_response('ok', _('OK'))
            d.present(self)
            return
        d = Adw.ShortcutsDialog()
        for titolo, voci in elenco:
            sez = Adw.ShortcutsSection(title=titolo)
            for t, acc in voci:
                sez.add(Adw.ShortcutsItem.new(t, acc))
            d.add(sez)
        d.present(self)

    def informazioni(self):
        d = Adw.AboutDialog(application_name='Cornice', application_icon=APP_ID,
                            version=VERSIONE, developer_name='Giovanni Vatti',
                            website=SITO, issue_url=SITO + '/issues',
                            comments=_('Photo viewer with a white frame, culling, a blur indicator and a built-in deblur.'),
                            license_type=Gtk.License.GPL_3_0)
        d.present(self)

    # ---------------------------------------------------------- configurazione e chiusura
    def salva(self):
        if self._salva_ok:
            salva_config(self.cfg, self._config_path)

    def primo_avvio(self):
        """Benvenuto al primo avvio: spiega cos'è Cornice e chiede dove mettere le foto approvate.
        Rende (finestra di dialogo, [cartella scelta]); alla chiusura la scelta si salva."""
        scelta = [self.cfg['destinazione']]
        d = Adw.AlertDialog(heading=_('Welcome to Cornice'),
                            body=_('Cornice shows your photos with an optional white frame, helps you go through '
                                   'them and keep the good ones (the others go to the trash, and you can undo), '
                                   'tells you about camera shake and can remove it.\n\n'
                                   'Where should approved photos go?'))
        riga = Adw.ActionRow(title=_('Destination folder'), subtitle=scelta[0])
        riga.set_subtitle_selectable(True)
        sfoglia = Gtk.Button(label=_('Choose…'), valign=Gtk.Align.CENTER)

        def scegli(b):
            fd = Gtk.FileDialog(title=_('Destination folder'))
            iniziale = scelta[0] if os.path.isdir(scelta[0]) else cartella_immagini()
            if os.path.isdir(iniziale):
                fd.set_initial_folder(Gio.File.new_for_path(iniziale))

            def fine(dlg, res):
                try:
                    f = dlg.select_folder_finish(res)
                except GLib.Error:
                    return
                if f and f.get_path():
                    scelta[0] = f.get_path()
                    riga.set_subtitle(scelta[0])
            fd.select_folder(self, None, fine)
        sfoglia.connect('clicked', scegli)
        riga.add_suffix(sfoglia)
        lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lista.add_css_class('boxed-list')
        lista.append(riga)
        d.set_extra_child(lista)
        d.add_response('continua', _('Continue'))
        d.set_default_response('continua')
        d.set_close_response('continua')
        d.set_response_appearance('continua', Adw.ResponseAppearance.SUGGESTED)

        def fine(dlg, res):
            dlg.choose_finish(res)
            self.cfg['destinazione'] = scelta[0]       # la cartella si crea solo alla prima approvazione
            self.cfg['destinazione_scelta'] = True
            self.salva()
            self._aggiorna_cernita()
        d.choose(self, None, fine)
        return d, scelta

    def _chiudendo(self, *a):
        if self.pannello is not None:
            if self.pannello.salvando:                 # a metà salvataggio non si chiude: niente stati a metà
                self.avviso(_('Saving: please wait until it finishes'))
                return True
            self.pannello.chiudi()
        if not self.is_fullscreen() and not self.is_maximized():
            w, h = self.get_width(), self.get_height()
            if w > 200 and h > 200:
                self.cfg['finestra'] = [w, h]
        self.salva()
        self.rilascia()
        return False

    def rilascia(self):
        """Ferma stimatore e thread di caricamento (anche all'uscita senza chiusura della finestra)."""
        if self.pannello is not None:
            self.pannello.chiudi()
        try:
            if self.stim:
                self.stim.chiudi()
                self.stim = None
        except Exception:  # noqa: BLE001
            pass
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------- autoprova
    _autoprova_attiva = False
    errori_autoprova = []

    def v_dopo_diverso(self):
        """Il ritaglio "Dopo" non è lo stesso del "Prima" (la correzione ha fatto qualcosa)."""
        p = self.pannello
        return bool(p and p.v_dopo.texture is not None and p.v_prima.texture is not None
                    and p.v_dopo.texture is not p.v_prima.texture)

    def avvia_autoprova(self, cartella, esci):
        """Percorso automatico, solo su copie: scorre, cornice, cernita (scarta/approva e annulla), Togli mosso
        (anteprima, cursori, salva copia, sostituisci e annulla) e primo avvio con una configurazione temporanea."""
        self._autoprova_attiva = True
        self.errori_autoprova = errori = []
        dest = os.path.join(cartella, 'Approved')
        self._dest_forzata = dest
        t0 = time.monotonic()
        print(f'  language: {lingua.lingua_attiva()}, theme: {os.environ.get("CORNICE_TEMA") or "dark"}')

        def controlla(cond, msg):
            if not cond:
                errori.append(msg)
                print(f'  FAILED: {msg}')
            else:
                print(f'  ok: {msg}')

        def attesa(cond, limite=10.0):
            fine = time.monotonic() + limite
            while time.monotonic() < fine:
                if cond():
                    return True
                yield
            return False

        def istantanea(nome):
            # salva la finestra come PNG se CORNICE_ISTANTANEE indica una cartella (controllo dell'aspetto)
            d = os.environ.get('CORNICE_ISTANTANEE')
            if not d:
                return
            try:
                sn = Gtk.Snapshot()
                Adw.ApplicationWindow.do_snapshot(self, sn)
                nodo = sn.to_node()
                if nodo is None:
                    print(f'  snapshot {nome}: nothing to draw')
                else:
                    self.get_renderer().render_texture(nodo, None).save_to_png(os.path.join(d, nome + '.png'))
            except Exception as e:  # noqa: BLE001
                print(f'  snapshot {nome} failed: {e}')

        def pronta():
            return (self._corrente is not None and self._mostrata == self._corrente
                    and self.vista.texture is not None)

        def sha(f):
            import hashlib
            with open(f, 'rb') as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        def passi():
            self.apri_percorsi([cartella])
            ok = yield from attesa(pronta)
            controlla(ok and len(self.scatti) >= 2, f'folder opened ({len(self.scatti)} shots, first photo shown)')
            if not ok:
                return
            for _i in range(3):
                self.vai(1)
                ok = yield from attesa(pronta)
                controlla(ok, f'photo {self.indice + 1} shown')
            self.vai_a(0)
            yield from attesa(pronta)
            yield from attesa(lambda: self.vista.get_width() > 100)
            self.t_cornice.set_active(True)
            yield from attesa(lambda: False, 0.3)
            controlla(self.vista.cornice and self.vista._bordo() > 0, 'white frame on')
            istantanea('1_cornice')
            self.vista.zoom_fattore(2.0)
            self.vista.cento()
            yield from attesa(lambda: False, 0.3)
            istantanea('2_cento')
            controlla(self.vista.zoom is not None, f'zoom {self.vista.percentuale():.0f}%')
            self.vista.adatta()
            controlla(self.vista.zoom is None, 'fit to window')
            self.t_cernita.set_active(True)
            self._aggiorna_cernita()
            self.indice = 0
            self.vai_a(1)
            yield from attesa(pronta)
            if self.stim:
                ok = yield from attesa(lambda: self.testo_mosso(self._scatto().da_mostrare)[0] != '…', 12)
                yield from attesa(lambda: False, 0.3)
                istantanea('3_cernita')
                print(f'  blur: "{self.testo_mosso(self._scatto().da_mostrare)[0]}" (answer {"received" if ok else "NOT yet"})')
            else:
                print('  blur: indicator off (numpy/Pillow or mosso.py not available)')

            # scarta + annulla
            n = len(self.scatti)
            s = self._scatto()
            self.scarta()
            ok = yield from attesa(lambda: not self.occupato and len(self.scatti) == n - 1)
            controlla(ok and not any(os.path.exists(f) for f in s.file), 'discard: files in the trash, shot out of the list')
            self.annulla_ultima()
            ok = yield from attesa(lambda: not self.occupato and len(self.scatti) == n)
            controlla(ok and all(os.path.exists(f) for f in s.file), 'undo discard: files back in place')
            yield from attesa(pronta)

            # approva + annulla
            n = len(self.scatti)
            s = self._scatto()
            files = [os.path.basename(f) for f in s.file]
            self.approva()
            ok = yield from attesa(lambda: not self.occupato and len(self.scatti) == n - 1)
            controlla(ok and all(os.path.exists(os.path.join(dest, f)) for f in files)
                      and not any(os.path.exists(f) for f in s.file), 'approve: files in the test destination')
            self.annulla_ultima()
            ok = yield from attesa(lambda: not self.occupato and len(self.scatti) == n)
            controlla(ok and all(os.path.exists(f) for f in s.file)
                      and not any(os.path.exists(os.path.join(dest, f)) for f in files),
                      'undo approve: files back in the source')
            controlla(self.cfg['destinazione'] != dest, 'the user\'s configuration was not touched')

            # RAW che rawpy non apre (o senza rawpy): si mostra l'anteprima JPEG incorporata
            solo_raw = [x for x in self.scatti if x.raw and not x.immagine]
            for x in solo_raw:
                self.vai_a(self.scatti.index(x))
                ok = yield from attesa(pronta, 15)
                ris = self.cache.get(x.raw)
                controlla(ok and ris is not None, f'RAW-only shot {x.nome} shown ({"embedded preview" if ris and ris.incorporata else "decoded by rawpy"})')
                if x.nome == 'IMG_0005' and ris is not None:
                    controlla(ris.incorporata, 'fake RAW (rawpy cannot open it): embedded preview shown')
                    yield from attesa(lambda: False, 0.3)
                    istantanea('12_anteprima_incorporata')

            # Togli mosso: modalità, anteprima, cursori, "Salva copia"
            self.t_cernita.set_active(False)
            if togli is None or self.stim is None:
                print('  deblur: numpy/Pillow not available, skipped')
                return
            for x in self.scatti:                       # stima di tutti gli scatti, per scegliere quelli mossi
                if x.da_mostrare not in self.esiti:
                    e = self.stim.in_cache(x.da_mostrare)
                    if e is not None:
                        self.esiti[x.da_mostrare] = e
                    else:
                        self.stim.richiedi(x.da_mostrare, self._mosso_fatto)
            ok = yield from attesa(lambda: all(x.da_mostrare in self.esiti for x in self.scatti), 60)
            rec = [x for x in self.scatti
                   if self.esiti.get(x.da_mostrare) and self.esiti[x.da_mostrare].stato == 'recuperabile']
            controlla(ok and rec, f'blurred shots found: {", ".join(x.nome for x in rec) or "none"}')
            if not rec:
                return
            k = self.scatti.index(rec[0])
            self.vai_a(k)
            yield from attesa(pronta)
            s = self._scatto()
            tutti = [f for f in s.file if not f.lower().endswith('.xmp')]
            prima = {f: os.path.getsize(f) for f in tutti}
            self.entra_togli()
            yield from attesa(lambda: False, 0.2)
            controlla(self.pannello is not None and self.b_fine.get_visible(), 'Deblur: mode open')
            controlla(self.titolo.get_subtitle() == _('Deblur · %s') % s.nome, f'title: "{self.titolo.get_subtitle()}"')
            controlla(not self.barra_info.get_visible() and not self.barra_cernita.get_visible()
                      and not self.chip.is_ancestor(self.pannello), 'bars hidden in Deblur')
            istantanea('4_togli_caricamento')
            ok = yield from attesa(lambda: self.pannello.pronto, 60)
            controlla(ok, 'Deblur: photo loaded and preview ready')
            if not ok:
                self.esci_togli()
                return
            pn = self.pannello
            controlla(pn.lung() > 3, f'initial estimate: {pn.lung():.1f} px at {pn.ang():.0f}° '
                                     f'({", ".join(f.nome for f in pn.fotos)}; crop {pn.rit} physical px)')
            controlla(self.v_dopo_diverso(), '"After" crop differs from "Before"')
            yield from attesa(lambda: False, 0.3)
            istantanea('5_togli_mosso')
            # un ritocco dei cursori e uno spostamento della zona: l'anteprima si rifà da sola
            g = pn.giro
            pn.s_forza.set_value(12)
            ok = yield from attesa(lambda: pn.giro > g and pn.pronto, 30)
            controlla(ok, 'slider change: preview redone')
            g = pn.giro
            h, w = pn.base.lin.shape[:2]
            pn._zona_scelta(w // 3, h // 3)
            ok = yield from attesa(lambda: pn.giro > g and pn.pronto, 30)
            controlla(ok and pn.centro == (w // 3, h // 3), 'area picked on the overview: preview redone')
            pn.s_forza.set_value(20)
            ok = yield from attesa(lambda: pn.pronto, 30)
            yield from attesa(lambda: False, 0.3)
            istantanea('6_togli_mosso_zona')
            # salva copia
            n = len(self.scatti)
            attesi = [os.path.join(os.path.dirname(f.path), f.nome_uscita()) for f in pn.fotos]   # solo i file correggibili
            non_corr = [f for f in tutti if os.path.abspath(f) not in [os.path.abspath(x.path) for x in pn.fotos]]
            controlla(not non_corr or bool(pn.nota),
                      'files that cannot be corrected (%s) are reported in the sidebar' % (', '.join(os.path.basename(f) for f in non_corr) or 'none'))
            pn.salva_copia()
            yield from attesa(lambda: False, 1.0)
            istantanea('7_togli_salvataggio')
            ok = yield from attesa(lambda: self.pannello is None and len(self.scatti) == n + 1, 120)
            controlla(ok, 'save copy: mode closed and new shot in the list')
            controlla(all(os.path.isfile(f) for f in attesi), 'save copy: ' + ', '.join(os.path.basename(f) for f in attesi))
            controlla(all(os.path.getsize(f) == prima[f] for f in tutti), 'originals intact')
            yield from attesa(lambda: self._scatto() and self._scatto().da_mostrare in self.cache, 15)
            controlla(self._scatto().nome == s.nome + '_nitida' and self.indice == k + 1,
                      f'new shot shown ({self._scatto().nome}) right after the original')
            yield from attesa(lambda: False, 0.5)
            istantanea('8_dopo_copia')

            # sostituisci l'originale + annulla (su un altro scatto mosso, se c'è)
            altri = [x for x in rec if x.nome != s.nome]
            t = altri[0] if altri else rec[0]
            self.vai_a(self._indice_scatto(t))
            yield from attesa(pronta)
            files = [f for f in t.file if not f.lower().endswith('.xmp')]
            h0 = {f: sha(f) for f in files}
            self.entra_togli()
            ok = yield from attesa(lambda: self.pannello is not None and self.pannello.pronto, 60)
            controlla(ok, f'replace: Deblur open on {t.nome}')
            n = len(self.scatti)
            self.pannello.sostituisci(confermato=True)
            yield from attesa(lambda: False, 1.0)
            istantanea('9_togli_sostituzione')
            ok = yield from attesa(lambda: self.pannello is None and self.pila and self.pila[-1][0].tipo == 'sostituisci', 120)
            controlla(ok, 'replace: mode closed and action on the Ctrl+Z stack')
            if ok:
                az = self.pila[-1][0]
                yield from attesa(lambda: self._scatto().da_mostrare in self.cache, 15)
                controlla(len(self.scatti) == n and self._scatto().nome == t.nome, 'replace: same shot shown')
                controlla(all(os.path.isfile(f) and sha(f) != h0[f] for f in [c[0] for c in az.corretti]),
                          'replace: corrected files in place of the originals')
                controlla(not [x for x in os.listdir(t.cartella) if x.startswith('.cornice-')], 'replace: no temporary file left')
                istantanea('10_dopo_sostituzione')
                self.annulla_ultima()
                ok = yield from attesa(lambda: not self.occupato and not self.pila, 30)
                controlla(ok and all(os.path.isfile(f) and sha(f) == h0[f] for f in files),
                          'undo (Ctrl+Z): originals back in place, byte for byte')
                controlla(not [x for x in os.listdir(t.cartella) if x.startswith('.cornice-')], 'undo: no temporary file left')
                yield from attesa(lambda: self._scatto().da_mostrare in self.cache, 15)

            # primo avvio, con una configurazione temporanea (mai quella dell'utente)
            cfg_prova = os.path.join(cartella, '.config_prova.json')
            if os.path.exists(cfg_prova):
                os.remove(cfg_prova)
            c0 = carica_config(cfg_prova)
            controlla(not c0['destinazione_scelta'] and c0['destinazione'] == dest_predefinita(),
                      f'no config file: destination not chosen, proposal {c0["destinazione"]}')
            with open(cfg_prova + '.vecchia', 'w', encoding='utf-8') as f:
                json.dump({'destinazione': '/da/una/versione/precedente', 'spessore': 9}, f)
            c1 = carica_config(cfg_prova + '.vecchia')
            controlla(c1['destinazione_scelta'] and c1['destinazione'] == '/da/una/versione/precedente'
                      and c1['spessore'] == 9, 'config of a previous version keeps its destination as chosen')
            os.remove(cfg_prova + '.vecchia')
            self._config_path, self._salva_ok = cfg_prova, True
            dlg, scelta = self.primo_avvio()
            yield from attesa(lambda: False, 0.8)
            istantanea('11_primo_avvio')
            nuova = os.path.join(cartella, 'scelta_prova')
            scelta[0] = nuova
            dlg.close()
            ok = yield from attesa(lambda: os.path.exists(cfg_prova), 5)
            c2 = carica_config(cfg_prova)
            controlla(ok and c2['destinazione_scelta'] and c2['destinazione'] == nuova and self.cfg['destinazione'] == nuova,
                      'first run: choice saved and marked as chosen')
            controlla(not os.path.exists(nuova), 'first run: the folder is not created before the first approval')
            self._config_path, self._salva_ok = CONFIG_PATH, False
            self.cfg['destinazione'] = dest_predefinita()

        gen = passi()

        def tick():
            try:
                if time.monotonic() - t0 > 300:
                    errori.append('timeout (300 s)')
                    raise StopIteration
                next(gen)
                return GLib.SOURCE_CONTINUE
            except StopIteration:
                print('OK' if not errori else 'ERRORS: ' + '; '.join(errori))
                esci(0 if not errori else 1)
                return GLib.SOURCE_REMOVE
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                esci(1)
                print(f'ERRORS: exception {e}')
                return GLib.SOURCE_REMOVE
        GLib.timeout_add(40, tick)


# ---------------------------------------------------------------- applicazione

class Applicazione(Adw.Application):
    def __init__(self, autoprova=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.autoprova = autoprova
        self.esito = 0
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Cornice è scura per scelta; CORNICE_TEMA=light|dark serve solo alle prove (aspetto con il tema di serie)
        tema = {'light': Adw.ColorScheme.FORCE_LIGHT}.get(os.environ.get('CORNICE_TEMA'), Adw.ColorScheme.FORCE_DARK)
        Adw.StyleManager.get_default().set_color_scheme(tema)
        Gtk.Window.set_default_icon_name(APP_ID)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _finestra(self):
        if self.win is None:
            if self.autoprova:
                cfg = dict(CONFIG_PREDEFINITA)
                cfg['destinazione'] = dest_predefinita()
                cfg['destinazione_scelta'] = True         # niente benvenuto: lo prova l'autoprova a parte
                cache = os.path.join(self.autoprova, '.mosso_prova.json')
                self.win = Finestra(self, cfg, salva=False, cache_mosso=cache)
            else:
                self.win = Finestra(self, carica_config())
        return self.win

    def _benvenuto(self, w):
        if not self.autoprova and not w.cfg['destinazione_scelta'] and not getattr(w, '_benvenuto_fatto', False):
            w._benvenuto_fatto = True
            GLib.idle_add(lambda: (w.primo_avvio(), GLib.SOURCE_REMOVE)[1])

    def do_activate(self):
        w = self._finestra()
        w.present()
        if self.autoprova:
            w.avvia_autoprova(self.autoprova, self._esci)
        else:
            self._benvenuto(w)

    def do_open(self, files, n_files, hint):
        w = self._finestra()
        w.present()
        percorsi = [f.get_path() for f in files if f.get_path()]
        if percorsi:
            w.apri_percorsi(percorsi)
        self._benvenuto(w)

    def do_shutdown(self):
        if self.win is not None:
            self.win.rilascia()
        Adw.Application.do_shutdown(self)

    def _esci(self, codice):
        self.esito = codice
        GLib.idle_add(self.quit)


def aiuto():
    return _('Usage: cornice.py [OPTIONS] [PHOTO or FOLDER ...]\n\n'
             'Options:\n'
             '  -h, --help          show this help and exit\n'
             '  --version           show the version and exit\n'
             '  --autoprova FOLDER  run the automatic window test on a folder of COPIES (developers)\n')


def main():
    argv = sys.argv[:]
    if '--version' in argv:
        print(f'Cornice {VERSIONE}')
        return 0
    if '--help' in argv or '-h' in argv:
        print(aiuto())
        return 0
    autoprova = None
    if '--autoprova' in argv:
        i = argv.index('--autoprova')
        if i + 1 >= len(argv):
            print('Usage: --autoprova FOLDER', file=sys.stderr)
            return 2
        autoprova = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    app = Applicazione(autoprova)
    app.run(argv)
    return app.esito


if __name__ == '__main__':
    sys.exit(main())
