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
# -s makes it a STAY-OPEN applet, which is what gives it an idle handler: the
# droplet gathers a selection that arrives in several deliveries before acting.
osacompile -s -o "$app" "$source_script"

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

# --- Self-tests: the logic, without needing a screen ---------------------- #
scpt="$app/Contents/Resources/Scripts/main.scpt"
fail=0

check() {   # label expected actual
    if [ "$3" != "$2" ]; then
        echo "SELF-TEST FAILED: $1" >&2
        echo "  got:      $3" >&2
        echo "  expected: $2" >&2
        fail=1
    fi
}

# The command builder: quoting, including a path with a space, and a film ID
# that would read as an option if it were not attached to its flag.
built=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", "S0123-10", "", {"/a b/one.arw", "/two.arw", "/three.arw"})')
check "command quoting" \
    "'/usr/bin/true' '--filmid=S0123-10' '/a b/one.arw' '/two.arw' '/three.arw' 2>&1" "$built"

built=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to buildCommand("/usr/bin/true", "-it'"'"'s", "/out dir", {"/one.arw"})')
check "film ID and output folder" \
    "'/usr/bin/true' '--filmid=-it'\''s' '--out=/out dir' '/one.arw' 2>&1" "$built"

# What the settings dialog refuses before it lets the merge run.
problems=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to set a to settingsProblem("", false, "")' \
    -e 'tell s to set b to settingsProblem("S0123-10", true, "")' \
    -e 'tell s to set c to settingsProblem("S0123-10", false, "")' \
    -e 'tell s to set d to settingsProblem("S0123-10", true, "/out")' \
    -e '((a is not "") as text) & "/" & ((b is not "") as text) & "/" & ((c is "") as text) & "/" & ((d is "") as text)')
check "blank film ID and missing folder are refused" \
    "true/true/true/true" "$problems"

selection=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to ((selectionProblem(1) is not "") as text) & "/" & ((selectionProblem(2) is not "") as text) & "/" & ((selectionProblem(3) is "") as text) & "/" & ((selectionProblem(4) is not "") as text) & "/" & ((selectionProblem(6) is not "") as text)')
check "anything but three files is refused before the dialog" \
    "true/true/true/true/true" "$selection"

trim=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to trimmed("  S0123-10 " & tab & linefeed)')
check "whitespace around a setting is trimmed" "S0123-10" "$trim"

# The coalescing: two deliveries, one batch of three, and an empty accumulator
# afterwards so the next selection starts clean.
batch=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to absorbItems({"b", "c"})' \
    -e 'tell s to absorbItems({"a"})' \
    -e 'tell s to set taken to takeBatch()' \
    -e 'tell s to ((count of taken) as text) & "/" & (absorbItems({}) as text)')
check "split deliveries are gathered into one batch" "3/0" "$batch"

# The one line of trichrome's output this parses.
written=$(osascript \
    -e "set s to load script POSIX file \"$scpt\"" \
    -e 'tell s to item 1 of writtenPathsFrom("S0123-10: …" & linefeed & linefeed & "wrote /a b/S0123-10.dng  (6024x4024, uint16)")')
check "the written path is read out of the output" "/a b/S0123-10.dng" "$written"

[ "$fail" -eq 0 ] || exit 1

echo "Built and registered. Document types: $extensions"
echo "Self-tests passed."
