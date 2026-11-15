# Developing Cornice

Python 3 (developed on 3.14, written to run on 3.12+), GTK 4 and libadwaita through PyGObject, numpy, Pillow,
and optionally rawpy (RAW decoding) and pillow-heif (HEIC). No build step, no pip needed to run it.

Code identifiers and comments are in Italian (the original author's language); user-visible strings are in
English and translated with gettext (see below).

## Layout

| File | What it does | GTK? |
|---|---|---|
| `cornice.py` | The window (`Finestra`), the viewer widget (`VistaFoto`), the Deblur mode (`PannelloTogli`, `RitaglioVista`, `Panoramica`), configuration, first-run dialog, command line and the `--autoprova` self-test | yes |
| `operazioni.py` | Shots (a JPG + RAW pair grouped by file stem), discard, approve, replace, undo, trash helpers | only `Gio`, for the trash |
| `mosso.py` | Blur estimate in a separate process (forkserver) with a JSON cache | no |
| `togli.py` | The deblur algorithm: TIFF/DNG low level, `Foto` (load and save), estimates, Richardson–Lucy | no |
| `incorporata.py` | The JPEG preview embedded in a RAW (used when rawpy is missing or fails) | no |
| `lingua.py` | gettext setup: `_()`, `ngettext()`, `imposta(['it'])` | no |
| `tools/i18n.py` | Pure-Python replacement for xgettext/msgmerge/msgfmt | no |
| `po/`, `locale/` | Translations: `cornice.pot`, `it.po`, and the compiled `locale/<lang>/LC_MESSAGES/cornice.mo` (committed) | |
| `data/` | Desktop file template and icon (`install.sh` fills in the paths) | |
| `prove/` | Tests | |

`togli.py` derives from the author's standalone *Togli mosso* program.

## Safety rules ("never lose a file")

`operazioni.py` is the only place that moves or deletes the user's files. Its rules:

- **Discard** sends every file of the shot to the trash (`Gio.File.trash`). If the trash is not available
  (some SD cards) it raises `CestinoNonDisponibile` before touching anything and the UI asks before deleting
  for good. If it fails halfway it restores what it already trashed.
- **Approve** first checks every destination; a different file with the same name raises `Conflitto` and nothing
  is touched. Each file is copied to a hidden temporary name, `fsync`ed, compared byte by byte with the original,
  and published with a hard link (so it can never overwrite). Originals are deleted only after *all* copies are
  verified; if that fails the copies are removed or the originals restored. The destination folder is created
  here, not earlier.
- **Replace** (Deblur): the corrected files are written to `.cornice-<stem>-XXXX.tmp` next to the originals, then
  the originals go to the trash, then the temporaries are renamed to their final names. Any error puts
  everything back. If a final name already exists and is not one of the originals, nothing happens.
- **Undo** of a replace moves the corrected files aside, restores the originals from the trash and only then
  deletes the aside files; it refuses if a corrected file was modified in the meantime. Undo never overwrites.
- `.xmp` sidecars of a shot travel with it on discard/approve, and are left alone on replace.
- Tests never touch the user's files or trash entries other than their own (see the cleanup in
  `prove/test_operazioni.py`).

## Threading and processes

- GTK is touched only from the main thread. Worker threads return to it with `GLib.idle_add`.
- Photo loading uses a small `ThreadPoolExecutor`; file operations run in a thread per action
  (`Finestra._in_thread`) with the window's `occupato` flag blocking concurrent actions.
- The blur estimate runs in a **forkserver** child process (`mosso.Stimatore`), never `fork` (the parent has GTK).
  One estimate at a time, a queue in the parent (so requests can be dropped), results cached in
  `$XDG_CACHE_HOME/cornice/mosso.json` keyed by path, mtime and size. Because of forkserver the child re-imports
  the main module, so start-up code must stay under `if __name__ == '__main__':`.
- The Deblur preview computes only the visible crop (+80 px margin) in a daemon thread. A newer request sets the
  `threading.Event` of the older one (`ferma`), which `togli.togli_mosso` checks every iteration. Saving runs in
  a thread with a progress bar; the window refuses to close during a save.
- Estimates for photos above 6500 px on the long side run on a copy reduced by an integer factor (box average)
  and the length is multiplied back (`togli._riduzione`).

## Translations (gettext)

Source strings are English, wrapped in `_()` (or `ngettext()` for plurals) imported from `lingua`. The extractor
only accepts string literals. Number formats follow the language through the translatable `_('.')` (decimal
separator) and `_('%Y-%m-%d %H:%M')` (date format).

```sh
python3 tools/i18n.py extract    # scan the code -> po/cornice.pot, merge new strings into po/*.po
# ...translate the empty msgstr entries in po/it.po...
python3 tools/i18n.py compile    # po/*.po -> locale/<lang>/LC_MESSAGES/cornice.mo  (commit the .mo)
python3 tools/i18n.py check      # untranslated strings, placeholder mismatches
```

New language: copy `po/cornice.pot` to `po/<code>.po`, set the `Language:` and `Plural-Forms:` header, translate,
`compile`. The language is chosen like gettext does (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`); try it with
`LANGUAGE=it python3 cornice.py`. `prove/test_i18n.py` fails if the code, the `.pot`, `it.po` and the `.mo` drift
apart.

## Tests

```sh
python3 -m unittest discover -s prove -t .        # everything; ~20 s
python3 -m unittest prove.test_operazioni         # one module
```

All tests are synthetic (generated images, fake files in temporary folders): no photos are needed. Optional
tests on real photos run when `CORNICE_FOTO_PROVA=<folder of copies>` is set (`prove/test_foto_reali.py` lists
the file names it looks for; tests whose file is missing are skipped).

### Testing the window without disturbing your screen

`python3 cornice.py --autoprova FOLDER_OF_COPIES` drives the real window through a scripted run: scroll, frame,
cull (discard/approve and undo), Deblur (preview, sliders, save a copy, replace and undo) and the first-run dialog
with a temporary config. Never run it on originals. To keep any window off your real display, use a headless
GNOME shell:

```sh
dbus-run-session -- prove/prova_finestra.sh [FOLDER_OF_PHOTOS_TO_COPY]
LANGUAGE=en CORNICE_TEMA=light XDG_CONFIG_HOME=$(mktemp -d) dbus-run-session -- prove/prova_finestra.sh
```

Without an argument it generates synthetic photos (`prove/sintetiche.py`: two blurred, two sharp, one fake RAW with
an embedded preview). It starts `gnome-shell --headless --virtual-monitor 1600x1000`, runs the self-test and
writes PNG snapshots into `$CORNICE_ISTANTANEE` (a new temporary folder by default). `XDG_CONFIG_HOME` pointing to
an empty folder gives the stock GNOME/Adwaita settings; `CORNICE_TEMA=light|dark` forces the colour scheme
(Cornice is dark by default). Colours of buttons look wrong in the PNGs; judge the layout. The photos are copied to
`prove/tmp_finestra` (same file system as your home, because the trash does not work in `/tmp` on many systems)
and deleted afterwards.

## Known limits and open items

- RAW sensors that are not Bayer (Fujifilm X-Trans) cannot be deblurred; the JPG of the pair still can.
- The estimator only sees straight-line blur of about 6 px or more; blur length is measured at full resolution
  (reduced copy above 6500 px, tested only on synthetic images).
- Synthetic test images can give an "uncertain" verdict on sharp content; real sharp photos give "sharp".
- HEIC/HEIF support is wired in but untested here (pillow-heif was not available in the development environment).
- The embedded-preview fallback was tested with synthetic RAW containers and a Sony ARW; Canon CR3 and other
  container formats use the "search for JPEG segments" fallback and have not been tried on real files.
- Deblur has no *Cancel* button once a save has started, and a running blur estimate cannot be interrupted.
- The app icon appears in the About dialog and the dash only after `./install.sh` (it lives in the icon theme).
- Libadwaita older than 1.8 has no `Adw.ShortcutsDialog`: a plain list is shown instead (not tested).
- Some dialog texts could be shorter; the Italian number formatting in the Deblur messages now uses a decimal
  comma (the old standalone program printed points).
