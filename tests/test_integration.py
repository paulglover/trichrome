"""
End-to-end tests through the REAL rawpy decode, on synthetic RGGB DNGs built by
dngfixture (no test assets needed, no camera required).

These are what prove the two extraction modes actually behave as documented:
each output channel comes only from its own frame's own photosites, at the
resolution the mode promises.
"""
import os

import numpy as np
import pytest
import tifffile

from trichrome import bake, cli, icc, merge, tiff

rawpy = pytest.importorskip("rawpy")

from dngfixture import rggb_mosaic, write_cfa_dng, write_triplet  # noqa: E402


@pytest.fixture
def triplet(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    paths, expected = write_triplet(shoot)
    return shoot, paths, expected


def test_demosaic_merge_is_full_sensor_resolution_and_channel_pure(triplet):
    _shoot, paths, expected = triplet
    merged, full_size = merge.merge_raw_channels(paths, demosaic=True)
    assert merged.dtype == np.uint16 and merged.shape == (64, 96, 3)
    assert full_size == (64, 96)
    # Sample away from the edges, where bilinear interpolation has fewer
    # neighbours; the interior must be exactly each frame's own level.
    interior = merged[8:-8, 8:-8]
    for ch, want in enumerate(expected):
        assert abs(int(interior[..., ch].mean()) - want) <= 1, f"channel {ch}"


def test_photosite_merge_is_half_resolution_and_channel_pure(triplet):
    _shoot, paths, expected = triplet
    merged, full_size = merge.merge_raw_channels(paths, demosaic=False)
    assert merged.shape == (32, 48, 3)          # one site per 2x2 quad
    assert full_size == (32, 48)
    for ch, want in enumerate(expected):
        assert np.all(merged[..., ch] == want), f"channel {ch}"


def test_both_modes_agree_on_the_values_they_extract(triplet):
    _shoot, paths, _expected = triplet
    dm, _ = merge.merge_raw_channels(paths, demosaic=True)
    ph, _ = merge.merge_raw_channels(paths, demosaic=False)
    assert abs(dm.shape[0] - 2 * ph.shape[0]) <= 2
    assert np.allclose(dm[8:-8, 8:-8].mean(axis=(0, 1)),
                       ph.mean(axis=(0, 1)), atol=2)


def test_light_order_bgr_swaps_which_frame_feeds_red_and_blue(triplet):
    _shoot, paths, expected = triplet
    rgb, _ = merge.merge_raw_channels(paths, demosaic=False)
    bgr, _ = merge.merge_raw_channels(paths, demosaic=False, light_order="BGR")
    # Under BGR, frame 3 (shot under blue light) supplies RED, and its R sites
    # were at the dim floor — so red must drop, and green is unchanged.
    assert bgr[0, 0, 0] < rgb[0, 0, 0]
    assert bgr[0, 0, 1] == rgb[0, 0, 1] == expected[1]


@pytest.mark.parametrize("demosaic", [True, False])
def test_a_black_pedestal_changes_nothing_about_the_merged_output(tmp_path,
                                                                  demosaic):
    """The same scene recorded on two sensors that differ ONLY in where zero
    sits: one with no pedestal, one with a 512 pedestal under an equally-raised
    white level, so both have the same 4095 codes of usable range.

    Identical light, identical usable range -> the merges must be identical. They
    are only identical if the pedestal is both subtracted from the plane AND
    taken off the divisor; normalising by the raw white level instead leaves the
    pedestal frame darker by black/white."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    paths_a, expected = write_triplet(a, h=32, w=32, white=4095, black=0)
    paths_b, _ = write_triplet(b, h=32, w=32, white=4095, black=512)

    no_black, _ = merge.merge_raw_channels(paths_a, demosaic=demosaic)
    with_black, _ = merge.merge_raw_channels(paths_b, demosaic=demosaic)
    assert np.array_equal(no_black, with_black)

    # And both land on the level the usable range says they should, rather than
    # 4095/4607 of it (what dividing by the un-subtracted white level gives).
    interior = with_black[8:-8, 8:-8] if demosaic else with_black
    for ch, want in enumerate(expected):
        assert abs(int(interior[..., ch].mean()) - want) <= 1, f"channel {ch}"


def test_a_full_scale_site_normalises_to_full_scale_despite_the_pedestal(
        tmp_path):
    """A site sitting AT the white level is a clipped highlight and must come out
    at full scale, not at (white - black)/white of it (4095/4607 -> ~58000).

    The bound is 65534, not 65535: `combine_channels` truncates rather than
    rounds when it casts to uint16, and 65535/(white - black) is not exact in
    float32. That 1-LSB shortfall is pre-existing and independent of the
    pedestal — a no-pedestal frame at its white level lands on 65534 too."""
    folder = tmp_path / "s"
    folder.mkdir()
    white, black = 4607, 512
    for i in range(1, 4):
        write_cfa_dng(folder / f"f{i}.dng",
                      rggb_mosaic(32, 32, white, white, white),
                      white=white, black=black)
    merged, _ = merge.merge_raw_channels(
        [str(folder / f"f{i}.dng") for i in (1, 2, 3)], demosaic=False)
    assert np.all(merged >= 65534)


def test_frames_with_different_pedestals_do_not_tint_the_merge(tmp_path):
    """Three frames from sensors with three different pedestals, each carrying
    the same fraction of its own usable range. Scaling by the raw white level
    would shift each channel by a different amount — a colour cast, not just a
    darkening — so the three channels must still come out equal."""
    folder = tmp_path / "t"
    folder.mkdir()
    names = []
    for i, black in enumerate((0, 256, 2048), start=1):
        white = 4095 + black                     # same 4095 codes of usable range
        level = black + 2000                     # same signal above the pedestal
        p = folder / f"f{i}.dng"
        write_cfa_dng(p, rggb_mosaic(32, 32, level, level, level),
                      white=white, black=black)
        names.append(str(p))
    merged, _ = merge.merge_raw_channels(names, demosaic=False)
    want = int(2000 * 65535.0 / 4095)
    for ch in range(3):
        assert abs(int(merged[..., ch].mean()) - want) <= 1, f"channel {ch}"


def test_full_cli_run_writes_a_readable_tiff_and_deletes_on_request(triplet,
                                                                    capsys):
    shoot, paths, expected = triplet
    assert cli.main(["merge", str(shoot), "--delete-originals"]) == 0

    out = str(shoot / "frame1_RGB.tif")
    assert os.path.exists(out)
    data = tifffile.imread(out)
    assert data.shape == (64, 96, 3) and data.dtype == np.uint16
    for ch, want in enumerate(expected):
        assert abs(int(data[8:-8, 8:-8, ch].mean()) - want) <= 1
    assert not any(os.path.exists(p) for p in paths)     # RAWs gone
    assert tiff.embedded_icc_profile(out) == icc.linear_rgb_profile()


def test_cli_no_icc_writes_the_same_pixels_without_the_profile(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    write_triplet(shoot)
    assert cli.main(["merge", str(shoot), "--no-icc"]) == 0
    untagged = str(shoot / "frame1_RGB.tif")
    assert tiff.embedded_icc_profile(untagged) is None

    assert cli.main(["merge", str(shoot)]) == 0
    tagged = str(shoot / "frame1_RGB_2.tif")            # never overwrites
    assert tiff.embedded_icc_profile(tagged) == icc.linear_rgb_profile()
    assert np.array_equal(tifffile.imread(untagged), tifffile.imread(tagged))
