#!/bin/sh
# Build the Mahlah front end and install it as Tirzah's served UI (web/static).
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
UI_SRC="${MAHLAH_DIR:-$HERE/../Mahlah}"
if [ ! -f "$UI_SRC/package.json" ]; then
  echo "Mahlah not found at $UI_SRC (set MAHLAH_DIR)." >&2; exit 1
fi
echo "Building Mahlah UI from $UI_SRC ..."
npm --prefix "$UI_SRC" install
npm --prefix "$UI_SRC" run build
DEST="$HERE/src/tirzah/web/static"
rm -rf "$DEST/assets"
cp -r "$UI_SRC"/dist/. "$DEST"/
echo "Installed Mahlah UI -> $DEST"
