#!/bin/sh
# Remove what install.sh added. Your photos are never touched.
APP_ID=io.github.giovatti.Cornice
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

rm -f "$DATA/applications/$APP_ID.desktop" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
rm -rf "$DATA/cornice"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
echo "Cornice removed from the menu (the program folder itself is still here)."

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/cornice"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cornice"
if [ -d "$CONF" ] || [ -d "$CACHE" ]; then
    printf "Also delete the settings and the blur cache (%s, %s)? [y/N] " "$CONF" "$CACHE"
    read -r answer
    case "$answer" in
        y|Y|yes) rm -rf "$CONF" "$CACHE"; echo "Settings deleted." ;;
    esac
fi
