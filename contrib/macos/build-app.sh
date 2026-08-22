#!/bin/bash
#
# Compile trichrome-merge.applescript into an .app bundle that Finder and
# digiKam will offer under "Open With" for RAW files.
#
#   ./build-app.sh                      -> ~/Applications/Trichrome Merge.app
#   ./build-app.sh /path/to/Some.app    -> there instead
#
# osacompile alone produces an app that runs, but that no file can be opened
# WITH: an app only appears in Open With if its Info.plist declares document
# types it handles. Most of this script is that declaration.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
source_script="$here/trichrome-merge.applescript"
app="${1:-$HOME/Applications/Trichrome Merge.app}"
plist="$app/Contents/Info.plist"
plistbuddy=/usr/libexec/PlistBuddy

# The extensions the app claims, taken from trichrome itself where it is
# importable so the two cannot drift, and hardcoded to the same list when it is
# not (this script must work on a machine that only has the .app).
extensions=$(python3 -c \
  "import trichrome; print(' '.join(sorted(e[1:] for e in trichrome.RAW_EXTENSIONS)))" \
  2>/dev/null || echo "3fr arw cr2 cr3 dng nef orf pef raf rw2 srw")

echo "Building $app"
rm -rf "$app"
mkdir -p "$(dirname "$app")"
osacompile -o "$app" "$source_script"

# Set a key whether or not osacompile already wrote one.
plist_set() {   # key type value...
    local key=$1 type=$2
    shift 2
    $plistbuddy -c "Set :$key $*" "$plist" 2>/dev/null \
        || $plistbuddy -c "Add :$key $type $*" "$plist"
}

# --- Identity ------------------------------------------------------------- #
plist_set CFBundleIdentifier string net.trichrome.merge-droplet
plist_set CFBundleName string Trichrome Merge
plist_set NSHighResolutionCapable bool true

# Reaching Finder for the "Show in Finder" button is an Automation request, and
# macOS kills an app that makes one without a stated reason.
plist_set NSAppleEventsUsageDescription string \
    Trichrome Merge reveals the files it wrote in Finder.

# --- Document types: what makes "Open With" offer this app ---------------- #
$plistbuddy -c "Delete :CFBundleDocumentTypes" "$plist" 2>/dev/null || true
$plistbuddy -c "Add :CFBundleDocumentTypes array" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:0 dict" "$plist"
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'RAW image'" "$plist"
# Viewer, not Editor: the app never writes over what it is given.
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$plist"
# Alternate, not Owner: being in the Open With list is the whole point, but
# nothing here should displace the user's normal handler for a RAW file.
$plistbuddy -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$plist"

$plistbuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$plist"
i=0
for uti in public.camera-raw-image public.image public.data; do
    $plistbuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:$i string $uti" "$plist"
    i=$((i + 1))
done

# Extensions as well as UTIs: a RAW format macOS has no UTI for (a new camera,
# or one this Mac has never seen) would otherwise not match anything above.
$plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$plist"
i=0
for ext in $extensions; do
    $plistbuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:$i string $ext" "$plist"
    i=$((i + 1))
done

# --- Re-sign ------------------------------------------------------------- #
# osacompile ad-hoc signs the bundle; editing Info.plist afterwards invalidates
# that signature, and recent macOS refuses to launch an app whose signature does
# not match its contents. Re-sign now that the edits are done.
codesign --force --sign - "$app" >/dev/null 2>&1 \
    || echo "warning: could not re-sign the bundle" >&2

# --- Register, so Open With sees it without a logout ---------------------- #
lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$lsregister" ] && "$lsregister" -f "$app" || true
touch "$app"

# --- Self-test: the command builder, without needing a screen ------------- #
built=$(osascript \
    -e "set s to load script POSIX file \"$app/Contents/Resources/Scripts/main.scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", "dng", {"/a b/one.arw", "/two.arw"})')
expected="'/usr/bin/true' merge --format dng '/a b/one.arw' '/two.arw' 2>&1"
if [ "$built" != "$expected" ]; then
    echo "SELF-TEST FAILED" >&2
    echo "  built:    $built" >&2
    echo "  expected: $expected" >&2
    exit 1
fi

echo "Built and registered. Document types: $extensions"
echo "Self-test passed."
