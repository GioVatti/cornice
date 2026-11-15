# SPDX-License-Identifier: GPL-3.0-or-later
"""Operazioni sui file di Cornice: scatti (coppie JPG+RAW), scarta, approva, sostituisci, annulla.

Niente GTK: solo `Gio` per il cestino. Regola d'oro: mai perdere un file. Le copie si fanno su
nome temporaneo, si verificano byte per byte e solo dopo si cancellano gli originali.
"""
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

from lingua import _

IMG_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp'}
# unica fonte: `togli.py` importa questi due insiemi da qui
RAW_EXT = {'.dng', '.cr2', '.cr3', '.crw', '.nef', '.nrw', '.arw', '.srf', '.sr2', '.raf', '.orf', '.rw2',
           '.pef', '.srw', '.x3f', '.erf', '.mrw', '.3fr', '.iiq', '.rwl', '.kdc', '.dcr', '.mef', '.mos'}
_PREFERENZA_IMG = ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp']
try:                    # HEIC/HEIF solo se c'è pillow_heif (nessuna dipendenza obbligatoria)
    import pillow_heif
    pillow_heif.register_heif_opener()
    IMG_EXT |= {'.heic', '.heif'}
    _PREFERENZA_IMG += ['.heic', '.heif']
except Exception:
    pass
_BLOCCO = 1024 * 1024


# ----------------------------------------------------------------------------- errori

class Conflitto(Exception):
    """Nella destinazione esistono già file con lo stesso nome ma contenuto diverso."""

    def __init__(self, file):
        self.file = list(file)
        super().__init__(_('Different files with the same names already exist:') + '\n' + '\n'.join(self.file))


class CestinoNonDisponibile(Exception):
    """Il cestino non è utilizzabile per questo file (es. scheda SD senza cestino)."""


# ----------------------------------------------------------------------------- scatti

@dataclass
class Scatto:
    cartella: str
    nome: str                       # radice comune, es. 'IMG_3612'
    file: list = field(default_factory=list)   # percorsi completi di tutti i file, sidecar compresi
    immagine: str = None            # il file non RAW da mostrare (preferenza jpg/jpeg)
    raw: str = None                 # il RAW

    @property
    def da_mostrare(self):
        return self.immagine or self.raw


def _ext(nome):
    return os.path.splitext(nome)[1].lower()


def _radice(nome):
    """Radice di un nome di file: IMG_1.DNG.xmp e IMG_1.xmp -> IMG_1."""
    base, e = os.path.splitext(nome)
    if e.lower() == '.xmp':
        b2, e2 = os.path.splitext(base)
        # si toglie la seconda estensione solo se è davvero quella di una foto (IMG_1.v2.xmp -> IMG_1.v2)
        if e2.lower() in IMG_EXT or e2.lower() in RAW_EXT:
            return b2
        return base
    return base


def _chiave_naturale(nome):
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', nome.casefold())] + [nome]


def _costruisci(cartella, nomi):
    """Scatti a partire dai nomi di file di una cartella."""
    gruppi = {}
    for n in nomi:
        e = _ext(n)
        if e == '.xmp' or e in IMG_EXT or e in RAW_EXT:
            gruppi.setdefault(_radice(n), []).append(n)
    out = []
    for radice, elenco in gruppi.items():
        foto = [n for n in elenco if _ext(n) in IMG_EXT or _ext(n) in RAW_EXT]
        if not foto:
            continue                                   # un .xmp senza foto non è uno scatto
        elenco.sort(key=_chiave_naturale)
        imgs = sorted((n for n in foto if _ext(n) in IMG_EXT),
                      key=lambda n: (_PREFERENZA_IMG.index(_ext(n)), _chiave_naturale(n)))
        raws = sorted((n for n in foto if _ext(n) in RAW_EXT), key=_chiave_naturale)
        j = lambda n: os.path.join(cartella, n)
        out.append(Scatto(cartella, radice, [j(n) for n in elenco],
                          j(imgs[0]) if imgs else None, j(raws[0]) if raws else None))
    out.sort(key=lambda s: _chiave_naturale(s.nome))
    return out


def _file_normali(cartella):
    nomi = []
    for n in os.listdir(cartella):
        if n.startswith('.'):
            continue
        if os.path.isfile(os.path.join(cartella, n)):
            nomi.append(n)
    return nomi


def elenca(cartella):
    """Tutti gli scatti di una cartella, in ordine naturale (IMG_2 prima di IMG_10)."""
    cartella = os.path.abspath(cartella)
    return _costruisci(cartella, _file_normali(cartella))


def scatto_di(percorso):
    """Lo scatto a cui appartiene un file."""
    percorso = os.path.abspath(percorso)
    cartella, nome = os.path.split(percorso)
    if nome.startswith('.') or not os.path.isfile(percorso):
        raise FileNotFoundError(_('File not found: %s') % percorso)
    radice = _radice(nome)
    for s in _costruisci(cartella, _file_normali(cartella)):
        if s.nome == radice and percorso in s.file:
            return s
    raise ValueError(_('This file does not belong to any shot: %s') % percorso)


# ----------------------------------------------------------------------------- azioni

@dataclass
class Azione:
    tipo: str                       # 'scarta' | 'approva' | 'sostituisci'
    scatto: Scatto                  # com'era prima
    descrizione: str                # es. 'IMG_3612 scartata'
    destinazione: str = ''          # approva: cartella di destinazione
    nuovo: Scatto = None            # approva: lo scatto com'è ora nella destinazione
    copiati: list = field(default_factory=list)       # approva: file creati da questa azione
    gia_presenti: list = field(default_factory=list)  # approva: file già identici in destinazione
    voci_cestino: list = field(default_factory=list)  # scarta/sostituisci: (percorso originale, nome nel cestino o None)
    corretti: list = field(default_factory=list)      # sostituisci: (file corretto, (dimensione, mtime_ns))


# ----------------------------------------------------------------------------- confronto e copia verificata

def _uguali(a, b):
    """Stesso contenuto, byte per byte."""
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        while True:
            x, y = fa.read(_BLOCCO), fb.read(_BLOCCO)
            if x != y:
                return False
            if not x:
                return True


def _rimuovi_se_c_e(p):
    try:
        os.remove(p)
    except FileNotFoundError:
        pass


def _cancella(p):
    """Cancella un originale (punto unico, così i test possono simulare un errore)."""
    os.remove(p)


def _fsync_cartella(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _copia_verificata(src, dst):
    """Copia src in dst senza mai sovrascrivere: nome temporaneo, fsync, confronto, poi nome finale.
    Se dst esiste (anche per una corsa con un altro programma) -> Conflitto."""
    cartella, nome = os.path.split(dst)
    tmp = os.path.join(cartella, '.%s.parziale' % nome)
    try:
        _rimuovi_se_c_e(tmp)                       # avanzo di una copia interrotta
        shutil.copyfile(src, tmp)
        try:
            shutil.copystat(src, tmp)
        except OSError:                            # alcuni file system rifiutano i permessi
            try:
                st = os.stat(src)
                os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                pass
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
            try:                                   # il confronto deve leggere dal disco, non dalla cache
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except (AttributeError, OSError):
                pass
        finally:
            os.close(fd)
        if not _uguali(src, tmp):
            raise OSError(_('The copy of %s does not match the original.') % nome)
        try:
            os.link(tmp, dst)                      # fallisce se dst esiste: non sovrascrive mai
            os.remove(tmp)
        except FileExistsError:
            raise Conflitto([dst])
        except OSError:                            # file system senza collegamenti fisici (FAT)
            if os.path.lexists(dst):
                raise Conflitto([dst])
            os.rename(tmp, dst)
        _fsync_cartella(cartella)
    except BaseException:
        _rimuovi_se_c_e(tmp)
        raise


def _crea_cartelle(cartelle):
    """Crea le cartelle mancanti e rende quelle create (dalla più esterna)."""
    create = []
    for c in cartelle:
        mancanti = []
        p = c
        while p and not os.path.isdir(p):
            mancanti.append(p)
            nuovo = os.path.dirname(p)
            if nuovo == p:
                break
            p = nuovo
        for m in reversed(mancanti):
            if m not in create:
                os.mkdir(m)
                create.append(m)
    return create


def _togli_cartelle(create):
    for c in reversed(create):
        try:
            os.rmdir(c)
        except OSError:
            pass


def _sposta_verificato(coppie, cancella):
    """Sposta in modo sicuro: coppie = [(sorgente, destinazione)]; cancella(sorgente) dice quali
    sorgenti cancellare alla fine. Rende (file creati, file già identici in destinazione)."""
    for s, d in coppie:
        if not os.path.isfile(s):
            raise FileNotFoundError(_('File not found: %s') % s)
    for s, d in coppie:
        if cancella(s) and not os.access(os.path.dirname(s), os.W_OK | os.X_OK):
            raise PermissionError(_('Cannot delete from the folder: %s') % os.path.dirname(s))
    conflitti, identici = [], []
    for s, d in coppie:
        if os.path.lexists(d):
            if os.path.isfile(d) and _uguali(s, d):
                identici.append(d)
            else:
                conflitti.append(d)
    if conflitti:
        raise Conflitto(conflitti)

    cartelle = _crea_cartelle(sorted({os.path.dirname(d) for s, d in coppie}))
    creati = []
    try:
        for s, d in coppie:
            if d not in identici:
                _copia_verificata(s, d)
                creati.append(d)
    except BaseException:
        for d in creati:
            _rimuovi_se_c_e(d)
        _togli_cartelle(cartelle)
        raise

    cancellati = []
    try:
        for s, d in coppie:
            if cancella(s):
                _cancella(s)
                cancellati.append((s, d))
    except Exception as e:
        # un originale non si cancella: rimette a posto quelli già cancellati e toglie le copie
        guasti = []
        for s, d in cancellati:
            try:
                _copia_verificata(d, s)
            except Exception as e2:
                guasti.append('%s (%s)' % (s, e2))
        if guasti:
            raise OSError(_('Operation interrupted (%s) and I could not put everything back. '
                            'The copies remain in %s; the originals of these are missing: %s')
                          % (e, os.path.dirname(coppie[0][1]), '; '.join(guasti))) from e
        for d in creati:
            _rimuovi_se_c_e(d)
        _togli_cartelle(cartelle)
        raise OSError(_('Cannot delete the original: %s. Nothing has changed.') % e) from e
    return creati, identici


# ----------------------------------------------------------------------------- approva / annulla

def approva(scatto, destinazione):
    """Sposta tutti i file dello scatto in `destinazione` (copia verificata, poi cancella gli originali)."""
    dest = os.path.abspath(os.path.expanduser(destinazione))
    if os.path.exists(dest):
        if not os.path.isdir(dest):
            raise NotADirectoryError(_('The destination is not a folder: %s') % dest)
        if os.path.samefile(dest, scatto.cartella):
            raise ValueError(_('The destination is the folder the photo is already in.'))
    elif os.path.realpath(dest) == os.path.realpath(scatto.cartella):
        raise ValueError(_('The destination is the folder the photo is already in.'))
    coppie = [(f, os.path.join(dest, os.path.basename(f))) for f in scatto.file]
    creati, identici = _sposta_verificato(coppie, lambda s: True)
    mappa = dict(coppie)
    nuovo = Scatto(dest, scatto.nome, [d for s, d in coppie],
                   mappa.get(scatto.immagine), mappa.get(scatto.raw))
    return Azione('approva', scatto, _('%s approved') % scatto.nome, dest, nuovo, creati, identici)


def _annulla_approva(azione):
    s = azione.scatto
    nuovo = azione.nuovo
    coppie = list(zip(nuovo.file, s.file))             # (dalla destinazione, al posto originale)
    copiati = set(azione.copiati)
    _sposta_verificato(coppie, lambda sorgente: sorgente in copiati)
    return s


# ----------------------------------------------------------------------------- cestino

_ATTR_CESTINO = 'standard::name,trash::orig-path,trash::deletion-date'


def _gio():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    return Gio, GLib


def _voci_cestino():
    """Voci del cestino: [(nome nel cestino, percorso originale, data, Gio.File)]."""
    Gio, GLib = _gio()
    radice = Gio.File.new_for_uri('trash:///')
    try:
        en = radice.enumerate_children(_ATTR_CESTINO, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    except GLib.Error as e:
        raise OSError(_('Cannot read the trash: %s') % e.message) from e
    out = []
    try:
        while True:
            info = en.next_file(None)
            if info is None:
                break
            orig = info.get_attribute_byte_string('trash::orig-path') or info.get_attribute_string('trash::orig-path')
            data = info.get_attribute_string('trash::deletion-date') or ''
            out.append((info.get_name(), orig, data, radice.get_child(info.get_name())))
    finally:
        en.close(None)
    return out


def _nel_cestino(percorso):
    Gio, GLib = _gio()
    f = Gio.File.new_for_path(percorso)
    try:
        f.trash(None)
    except GLib.Error as e:
        if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
            raise CestinoNonDisponibile(_('The trash is not available for this file: %s') % percorso) from e
        raise OSError(_('Cannot move %s to the trash: %s') % (percorso, e.message)) from e


def _riconosci(percorsi, prima):
    """Dopo aver cestinato `percorsi`: [(percorso, nome nel cestino)], con None se non univoco.
    Il cestino di gvfs si aggiorna con un po' di ritardo: si riprova per qualche secondo."""
    import time
    fine = time.monotonic() + 5.0
    while True:
        ora = _voci_cestino()
        out = []
        for p in percorsi:
            nuove = [n for n, o, d, f in ora if o == p and n not in prima]
            out.append((p, nuove[0] if len(nuove) == 1 else None))
        if all(n is not None for p, n in out) or time.monotonic() > fine:
            return out
        time.sleep(0.05)


def _ripristina(percorso, nome_cestino):
    """Rimette `percorso` dal cestino. Se il nome nel cestino è noto si usa quello, altrimenti la voce
    più recente con quel percorso originale. Non sovrascrive mai."""
    Gio, GLib = _gio()
    voci = [v for v in _voci_cestino() if v[1] == percorso]
    if nome_cestino is not None:
        voci = [v for v in voci if v[0] == nome_cestino]
        if not voci:
            raise FileNotFoundError(_('No longer in the trash: %s') % percorso)
    elif not voci:
        raise FileNotFoundError(_('Not found in the trash: %s') % percorso)
    voce = max(voci, key=lambda v: (v[2], v[0]))
    if os.path.lexists(percorso):
        raise Conflitto([percorso])
    _crea_cartelle([os.path.dirname(percorso)])
    try:
        voce[3].move(Gio.File.new_for_path(percorso), Gio.FileCopyFlags.NONE, None, None, None)
    except GLib.Error as e:
        if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
            raise Conflitto([percorso]) from e
        raise OSError(_('Cannot restore %s from the trash: %s') % (percorso, e.message)) from e


def _cestina(percorsi):
    """Manda nel cestino i file indicati; rende [(percorso, nome nel cestino)]. Se il cestino non c'e' per
    il primo file -> CestinoNonDisponibile senza aver toccato nulla; se fallisce a meta' rimette a posto
    quelli già cestinati e rilancia."""
    for f in percorsi:
        if not os.path.isfile(f):
            raise FileNotFoundError(_('File not found: %s') % f)
    prima = {v[0] for v in _voci_cestino()}
    fatti = []
    try:
        for f in percorsi:
            _nel_cestino(f)                    # il primo può dare CestinoNonDisponibile senza aver toccato nulla
            fatti.append(f)
    except BaseException as e:
        guasti = []
        if fatti:
            try:
                voci = _riconosci(fatti, prima)
            except Exception as e2:
                voci = [(f, None) for f in fatti]
                guasti.append(str(e2))
            for f, n in voci:
                try:
                    _ripristina(f, n)
                except Exception as e2:
                    guasti.append('%s (%s)' % (f, e2))
        if guasti and isinstance(e, Exception):
            raise OSError(_('%s. I also could not put these back: %s. They are in the trash.')
                          % (e, '; '.join(guasti))) from e
        raise
    return _riconosci(fatti, prima)


def scarta(scatto):
    """Manda nel cestino tutti i file dello scatto."""
    voci = _cestina(scatto.file)
    return Azione('scarta', scatto, _('%s discarded') % scatto.nome, voci_cestino=voci)


def puo_cestinare(percorso):
    """False se il cestino non si può usare per questo file (es. scheda SD). Un controllo rapido e
    prudente: nel dubbio rende True e decide poi `scarta`/`sostituisci`."""
    try:
        Gio, GLib = _gio()
        info = Gio.File.new_for_path(percorso).query_info('access::can-trash', Gio.FileQueryInfoFlags.NONE, None)
        if info.has_attribute('access::can-trash'):
            return bool(info.get_attribute_boolean('access::can-trash'))
    except Exception:
        pass
    return True


def elimina_definitivo(scatto):
    """Cancella per sempre i file dello scatto (da usare solo dopo conferma, se manca il cestino)."""
    errori = []
    for f in scatto.file:
        try:
            _rimuovi_se_c_e(f)
        except OSError as e:
            errori.append('%s (%s)' % (f, e))
    if errori:
        raise OSError(_('Could not delete: ') + '; '.join(errori))


def _annulla_scarta(azione):
    # prima si controlla che si possa ripristinare tutto, poi si ripristina
    voci = _voci_cestino()
    for p, n in azione.voci_cestino:
        c = [v for v in voci if v[1] == p and (n is None or v[0] == n)]
        if not c:
            raise FileNotFoundError(_('No longer in the trash: %s') % p)
    conflitti = [p for p, n in azione.voci_cestino if os.path.lexists(p)]
    if conflitti:
        raise Conflitto(conflitti)
    fatti = []
    try:
        for p, n in azione.voci_cestino:
            _ripristina(p, n)
            fatti.append(p)
    except Exception as e:
        raise OSError(_('%s. Restored before the error: %s.') % (e, ', '.join(fatti) or _('none'))) from e
    return azione.scatto


# ----------------------------------------------------------------------------- sostituisci / annulla

def temporaneo(percorso):
    """Nome temporaneo nascosto, nella stessa cartella del file: .cornice-<radice>-XXXX.tmp"""
    cartella, nome = os.path.split(percorso)
    return os.path.join(cartella, '.cornice-%s-%s.tmp' % (os.path.splitext(nome)[0], uuid.uuid4().hex[:6]))


def _firma(p):
    st = os.stat(p)
    return (st.st_size, st.st_mtime_ns)


def _pubblica(tmp, dst):
    """Dà a `tmp` il nome finale `dst` senza mai sovrascrivere (se dst esiste -> Conflitto)."""
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, dst)                      # fallisce se dst esiste
        os.remove(tmp)
    except FileExistsError:
        raise Conflitto([dst])
    except OSError:                            # file system senza collegamenti fisici (FAT)
        if os.path.lexists(dst):
            raise Conflitto([dst])
        os.rename(tmp, dst)
    _fsync_cartella(os.path.dirname(dst))


def sostituisci(scatto, corretti):
    """Sostituisce originali con versioni corrette, senza mai perdere un file.

    corretti = [(originale, temporaneo, nome_finale)]: il temporaneo è un file già scritto nella stessa
    cartella dell'originale (vedi `temporaneo`), nome_finale il nome che prenderà. Controlla prima tutto
    (se un nome finale esiste e non è uno degli originali -> Conflitto, nulla toccato), poi manda gli
    originali nel cestino (se non c'è -> CestinoNonDisponibile, nulla toccato) e infine dà ai temporanei il
    nome finale. Qualunque errore rimette tutto com'era: temporanei tolti, originali di nuovo al loro
    posto. I file dello scatto non elencati (es. i sidecar .xmp) non si toccano."""
    corretti = [(os.path.abspath(o), os.path.abspath(t), n) for o, t, n in corretti]
    temp = [t for o, t, n in corretti]
    try:
        if not corretti:
            raise ValueError(_('Nothing to replace.'))
        originali = [o for o, t, n in corretti]
        finali = []
        for o, t, n in corretti:
            if os.path.basename(n) != n or n.startswith('.'):
                raise ValueError(_('Invalid final name: %s') % n)
            if o not in scatto.file:
                raise ValueError(_('The file is not part of the shot: %s') % o)
            if not os.path.isfile(o):
                raise FileNotFoundError(_('File not found: %s') % o)
            if not os.path.isfile(t) or os.path.dirname(t) != os.path.dirname(o):
                raise FileNotFoundError(_('Corrected file not found in the original\'s folder: %s') % t)
            if not os.access(os.path.dirname(o), os.W_OK | os.X_OK):
                raise PermissionError(_('Cannot write to the folder: %s') % os.path.dirname(o))
            finali.append(os.path.join(os.path.dirname(o), n))
        if len(set(finali)) != len(finali):
            raise Conflitto(finali)
        conflitti = [f for f in finali if os.path.lexists(f) and f not in originali]
        if conflitti:
            raise Conflitto(conflitti)
        if not puo_cestinare(originali[0]):
            raise CestinoNonDisponibile(_('The trash is not available for this file: %s') % originali[0])

        voci = _cestina(originali)             # da qui gli originali sono nel cestino
        pubblicati = []
        try:
            for (o, t, n), f in zip(corretti, finali):
                _pubblica(t, f)
                pubblicati.append(f)
        except BaseException as e:
            for f in pubblicati:
                _rimuovi_se_c_e(f)
            guasti = []
            for p, nome in voci:
                try:
                    _ripristina(p, nome)
                except Exception as e2:
                    guasti.append('%s (%s)' % (p, e2))
            if guasti and isinstance(e, Exception):
                raise OSError(_('%s. I also could not put these back: %s. They are in the trash.')
                              % (e, '; '.join(guasti))) from e
            raise
    except BaseException:
        for t in temp:
            _rimuovi_se_c_e(t)
        raise
    try:
        nuovo = scatto_di(finali[0])
    except Exception:
        nuovo = None
    return Azione('sostituisci', scatto, _('%s corrected') % scatto.nome, nuovo=nuovo, voci_cestino=voci,
                  corretti=[(f, _firma(f)) for f in finali])


def _annulla_sostituisci(azione):
    voci = _voci_cestino()
    for p, n in azione.voci_cestino:
        if not [v for v in voci if v[1] == p and (n is None or v[0] == n)]:
            raise FileNotFoundError(_('No longer in the trash: %s') % p)
    for f, firma in azione.corretti:
        if not os.path.isfile(f):
            raise FileNotFoundError(_('Corrected file not found: %s') % f)
        if _firma(f) != firma:
            raise Conflitto([f])               # modificato dopo la correzione: non lo si butta
    nomi_corretti = [f for f, firma in azione.corretti]
    for p, n in azione.voci_cestino:           # un file al posto di un originale è un conflitto,
        if os.path.lexists(p) and p not in nomi_corretti:       # salvo il corretto che stiamo togliendo
            raise Conflitto([p])
    # i corretti si mettono da parte (non si cancellano finché gli originali non sono tornati)
    messi = []
    ripristinati = []
    try:
        for f in nomi_corretti:
            t = temporaneo(f)
            os.rename(f, t)
            messi.append((f, t))
        for p, n in azione.voci_cestino:
            _ripristina(p, n)
            ripristinati.append(p)
    except BaseException as e:
        guasti = []
        prima = {v[0] for v in _voci_cestino()}
        rimessi = []
        for p in ripristinati:                 # gli originali già tornati rientrano nel cestino
            try:
                _nel_cestino(p)
                rimessi.append(p)
            except Exception as e2:
                guasti.append('%s (%s)' % (p, e2))
        if rimessi:                            # nel cestino hanno un nome nuovo: si aggiorna l'azione
            try:
                nuovi = dict(_riconosci(rimessi, prima))
                azione.voci_cestino = [(p, nuovi.get(p, n) if p in nuovi else n) for p, n in azione.voci_cestino]
            except Exception:
                pass
        for f, t in messi:
            try:
                if not os.path.lexists(f):
                    os.rename(t, f)
                else:
                    guasti.append(_('the corrected file stays in %s') % t)
            except Exception as e2:
                guasti.append('%s (%s)' % (t, e2))
        if guasti:
            raise OSError(_('%s. Undo interrupted: %s.') % (e, '; '.join(guasti))) from e
        raise
    for f, t in messi:
        _rimuovi_se_c_e(t)
    return azione.scatto


def annulla(azione):
    """Rende lo scatto di nuovo al suo posto."""
    if azione.tipo == 'scarta':
        return _annulla_scarta(azione)
    if azione.tipo == 'approva':
        return _annulla_approva(azione)
    if azione.tipo == 'sostituisci':
        return _annulla_sostituisci(azione)
    raise ValueError(_('Unknown action: %s') % azione.tipo)
