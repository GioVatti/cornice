#!/bin/sh
# Install Cornice for the current user (Ubuntu / Debian and derivatives).
# Run it from the folder you cloned:  ./install.sh
# Nothing is copied: the menu entry starts Cornice from this folder, so `git pull` updates it.
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
APP_ID=io.github.giovatti.Cornice
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/cornice/venv"

echo "Installing Cornice from $HERE"

# 1. System packages (GTK 4, libadwaita, numpy, Pillow). Only the missing ones are installed.
NEEDED="python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-numpy python3-pil"
if ! python3 -c 'import rawpy' 2>/dev/null; then
    NEEDED="$NEEDED python3-venv"        # to install rawpy (RAW files) in a private environment
fi
MISSING=""
for p in $NEEDED; do
    if ! dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then
        MISSING="$MISSING $p"
    fi
done
if [ -n "$MISSING" ]; then
    echo "These system packages are missing:$MISSING"
    if command -v apt-get >/dev/null; then
        echo "Installing them with apt (your password may be asked)..."
        sudo apt-get install -y $MISSING
    else
        echo "Please install them with your package manager and run ./install.sh again."
        exit 1
    fi
fi

# 2. rawpy, the RAW decoder. It is not packaged by Ubuntu, so it goes into a private
#    virtual environment that also sees the system packages above.
PYTHON=python3
if python3 -c 'import rawpy' 2>/dev/null; then
    echo "rawpy found: using the system Python."
else
    echo "Installing rawpy (RAW support) in $VENV ..."
    mkdir -p "$(dirname "$VENV")"
    if python3 -m venv --system-site-packages "$VENV" && "$VENV/bin/pip" install --quiet --upgrade rawpy; then
        PYTHON="$VENV/bin/python3"
    else
        echo "WARNING: rawpy could not be installed (no internet?). Cornice will work, but RAW files"
        echo "will show only their embedded preview and cannot be deblurred. Run ./install.sh again later."
        rm -rf "$VENV"
    fi
fi

# 3. Menu entry, "Open With" and icon.
mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
cp "$HERE/data/icons/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
sed -e "s|@PYTHON@|$PYTHON|" -e "s|@HERE@|$HERE|" "$HERE/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
# (no gtk-update-icon-cache here: a stale per-user icon cache would hide icons added later by other apps)

echo
echo "Done. Look for \"Cornice\" in your applications, or right-click a photo > Open With."
echo "To remove it: ./uninstall.sh"
