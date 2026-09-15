# trichrome

[![CI](https://github.com/paulglover/trichrome/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/paulglover/trichrome/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/paulglover/trichrome?label=release)](https://github.com/paulglover/trichrome/releases)

Merge red/green/blue-light RAW triplets into 16-bit **linear** TIFFs or DNGs,
then (optionally) delete the source RAWs.

You shoot one static scene three times — once under a pure red light, once
green, once blue. This tool takes every consecutive triplet of RAWs and builds
one full-colour image from them by keeping each frame's **own** colour channel
and throwing the other two away. The result is the raw channel combination and
nothing else, written as an archival linear TIFF — or, with `--format dng`, as a
linear DNG that your converter opens through its **RAW** pipeline instead.

Extracted from [FreeCCR](https://github.com/paulglover/FreeCCR)'s 3-way RGB
merge, stripped of the GUI, catalog, crop and colour pipeline. Standalone — it
is not intended to merge back.

## Install

```bash
pip install -e .            # needs numpy, rawpy, tifffile, imagecodecs
```

Python 3.9+.

## Use

```bash
trichrome list ./shoot                        # show the triplet grouping first
trichrome merge ./shoot                       # write TIFFs, keep the RAWs
trichrome merge ./shoot --format dng          # linear DNGs instead
trichrome merge ./shoot --out ./merged        # write them somewhere else
trichrome merge ./shoot --despeck             # also remove dust, hairs, scratches
trichrome merge ./shoot --dry-run             # plan only, decode nothing
trichrome merge ./shoot --delete-originals    # destructive; no prompt
```

```
$ trichrome merge ./shoot
2 triplet(s) · light order RGB · demosaic (full resolution)
[1/2] img001.arw + img002.arw + img003.arw  ->  img001_RGB.tif
[2/2] img004.arw + img005.arw + img006.arw  ->  img004_RGB.tif

wrote /shoot/img001_RGB.tif  (6024x4024, uint16)
wrote /shoot/img004_RGB.tif  (6024x4024, uint16)

2 merged, 0 failed
```

### Options

| Flag | Meaning |
| --- | --- |
| `-r, --recursive` | descend into subfolders of an input folder |
| `-o, --out DIR` | write the merged files here instead of beside each triplet's first frame |
| `--format tiff\|dng` | container to write — see *The output file* below (default `tiff`) |
| `--suffix S` | output name is `<first frame><S>` plus the format's extension (default `_RGB`) |
| `--order RGB` | which light each frame of a triplet was shot under, in filename order — `BGR` if you shot blue first |
| `--demosaic` / `--photosite` | see *Two ways to extract a channel* below (default `--demosaic`) |
| `--no-icc` | write a strictly untagged TIFF, with no linear ICC profile (no effect on `--format dng`) |
| `--despeck` | detect and repair dust, hairs and fine scratches — see *Removing dust* below (off by default; the only option here that changes pixels) |
| `--despeck-threshold D` | minimum neutral density a defect must add before it is repaired (default `0.15`) |
| `--despeck-radius PX` | half-width of the structuring element: the largest defect that can be detected (default `4`) |
| `--despeck-neutrality F` | how equally a defect must attenuate R, G and B to count as dust, 0–1 (default `0.7`) |
| `--despeck-floor-percentile P` | frame percentile taken as the densest real film; nothing below it is repaired, which is what keeps specular highlights out. `0` disables (default `99.9`) |
| `--despeck-mask` | also write the defect mask beside each output, to check the detector by eye |
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

Two containers, one merge. The **pixels are identical either way** — the same
16-bit linear numbers, at the same resolution. What differs is how a converter
treats the file when you open it.

| | `--format tiff` (default) | `--format dng` |
| --- | --- | --- |
| Converter treats it as | a rendered image | a **RAW** file |
| Raw white balance (Kelvin/tint) | no | yes |
| Exposure applied | after the tone curve | before it, in stops |
| Compression | deflate + Predictor 2 | none (see below) |
| Size, 6024×4024 | whatever deflate manages | 145 MB, always |
| Says "I am linear" via | an ICC profile | its own `BlackLevel`/`WhiteLevel` tags |

Both carry FreeCCR's merge marker (`FreeCCR:3-way-RGB-merge-linear-v1`) in the
`Software` tag, so a file this tool writes opens in FreeCCR as a normal image
even while FreeCCR's own 3-way merge mode is on.

### The linear TIFF

A 16-bit RGB TIFF, deflate-compressed with Predictor 2 (lossless, universally
readable, ~1.3–2× smaller than plain deflate on 16-bit continuous-tone data),
linear, carrying a linear ICC profile. It is the archival form: compact, opens
anywhere, and claims nothing about colour that it cannot back up.

#### Why it carries a profile

The data is scene-linear. Nothing in a bare TIFF says so, so a colour-managed
viewer assumes sRGB and decodes the linear numbers through an sRGB curve — which
shows a perfectly good merge about **1.4 stops dark at the midtones** and nearly
3 in the shadows. A linear 0.18 midtone sits at 46/255 in the file and should
display around 118/255; untagged, it displays at 46.

So each TIFF gets a 568-byte ICC v2 profile whose three tone curves are the
identity. Be clear about what it does and does not claim:

* **The tone curve is exact.** The data really is linear, and `curv` with a count
  of zero is the ICC spelling of "identity" — not a gamma of 1.0 approximated by
  a sampled table.
* **The primaries are a convention, not a measurement.** A merge is camera-native
  RGB, and it is not colorimetric anyway: each channel came from a separate
  exposure under its own narrow-band light, at whatever relative brightness those
  lights happened to have. No matrix profile can describe that honestly. An ICC
  matrix/TRC profile has to name primaries, so this one names sRGB's — the
  ordinary scene-linear working-space assumption, and the one least likely to
  send a converter through a bogus colorimetric transform.

Trust the curve and treat the primaries as a placeholder.

The profile is metadata only: **the image data is byte-identical with or without
it.** `--no-icc` leaves it out if you want a strictly untagged file.

### The linear DNG

A TIFF is a *rendered* image as far as Lightroom, ACR, Capture One and darktable
are concerned: no raw white balance, no camera profile, no highlight
reconstruction, and exposure applied after the tone curve rather than before it.

`--format dng` writes the same pixels as a **linear DNG** instead
(`PhotometricInterpretation = 34892`, LinearRaw, three samples per pixel), which
those converters ingest through the RAW pipeline. That gets you white balance as
a Kelvin/tint pair — the usual way to neutralise a negative's orange mask — and
exposure in stops ahead of the curve. For a scan heading into a negative
conversion, that is the difference between grading with the raw controls and
grading without them.

It also lets the file say what it is without a workaround. The merge is
scene-linear data bounded by a black and a white level, and DNG has tags for
exactly that: `BlackLevel` is 0 and `WhiteLevel` is 65535, which is what the
merge already normalised to. (DNG ignores embedded ICC profiles, so `--no-icc`
does nothing here.)

The file is laid out as the spec prescribes: a small sRGB-encoded thumbnail in
IFD0 — a preview only, and the one place in this tool where a gamma is applied —
with the full-resolution linear image in a SubIFD.

#### What it is forced to claim

DNG makes the colour spec **mandatory**, and a reader will act on it. That is a
stronger claim than the TIFF's ICC profile, whose fabricated part (the primaries)
is inert while its exact part (the identity curve) is the whole point. Here the
fabricated part moves pixels.

There is no honest answer available, for the reason given above — a merge is not
colorimetric. So the DNG makes the **same** assumption the ICC profile does, so
that the two formats cannot contradict each other:

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
  way a DNG can open lighter than the TIFF of the same pixels — so it is there
  to leave nothing for a default to fill;
* `DefaultBlackRender` is `None`. Left at `Auto`, a converter subtracts its own
  estimated black point — but this file's black is known exactly and stated as
  `BlackLevel = 0`, and a negative's darkest values sit well above it on mask
  density alone, so an automatic black point grades the mask out by an amount
  that depends on the image.

Those last two decline both of the default renderings a converter applies before
you have touched anything. They are as far as a file can go: a converter's own
process-version baseline is still its own.

Treat the primaries as a placeholder exactly as with the TIFF, and grade from
there.

#### Why it is uncompressed

DNG's lossless choices are uncompressed and lossless JPEG. ZIP/deflate — what the
TIFF path uses — is not among them for 16-bit integer data; libraw rejects such a
file outright. So a DNG is exactly `width × height × 6` bytes, 145 MB for a
6024×4024 frame, where the equivalent TIFF is however far deflate gets on your
particular images. Expect the DNG to be the larger of the two, often by a good
margin. If that matters more to you than the raw pipeline does, `--format tiff`
is the default for a reason. (Adobe DNG Converter will losslessly recompress one
if you want both.)

Because `.dng` is itself a supported RAW extension, a merged DNG left beside its
sources would otherwise be picked up as an *input* on the next run. It isn't:
folder scans skip files carrying the merge marker, so running the same command
twice over a folder is safe — the second run simply finds nothing to do.

## Removing dust

`--despeck` finds dust, hairs and fine scratches on the merged **uninverted**
negative and repairs them before the file is written. It is off by default and
it is the only option in this tool that changes pixels rather than metadata.

```
$ trichrome merge ./shoot --despeck --despeck-mask
1 triplet(s) · light order RGB · demosaic (full resolution) · linear TIFF · despeck (threshold 0.15 D, radius 4 px)
[1/1] img001.arw + img002.arw + img003.arw  ->  img001_RGB.tif

wrote /shoot/img001_RGB.tif  (6024x4024, uint16)
  despeck: 298 defect(s), 0.505% of pixels  (41 thinned, 257 filled)
  mask:    /shoot/img001_RGB_mask.tif
```

It works on four ideas, and the module docstring in `src/trichrome/despeck.py`
goes through each in full:

* **Density, not linear.** A speck is a *multiplicative* attenuation, so in the
  linear data it costs thousands of ADU in a thin part of the negative and a few
  dozen in a dense one. In density, `D = -log10(value/white)`, it adds a roughly
  constant offset anywhere in the frame — so one threshold works everywhere.
* **A local baseline, to FIND a defect.** "Darker than the darkest exposed area"
  founders as a detector — on in-scene light sources, on the lens PSF blurring
  small specks, and on illumination falloff. A morphological opening removes
  anything narrower than its structuring element, and the top-hat
  `D - opening(D)` is the candidate.
* **An absolute density floor, to CONFIRM it.** The local measurement on its own
  cannot tell dust from a small bright **highlight** in the scene: both are
  small, both are neutral, both are locally much denser than their surroundings.
  What separates them is the level the top-hat throws away. A highlight is
  exposure, bounded by what the film can record; dust is an obstruction, denser
  than any exposed part of the negative. So nothing is repaired unless it clears
  `--despeck-floor-percentile` of the frame's own density.
* **Neutrality across the three channels.** This is what a trichrome merge gets
  for free. Dust attenuates R, G and B equally; real colour-negative detail does
  not, because the dye layers respond independently. It needs no white balance —
  the local residual cancels any per-channel gain.
* **Subtract where possible, fill only where necessary.** Under partial
  occlusion the picture is still there, just attenuated, so the measured density
  is subtracted and the real detail and grain survive. Only where attenuation
  destroyed the signal is a pixel replaced, by a diffusion fill with grain
  reinjected to match its surroundings.

Pixels outside the defect mask come back **bit-identical** — despeckling is
surgery, not a re-render.

### Cost

Detection sweeps the frame in horizontal strips and the repair works one defect
window at a time, so neither stage ever holds a frame-sized stack of float
planes. Measured on this machine, with ~0.4% of pixels defective:

| frame | time | peak RSS |
| --- | --- | --- |
| 24 MP (6024x4024) | 9 s | 1.4 GB |
| 48 MP (8000x6000) | 16 s | 2.2 GB |
| 80 MP (10000x8000) | 23 s | 2.9 GB |
| 102 MP (11648x8736) | 27 s | 3.5 GB |

Both scale linearly with pixel count. The peak includes the input frame and the
repaired copy; the merge that precedes it peaks lower, so despeckling sets the
high-water mark for the run. If you ever need it lower, `_STRIP_ROWS` in
`despeck.py` trades a little speed for a smaller detection working set.

### Tuning it

The defaults are conservative, and deliberately so: on a real 80MP scan carrying
no dust at all, a 0.15 D threshold found 211 "defects" — every one a catchlight
in the picture — while the current 0.5 D found none. With dust planted into that
same frame, the defaults caught 58 of 60 specks: everything down to 0.8 D of
attenuation, and four in five of the faintest at 0.5 D.

If real dust is being missed, in rough order of what to reach for:

* `--despeck-threshold 0.3` — the main sensitivity control. Lower catches
  fainter dust and starts risking scene detail.
* `--despeck-floor-percentile 0` — turns off the highlight protection. Worth it
  for faint dust on a **thin** part of a contrasty negative, which adds its
  density to a low base and may never clear the floor. Expect specular
  highlights to start being "repaired".
* `--despeck-radius` — raise it for dust larger than ~6 px across.

Always check `--despeck-mask` before trusting a loosened setting.

### When it does not work

* **B&W negatives.** The silver image is itself neutral, so the cross-channel
  test goes vacuous: it passes real detail as readily as dust and detection
  falls back on shape and absolute density. It does no harm, it just stops
  helping, and `--despeck-threshold` becomes the main knob that bites.
* **Faint dust on thin film.** Dust lying over a shadow area of the scene adds
  its density to a low base, and may not clear the floor. This is the deliberate
  direction to err — see *Tuning it* above.
* **Defects wider than `--despeck-radius`.** An opening only removes what is
  narrower than its element, so a big blob survives in its middle and the
  top-hat sees its rim alone. Repairing that rim would paint a dark ring around
  an untouched core — worse than doing nothing — so a second top-hat at four
  times the radius is run purely to *classify*: anything whose neighbourhood
  answers much larger at the coarse scale than the component itself is a rim,
  and is **left alone**. The run says so, and raising the radius fixes it:

  ```
  despeck: 12 defect(s), 0.031% of pixels  (3 thinned, 9 filled)
           2 too wide for the current --despeck-radius and left alone; raise it to catch them
  ```

* **Scratches that gouged emulsion away.** Those removed *dye*, so they are
  neither neutral nor denser than their surroundings, and this detector does not
  look for them at all.
* **Long scratches** are caught only while they stay thinner than the
  structuring element. A multiscale ridge filter would do better; see the TODO
  at the top of `despeck.py`.
* **A hair crossing an oversized blob** joins the two into one component, and
  the part of the rim the hair touches is repaired with it. The run still
  reports the blob as oversized; raising the radius is the fix.

Start with `--despeck-mask` and look at the mask before trusting the repair.

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

## From digiKam, on macOS

[`contrib/macos/`](contrib/macos/) has an AppleScript droplet and a build script
that turn this tool into an app you can reach from digiKam's (or Finder's) **Open
With** menu: select the frames of a shoot, open them with it, pick TIFF or DNG.

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

`plan_jobs(..., fmt="dng")` is the library spelling of `--format dng`. The format
is settled at plan time because it decides the extension, and so the output path;
each `Job` carries it, and `run_jobs` writes what the job says.

`run_jobs(..., despeck=DespeckOptions())` is the library spelling of
`--despeck`; `trichrome.despeck.despeck(rgb, DespeckOptions())` runs the pass on
an array you already have, returning `(cleaned, stats, mask)`.

`merge_raw_channels(sources, demosaic=True, light_order="RGB")` returns
`(uint16 HxWx3 array, (H, W))` if you just want the pixels. `run_jobs(...,
icc=False)` is the library spelling of `--no-icc`, `linear_rgb_profile()` hands
you the ICC profile bytes on their own, and `read_linear_dng(path)` reads the
linear image back out of a written DNG.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The decode is monkeypatched, so the suite needs no RAW files and runs in about a
second. It covers the pure merge maths (CFA phase slicing, black/white-level
normalisation, light order), the ICC profile field by field, the DNG's structure
and colour metadata, and every deletion-safety rule above. The end-to-end tests
build synthetic RAWs, run the real decode over them, and hand the written DNG
back to libraw to confirm it decodes to the merge that went in. Where Pillow is installed — it is in the `dev`
extra — the profile is also handed to littleCMS to confirm an independent colour
engine accepts it and applies the linear curve.

## Contributing

`main` takes no direct pushes; changes land through a pull request, and the
tracked `hooks/` directory needs `git config core.hooksPath hooks` once per
clone. See [CONTRIBUTING.md](CONTRIBUTING.md).
