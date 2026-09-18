# Trichrome Merge droplet (macOS)

An AppleScript app that hands the three RAW frames of one shot to `trichrome`,
so a merge can be started from digiKam's (or Finder's) **Open With** menu instead
of a terminal.

It is a thin wrapper on purpose. It checks only what it must know before asking
you anything — that exactly three files were selected — then asks for the film
ID, runs the tool, and shows you what it said. Every other rule — all supported
RAW, all the same sensor, a usable film ID, nothing overwritten — stays in
trichrome, which already reports those in language meant for a person.

## Build it

```bash
./build-app.sh                                  # -> ~/Applications/Trichrome Merge.app
./build-app.sh "/Applications/Trichrome Merge.app"
```

`osacompile` alone would produce an app that runs but that no file can be opened
*with*: appearing in **Open With** requires the bundle to declare the document
types it handles. That declaration — the RAW extensions trichrome supports, read
out of the installed package so the two cannot drift — is most of what the build
script does. It compiles the script as a **stay-open** applet (see *Why it stays
open* below), re-signs the bundle afterwards (editing `Info.plist` invalidates
the signature `osacompile` applies, and recent macOS will not launch a bundle
whose signature no longer matches), registers it with Launch Services so the menu
picks it up without a logout, and self-tests the command builder, the selection
and settings checks, and the gathering of a split selection.

Re-run it after editing the script.

## Use it

**From digiKam:** select the three frames of one shot, then right-click →
**Open With** → *Trichrome Merge*. If it is not listed, choose *Other…* and pick
it from `~/Applications`.

**From Finder:** the same, or drag the three frames onto the app icon.
Double-clicking the app asks you to choose them.

Anything other than exactly three files is refused with a message before
anything else happens. Otherwise one dialog asks for:

* **Film ID** — required; *Merge* stays disabled until one is typed. The merged
  file is `FILMID.dng` and the ID is written into its XMP `dc:identifier`. It is
  asked fresh every time.
* **Different output folder** — unticked, the merge is written beside the first
  frame; ticked, into the folder you choose. The tick and the folder are
  remembered between runs, in the app's own preferences
  (`net.trichrome.merge-droplet`), not in the script — an applet that saves its
  own properties rewrites its bundle and breaks its signature.

Selection order does not matter: trichrome takes the frames in filename order.
If `FILMID.dng` already exists, nothing is written and trichrome says so.

digiKam will not show the new file until the album is re-read (**F5**).

### Why it stays open

macOS does not always deliver a multi-file selection as one event: three files
can arrive as two deliveries a few milliseconds apart (two, then one). A droplet
that acted on each would see two files, then one, and refuse both. So the app
stays open, gathers deliveries until a second passes with nothing new, then acts
once on the whole selection and quits.

## Configure it

The properties at the top of `trichrome-merge.applescript`:

| Property | Default | |
| --- | --- | --- |
| `trichromePath` | `""` | absolute path to the tool; empty means auto-detect |
| `searchPaths` | Homebrew, `/usr/local`, MacPorts | where auto-detection looks first |
| `deleteOriginals` | `false` | **destructive** — see below |
| `revealResult` | `true` | offer a *Show in Finder* button on success |
| `idleSeconds`, `quietTicks` | `0.5`, `2` | how long a quiet spell ends the gathering of a selection |

Auto-detection matters more than it looks. AppleScript's `do shell script` runs
with a bare `PATH` of `/usr/bin:/bin:/usr/sbin:/sbin`, which is never where
trichrome is installed — so the script checks the usual package-manager prefixes
and then falls back to asking a **login** shell, which is what finds it inside a
venv, pyenv or conda environment.

### On `deleteOriginals`

Off by default, and when it is on the app confirms before running: a stray
double-click on a droplet should not be able to delete a shoot. trichrome's own
safety rules still apply underneath — nothing is deleted until its replacement
has been written and verified.

Note that digiKam's database will still hold entries for the deleted RAWs until
the album is re-read.

## Known rough edges

* **The app is unresponsive while a merge runs.** `do shell script` blocks, and a
  full-resolution triplet takes a while. It is working, not hung.
* **There is a one-second pause before the dialog**, while the app waits to be
  sure the whole selection has arrived.
* **First use of *Show in Finder* prompts for Automation permission.** That is
  macOS asking whether this app may talk to Finder; the bundle declares why.
* Gatekeeper does not quarantine it, because you built it locally rather than
  downloading it.

## Could digiKam just run trichrome itself?

Not cleanly, for one structural reason: digiKam's Batch Queue Manager has a user
script tool, but BQM processes **one item at a time** — each queued image is
handed to the script on its own. A trichrome merge is inherently a three-file
operation, so there is nothing for a per-item hook to group. That is what makes
*Open With* the right seam here: it is digiKam's one path that hands over a whole
selection at once.

I have not verified this against a running digiKam — check the *Open With* menu on
your build, and if your version routes it differently, the app still works as a
plain Finder droplet.
