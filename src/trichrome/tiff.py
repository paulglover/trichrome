"""
Linear TIFF output for a merged trichrome frame.

The merge result is written byte-for-byte as an UNTAGGED 16-bit linear RGB TIFF:
exactly what `merge_raw_channels` produced, at full canonical resolution, with no
orientation, no inversion, no colour management and no adjustments. It is an
archival intermediate — the raw channel combination, nothing else — meant to be
opened in a converter (FreeCCR, Lightroom, darktable …) for the negative
conversion and grade.

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

# Kept BYTE-IDENTICAL to FreeCCR's marker (src/core/ccr_merge.py) so files this
# tool writes are recognised there. FreeCCR substring-matches the Software tag,
# so appending this tool's own identity after it is safe.
FREECCR_MERGE_TIFF_MARKER = "FreeCCR:3-way-RGB-merge-linear-v1"


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
                      version: Optional[str] = None) -> None:
    """Write `merged` (H, W, 3 uint16 linear RGB) to `path` as a marked,
    losslessly compressed 16-bit TIFF. Raises IOError on failure."""
    if merged.dtype != np.uint16 or merged.ndim != 3 or merged.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) uint16 array, got "
                         f"shape={merged.shape} dtype={merged.dtype}")
    try:
        tifffile.imwrite(os.path.normpath(path), merged, photometric="rgb",
                         compression="deflate", predictor=True,
                         software=software_tag(version))
    except Exception as e:
        raise IOError(f"failed to write {path}: {e}") from e


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
    """`folder/stem.tiff`, with a numeric suffix if that name is already taken —
    this tool NEVER overwrites an existing file."""
    out = os.path.join(folder, stem + ".tiff")
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem}_{n}.tiff")
        n += 1
    return out
