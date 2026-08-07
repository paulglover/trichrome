"""
Tests for the merge core — the pure, rawpy-free half: input validation, filename
sorting and triplet grouping, light-order parsing, the color_desc -> channel
index mapping, CFA phase slicing, and the channel-combine maths.

`merge_raw_channels` itself needs real RAW files; the decode is exercised
end-to-end in test_bake.py with a monkeypatched decoder, and against real RAWs
only if you point TRICHROME_TEST_RAWS at a folder holding a triplet.
"""
import os

import numpy as np
import pytest

from trichrome import merge


# --------------------------------------------------------------------------- #
# is_raw_path / validate_merge_inputs
# --------------------------------------------------------------------------- #
def test_is_raw_path_accepts_every_raw_extension_case_insensitively():
    for ext in merge.RAW_EXTENSIONS:
        assert merge.is_raw_path(f"/shots/frame{ext}")
        assert merge.is_raw_path(f"C:/shots/frame{ext.upper()}")


def test_is_raw_path_rejects_non_raw_and_fff():
    for ext in (".jpg", ".png", ".tif", ".tiff", ".fff", ".heic", ""):
        assert not merge.is_raw_path(f"frame{ext}"), ext


def test_validate_empty_is_invalid():
    ok, msg = merge.validate_merge_inputs([])
    assert not ok and msg


def test_validate_three_and_six_raw_are_valid():
    ok3, _ = merge.validate_merge_inputs(["a.arw", "b.arw", "c.arw"])
    ok6, _ = merge.validate_merge_inputs([f"{n}.nef" for n in range(6)])
    assert ok3 and ok6


def test_validate_count_not_multiple_of_three_reports_the_count():
    ok, msg = merge.validate_merge_inputs(["a.arw", "b.arw", "c.arw", "d.arw"])
    assert not ok and "4" in msg


def test_validate_non_raw_present_names_the_file():
    ok, msg = merge.validate_merge_inputs(["r.arw", "g.arw", "still.jpg"])
    assert not ok and "still.jpg" in msg


# --------------------------------------------------------------------------- #
# sort_for_merge / group_into_triplets
# --------------------------------------------------------------------------- #
def test_sort_is_by_basename_ignoring_directory_and_case():
    paths = ["z/B.arw", "a/c.ARW", "m/a.arw"]
    assert [os.path.basename(p) for p in merge.sort_for_merge(paths)] == \
        ["a.arw", "B.arw", "c.ARW"]


def test_sort_is_stable_for_basename_collisions_across_dirs():
    paths = ["b/img.arw", "a/img.arw"]
    out = merge.sort_for_merge(paths)
    assert out == merge.sort_for_merge(list(reversed(paths)))


def test_group_into_triplets_drops_a_trailing_remainder():
    paths = [f"{n}.arw" for n in range(8)]
    groups = merge.group_into_triplets(paths)
    assert groups == [("0.arw", "1.arw", "2.arw"), ("3.arw", "4.arw", "5.arw")]


# --------------------------------------------------------------------------- #
# parse_light_order
# --------------------------------------------------------------------------- #
def test_light_order_rgb_is_identity_and_bgr_reverses():
    assert merge.parse_light_order("RGB") == (0, 1, 2)
    assert merge.parse_light_order("bgr") == (2, 1, 0)
    assert merge.parse_light_order("GRB") == (1, 0, 2)


@pytest.mark.parametrize("bad", ["RG", "RGBB", "RGX", "", "RRG"])
def test_light_order_rejects_non_permutations(bad):
    with pytest.raises(ValueError):
        merge.parse_light_order(bad)


# --------------------------------------------------------------------------- #
# bayer_channel_indices / is_monochrome_sensor
# --------------------------------------------------------------------------- #
def test_channel_indices_for_canonical_rgbg():
    assert merge.bayer_channel_indices(b"RGBG") == (0, 1, 2)


def test_channel_indices_honour_a_permuted_desc():
    assert merge.bayer_channel_indices(b"GRBG") == (1, 0, 2)


@pytest.mark.parametrize("desc", [b"G", b"CMYG", b"GMCY"])
def test_channel_indices_reject_a_desc_missing_r_g_or_b(desc):
    with pytest.raises(ValueError):
        merge.bayer_channel_indices(desc)


def test_channel_indices_alone_does_not_reject_four_colour_rgbe():
    """b'RGBE' does contain R, G and B, so this mapping accepts it — 4-colour
    sensors are caught by the num_colors != 3 check in _decode_frame_plane,
    which is where the rejection belongs."""
    assert merge.bayer_channel_indices(b"RGBE") == (0, 1, 2)


def test_monochrome_detected_by_num_colors_desc_and_flat_pattern():
    assert merge.is_monochrome_sensor(1, b"G")
    assert merge.is_monochrome_sensor(3, b"GREY")
    assert merge.is_monochrome_sensor(3, b"RGBG", np.zeros((2, 2), int))


def test_bayer_is_not_reported_as_monochrome():
    assert not merge.is_monochrome_sensor(3, b"RGBG", np.array([[0, 1], [3, 2]]))


# --------------------------------------------------------------------------- #
# extract_cfa_channel — the no-demosaic phase slice
# --------------------------------------------------------------------------- #
def _rggb_mosaic(h=4, w=4, r=100, g1=200, g2=220, b=300):
    """A synthetic RGGB mosaic and its colour-index map, tiled from
    [[R, G], [G, B]] with libraw's canonical b'RGBG' indices [[0, 1], [3, 2]]."""
    tile_vals = np.array([[r, g1], [g2, b]], dtype=np.uint16)
    tile_idx = np.array([[0, 1], [3, 2]], dtype=np.uint8)
    return (np.tile(tile_vals, (h // 2, w // 2)),
            np.tile(tile_idx, (h // 2, w // 2)))


def test_cfa_slice_takes_the_single_site_for_red_and_blue():
    mosaic, colors = _rggb_mosaic()
    r = merge.extract_cfa_channel(mosaic, colors, b"RGBG", "R")
    b = merge.extract_cfa_channel(mosaic, colors, b"RGBG", "B")
    assert r.shape == (2, 2) and np.all(r == 100)     # half resolution
    assert np.all(b == 300)


def test_cfa_slice_averages_the_two_green_sites():
    mosaic, colors = _rggb_mosaic(g1=200, g2=220)
    g = merge.extract_cfa_channel(mosaic, colors, b"RGBG", "G")
    assert np.all(g == 210)                            # (200 + 220) / 2


def test_cfa_slice_subtracts_per_channel_black_levels():
    mosaic, colors = _rggb_mosaic()
    r = merge.extract_cfa_channel(mosaic, colors, b"RGBG", "R",
                                  black_levels=[16, 16, 16, 16])
    assert np.all(r == 84)                             # 100 - 16


def test_cfa_slice_is_offset_safe_when_the_visible_origin_shifts():
    """The CFA is period-2, so a mosaic whose visible origin starts on a GREEN
    site must still yield the right colour — the phase is read from `colors`."""
    mosaic, colors = _rggb_mosaic(h=6, w=6)
    shifted_m, shifted_c = mosaic[1:, 1:], colors[1:, 1:]
    b = merge.extract_cfa_channel(shifted_m, shifted_c, b"RGBG", "B")
    assert np.all(b == 300)


def test_cfa_slice_rejects_a_colour_not_in_the_tile():
    mosaic, colors = _rggb_mosaic()
    with pytest.raises(ValueError):
        merge.extract_cfa_channel(mosaic, colors, b"RGBG", "X")


# --------------------------------------------------------------------------- #
# combine_channels
# --------------------------------------------------------------------------- #
def test_combine_puts_each_frames_plane_in_its_own_channel():
    r = np.full((4, 5), 100, np.float32)
    g = np.full((4, 5), 200, np.float32)
    b = np.full((4, 5), 300, np.float32)
    out = merge.combine_channels(r, g, b, [1000, 1000, 1000])
    assert out.shape == (4, 5, 3) and out.dtype == np.uint16
    scale = 65535.0 / 1000
    assert np.all(out[..., 0] == int(100 * scale))
    assert np.all(out[..., 1] == int(200 * scale))
    assert np.all(out[..., 2] == int(300 * scale))


def test_combine_scales_each_frame_by_its_own_white_level():
    plane = np.full((2, 2), 500, np.float32)
    out = merge.combine_channels(plane, plane, plane, [1000, 2000, 4000])
    # Same sensor value, different white levels -> different normalised output.
    assert out[0, 0, 0] > out[0, 0, 1] > out[0, 0, 2]


def test_combine_clips_to_16_bit_and_never_wraps():
    hot = np.full((2, 2), 5000, np.float32)
    out = merge.combine_channels(hot, hot, hot, [1000, 1000, 1000])
    assert np.all(out == 65535)


def test_combine_crops_mismatched_planes_to_the_common_size():
    out = merge.combine_channels(np.ones((4, 6), np.float32),
                                 np.ones((5, 5), np.float32),
                                 np.ones((6, 7), np.float32),
                                 [1, 1, 1])
    assert out.shape == (4, 5, 3)


def test_combine_requires_three_white_levels():
    plane = np.ones((2, 2), np.float32)
    with pytest.raises(ValueError):
        merge.combine_channels(plane, plane, plane, [1, 1])


# --------------------------------------------------------------------------- #
# merge_raw_channels — argument contract (no rawpy needed)
# --------------------------------------------------------------------------- #
def test_merge_rejects_a_wrong_sized_group():
    with pytest.raises(ValueError):
        merge.merge_raw_channels(["a.arw", "b.arw"])


def test_merge_rejects_a_missing_source(tmp_path):
    p = tmp_path / "a.arw"
    p.write_bytes(b"x")
    with pytest.raises(ValueError, match="missing"):
        merge.merge_raw_channels([str(p), str(p), str(tmp_path / "gone.arw")])


def test_merge_light_order_selects_which_frame_feeds_which_channel(monkeypatch,
                                                                   tmp_path):
    """With --order BGR the FIRST frame was shot under blue light, so it must
    supply the output's blue channel, not its red."""
    paths = []
    for name in ("f1.arw", "f2.arw", "f3.arw"):
        p = tmp_path / name
        p.write_bytes(b"x")
        paths.append(str(p))
    asked = []

    def fake_decode(path, letter, preview=False, demosaic=True):
        asked.append((os.path.basename(path), letter))
        return np.zeros((4, 4), np.float32), 1000.0, False, (4, 4)

    monkeypatch.setattr(merge, "_decode_frame_plane", fake_decode)
    merge.merge_raw_channels(paths, light_order="BGR")
    assert asked == [("f3.arw", "R"), ("f2.arw", "G"), ("f1.arw", "B")]


def test_merge_rejects_mixed_sensor_types(monkeypatch, tmp_path):
    paths = []
    for name in ("a.arw", "b.arw", "c.arw"):
        p = tmp_path / name
        p.write_bytes(b"x")
        paths.append(str(p))
    monos = iter([True, False, False])

    def fake_decode(path, letter, preview=False, demosaic=True):
        return np.zeros((4, 4), np.float32), 1000.0, next(monos), (8, 8)

    monkeypatch.setattr(merge, "_decode_frame_plane", fake_decode)
    with pytest.raises(ValueError, match="same sensor type"):
        merge.merge_raw_channels(paths)
