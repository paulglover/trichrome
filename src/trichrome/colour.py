"""
The colorimetry the DNG's two colour matrices are built from.

A merged frame is camera-native linear RGB: `combine_channels` normalises each
plane by its own frame's usable range and stops there — no white balance, no
colour matrix, no gamma, no tone curve. DNG nonetheless makes a colour spec
MANDATORY, so dng.py has to state one, and this module holds the arithmetic it
states it with.

The primaries are a CONVENTION, not a measurement, and everything here should be
read in that light. A trichrome merge is not colorimetric: each channel came
from a separate exposure under its own narrow-band light, at whatever relative
brightness those lights happened to have, so no set of primaries can describe it
honestly. The file has to name some, so it names sRGB's (Rec.709) — the ordinary
scene-linear working-space assumption, and the one least likely to send a
converter through a bogus colorimetric transform. See dng.py on what the file
does with the result, and why its two matrices are deliberately not inverses.

The maths is plain Python, not numpy, so the assumption is visible and checkable
line by line and the module stands on its own.
"""
from typing import List, Sequence, Tuple

# sRGB / Rec.709 primaries and D65 white, as CIE xy. See the module docstring on
# why these and not the sensor's own.
SRGB_PRIMARIES_XY = ((0.6400, 0.3300), (0.3000, 0.6000), (0.1500, 0.0600))
D65_XY = (0.3127, 0.3290)

# D50, the white a DNG's ForwardMatrix1 is defined to land on: it feeds the
# profile connection space, which is always D50.
D50_XY = (0.34567, 0.35850)

# Bradford cone response matrix, the adaptation this kind of colour spec
# conventionally uses.
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
