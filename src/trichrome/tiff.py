"""
Linear TIFF output for a merged trichrome frame.

The merge result is written byte-for-byte as a 16-bit linear RGB TIFF: exactly
what `merge_raw_channels` produced, at full canonical resolution, with no
orientation, no inversion and no adjustments. It is an archival intermediate —
the raw channel combination, nothing else — meant to be opened in a converter
(FreeCCR, Lightroom, darktable …) for the negative conversion and grade.

The one thing it does carry is an ICC profile saying the data is LINEAR (see
icc.py). Without it every colour-managed viewer assumes sRGB and decodes the
linear numbers through an sRGB curve, which shows the file about 1.4 stops dark
at the midtones — the most common reason a correct merge looks wrong on opening.
The profile is metadata: the pixels written are identical either way, and
`icc=False` leaves it out for a strictly untagged file.

Compression is deflate with TIFF Predictor 2 (horizontal differencing), which is
fully lossless and universally readable (libtiff / OpenCV / tifffile) but ~1.3-2x
smaller on 16-bit continuous-tone data than plain deflate, which barely
compresses it.

The Software tag carries FREECCR_MERGE_TIFF_MARKER. FreeCCR recognises that
marker and re-opens such a file as a normal image even while its own 3-way merge
mode is on, instead of trying to treat it as a merge input — so files this tool
writes drop straight into a FreeCCR session.
"""
import os
from typing import Optional, Tuple

import numpy as np
import tifffile

from . import icc as icc_mod

# Kept BYTE-IDENTICAL to FreeCCR's marker (src/core/ccr_merge.py) so files this
# tool writes are recognised there. FreeCCR substring-matches the Software tag,
# so appending this tool's own identity after it is safe.
FREECCR_MERGE_TIFF_MARKER = "FreeCCR:3-way-RGB-merge-linear-v1"

# Extension given to every file this tool writes. Reading still accepts .tiff.
OUTPUT_EXTENSION = ".tif"


def software_tag(version: Optional[str] = None) -> str:
    """The Software tag value stamped into a written TIFF: FreeCCR's merge
    marker plus this tool's identity."""
    from . import __version__
    return f"{FREECCR_MERGE_TIFF_MARKER} (trichrome {version or __version__})"


def is_merge_tiff(path) -> bool:
    """True when `path` is a TIFF carrying the merge marker in its Software tag
    (written by this tool or by FreeCCR). Reads only the TIFF header; a non-TIFF,
    unreadable, or unmarked file returns False."""
    if os.path.splitext(str(path))[1].lower() not in (".tif", ".tiff"):
        return False
    try:
        with tifffile.TiffFile(os.path.normpath(str(path))) as tf:
            tag = tf.pages[0].tags.get("Software")
            value = tag.value if tag is not None else ""
        return isinstance(value, str) and FREECCR_MERGE_TIFF_MARKER in value
    except Exception:
        return False


def write_linear_tiff(path: str, merged: np.ndarray,
                      version: Optional[str] = None, icc: bool = True) -> None:
    """Write `merged` (H, W, 3 uint16 linear RGB) to `path` as a marked,
    losslessly compressed 16-bit TIFF. Raises IOError on failure.

    `icc=True` (the default) embeds the linear profile, so viewers stop decoding
    the data as sRGB and showing it dark. It affects the tags only — the image
    data written is byte-identical with it off."""
    if merged.dtype != np.uint16 or merged.ndim != 3 or merged.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) uint16 array, got "
                         f"shape={merged.shape} dtype={merged.dtype}")
    try:
        tifffile.imwrite(os.path.normpath(path), merged, photometric="rgb",
                         compression="deflate", predictor=True,
                         software=software_tag(version),
                         iccprofile=icc_mod.linear_rgb_profile() if icc else None)
    except Exception as e:
        raise IOError(f"failed to write {path}: {e}") from e


def embedded_icc_profile(path: str) -> Optional[bytes]:
    """The raw ICC profile bytes in a TIFF, or None when it carries no profile.
    Reads only the header."""
    with tifffile.TiffFile(os.path.normpath(path)) as tf:
        tag = tf.pages[0].tags.get("InterColorProfile")
        return bytes(tag.value) if tag is not None else None


def verify_linear_tiff(path: str,
                       expect_shape: Optional[Tuple[int, int]] = None) -> None:
    """Confirm a just-written linear TIFF is a real, non-empty uint16 RGB image
    BEFORE its source RAWs (the only copy of that data) are deleted. Raises
    IOError on any mismatch.

    `expect_shape`, when given, is the (H, W) the merge produced — checked so a
    truncated or mis-sized write can never pass for a good replacement."""
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        raise IOError(f"linear TIFF not written or empty: {path}")
    with tifffile.TiffFile(os.path.normpath(path)) as tf:
        series = tf.series[0]
        shape = tuple(series.shape)
        dtype = np.dtype(series.dtype)
    if (dtype != np.uint16 or len(shape) != 3 or shape[2] != 3
            or shape[0] <= 0 or shape[1] <= 0):
        raise IOError(f"linear TIFF failed verification "
                      f"(shape={shape}, dtype={dtype}): {path}")
    if expect_shape is not None and tuple(shape[:2]) != tuple(expect_shape):
        raise IOError(f"linear TIFF is {shape[:2]}, expected "
                      f"{tuple(expect_shape)}: {path}")


def unique_output_path(folder: str, stem: str) -> str:
    """`folder/stem.tif`, with a numeric suffix if that name is already taken —
    this tool NEVER overwrites an existing file."""
    out = os.path.join(folder, stem + OUTPUT_EXTENSION)
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem}_{n}{OUTPUT_EXTENSION}")
        n += 1
    return out
