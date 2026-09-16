# Trichrome Merge droplet (macOS)

An AppleScript app that hands a selection of RAW frames to `trichrome merge`, so
a merge can be started from digiKam's (or Finder's) **Open With** menu instead of
a terminal.

It is a thin wrapper on purpose. It finds the tool, runs it, and shows you what
it said. Every rule about what makes a valid batch —
three frames per shot, all supported RAW, all the same sensor — stays in
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
script does. It re-signs the bundle afterwards (editing `Info.plist` invalidates
the signature `osacompile` applies, and recent macOS will not launch a bundle
whose signature no longer matches), registers it with Launch Services so the menu
picks it up without a logout, and self-tests the command builder.

Re-run it after editing the script.

## Use it

**From digiKam:** select the frames of a shoot — three, or any multiple of three
— then right-click → **Open With** → *Trichrome Merge*. If it is not listed,
choose *Other…* and pick it from `~/Applications`.

**From Finder:** the same, or drag the frames onto the app icon.

Selection order does not matter. trichrome sorts the batch by filename before
grouping into triplets, so whatever order digiKam hands the files over in, the
triplets come out the same. Merged files are written beside the first frame of
each triplet.

digiKam will not show the new files until the album is re-read (**F5**).

## Configure it

The properties at the top of `trichrome-merge.applescript`:

| Property | Default | |
| --- | --- | --- |
| `trichromePath` | `""` | absolute path to the tool; empty means auto-detect |
| `searchPaths` | Homebrew, `/usr/local`, MacPorts | where auto-detection looks first |
| `deleteOriginals` | `false` | **destructive** — see below |
| `revealResult` | `true` | offer a *Show in Finder* button on success |

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
