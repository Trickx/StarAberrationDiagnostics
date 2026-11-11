#!/bin/bash
# ============================================================================
# make-release.sh - packages the signed StarAberrationDiagnostics script
# ----------------------------------------------------------------------------
# Expects the script, its icon and its code signature in this directory:
#
#   StarAberrationDiagnostics.js     (developed in this directory)
#   StarAberrationDiagnostics.svg
#   StarAberrationDiagnostics.xsgn   (signed in PixInsight after copying)
#
# and the compiled documentation (pidoc-src/make-pidoc.py, then compiled in
# PixInsight) in pidoc-src/build/system/scripts/StarAberrationDiagnostics/.
#
# Creates the package in this directory and rewrites updates.xri for it:
#
#   StarAberrationDiagnostics<YYYYMMDD>v<version>.tar.gz
#      src/scripts/Tricx/StarAberrationDiagnostics/StarAberrationDiagnostics.js
#      src/scripts/Tricx/StarAberrationDiagnostics/StarAberrationDiagnostics.svg
#      src/scripts/Tricx/StarAberrationDiagnostics/StarAberrationDiagnostics.xsgn
#      doc/scripts/StarAberrationDiagnostics/StarAberrationDiagnostics.html
#      doc/scripts/StarAberrationDiagnostics/images/...
#
# updates.xri must be signed afterwards, like the other repositories. Once
# the script moves, add a <remove> list of its old paths (see FilterZWOFit).
#
# Usage: ./make-release.sh [version]
#        The version defaults to the VERSION constant of the script
#        ("0.9" is taken as 0.9.0).
# ============================================================================

set -euo pipefail

SCRIPT="StarAberrationDiagnostics"
INSTALL_DIR="src/scripts/Tricx/$SCRIPT"
PI_VERSIONS="1.9.4:1.9.99"
FILES="$SCRIPT.js $SCRIPT.svg $SCRIPT.xsgn"
DOC_INSTALL_DIR="doc/scripts/$SCRIPT"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOC_DIR="$HERE/pidoc-src/build/system/scripts/$SCRIPT"

# ----------------------------------------------------------------------------
# Check the files; the signature must be newer than the script
# ----------------------------------------------------------------------------
for f in $FILES; do
   if [ ! -f "$HERE/$f" ]; then
      echo "Missing $HERE/$f" >&2
      exit 1
   fi
done
if [ "$HERE/$SCRIPT.js" -nt "$HERE/$SCRIPT.xsgn" ]; then
   echo "$SCRIPT.js is newer than its signature; sign it again in PixInsight." >&2
   exit 1
fi
if [ ! -f "$DOC_DIR/$SCRIPT.html" ]; then
   echo "Missing $DOC_DIR/$SCRIPT.html; run pidoc-src/make-pidoc.py and compile it in PixInsight." >&2
   exit 1
fi
if [ "$HERE/pidoc-src/build/$SCRIPT.pidoc" -nt "$DOC_DIR/$SCRIPT.html" ]; then
   echo "$SCRIPT.pidoc is newer than the compiled documentation; compile it again in PixInsight." >&2
   exit 1
fi

# ----------------------------------------------------------------------------
# Version
# ----------------------------------------------------------------------------
VERSION="${1:-$(sed -n 's/^const VERSION = "\([^"]*\)";.*/\1/p' "$HERE/$SCRIPT.js")}"
if [ -z "$VERSION" ]; then
   echo "No version given and no VERSION constant found in $SCRIPT.js." >&2
   exit 1
fi
IFS=. read -r MAJOR MINOR PATCH <<< "$VERSION"
MINOR="${MINOR:-0}"
PATCH="${PATCH:-0}"
VERSION="$MAJOR.$MINOR.$PATCH"
VCODE="$(printf 'v%02d%d%d' "$MAJOR" "$MINOR" "$PATCH")"
DATE="$(date +%Y%m%d)"
PACKAGE="$SCRIPT$DATE$VCODE.tar.gz"

if [ -e "$HERE/$PACKAGE" ]; then
   echo "$PACKAGE already exists; remove it or use another version." >&2
   exit 1
fi

# ----------------------------------------------------------------------------
# Package
# ----------------------------------------------------------------------------
pkgdir="$HERE/.package"
rm -rf "$pkgdir"
mkdir -p "$pkgdir/$INSTALL_DIR"
for f in $FILES; do
   cp "$HERE/$f" "$pkgdir/$INSTALL_DIR/"
done
chmod 644 "$pkgdir/$INSTALL_DIR/"*
mkdir -p "$pkgdir/$DOC_INSTALL_DIR"
# Without hidden files (e.g. .DS_Store, or tool markers in the build tree).
rsync -a --exclude='.*' "$DOC_DIR/" "$pkgdir/$DOC_INSTALL_DIR/"
find "$pkgdir/$DOC_INSTALL_DIR" -type d -exec chmod 755 {} +
find "$pkgdir/$DOC_INSTALL_DIR" -type f -exec chmod 644 {} +
# COPYFILE_DISABLE: no AppleDouble (._*) files from macOS tar.
COPYFILE_DISABLE=1 tar -czf "$HERE/$PACKAGE" -C "$pkgdir" src doc
rm -rf "$pkgdir"

SHA1="$(shasum -a 1 "$HERE/$PACKAGE" | cut -d' ' -f1)"
echo "$PACKAGE  $SHA1"

# ----------------------------------------------------------------------------
# updates.xri
# ----------------------------------------------------------------------------
cat > "$HERE/updates.xri" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<xri version="1.0">
   <description>
      <p>
         $SCRIPT PixInsight Script Updates
      </p>
   </description>
   <platform os="all" arch="noarch" version="$PI_VERSIONS">
      <package fileName="$PACKAGE" sha1="$SHA1" type="script" releaseDate="$DATE">
         <title>
         $SCRIPT $VERSION
         </title>
         <description>
            <p>
               Installs the $SCRIPT PixInsight feature script: diagnostics of the optical
               system from the shapes of the stars - sensor tilt, corrector spacing, field
               curvature, coma, collimation and tracking errors - with a vector map preview
               and an assessment with suggested corrections.
            </p>
            <p>
               Installed files:
               <br/>$INSTALL_DIR/$SCRIPT.js
               <br/>$INSTALL_DIR/$SCRIPT.svg
               <br/>$INSTALL_DIR/$SCRIPT.xsgn
               <br/>$DOC_INSTALL_DIR/$SCRIPT.html (reference documentation, with images)
            </p>
            <p>
               Released under the GNU General Public License v3 or later.
            </p>
         </description>
      </package>
   </platform>
</xri>
EOF

cat <<EOF

updates.xri written for $SCRIPT $VERSION ($DATE$VCODE).
Next steps:
  1. Sign updates.xri (as for the other repositories).
  2. Commit the new package, updates.xri and index.html, then push.
EOF
