#!/bin/bash
# Prova la finestra di Cornice (--autoprova) senza aprire nulla sullo schermo vero: una GNOME Shell senza testa
# (Wayland, schermo virtuale) dentro una sessione dbus a parte.
#
#   dbus-run-session -- prove/prova_finestra.sh [CARTELLA_DI_FOTO]
#
# Senza argomenti genera foto sintetiche (prove/sintetiche.py: due mosse, due nitide, un RAW finto con anteprima).
# Con una cartella di foto vere (JPG + RAW) ne lavora una COPIA: l'originale non si tocca mai.
# Variabili utili:
#   LANGUAGE=it|en            lingua dell'interfaccia
#   CORNICE_TEMA=light|dark   tema chiaro o scuro (predefinito: scuro)
#   XDG_CONFIG_HOME=$(mktemp -d)   tema e impostazioni di serie (nessuna personalizzazione dell'utente)
#   CORNICE_ISTANTANEE=cartella    dove salvare le istantanee PNG (predefinito: una cartella temporanea nuova)
# Le copie stanno in prove/tmp_finestra (nello stesso file system della cartella personale: serve per il
# cestino, che per esempio in /tmp spesso non c'e'); si cancellano alla fine.
# Nelle istantanee i colori dei pulsanti possono risultare sbagliati: si giudica la disposizione.
set -u
QUI="$(cd "$(dirname "$0")/.." && pwd)"
FOTO="$QUI/prove/tmp_finestra"
IST="${CORNICE_ISTANTANEE:-$(mktemp -d "${TMPDIR:-/tmp}/cornice-istantanee.XXXXXX")}"
rm -rf "$FOTO"
mkdir -p "$FOTO" "$IST"

if [ $# -ge 1 ]; then
    [ -d "$1" ] || { echo "Non e' una cartella: $1"; exit 2; }
    cp -r "$1"/. "$FOTO"/
else
    python3 "$QUI/prove/sintetiche.py" "$FOTO" >/dev/null || exit 2
fi

WL=cornice-prova
LOG="$IST/gnome-shell.log"
gnome-shell --headless --wayland-display "$WL" --virtual-monitor 1600x1000 >"$LOG" 2>&1 &
GS=$!
trap 'kill $GS 2>/dev/null; rm -rf "$FOTO"' EXIT
for i in $(seq 1 150); do
    [ -S "$XDG_RUNTIME_DIR/$WL" ] && break
    sleep 0.2
done
if [ ! -S "$XDG_RUNTIME_DIR/$WL" ]; then echo "La shell senza testa non e' partita (vedi $LOG)"; exit 3; fi
sleep 1
cd "$QUI"
WAYLAND_DISPLAY=$WL DISPLAY= GDK_BACKEND=wayland CORNICE_ISTANTANEE="$IST" python3 -u cornice.py --autoprova "$FOTO"
ESITO=$?
echo "esito: $ESITO; istantanee in $IST"
exit $ESITO
