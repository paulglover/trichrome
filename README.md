# trichrome

[![CI](https://github.com/paulglover/trichrome/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/paulglover/trichrome/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulglover/trichrome?label=release)](https://github.com/paulglover/trichrome/releases)

Merge red/green/blue-light RAW triplets into 16-bit **linear DNGs**, then
(optionally) delete the source RAWs.

You shoot one static scene three times — once under a pure red light, once
green, once blue. This tool takes every consecutive triplet of RAWs and builds
one full-colour image from them by keeping each frame's **own** colour channel
and throwing the other two away. The result is the raw channel combination and
nothing else, written as a linear DNG that your converter opens through its
**RAW** pipeline and that carries the shoot's own camera metadata: date, body,
lens, exposure.

Extracted from [FreeCCR](https://github.com/paulglover/FreeCCR)'s 3-way RGB
merge, stripped of the GUI, catalog, crop and colour pipeline. Standalone — it
is not intended to merge back.

## Install

```bash
pip install -e .            # needs numpy, rawpy, tifffile
```

Python 3.9+.

## Use

```bash
trichrome list ./shoot                        # show the triplet grouping first
trichrome merge ./shoot                       # write DNGs, keep the RAWs
trichrome merge ./shoot --out ./merged        # write them somewhere else
trichrome merge ./shoot --dry-run             # plan only, decode nothing
trichrome merge ./shoot --delete-originals    # destructive; no prompt
```

```
$ trichrome merge ./shoot
2 triplet(s) · light order RGB · demosaic (full resolution) · linear DNG
[1/2] img001.arw + img002.arw + img003.arw  ->  img001_RGB.dng
[2/2] img004.arw + img005.arw + img006.arw  ->  img004_RGB.dng

wrote /shoot/img001_RGB.dng  (6024x4024, uint16)
wrote /shoot/img004_RGB.dng  (6024x4024, uint16)

2 merged, 0 failed
```

### Options

| Flag | Meaning |
| --- | --- |
| `-r, --recursive` | descend into subfolders of an input folder |
| `-o, --out DIR` | write the merged files here instead of beside each triplet's first frame |
| `--suffix S` | output name is `<first frame><S>.dng` (default `_RGB`) |
| `--order RGB` | which light each frame of a triplet was shot under, in filename order — `BGR` if you shot blue first |
| `--demosaic` / `--photosite` | see *Two ways to extract a channel* below (default `--demosaic`) |
| `--delete-originals` | permanently delete each triplet's RAWs once its merged file verifies |
| `-n, --dry-run` | show what would happen; decode, write and delete nothing |

Files are ordered by **filename** (directory ignored, case-insensitive) and taken
three at a time. The batch must be a multiple of three and every file must be a
supported RAW, or nothing runs.

Supported RAW: `.cr3 .cr2 .nef .arw .dng .rw2 .orf .raf .srw .pef .3fr`.

## How the merge works

Each frame contributes exactly one channel — R from the red-light frame, G from
green, B from blue. Each is black-subtracted and then normalised to 16-bit by
`65535 / (white_level - black_level)` — the sensor's usable range, read from that
frame's own metadata, so a camera with a large pedestal is not left dark and
frames with differing pedestals do not drift apart into a cast. No white balance,
no colour matrix, no gamma, no tone curve, no inversion. The output is
camera-native linear RGB.

**Bayer (RGGB) sensors** — two ways to extract a channel:

* `--demosaic` (default): a LINEAR (bilinear) demosaic at **full sensor
  resolution**, then take this frame's channel. Bilinear interpolates each colour
  plane only from its **own** photosites, so there is still no inter-channel
  crosstalk.
* `--photosite`: no demosaic at all. The RAW mosaic is read directly and only
  this colour's photosites are taken, at **half sensor resolution** (one site per
  2×2 quad — that is this mode's full resolution). R and B use their single site
  per quad; green averages its two same-colour sites, which keeps the green SNR.
  Black levels are subtracted per site.

**Monochrome sensors** have no CFA, so there is nothing to demosaic: the whole
grayscale frame *is* that frame's channel, at full resolution. Both modes decode
identically. A monochrome sensor is in fact the ideal trichrome sensor — no
wasted photosites, full resolution, zero crosstalk by construction.

X-Trans and 4-colour (CYGM/RGBE) sensors are rejected. All three frames of a
triplet must be the same sensor type.

## The output file

One container: a **linear DNG** (`PhotometricInterpretation = 34892`, LinearRaw,
three samples per pixel). It holds the merge exactly as it came out — 16-bit
linear RGB at full resolution, no orientation applied, no inversion, no
adjustment — and everything else in it is metadata saying what those numbers are
and where they came from.

It carries FreeCCR's merge marker (`FreeCCR:3-way-RGB-merge-linear-v1`) in the
`Software` tag, so a file this tool writes opens in FreeCCR as a normal image
even while FreeCCR's own 3-way merge mode is on.

### Why a RAW container and not a rendered one

A TIFF or a PNG is an *already-rendered* image as far as Lightroom, ACR, Capture
One and darktable are concerned: no camera profile, no raw white balance, no
highlight reconstruction, and exposure applied after the tone curve rather than
before it.

A DNG is ingested through the **RAW pipeline** instead. That gets you white
balance as a Kelvin/tint pair — the usual way to neutralise a negative's orange
mask — and exposure in stops ahead of the curve. For a scan heading into a
negative conversion, that is the difference between grading with the raw
controls and grading without them.

It also lets the file say what it is without a workaround. The merge is
scene-linear data bounded by a black and a white level, and DNG has tags for
exactly that: `BlackLevel` is 0 and `WhiteLevel` is 65535, which is what the
merge already normalised to. A rendered format has nowhere to put that claim, so
a colour-managed viewer assumes sRGB and decodes the linear numbers through an
sRGB curve — which shows a perfectly good merge about **1.4 stops dark at the
midtones** and nearly 3 in the shadows. (Tagging such a file with a linear ICC
profile is the usual workaround for that; DNG ignores embedded ICC profiles
entirely, and needs none.)

The file is laid out as the spec prescribes: a small sRGB-encoded thumbnail in
IFD0 — a preview only, and the one place in this tool where a gamma is applied —
with the full-resolution linear image in a SubIFD.

#### What it is forced to claim

DNG makes the colour spec **mandatory**, and a reader will act on it — this is
the one part of the file that is fabricated *and* moves pixels.

There is no honest answer available: a merge is not colorimetric, because each
channel came from a separate exposure under its own narrow-band light, at
whatever relative brightness those lights happened to have. No set of primaries
describes that. So the file states a convention, and states it consistently:

* the merge is treated as linear RGB on sRGB/Rec.709 primaries, written as
  `ColorMatrix1` under a D65 calibration illuminant — the white those primaries
  are defined against — and `ForwardMatrix1` onto D50, the white the DNG spec
  fixes for that tag. The two are not inverses of each other, and a file where
  they are renders lighter with red and green clipped in any converter that
  derives its own white balance from the matrix instead of reading
  `AsShotNeutral`;
* `AsShotNeutral` is (1, 1, 1) — not a guess but a fact, since the merge applies
  no white balance, so its neutral is equal channels by construction;
* `UniqueCameraModel` is `Trichrome 3-way RGB merge`, a name no profile database
  knows, so a reader goes to the embedded matrices rather than to some real
  camera's profile;
* `ProfileToneCurve` is the identity, (0, 0) -> (1, 1). A profile that states no
  curve is rendered through the converter's own default S-curve — the remaining
  way a merge can open lighter than its own numbers — so it is there to leave
  nothing for a default to fill;
* `DefaultBlackRender` is `None`. Left at `Auto`, a converter subtracts its own
  estimated black point — but this file's black is known exactly and stated as
  `BlackLevel = 0`, and a negative's darkest values sit well above it on mask
  density alone, so an automatic black point grades the mask out by an amount
  that depends on the image.

Those last two decline both of the default renderings a converter applies before
you have touched anything. They are as far as a file can go: a converter's own
process-version baseline is still its own.

Treat the primaries as a placeholder and grade from there.

#### What it carries from the camera

A converter is not just a viewer: it sorts, filters and groups by capture time,
body and lens, and a file with none of that lands outside every collection it
belongs to. That information exists only in the source RAWs — and
`--delete-originals` is what destroys it.

So a DNG carries it. From the triplet's **first** frame (the one the merged file
is already named after, and the one whose name goes into `OriginalRawFileName`),
trichrome copies:

* the whole **EXIF IFD**, tag for tag — shutter, aperture, ISO, metering,
  focal length, lens, the date and time, and whatever else the body recorded;
* the **GPS IFD**;
* `Make`, `Model`, `DateTime`, `Artist`, `Copyright` and `Orientation`;
* `CameraSerialNumber`, `LensInfo` and `OriginalRawFileName`, the DNG spellings
  a converter looks for in IFD0.

Picking one of the three frames is not a compromise: the same body and lens shot
all three, seconds apart, at the same settings. It is one answer written three
times.

Four things are deliberately **not** carried:

* **The maker note.** Most vendors store its internal offsets relative to the
  start of the file it was written in, so the block only means anything where it
  was written. Everything standardised — including `LensModel` and
  `LensSpecification` on any body of the last fifteen years — is in the EXIF IFD
  proper and survives.
* **XMP.** It records *edits* — crop, white balance, develop settings keyed to
  the source's raw pipeline — which describe a different image than the merge.
* **`PixelXDimension` / `PixelYDimension`**, which are restated for the merged
  image rather than copied; a `--photosite` merge is half the source's size.
* **`UniqueCameraModel`**, which stays `Trichrome 3-way RGB merge`. This is the
  point of the split: the camera tags say what *took* the frames, while
  `UniqueCameraModel` says what the *file* is, so a converter still resolves its
  profile to the matrices above rather than to a profile for the body in `Make`.

`Orientation` is copied because it is true of the merge too: the merge is
unrotated sensor data, which is exactly what the source's tag is a claim about,
so a sideways-mounted body's frames come up the right way.

All of this is read in-process — a TIFF-structured raw (`.arw .nef .cr2 .dng
.orf .pef .srw .rw2 .3fr`), Canon's `CMT` boxes in a `.cr3`, or the `APP1`
segment of the JPEG inside a `.raf` — with no exiftool and no extra dependency.
It is also the reason a merged file is still worth having after the RAWs are
gone, rather than an anonymous grid of pixels.
If a source's metadata cannot be read, the merge is still written and verified
and the run says so:

```
WARNING img001_RGB.dng: no camera metadata copied from img001.arw: …
```

The merge is the part that cannot be reconstructed, and no metadata problem is
worth losing it over. Worth reading before you rely on `--delete-originals`,
though: after it, the warning is about data that no longer exists anywhere.

#### Why it is uncompressed

DNG's lossless choices are uncompressed and lossless JPEG. ZIP/deflate is not
among them for 16-bit integer data; libraw rejects such a file outright. So the
file is exactly `width × height × 6` bytes — 145 MB for a 6024×4024 frame,
whatever the image. If that matters, Adobe DNG Converter will losslessly
recompress one, typically to around half.

Because `.dng` is itself a supported RAW extension, the tool writes its own
input format, and a merged file left beside its sources would otherwise be
picked up as an *input* on the next run. It isn't: folder scans skip files
carrying the merge marker, so running the same command twice over a folder is
safe — the second run simply finds nothing to do.

## Deleting the originals

`--delete-originals` is destructive and off by default. When it is on:

* A RAW is deleted **only** after its replacement exists on disk and reads back
  as a valid uint16 RGB image **of the expected size** (for a DNG that means the
  LinearRaw image itself, not the thumbnail — a file whose preview survived and
  whose data did not must fail).
* If a triplet fails, none of its sources are deleted — and neither are those
  same files if another triplet referenced them. A frame's only copy is never
  orphaned.
* A failed triplet leaves no partial file behind.
* Interrupting deletes nothing and removes the files written so far.
* An existing file is **never** overwritten; a numeric suffix is added instead.
* There is no confirmation prompt: passing the flag is the confirmation. Use
  `--dry-run` first to see exactly which files a run would delete.

The capture metadata survives the deletion (above), so what is left is still a
photograph with a date, a body and a lens on it — but read any `WARNING` lines
first, since they are about metadata that will not exist anywhere else
afterwards.

## From digiKam, on macOS

[`contrib/macos/`](contrib/macos/) has an AppleScript droplet and a build script
that turn this tool into an app you can reach from digiKam's (or Finder's) **Open
With** menu: select the frames of a shoot and open them with it.

```bash
cd contrib/macos && ./build-app.sh      # -> ~/Applications/Trichrome Merge.app
```

Selection order does not matter — trichrome sorts by filename before grouping.
See [contrib/macos/README.md](contrib/macos/README.md) for the properties it
takes and why *Open With* rather than digiKam's batch queue.

## As a library

```python
from trichrome import collect_raw_files, plan_jobs, run_jobs

jobs = plan_jobs(collect_raw_files(["./shoot"]), out_dir="./merged")
summary = run_jobs(jobs, demosaic=True, delete_originals=False)
print(len(summary.written), "merged;", len(summary.failures), "failed")
```

`plan_jobs` settles the output path — including the `.dng` extension and any
numeric suffix needed to avoid an existing file — and `run_jobs` writes what the
plan says.

`write_linear_dng(path, merged, source=first_raw)` is what carries the camera
metadata over, and returns `None` or a one-line warning; `run_jobs` does this
for you and puts the warning on the `JobResult`. `read_source_metadata(path)`
reads a RAW's EXIF on its own.

`merge_raw_channels(sources, demosaic=True, light_order="RGB")` returns
`(uint16 HxWx3 array, (H, W))` if you just want the pixels, and
`read_linear_dng(path)` reads the linear image back out of a written file.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The decode is monkeypatched, so the suite needs no RAW files and runs in about a
second. It covers the pure merge maths (CFA phase slicing, black/white-level
normalisation, light order), the colorimetry behind the two matrices, the DNG's
structure and the colour spec it is forced to state, the camera metadata it
carries over (read back with tifffile's own EXIF parser, out of hand-built TIFF,
CR3 and RAF sources in both byte orders), and every deletion-safety rule above.
The end-to-end tests build synthetic RAWs, run the real decode over them, and
hand the written file back to libraw to confirm it decodes to the merge that
went in. Where exiftool is on `PATH`, one test also hands it a merged DNG to
confirm an outside reader parses the metadata as an ordinary camera file's.

## Contributing

`main` takes no direct pushes; changes land through a pull request, and the
tracked `hooks/` directory needs `git config core.hooksPath hooks` once per
clone. See [CONTRIBUTING.md](CONTRIBUTING.md).
