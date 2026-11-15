# SPDX-License-Identifier: GPL-3.0-or-later
"""Traduzioni di Cornice (gettext, dominio 'cornice').

Le stringhe nel codice sono in inglese; le traduzioni stanno in `po/<lingua>.po` e, compilate, in
`locale/<lingua>/LC_MESSAGES/cornice.mo` (vedi `tools/i18n.py`). La lingua si sceglie come fa gettext:
variabili d'ambiente LANGUAGE, LC_ALL, LC_MESSAGES, LANG. Niente GTK.
"""
import gettext
import os

DOMINIO = 'cornice'
CARTELLA_LOCALE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')

_traduzione = gettext.NullTranslations()


def imposta(lingue=None):
    """Sceglie la lingua. `lingue`: lista di codici ('it', 'en'...) oppure None per usare l'ambiente."""
    global _traduzione
    _traduzione = gettext.translation(DOMINIO, CARTELLA_LOCALE, languages=lingue, fallback=True)
    return _traduzione


def _(testo):
    return _traduzione.gettext(testo)


def ngettext(singolare, plurale, n):
    return _traduzione.ngettext(singolare, plurale, n)


def lingua_attiva():
    """Codice della lingua in uso ('it', 'en'...), come lo vede gettext."""
    info = getattr(_traduzione, '_info', None) or {}
    return (info.get('language') or '').split('_')[0] or 'en'


imposta()
