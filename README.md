# trichrome

Merge red/green/blue-light RAW triplets into 16-bit **linear** TIFFs, then
(optionally) delete the source RAWs.

You shoot one static scene three times — once under a pure red light, once
green, once blue. This tool takes every consecutive triplet of RAWs and builds
one full-colour image from them by keeping each frame's **own** colour channel
and throwing the other two away. The result is written as an archival linear
TIFF: the raw channel combination and nothing else, ready for whatever converter
you grade in.

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
trichrome merge ./shoot --out ./merged        # write them somewhere else
trichrome merge ./shoot --dry-run             # plan only, decode nothing
trichrome merge ./shoot --delete-originals    # destructive; asks first
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
| `-o, --out DIR` | write TIFFs here instead of beside each triplet's first frame |
| `--suffix S` | output name is `<first frame><S>.tif` (default `_RGB`) |
| `--order RGB` | which light each frame of a triplet was shot under, in filename order — `BGR` if you shot blue first |
| `--demosaic` / `--photosite` | see *Two ways to extract a channel* below (default `--demosaic`) |
| `--delete-originals` | permanently delete each triplet's RAWs once its TIFF verifies |
| `-y, --yes` | skip the confirmation prompt |
| `-n, --dry-run` | show what would happen; decode, write and delete nothing |

Files are ordered by **filename** (directory ignored, case-insensitive) and taken
three at a time. The batch must be a multiple of three and every file must be a
supported RAW, or nothing runs.

Supported RAW: `.cr3 .cr2 .nef .arw .dng .rw2 .orf .raf .srw .pef .3fr`.

## How the merge works

Each frame contributes exactly one channel — R from the red-light frame, G from
green, B from blue — scaled to 16-bit by `65535 / white_level`. No white balance,
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

A 16-bit RGB TIFF, deflate-compressed with Predictor 2 (lossless, universally
readable, ~1.3–2× smaller than plain deflate on 16-bit continuous-tone data), no
ICC profile, linear.

Its `Software` tag carries FreeCCR's merge marker
(`FreeCCR:3-way-RGB-merge-linear-v1`), so a file this tool writes opens in
FreeCCR as a normal image even while FreeCCR's own 3-way merge mode is on.

## Deleting the originals

`--delete-originals` is destructive and off by default. When it is on:

* A RAW is deleted **only** after its replacement TIFF exists on disk and reads
  back as a valid uint16 RGB image **of the expected size**.
* If a triplet fails, none of its sources are deleted — and neither are those
  same files if another triplet referenced them. A frame's only copy is never
  orphaned.
* A failed triplet leaves no partial TIFF behind.
* Interrupting deletes nothing and removes the TIFFs written so far.
* An existing file is **never** overwritten; a numeric suffix is added instead.
* Without a terminal (a script, a pipe) the confirmation prompt declines rather
  than guessing — pass `--yes` to mean it.

## As a library

```python
from trichrome import collect_raw_files, plan_jobs, run_jobs

jobs = plan_jobs(collect_raw_files(["./shoot"]), out_dir="./merged")
summary = run_jobs(jobs, demosaic=True, delete_originals=False)
print(len(summary.written), "merged;", len(summary.failures), "failed")
```

`merge_raw_channels(sources, demosaic=True, light_order="RGB")` returns
`(uint16 HxWx3 array, (H, W))` if you just want the pixels.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The decode is monkeypatched, so the suite needs no RAW files and runs in about a
second. It covers the pure merge maths (CFA phase slicing, white-level scaling,
light order) and every deletion-safety rule above.
