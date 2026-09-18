"""
Linear DNG output for a merged trichrome frame.

What the file is
----------------
The merge, written exactly as `merge_raw_channels` produced it: 16-bit linear
RGB at full canonical resolution, with no orientation applied, no inversion and
no adjustment. Everything else in the file is metadata saying what those numbers
are, and where they came from.

Why a DNG and not a rendered image
----------------------------------
Every converter — Lightroom, ACR, Capture One, darktable — treats a TIFF or a
PNG as an ALREADY-RENDERED image: no camera profile, no raw white balance, no
highlight reconstruction, and exposure applied after the tone curve rather than
before it.

A linear DNG (`PhotometricInterpretation = 34892`, LinearRaw, three samples per
pixel) is ingested through the RAW pipeline instead, which is what a trichrome
scan actually wants: white balance as a Kelvin/tint pair — the usual way to
neutralise a negative's orange mask — and exposure in stops ahead of the curve.
For a scan heading into a negative conversion, that is the difference between
grading with the raw controls and grading without them.

It also lets the file say what it is without a workaround. The merge is
scene-linear data bounded by a black and a white level, and DNG has tags for
exactly that (`BlackLevel = 0`, `WhiteLevel = 65535`, which is what
combine_channels already normalises to) — where a rendered format has nowhere to
put the claim, and viewers fall back to assuming sRGB and showing the file about
1.4 stops dark. DNG ignores embedded ICC profiles entirely, and needs none.

What is honest in this file and what is a convention
----------------------------------------------------
DNG makes the colour spec MANDATORY: a file has to carry ColorMatrix1, and a
reader will act on it. This is the one part of the file that is fabricated AND
acted upon — the fabricated part moves pixels.

There is no honest answer available — a trichrome merge is not colorimetric, as
each channel came from a separate exposure under its own narrow-band light at
whatever relative brightness those lights had. So this module states the
convention colour.py documents:

* the merge is treated as linear RGB on sRGB/Rec.709 primaries,
* `AsShotNeutral` is (1, 1, 1) — which is not a guess but a fact: the merge
  applies no white balance, so its neutral is equal channels by construction.

`ForwardMatrix1` (camera -> XYZ D50) is written as well as `ColorMatrix1`
(XYZ -> camera), because a reader given both uses the forward matrix directly
instead of inverting and chromatically adapting the other one — fewer
assumptions applied on top of ours.

The two are NOT inverses, and must not be: the DNG spec defines them against
different white points. ColorMatrix1 is stated under the calibration illuminant,
which is D65 here because that is the white sRGB primaries are defined against
— so it is the unadapted XYZ -> RGB
matrix, and it takes D65's XYZ onto (1, 1, 1), agreeing with AsShotNeutral.
ForwardMatrix1's output is always D50 by definition (it feeds the profile
connection space), so it carries a Bradford adaptation and takes (1, 1, 1) onto
D50's XYZ.

Getting that split wrong is not cosmetic. A reader that honours AsShotNeutral
renders either version identically, but one that instead derives a daylight
white balance from ColorMatrix1 — libraw and dcraw do this by default, as does
any "camera reference" white balance preset — divides by that matrix's response
to D65. State the matrix under D50 and that response is (0.83, 1.02, 1.37),
so such a reader multiplies the merge by (1.66, 1.34, 1.00): two thirds of a
stop of extra red, four tenths of extra green, clipping red above 39,600 and
green above 48,900.

Treat the primaries as a placeholder and grade from there.

The tone curve
--------------
`ProfileToneCurve` is written as the identity, (0, 0) -> (1, 1).

Colour is not the only default a raw converter supplies. Given a profile with no
tone curve of its own, Adobe's SDK — and everything modelled on it — renders
through its default curve instead, a contrasty S built for camera sensor data.
That curve is tone applied to data that has no business being toned, and the
remaining reason a merge can open lighter than the numbers in it once the
matrices agree. Declaring the identity leaves the slot filled, so there is
no default for a converter to fall back on.

The curve is two control points, which is how the spec says "identity" — the
first sample must be (0, 0) and the last (1, 1), and a cubic spline through
nothing else is the straight line between them.

The black point
---------------
`DefaultBlackRender` is written as 1, None.

The other default a converter applies before you have touched anything. Left at
0, Auto, a raw converter subtracts its own estimate of a black point — a flare
correction, reasonable for a lens pointed at a scene and wrong for this file
twice over. The merge's black is already known exactly, and stated: each channel
was normalised by its own frame's usable range, so 0 IS black, which is what
`BlackLevel = 0` says a few tags along. And a trichrome negative's darkest values
sit nowhere near zero — the orange mask holds them up — so an automatic black
point has a large, entirely image-dependent amount to subtract, which is a grade
being made for you out of the mask density.

None says: the black in the file is the black. Together with the identity tone
curve, that is both of the converter's default renderings declined, which is as
far as a file can go — a converter's process-version baseline is still its own.

`DefaultBlackRender` is a DNG 1.4 tag and `DNGBackwardVersion` stays at 1.2,
deliberately: a 1.2 reader that has never heard of it skips it and renders as it
always would, which is a different default, not a failure to read the file. The
backward version is a claim about what a reader must UNDERSTAND to open the
file, and nothing here has moved that.

The camera's own metadata
-------------------------
The merge's pixels come from three RAWs, and so does everything a converter
needs to file it: when it was shot, on what body, through what lens, at what
exposure. `source=` hands this writer the triplet's FIRST frame and its EXIF
IFD is copied in whole — the tags that describe the exposure, not the
tags that describe the source's pixels. See exif.py for what is carried, what is
deliberately not, and why the camera tags do not disturb `UniqueCameraModel`.

The film ID
-----------
`identifier=` is written as the file's only XMP: a packet stating it as
`dc:identifier`, so a catalogue that has renamed or moved the file can still say
which film it is. The sources' own XMP is not carried (exif.py says why), so
there is nothing for this to be merged with.

It is best-effort by design. A source whose metadata cannot be read still gets
merged and still gets written; the writer returns a warning instead of raising,
because the pixels are the part that cannot be reconstructed and no metadata
problem is worth losing them over.

Compression
-----------
DNG's lossless choices are uncompressed and lossless JPEG. ZIP/deflate is not
among them for 16-bit integer data: libraw rejects such a file outright
("Corrupted data or unexpected EOF"), so this module writes uncompressed and a
file is exactly `width x height x 6` bytes. Adobe's DNG Converter will
losslessly recompress one if the size matters.

Layout
------
As the spec prescribes: IFD0 holds a small sRGB-encoded thumbnail (preview only —
it is the one place in this tool where a gamma is applied, and it touches no
image data), and the full-resolution linear image lives in a SubIFD. Readers
that only glance at IFD0 get a sensible preview; readers that want the data
follow the SubIFD.
"""
import os
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

import numpy as np
import tifffile

from . import colour as colour_mod
from . import exif as exif_mod

# Extension given to every file this tool writes.
OUTPUT_EXTENSION = ".dng"

# Kept BYTE-IDENTICAL to FreeCCR's marker (src/core/ccr_merge.py) so files this
# tool writes are recognised there: FreeCCR re-opens a file carrying it as a
# normal image even while its own 3-way merge mode is on, instead of trying to
# treat it as a merge input. FreeCCR substring-matches the Software tag, so
# appending this tool's own identity after it is safe.
FREECCR_MERGE_MARKER = "FreeCCR:3-way-RGB-merge-linear-v1"

# DNG tag numbers used below, named so the extratags lists stay readable.
_TAG_XMP = 700
_TAG_DNG_VERSION = 50706
_TAG_DNG_BACKWARD_VERSION = 50707
_TAG_UNIQUE_CAMERA_MODEL = 50708
_TAG_BLACK_LEVEL_REPEAT_DIM = 50713
_TAG_BLACK_LEVEL = 50714
_TAG_WHITE_LEVEL = 50717
_TAG_COLOR_MATRIX_1 = 50721
_TAG_AS_SHOT_NEUTRAL = 50728
_TAG_CALIBRATION_ILLUMINANT_1 = 50778
_TAG_PROFILE_TONE_CURVE = 50940
_TAG_FORWARD_MATRIX_1 = 50964
_TAG_DEFAULT_BLACK_RENDER = 51110

# PhotometricInterpretation for demosaiced, camera-native RGB.
PHOTOMETRIC_LINEAR_RAW = 34892

# CalibrationIlluminant code 21 is D65 — the white ColorMatrix1 is stated under,
# and the white sRGB primaries are defined against.
_ILLUMINANT_D65 = 21

# What the file calls the "camera" that produced it. Not a real camera, and
# deliberately not one: a name no camera profile database knows sends every
# reader to the embedded matrices, which is where the truth about this file is.
UNIQUE_CAMERA_MODEL = "Trichrome 3-way RGB merge"

# Denominator for the RATIONAL-encoded matrices: six decimal places, far finer
# than the matrices are meaningful to.
_RATIONAL_DEN = 1000000

# Longest edge of the embedded thumbnail, in pixels.
_THUMBNAIL_MAX_EDGE = 256

# ProfileToneCurve as (in, out) pairs of 32-bit floats: the identity. See the
# module docstring — this is here to DISPLACE the converter's default curve, not
# to shape anything.
IDENTITY_TONE_CURVE = (0.0, 0.0, 1.0, 1.0)

# DefaultBlackRender code 1 is None: the converter is asked not to subtract a
# black point of its own. Code 0, the default, is Auto.
_BLACK_RENDER_NONE = 1


def color_matrices() -> Tuple[List[List[float]], List[List[float]]]:
    """`(forward, color)`: ForwardMatrix1 (camera -> XYZ D50) and ColorMatrix1
    (XYZ D65 -> camera), for the sRGB-primaries convention this tool assumes for
    a merge (colour.py). See the module docstring on why a convention is all
    this can be.

    The pair is deliberately not an inverse pair. Each is stated against the
    white its tag is defined against, which is what makes the file consistent
    under BOTH ways a reader can find the merge's neutral: ColorMatrix1 takes the
    calibration illuminant's white onto (1, 1, 1), and ForwardMatrix1 takes
    (1, 1, 1) onto D50, the profile connection space's white."""
    to_xyz_d65 = np.array(colour_mod.rgb_to_xyz_matrix(
        colour_mod.SRGB_PRIMARIES_XY, colour_mod.D65_XY))
    chad = np.array(colour_mod.bradford_adaptation(colour_mod.D65_XY,
                                                   colour_mod.D50_XY))
    return (chad @ to_xyz_d65).tolist(), np.linalg.inv(to_xyz_d65).tolist()


def _rational(values) -> Tuple[int, ...]:
    """Flatten a matrix (or vector) into the (numerator, denominator, …) pairs a
    RATIONAL/SRATIONAL tag is written as."""
    out: List[int] = []
    for v in np.asarray(values, dtype=float).reshape(-1):
        out.extend((int(round(v * _RATIONAL_DEN)), _RATIONAL_DEN))
    return tuple(out)


def _thumbnail(merged: np.ndarray) -> np.ndarray:
    """A small 8-bit sRGB-encoded preview of `merged` for IFD0.

    This is the ONLY place in the tool where a transfer curve is applied, and it
    exists so file browsers and the converter's import grid show something
    recognisable instead of the dark linear data. It never touches the image in
    the SubIFD."""
    # Ceiling division: flooring would leave a step that still overshoots the
    # limit (800 // 256 = 3, and 800/3 is 267 pixels).
    longest = max(merged.shape[:2])
    step = max(1, -(-longest // _THUMBNAIL_MAX_EDGE))
    linear = merged[::step, ::step].astype(np.float32) / 65535.0
    encoded = np.where(linear <= 0.0031308, linear * 12.92,
                       1.055 * np.power(np.clip(linear, 0.0, None), 1 / 2.4)
                       - 0.055)
    return np.clip(encoded * 255.0 + 0.5, 0, 255).astype(np.uint8)


def software_tag(version: Optional[str] = None) -> str:
    """The Software tag value stamped into a written DNG: FreeCCR's merge marker
    plus this tool's identity."""
    from . import __version__
    return f"{FREECCR_MERGE_MARKER} (trichrome {version or __version__})"


def carries_merge_marker(path) -> bool:
    """True when `path` is a TIFF-structured file whose Software tag carries the
    merge marker — i.e. this tool or FreeCCR wrote it. Reads only the header; an
    unreadable or unmarked file returns False."""
    try:
        with tifffile.TiffFile(os.path.normpath(str(path))) as tf:
            tag = tf.pages[0].tags.get("Software")
            value = tag.value if tag is not None else ""
        return isinstance(value, str) and FREECCR_MERGE_MARKER in value
    except Exception:
        return False


def is_merge_dng(path) -> bool:
    """True when `path` is a DNG this tool (or FreeCCR) wrote, identified by the
    merge marker in its Software tag.

    It matters: `.dng` is itself a supported RAW extension, so without this check
    a merged DNG could be taken as a merge INPUT — and, with --delete-originals,
    deleted as one."""
    if os.path.splitext(str(path))[1].lower() != OUTPUT_EXTENSION:
        return False
    return carries_merge_marker(path)


def xmp_packet(identifier: str) -> bytes:
    """A minimal XMP packet stating `identifier` as dc:identifier — the film
    ID the merge was made under."""
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'   <dc:identifier>{xml_escape(identifier)}</dc:identifier>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    ).encode("utf-8")


def write_linear_dng(path: str, merged: np.ndarray,
                     version: Optional[str] = None,
                     source: Optional[str] = None,
                     identifier: Optional[str] = None) -> Optional[str]:
    """Write `merged` (H, W, 3 uint16 linear RGB) to `path` as an uncompressed
    linear DNG. Raises IOError on failure.

    The pixels written are exactly `merged`, unchanged. Everything else in the
    file is metadata saying what
    they are: linear, black at 0, white at 65535, neutral at (1, 1, 1), toned by
    nothing and blacked by nothing — and, from `source`, what took them.

    `identifier`, when given, is the film ID, written as XMP dc:identifier.

    `source` is the triplet's first frame, whose EXIF is copied in (exif.py).
    Returns None when that succeeded or was not asked for, and a one-line
    warning when it could not be done: the DNG is written and valid either way,
    because a metadata problem must not cost the merge."""
    if merged.dtype != np.uint16 or merged.ndim != 3 or merged.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) uint16 array, got "
                         f"shape={merged.shape} dtype={merged.dtype}")
    forward, color = color_matrices()
    ifd0_tags = [
        (_TAG_DNG_VERSION, 'B', 4, (1, 4, 0, 0), True),
        # The oldest reader that can make sense of this file: ForwardMatrix1
        # and ProfileToneCurve both arrived in DNG 1.2, and nothing here needs
        # anything newer.
        (_TAG_DNG_BACKWARD_VERSION, 'B', 4, (1, 2, 0, 0), True),
        (_TAG_UNIQUE_CAMERA_MODEL, 's', 0, UNIQUE_CAMERA_MODEL, True),
        (_TAG_COLOR_MATRIX_1, '2i', 9, _rational(color), True),
        (_TAG_FORWARD_MATRIX_1, '2i', 9, _rational(forward), True),
        (_TAG_CALIBRATION_ILLUMINANT_1, 'H', 1, _ILLUMINANT_D65, True),
        (_TAG_AS_SHOT_NEUTRAL, '2I', 3, _rational((1.0, 1.0, 1.0)), True),
        (_TAG_PROFILE_TONE_CURVE, 'f', len(IDENTITY_TONE_CURVE),
         IDENTITY_TONE_CURVE, True),
        (_TAG_DEFAULT_BLACK_RENDER, 'I', 1, _BLACK_RENDER_NONE, True),
    ]
    if identifier:
        packet = xmp_packet(identifier)
        ifd0_tags.append((_TAG_XMP, 'B', len(packet), packet, True))
    raw_tags = [
        # One black/white level per sample, with a 1x1 repeat pattern: the merge
        # has already subtracted the sensors' pedestals and normalised to the
        # full 16-bit range, so this is flatly true rather than a convention.
        (_TAG_BLACK_LEVEL_REPEAT_DIM, 'H', 2, (1, 1), False),
        (_TAG_BLACK_LEVEL, 'H', 3, (0, 0, 0), False),
        (_TAG_WHITE_LEVEL, 'I', 3, (65535, 65535, 65535), False),
    ]
    try:
        with tifffile.TiffWriter(os.path.normpath(path)) as tw:
            # metadata=None drops tifffile's own {"shape": …} ImageDescription:
            # it is a tifffile convention, meaningless to a DNG reader, and it
            # would otherwise sit in the slot the source's own description goes.
            tw.write(_thumbnail(merged), photometric="rgb", compression=None,
                     subfiletype=1, subifds=1, extratags=ifd0_tags,
                     metadata=None, software=software_tag(version))
            # planarconfig is stated rather than left to be inferred: 34892 is
            # not a photometric tifffile treats as having samples, so without
            # this the (H, W, 3) array is written as H pages of W x 3 grey
            # instead of one RGB image — and DNG requires chunky data anyway.
            tw.write(merged, photometric=PHOTOMETRIC_LINEAR_RAW,
                     planarconfig="contig", compression=None, subfiletype=0,
                     metadata=None, extratags=raw_tags)
    except Exception as e:
        raise IOError(f"failed to write {path}: {e}") from e
    if source is None:
        return None
    # The file on disk is already a complete, valid DNG; what follows only adds
    # to it, so anything that goes wrong here is reported, not raised.
    try:
        exif_mod.copy_into_dng(path, exif_mod.read_source_metadata(source),
                               pixel_size=merged.shape[:2])
    except Exception as e:
        return (f"no camera metadata copied from "
                f"{os.path.basename(source)}: {e}")
    return None


def _raw_page(tf: "tifffile.TiffFile"):
    """The SubIFD page holding the linear image, or None when the file has no
    such page (IFD0 is only the thumbnail)."""
    for page in tf.pages[0].pages or ():
        page = page.aspage() if hasattr(page, "aspage") else page
        photometric = page.tags.get("PhotometricInterpretation")
        if photometric is not None and photometric.value == PHOTOMETRIC_LINEAR_RAW:
            return page
    return None


def verify_linear_dng(path: str,
                      expect_shape: Optional[Tuple[int, int]] = None) -> None:
    """Confirm a just-written linear DNG is a real, non-empty uint16 LinearRaw
    RGB image BEFORE its source RAWs (the only copy of that data) are deleted.
    Raises IOError on any mismatch.

    `expect_shape`, when given, is the (H, W) the merge produced — checked so a
    truncated or mis-sized write can never pass for a good replacement. The
    thumbnail in IFD0 is deliberately NOT what gets checked: a file whose preview
    survived but whose image did not must fail."""
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        raise IOError(f"linear DNG not written or empty: {path}")
    with tifffile.TiffFile(os.path.normpath(path)) as tf:
        if _TAG_DNG_VERSION not in tf.pages[0].tags:
            raise IOError(f"file carries no DNGVersion tag: {path}")
        page = _raw_page(tf)
        if page is None:
            raise IOError(f"linear DNG has no LinearRaw image: {path}")
        shape = tuple(page.shape)
        dtype = np.dtype(page.dtype)
    if (dtype != np.uint16 or len(shape) != 3 or shape[2] != 3
            or shape[0] <= 0 or shape[1] <= 0):
        raise IOError(f"linear DNG failed verification "
                      f"(shape={shape}, dtype={dtype}): {path}")
    if expect_shape is not None and tuple(shape[:2]) != tuple(expect_shape):
        raise IOError(f"linear DNG is {shape[:2]}, expected "
                      f"{tuple(expect_shape)}: {path}")


def read_linear_dng(path: str) -> np.ndarray:
    """The (H, W, 3) uint16 linear image out of a DNG this tool wrote — the
    SubIFD, not the thumbnail. Mostly useful for checking a written file."""
    with tifffile.TiffFile(os.path.normpath(path)) as tf:
        page = _raw_page(tf)
        if page is None:
            raise IOError(f"linear DNG has no LinearRaw image: {path}")
        return page.asarray()
