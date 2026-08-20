"""
The linear ICC profile stamped into every merged TIFF.

A merged frame is scene-linear: `combine_channels` normalises each plane and
stops there, with no gamma and no tone curve. Nothing in a bare TIFF says so, so
every colour-managed viewer falls back to assuming sRGB and displays the linear
numbers through an sRGB decode — which reads about 1.4 stops dark at the
midtones and nearly 3 in the shadows. The signal is all there; the file simply
never claimed what it was.

This module builds the smallest profile that makes the claim: an ICC v2.1
matrix/TRC RGB display profile whose three tone curves are the identity.
Embedding it changes NO pixel data — same bytes in the strip, one extra tag —
and it is what lets a viewer show the file correctly on open.

What the profile does and does not know
---------------------------------------
* The tone curve is EXACT. The data really is linear, and `curv` with a count of
  zero is the ICC spelling of "identity" — not a gamma of 1.0 approximated by a
  sampled table.
* The primaries are a CONVENTION, not a measurement. A merged frame is
  camera-native RGB (libraw decodes with `output_color=raw`), so its true
  primaries are the sensor's, and a trichrome merge is not colorimetric anyway:
  each channel came from a separate exposure under its own narrow-band light, at
  whatever relative brightness the lights happened to have. No matrix profile can
  describe that honestly. An ICC matrix/TRC profile has to name primaries, so
  this one names sRGB's (Rec.709) — the ordinary scene-linear working-space
  assumption, and the one least likely to send a converter through a bogus
  colorimetric transform.

So: trust the curve, treat the primaries as a placeholder, and let your converter
apply its own camera profile as it always did. `--no-icc` writes the file with no
profile at all if you would rather it made no claim.

The profile is built here rather than shipped as a binary blob so that every
number in it is visible and checkable, and so the package keeps its four
dependencies.
"""
import struct
from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

# What the profile calls itself, in a viewer's profile list. Names the curve
# (the part that is exact) and the primaries (the part that is assumed).
PROFILE_DESCRIPTION = "Trichrome linear RGB (gamma 1.0, sRGB primaries)"
PROFILE_COPYRIGHT = "No copyright, public domain."

# sRGB / Rec.709 primaries and D65 white, as CIE xy. See the module docstring on
# why these and not the sensor's own.
SRGB_PRIMARIES_XY = ((0.6400, 0.3300), (0.3000, 0.6000), (0.1500, 0.0600))
D65_XY = (0.3127, 0.3290)

# The ICC profile connection space is ALWAYS D50, so the colorants and the white
# point below are Bradford-adapted from D65 to it.
D50_XY = (0.34567, 0.35850)

# Bradford cone response matrix, the adaptation ICC profiles conventionally use.
_BRADFORD = ((0.8951, 0.2664, -0.1614),
             (-0.7502, 1.7135, 0.0367),
             (0.0389, -0.0685, 1.0296))


def _xyz_from_xy(x: float, y: float) -> Tuple[float, float, float]:
    """The XYZ of a chromaticity at unit luminance (Y = 1)."""
    return (x / y, 1.0, (1.0 - x - y) / y)


def _mat_mul(a: Sequence[Sequence[float]],
             b: Sequence[Sequence[float]]) -> List[List[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(len(b)))
             for j in range(len(b[0]))] for i in range(len(a))]


def _mat_vec(m: Sequence[Sequence[float]],
             v: Sequence[float]) -> List[float]:
    return [sum(m[i][k] * v[k] for k in range(len(v))) for i in range(len(m))]


def _inverse3(m: Sequence[Sequence[float]]) -> List[List[float]]:
    """Inverse of a 3x3 by cofactors. Three-by-three only, so the closed form is
    shorter and clearer than any general routine — and numpy is not imported
    here, keeping this module usable on its own."""
    (a, b, c), (d, e, f), (g, h, i) = m
    cof = ((e * i - f * h, c * h - b * i, b * f - c * e),
           (f * g - d * i, a * i - c * g, c * d - a * f),
           (d * h - e * g, b * g - a * h, a * e - b * d))
    det = a * cof[0][0] + b * cof[1][0] + c * cof[2][0]
    if det == 0:
        raise ValueError("singular matrix")
    return [[cof[r][k] / det for k in range(3)] for r in range(3)]


def rgb_to_xyz_matrix(primaries_xy: Sequence[Tuple[float, float]],
                      white_xy: Tuple[float, float]) -> List[List[float]]:
    """The 3x3 taking linear RGB to XYZ for the given primaries and white point:
    the primaries as columns, each scaled so that RGB (1, 1, 1) lands exactly on
    the white point."""
    cols = [_xyz_from_xy(x, y) for x, y in primaries_xy]
    m = [[cols[j][i] for j in range(3)] for i in range(3)]
    scale = _mat_vec(_inverse3(m), _xyz_from_xy(*white_xy))
    return [[m[i][j] * scale[j] for j in range(3)] for i in range(3)]


def bradford_adaptation(src_xy: Tuple[float, float],
                        dst_xy: Tuple[float, float]) -> List[List[float]]:
    """The 3x3 chromatic adaptation from one white point to another: convert to
    Bradford cone space, scale each cone response by the ratio of the two whites,
    convert back. This is the `chad` tag's matrix."""
    src = _mat_vec(_BRADFORD, _xyz_from_xy(*src_xy))
    dst = _mat_vec(_BRADFORD, _xyz_from_xy(*dst_xy))
    ratio = [[dst[i] / src[i] if i == j else 0.0 for j in range(3)]
             for i in range(3)]
    return _mat_mul(_inverse3(_BRADFORD), _mat_mul(ratio, _BRADFORD))


def _s15f16(value: float) -> int:
    """One s15Fixed16Number: the ICC fixed-point encoding, 16 fractional bits."""
    return int(round(value * 65536.0))


def _pack_s15f16(values: Sequence[float]) -> bytes:
    return struct.pack(f">{len(values)}i", *(_s15f16(v) for v in values))


def _xyz_tag(xyz: Sequence[float]) -> bytes:
    """XYZType: signature, reserved, then the three components."""
    return b"XYZ " + b"\0\0\0\0" + _pack_s15f16(xyz)


def _identity_curve_tag() -> bytes:
    """curveType with a count of ZERO, which the ICC spec defines as the identity
    — the profile's whole point. Not a one-entry gamma of 1.0, and not a sampled
    ramp: an exact statement that the data is linear."""
    return b"curv" + b"\0\0\0\0" + struct.pack(">I", 0)


def _text_tag(text: str) -> bytes:
    """textType (ICC v2): signature, reserved, NUL-terminated ASCII."""
    return b"text" + b"\0\0\0\0" + text.encode("ascii") + b"\0"


def _text_description_tag(text: str) -> bytes:
    """textDescriptionType, which ICC v2 requires for the description tag: the
    ASCII form, then empty Unicode and ScriptCode forms. The ScriptCode field is
    a fixed 67 bytes whether or not it holds anything."""
    ascii_bytes = text.encode("ascii") + b"\0"
    return (b"desc" + b"\0\0\0\0"
            + struct.pack(">I", len(ascii_bytes)) + ascii_bytes
            + struct.pack(">II", 0, 0)              # Unicode language, count
            + struct.pack(">HB", 0, 0) + b"\0" * 67)  # ScriptCode


def _sf32_tag(values: Sequence[float]) -> bytes:
    """s15Fixed16ArrayType, used for the chromatic adaptation matrix."""
    return b"sf32" + b"\0\0\0\0" + _pack_s15f16(values)


def _assemble(tags: Dict[bytes, bytes]) -> bytes:
    """Header + tag table + tag data, with every tag body starting on a 4-byte
    boundary. Identical bodies (the three TRCs) are stored once and pointed at
    twice, which is both legal and what real profiles do.

    The tag table records each body's TRUE length, not its padded one."""
    header_size = 128
    table_size = 4 + 12 * len(tags)
    offset = header_size + table_size
    table, body, seen = b"", b"", {}
    for sig, data in tags.items():
        if data in seen:
            at = seen[data]
        else:
            at = offset + len(body)
            seen[data] = at
            body += data + b"\0" * (-len(data) % 4)
        table += sig + struct.pack(">II", at, len(data))
    table = struct.pack(">I", len(tags)) + table

    header = (
        struct.pack(">I", header_size + table_size + len(body))  # profile size
        + b"\0\0\0\0"                       # preferred CMM: none
        + struct.pack(">I", 0x02100000)     # ICC version 2.1
        + b"mntr" + b"RGB " + b"XYZ "       # display device, RGB data, XYZ PCS
        + b"\0" * 12                        # creation date/time: not recorded
        + b"acsp"
        + b"\0\0\0\0"                       # primary platform: none
        + struct.pack(">III", 0, 0, 0)      # flags, manufacturer, model
        + b"\0" * 8                         # device attributes
        + struct.pack(">I", 0)              # rendering intent: perceptual
        + _pack_s15f16(_xyz_from_xy(*D50_XY))   # PCS illuminant, always D50
        + b"\0\0\0\0"                       # profile creator
        + b"\0" * 16                        # profile ID: not computed (v2)
        + b"\0" * 28                        # reserved
    )
    assert len(header) == header_size, len(header)
    return header + table + body


@lru_cache(maxsize=1)
def linear_rgb_profile() -> bytes:
    """The embedded profile: linear TRCs, sRGB primaries, D50-adapted.

    Cached — it is the same few hundred bytes for every file in a batch."""
    to_xyz_d65 = rgb_to_xyz_matrix(SRGB_PRIMARIES_XY, D65_XY)
    chad = bradford_adaptation(D65_XY, D50_XY)
    to_xyz_d50 = _mat_mul(chad, to_xyz_d65)
    # A colorant tag is one COLUMN of the matrix: where that primary lands in
    # the PCS.
    colorant = [[to_xyz_d50[row][col] for row in range(3)] for col in range(3)]
    curve = _identity_curve_tag()
    return _assemble({
        b"desc": _text_description_tag(PROFILE_DESCRIPTION),
        b"wtpt": _xyz_tag(_xyz_from_xy(*D50_XY)),
        b"chad": _sf32_tag([v for row in chad for v in row]),
        b"rXYZ": _xyz_tag(colorant[0]),
        b"gXYZ": _xyz_tag(colorant[1]),
        b"bXYZ": _xyz_tag(colorant[2]),
        b"rTRC": curve,
        b"gTRC": curve,
        b"bTRC": curve,
        b"cprt": _text_tag(PROFILE_COPYRIGHT),
    })
