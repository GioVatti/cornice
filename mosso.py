# SPDX-License-Identifier: GPL-3.0-or-later
"""Stima del mosso per Cornice, in un processo separato e con cache.

Usa la stima di `togli.py` (parte di Cornice). Niente GTK.
Nota: con il contesto 'forkserver' il processo figlio reimporta il programma principale come
modulo: chi avvia Cornice deve avere il codice di avvio sotto `if __name__ == '__main__':`.
"""
import collections
import concurrent.futures
import json
import multiprocessing
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, asdict

from lingua import _

try:                                   # senza numpy o PIL non c'e' stima (e Cornice resta senza indicatore)
    import togli
except Exception:
    togli = None

CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'cornice', 'mosso.json')
MAX_VOCI_CACHE = 20000
_STATO = {'sicura': 'recuperabile', 'zone': 'recuperabile', 'incerta': 'incerto', 'niente': 'nitida'}


@dataclass
class Esito:
    stato: str              # 'recuperabile' | 'incerto' | 'nitida' | 'errore'
    lunghezza: float = 0.0
    angolo: float = 0.0
    spiegazione: str = ''

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def da_dict(d):
        return Esito(str(d['stato']), float(d['lunghezza']), float(d['angolo']), str(d['spiegazione']))


def stima_file(percorso):
    """Stima sincrona (da usare nel processo figlio). Non lancia eccezioni: rende Esito('errore')."""
    try:
        if togli is None:
            raise RuntimeError(_('numpy or PIL not available'))
        lin = togli.Foto(percorso).lin
        esito, lung, ang, spieg = togli.stima_completa(lin)
        return Esito(_STATO[esito], float(lung), float(ang), str(spieg))
    except Exception as e:
        return Esito('errore', 0.0, 0.0, '%s: %s' % (type(e).__name__, e))


def _lavoro(percorso):
    """Funzione eseguita nel processo figlio (a livello di modulo, per poterla trasferire)."""
    return stima_file(percorso).to_dict()


class Stimatore:
    def __init__(self, cache=None):
        self._file_cache = os.path.abspath(os.path.expanduser(cache or CACHE))
        self._lock = threading.Condition()
        self._cache = None                          # caricata al primo uso
        self._coda = collections.deque()            # percorsi in attesa
        self._attive = {}                           # percorso -> {'chiave', 'fatto': [callback]}
        self._in_corso = None
        self._chiuso = False
        self._ex = None
        self._thread = None

    @property
    def disponibile(self):
        return togli is not None

    # --- cache
    @staticmethod
    def _chiave(percorso):
        st = os.stat(percorso)
        return '%s|%d|%d' % (percorso, st.st_mtime_ns, st.st_size)

    def _leggi_file_cache(self):
        try:
            with open(self._file_cache, encoding='utf-8') as f:
                d = json.load(f)
            return {k: Esito.da_dict(v) for k, v in d.items()} if isinstance(d, dict) else {}
        except Exception:
            return {}                               # assente o rovinata: si riparte da vuoto

    def _cache_pronta(self):
        if self._cache is None:
            self._cache = self._leggi_file_cache()
        return self._cache

    def _scrivi_cache(self):
        """Fonde con quanto c'è sul disco (altre copie del programma) e scrive in modo atomico."""
        try:
            sul_disco = self._leggi_file_cache()
            sul_disco.update(self._cache)
            while len(sul_disco) > MAX_VOCI_CACHE:
                sul_disco.pop(next(iter(sul_disco)))
            self._cache = sul_disco
            cartella = os.path.dirname(self._file_cache)
            os.makedirs(cartella, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=cartella, prefix='.mosso-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump({k: v.to_dict() for k, v in sul_disco.items()}, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._file_cache)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except Exception:
            pass                                    # la cache è solo un aiuto: mai un errore per lei

    def in_cache(self, percorso):
        """Esito già noto, subito, senza calcolare."""
        try:
            chiave = self._chiave(os.path.abspath(percorso))
        except OSError:
            return None
        with self._lock:
            return self._cache_pronta().get(chiave)

    # --- richieste
    def richiedi(self, percorso, fatto):
        """Chiede la stima; fatto(percorso, Esito) viene chiamata da un thread qualsiasi (se l'esito è
        già in cache, direttamente da questo). Richieste doppie per lo stesso file condividono il risultato."""
        p = os.path.abspath(percorso)
        if not self.disponibile:
            self._consegna([fatto], percorso, Esito('errore', 0.0, 0.0, _('numpy or PIL not available')))
            return
        try:
            chiave = self._chiave(p)
        except OSError as e:
            self._consegna([fatto], percorso, Esito('errore', 0.0, 0.0, str(e)))
            return
        with self._lock:
            if self._chiuso:
                return
            noto = self._cache_pronta().get(chiave)
            if noto is None:
                voce = self._attive.get(p)
                if voce is not None and voce['chiave'] == chiave:
                    voce['fatto'].append((percorso, fatto))
                    return
                self._attive[p] = {'chiave': chiave, 'fatto': [(percorso, fatto)]}
                if p not in self._coda:
                    self._coda.append(p)
                self._avvia_thread()
                self._lock.notify_all()
                return
        self._consegna([fatto], percorso, noto)

    def solo_questi(self, percorsi):
        """Toglie dalla coda le richieste non in elenco (quella già in calcolo non si può fermare)."""
        tieni = {os.path.abspath(p) for p in percorsi}
        with self._lock:
            for p in list(self._coda):
                if p not in tieni:
                    self._coda.remove(p)
                    self._attive.pop(p, None)

    def chiudi(self):
        with self._lock:
            self._chiuso = True
            self._coda.clear()
            self._attive.clear()
            ex, self._ex = self._ex, None
            self._lock.notify_all()
        if ex is not None:
            procs = list(getattr(ex, '_processes', {}).values())
            ex.shutdown(wait=False, cancel_futures=True)
            for pr in procs:                        # niente attese alla chiusura dell'applicazione
                try:
                    pr.terminate()
                except Exception:
                    pass

    # --- interni
    @staticmethod
    def _consegna(callback, percorso, esito):
        for c in callback:
            fn = c[1] if isinstance(c, tuple) else c
            nome = c[0] if isinstance(c, tuple) else percorso
            try:
                fn(nome, esito)
            except Exception as e:
                print('mosso: error in the callback: %s' % e, file=sys.stderr)

    def _avvia_thread(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._ciclo, name='mosso', daemon=True)
            self._thread.start()

    def _executor(self):
        # mai 'fork': il genitore ha GTK
        ctx = multiprocessing.get_context('forkserver')
        return concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx)

    def _ciclo(self):
        """Un solo calcolo alla volta, nel processo figlio; la coda resta qui, così si può annullare."""
        while True:
            with self._lock:
                while not self._coda and not self._chiuso:
                    self._lock.wait()
                if self._chiuso:
                    return
                p = self._coda.popleft()
                chiave = self._attive[p]['chiave']
                self._in_corso = p
                if self._ex is None:
                    self._ex = self._executor()
                ex = self._ex
            try:
                esito = Esito.da_dict(ex.submit(_lavoro, p).result())
            except Exception as e:
                esito = Esito('errore', 0.0, 0.0, _('computation interrupted: %s') % e)
                with self._lock:                    # un processo morto: se ne riavvia uno nuovo
                    if self._ex is ex and not self._chiuso:
                        self._ex = None
            with self._lock:
                self._in_corso = None
                voce = self._attive.get(p)
                if voce is not None and voce['chiave'] == chiave:
                    del self._attive[p]
                    callback = voce['fatto']
                else:
                    callback = []
                if esito.stato != 'errore' and not self._chiuso:
                    self._cache_pronta()[chiave] = esito
                    self._scrivi_cache()
            self._consegna(callback, p, esito)
