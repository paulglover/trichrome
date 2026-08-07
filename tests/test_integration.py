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

from trichrome import bake, cli, merge

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


def test_black_level_is_subtracted_in_the_photosite_path(tmp_path):
    """Two identical scenes, one recorded with a black pedestal: after black
    subtraction they must agree."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    for folder, black, offset in ((a, 0, 0), (b, 256, 256)):
        for i, lv in enumerate([(2000, 100, 100), (100, 3000, 100),
                                (100, 100, 1000)], start=1):
            write_cfa_dng(folder / f"f{i}.dng",
                          rggb_mosaic(32, 32, *[v + offset for v in lv]),
                          white=4095, black=black)
    no_black, _ = merge.merge_raw_channels(
        [str(a / f"f{i}.dng") for i in (1, 2, 3)], demosaic=False)
    with_black, _ = merge.merge_raw_channels(
        [str(b / f"f{i}.dng") for i in (1, 2, 3)], demosaic=False)
    assert np.array_equal(no_black, with_black)


def test_full_cli_run_writes_a_readable_tiff_and_deletes_on_request(triplet,
                                                                    capsys):
    shoot, paths, expected = triplet
    assert cli.main(["merge", str(shoot), "--delete-originals", "--yes"]) == 0

    out = str(shoot / "frame1_RGB.tiff")
    assert os.path.exists(out)
    data = tifffile.imread(out)
    assert data.shape == (64, 96, 3) and data.dtype == np.uint16
    for ch, want in enumerate(expected):
        assert abs(int(data[8:-8, 8:-8, ch].mean()) - want) <= 1
    assert not any(os.path.exists(p) for p in paths)     # RAWs gone
