"""
Tests for the linear ICC profile trichrome builds and embeds.

The profile is a hand-assembled binary, so these check it two ways: by taking it
apart field by field here (which runs everywhere), and by handing it to
littleCMS via PIL.ImageCms (which runs wherever Pillow is installed — it is in
the dev extra) to confirm a real colour engine accepts it and applies the linear
curve it promises.
"""
import struct

import pytest

from trichrome import icc

try:                                    # littleCMS, for the cross-check below
    from PIL import Image, ImageCms
except ImportError:                     # everything else still runs without it
    Image = ImageCms = None

needs_littlecms = pytest.mark.skipif(
    ImageCms is None, reason="Pillow (the dev extra) provides littleCMS")

PROFILE = icc.linear_rgb_profile()

# The tags an ICC v2 RGB matrix/TRC display profile is required to carry.
REQUIRED_TAGS = (b"desc", b"cprt", b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ",
                 b"rTRC", b"gTRC", b"bTRC")


def tag_table():
    """{signature: (offset, size)} read out of the profile's tag table."""
    count = struct.unpack(">I", PROFILE[128:132])[0]
    table = {}
    for i in range(count):
        sig, off, size = struct.unpack(">4sII", PROFILE[132 + 12 * i:
                                                        144 + 12 * i])
        table[sig] = (off, size)
    return table


def s15f16(raw):
    return struct.unpack(">i", raw)[0] / 65536.0


def xyz_of(sig):
    off, _size = tag_table()[sig]
    assert PROFILE[off:off + 4] == b"XYZ "
    return tuple(s15f16(PROFILE[off + 8 + 4 * i:off + 12 + 4 * i])
                 for i in range(3))


# --------------------------------------------------------------------------- #
# header
# --------------------------------------------------------------------------- #
def test_the_declared_size_matches_the_actual_length():
    assert struct.unpack(">I", PROFILE[:4])[0] == len(PROFILE)


def test_header_declares_a_v2_rgb_display_profile_in_the_xyz_pcs():
    assert struct.unpack(">I", PROFILE[8:12])[0] == 0x02100000   # ICC 2.1
    assert PROFILE[12:16] == b"mntr"        # display device class
    assert PROFILE[16:20] == b"RGB "        # data colour space
    assert PROFILE[20:24] == b"XYZ "        # profile connection space
    assert PROFILE[36:40] == b"acsp"        # the file-signature magic


def test_the_header_pcs_illuminant_is_d50():
    illuminant = tuple(s15f16(PROFILE[68 + 4 * i:72 + 4 * i]) for i in range(3))
    assert illuminant == pytest.approx((0.9642, 1.0, 0.8249), abs=5e-4)


# --------------------------------------------------------------------------- #
# tag table
# --------------------------------------------------------------------------- #
def test_every_required_tag_is_present():
    assert set(REQUIRED_TAGS) <= set(tag_table())


def test_every_tag_body_is_aligned_and_inside_the_file():
    for sig, (off, size) in tag_table().items():
        assert off % 4 == 0, f"{sig!r} body is not 4-byte aligned"
        assert 128 < off and off + size <= len(PROFILE), f"{sig!r} is out of range"


def test_each_tag_body_starts_with_its_own_type_signature():
    expected = {b"desc": b"desc", b"cprt": b"text", b"wtpt": b"XYZ ",
                b"rXYZ": b"XYZ ", b"gXYZ": b"XYZ ", b"bXYZ": b"XYZ ",
                b"rTRC": b"curv", b"gTRC": b"curv", b"bTRC": b"curv",
                b"chad": b"sf32"}
    for sig, (off, _size) in tag_table().items():
        assert PROFILE[off:off + 4] == expected[sig], sig


# --------------------------------------------------------------------------- #
# what the profile actually claims
# --------------------------------------------------------------------------- #
def test_all_three_tone_curves_are_the_identity():
    """`curv` with a count of zero IS the identity in the ICC spec — an exact
    claim of linearity, not a gamma of 1.0 rounded into a sampled table. This is
    the entire reason the profile exists."""
    for sig in (b"rTRC", b"gTRC", b"bTRC"):
        off, size = tag_table()[sig]
        assert PROFILE[off:off + 4] == b"curv"
        assert struct.unpack(">I", PROFILE[off + 8:off + 12])[0] == 0
        assert size == 12


def test_the_three_tone_curves_share_one_body():
    table = tag_table()
    offsets = {table[sig][0] for sig in (b"rTRC", b"gTRC", b"bTRC")}
    assert len(offsets) == 1, "identical tag bodies should be stored once"


def test_the_media_white_point_is_d50():
    assert xyz_of(b"wtpt") == pytest.approx((0.9642, 1.0, 0.8249), abs=5e-4)


def test_the_colorants_are_the_d50_adapted_srgb_primaries():
    """Cross-checked against the values in the reference sRGB ICC profile."""
    assert xyz_of(b"rXYZ") == pytest.approx((0.436066, 0.222488, 0.013916),
                                            abs=1e-4)
    assert xyz_of(b"gXYZ") == pytest.approx((0.385147, 0.716873, 0.097076),
                                            abs=1e-4)
    assert xyz_of(b"bXYZ") == pytest.approx((0.143066, 0.060608, 0.714096),
                                            abs=1e-4)


def test_the_colorants_sum_to_the_white_point():
    """R + G + B is what RGB (1, 1, 1) maps to, and that must be the white the
    profile declares — the property the primary scaling exists to guarantee."""
    total = [sum(c) for c in zip(xyz_of(b"rXYZ"), xyz_of(b"gXYZ"),
                                 xyz_of(b"bXYZ"))]
    assert total == pytest.approx(xyz_of(b"wtpt"), abs=1e-3)


def test_the_description_names_the_profile():
    off, _size = tag_table()[b"desc"]
    length = struct.unpack(">I", PROFILE[off + 8:off + 12])[0]
    text = PROFILE[off + 12:off + 12 + length - 1].decode("ascii")
    assert text == icc.PROFILE_DESCRIPTION


def test_the_profile_is_built_once_and_reused():
    assert icc.linear_rgb_profile() is PROFILE


def test_the_profile_bytes_are_deterministic():
    """No creation timestamp and no profile ID, so every build of every run is
    the same bytes — two merges of the same triplet stay comparable, and the
    written tag can be compared against `linear_rgb_profile()` directly."""
    icc.linear_rgb_profile.cache_clear()
    try:
        assert icc.linear_rgb_profile() == PROFILE
    finally:
        icc.linear_rgb_profile.cache_clear()
    assert PROFILE[24:36] == b"\0" * 12          # creation date/time
    assert PROFILE[84:100] == b"\0" * 16         # profile ID


# --------------------------------------------------------------------------- #
# the colour maths behind the tags
# --------------------------------------------------------------------------- #
def test_the_matrix_maps_white_rgb_exactly_onto_the_white_point():
    m = icc.rgb_to_xyz_matrix(icc.SRGB_PRIMARIES_XY, icc.D65_XY)
    white = [sum(row) for row in m]                  # RGB (1, 1, 1)
    assert white == pytest.approx(icc._xyz_from_xy(*icc.D65_XY), abs=1e-9)


def test_bradford_adaptation_carries_the_source_white_to_the_destination():
    chad = icc.bradford_adaptation(icc.D65_XY, icc.D50_XY)
    moved = icc._mat_vec(chad, icc._xyz_from_xy(*icc.D65_XY))
    assert moved == pytest.approx(icc._xyz_from_xy(*icc.D50_XY), abs=1e-9)


def test_adapting_a_white_point_to_itself_is_the_identity():
    chad = icc.bradford_adaptation(icc.D65_XY, icc.D65_XY)
    for i in range(3):
        for j in range(3):
            assert chad[i][j] == pytest.approx(1.0 if i == j else 0.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# an independent colour engine's opinion
# --------------------------------------------------------------------------- #
def _open():
    import io
    return ImageCms.getOpenProfile(io.BytesIO(PROFILE))


def _to_srgb():
    return ImageCms.buildTransform(_open(), ImageCms.createProfile("sRGB"),
                                   "RGB", "RGB")


@needs_littlecms
def test_littlecms_accepts_the_profile_and_reads_it_back():
    prof = _open()
    assert ImageCms.getProfileDescription(prof).strip() == icc.PROFILE_DESCRIPTION
    assert prof.profile.is_matrix_shaper


@needs_littlecms
def test_littlecms_applies_the_linear_curve_when_converting_to_srgb():
    """The whole user-visible point. A linear midtone of 0.18 is 46/255; shown
    through an sRGB decode (what a viewer does with an untagged file) it stays
    at 46 and reads dark, but read through this profile it lands near 118 —
    where a RAW converter puts it."""
    transform = _to_srgb()
    for linear, srgb in ((46, 118), (118, 181), (200, 229)):
        pixel = Image.new("RGB", (1, 1), (linear, linear, linear))
        assert ImageCms.applyTransform(pixel, transform).getpixel((0, 0)) == \
            pytest.approx((srgb, srgb, srgb), abs=1)


@needs_littlecms
def test_littlecms_sees_a_neutral_grey_as_neutral():
    """Primaries that did not sum to the white point would tint the greys."""
    out = ImageCms.applyTransform(Image.new("RGB", (1, 1), (128, 128, 128)),
                                  _to_srgb()).getpixel((0, 0))
    assert max(out) - min(out) <= 1
