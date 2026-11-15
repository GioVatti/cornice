#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Strumenti per le traduzioni di Cornice, in puro Python (xgettext e msgfmt non servono).

  python3 tools/i18n.py extract   cerca le chiamate _() e ngettext() nei .py della radice, scrive
                                  po/cornice.pot e aggiunge le stringhe nuove ai po/<lingua>.po
                                  (le traduzioni esistenti restano; le stringhe sparite si tolgono)
  python3 tools/i18n.py compile   compila po/<lingua>.po in locale/<lingua>/LC_MESSAGES/cornice.mo
  python3 tools/i18n.py check     elenca stringhe senza traduzione e segnaposto che non coincidono

Le stringhe sorgente sono in inglese. Nuova lingua: copia po/cornice.pot in po/<codice>.po, traduci,
poi `compile`.
"""
import ast
import glob
import os
import re
import struct
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PO = os.path.join(RADICE, 'po')
LOCALE = os.path.join(RADICE, 'locale')
DOMINIO = 'cornice'
INTESTAZIONE = {
    'it': 'nplurals=2; plural=(n != 1);',
}
PIURALI_PREDEFINITI = 'nplurals=2; plural=(n != 1);'


# ------------------------------------------------------------------ estrazione dal codice

def stringhe_nel_codice():
    """[(msgid, msgid_plural o None, [posizioni])] nell'ordine di apparizione."""
    trovate = {}
    for percorso in sorted(glob.glob(os.path.join(RADICE, '*.py'))):
        nome = os.path.basename(percorso)
        if nome == 'lingua.py':        # definisce _() e ngettext(), non li usa
            continue
        with open(percorso, encoding='utf-8') as f:
            albero = ast.parse(f.read(), nome)
        for n in ast.walk(albero):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            fn = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if fn == '_' and n.args:
                a = [n.args[0]]
            elif fn == 'ngettext' and len(n.args) >= 2:
                a = n.args[:2]
            else:
                continue
            if not all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in a):
                raise SystemExit('%s:%d: la stringa da tradurre deve essere un letterale' % (nome, n.lineno))
            chiave = (a[0].value, a[1].value if len(a) == 2 else None)
            trovate.setdefault(chiave, []).append('%s:%d' % (nome, n.lineno))
    ordinate = sorted(trovate.items(), key=lambda kv: (kv[1][0].split(':')[0], int(kv[1][0].split(':')[1])))
    return [(k[0], k[1], pos) for k, pos in ordinate]


# ------------------------------------------------------------------ file .po

def _scrivi_str(s):
    s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t')
    return '"%s"' % s


def _campo(nome, valore):
    """msgid "..." su una riga, o su piu' righe se contiene ritorni a capo."""
    if '\n' in valore and valore.index('\n') != len(valore) - 1:
        pezzi = valore.split('\n')
        righe = [pz + '\n' for pz in pezzi[:-1]] + ([pezzi[-1]] if pezzi[-1] else [])
        return '%s ""\n%s\n' % (nome, '\n'.join(_scrivi_str(r) for r in righe))
    return '%s %s\n' % (nome, _scrivi_str(valore))


def leggi_po(percorso):
    """Rende (intestazione, voci). voce = dict(id, plurale, tr [lista], fuzzy, posizioni)."""
    voci = []
    corrente = None
    campo = None
    fuzzy = False
    posizioni = []

    def chiudi():
        nonlocal corrente
        if corrente is not None:
            voci.append(corrente)
        corrente = None

    with open(percorso, encoding='utf-8') as f:
        righe = f.read().split('\n')
    for r in righe:
        if r.startswith('#,'):
            chiudi()
            fuzzy = 'fuzzy' in r
        elif r.startswith('#:'):
            chiudi()
            posizioni = r[2:].split()
        elif r.startswith('#'):
            continue
        elif r.startswith('msgid_plural'):
            corrente['plurale'] = ast.literal_eval(r[len('msgid_plural'):].strip())
            campo = ('plurale',)
        elif r.startswith('msgid'):
            chiudi()
            corrente = dict(id=ast.literal_eval(r[5:].strip()), plurale=None, tr=[''], fuzzy=fuzzy,
                            posizioni=posizioni)
            fuzzy, posizioni = False, []
            campo = ('id',)
        elif r.startswith('msgstr['):
            i = int(r[7:r.index(']')])
            while len(corrente['tr']) <= i:
                corrente['tr'].append('')
            corrente['tr'][i] = ast.literal_eval(r[r.index(']') + 1:].strip())
            campo = ('tr', i)
        elif r.startswith('msgstr'):
            corrente['tr'][0] = ast.literal_eval(r[6:].strip())
            campo = ('tr', 0)
        elif r.startswith('"'):
            v = ast.literal_eval(r)
            if campo[0] == 'tr':
                corrente['tr'][campo[1]] += v
            else:
                corrente[campo[0]] += v
    chiudi()
    intest = ''
    if voci and voci[0]['id'] == '':
        intest = voci.pop(0)['tr'][0]
    return intest, voci


def scrivi_po(percorso, intestazione, voci):
    out = ['msgid ""\nmsgstr ""\n' + ''.join(_riga_intest(intestazione)) + '\n']
    for v in voci:
        s = ''
        if v.get('posizioni'):
            s += '#: ' + ' '.join(v['posizioni']) + '\n'
        if v.get('fuzzy'):
            s += '#, fuzzy\n'
        s += _campo('msgid', v['id'])
        if v.get('plurale') is not None:
            s += _campo('msgid_plural', v['plurale'])
            for i, t in enumerate(v['tr'] if len(v['tr']) > 1 else v['tr'] + ['']):
                s += _campo('msgstr[%d]' % i, t)
        else:
            s += _campo('msgstr', v['tr'][0])
        out.append(s)
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))


def _riga_intest(testo):
    return [_scrivi_str(r + '\n') + '\n' for r in testo.split('\n') if r]


def intestazione_per(lingua):
    return ('Project-Id-Version: Cornice\nLanguage: %s\nMIME-Version: 1.0\n'
            'Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n'
            'Plural-Forms: %s\n' % (lingua, INTESTAZIONE.get(lingua, PIURALI_PREDEFINITI)))


# ------------------------------------------------------------------ comandi

def estrai():
    codice = stringhe_nel_codice()
    pot = [dict(id=i, plurale=p, tr=['', ''] if p else [''], fuzzy=False, posizioni=pos) for i, p, pos in codice]
    scrivi_po(os.path.join(PO, DOMINIO + '.pot'),
              'Project-Id-Version: Cornice\nMIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n'
              'Content-Transfer-Encoding: 8bit\n', pot)
    print('po/cornice.pot: %d stringhe' % len(pot))
    for percorso in sorted(glob.glob(os.path.join(PO, '*.po'))):
        lingua = os.path.basename(percorso)[:-3]
        intest, vecchie = leggi_po(percorso)
        per_id = {(v['id'], v['plurale']): v for v in vecchie}
        nuove, mancanti = [], 0
        for i, p, pos in codice:
            v = per_id.get((i, p))
            if v is None:
                v = dict(id=i, plurale=p, tr=['', ''] if p else [''], fuzzy=False)
                mancanti += 1
            v['posizioni'] = pos
            nuove.append(v)
        tolte = len(vecchie) - (len(nuove) - mancanti)
        scrivi_po(percorso, intest or intestazione_per(lingua), nuove)
        print('po/%s.po: %d stringhe, %d da tradurre, %d tolte' % (lingua, len(nuove), mancanti, tolte))


def compila():
    for percorso in sorted(glob.glob(os.path.join(PO, '*.po'))):
        lingua = os.path.basename(percorso)[:-3]
        intest, voci = leggi_po(percorso)
        cat = {'': intest}
        for v in voci:
            if v['fuzzy'] or not any(v['tr']):
                continue
            if v['plurale'] is not None:
                cat[v['id'] + '\0' + v['plurale']] = '\0'.join(v['tr'])
            else:
                cat[v['id']] = v['tr'][0]
        dest = os.path.join(LOCALE, lingua, 'LC_MESSAGES')
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, DOMINIO + '.mo'), 'wb') as f:
            f.write(scrivi_mo(cat))
        print('locale/%s/LC_MESSAGES/cornice.mo: %d voci' % (lingua, len(cat) - 1))


def scrivi_mo(cat):
    """Formato .mo (little endian, senza tabella hash)."""
    chiavi = sorted(cat)
    ids = b''
    strs = b''
    offs = []
    for k in chiavi:
        kb, vb = k.encode('utf-8'), cat[k].encode('utf-8')
        offs.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b'\0'
        strs += vb + b'\0'
    n = len(chiavi)
    inizio_ids = 7 * 4 + 16 * n
    inizio_strs = inizio_ids + len(ids)
    tab = b''
    for o1, l1, o2, l2 in offs:
        tab += struct.pack('<II', l1, inizio_ids + o1)
    tab2 = b''
    for o1, l1, o2, l2 in offs:
        tab2 += struct.pack('<II', l2, inizio_strs + o2)
    testa = struct.pack('<IIIIIII', 0x950412de, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0)
    return testa + tab + tab2 + ids + strs


_SEGNAPOSTO = re.compile(r'%(?:\([^)]+\))?[-#0 +]*\d*(?:\.\d+)?[sdifgxXr%]|\{[^{}]*\}')


def segnaposto(s):
    return sorted(m for m in _SEGNAPOSTO.findall(s) if m != '%%')


def controlla():
    """Rende l'elenco dei problemi: stringhe senza traduzione o con segnaposto diversi."""
    problemi = []
    for percorso in sorted(glob.glob(os.path.join(PO, '*.po'))):
        lingua = os.path.basename(percorso)[:-3]
        intest, voci = leggi_po(percorso)
        for v in voci:
            mancante = (not all(v['tr'])) if v['plurale'] is not None else (not v['tr'][0])
            if mancante or v['fuzzy']:
                problemi.append('%s: senza traduzione: %r' % (lingua, v['id']))
                continue
            for t in v['tr']:
                if segnaposto(t) != segnaposto(v['id']):
                    problemi.append('%s: segnaposto diversi: %r -> %r' % (lingua, v['id'], t))
    return problemi


def main(argv):
    if len(argv) != 2 or argv[1] not in ('extract', 'compile', 'check'):
        print(__doc__)
        return 2
    if argv[1] == 'extract':
        estrai()
    elif argv[1] == 'compile':
        compila()
    else:
        p = controlla()
        print('\n'.join(p) or 'tutto a posto')
        return 1 if p else 0
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
