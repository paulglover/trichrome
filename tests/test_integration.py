"""
End-to-end tests through the REAL rawpy decode, on synthetic RGGB DNGs built by
dngfixture (no test assets needed, no camera required).

These are what prove the two extraction modes actually behave as documented:
each output channel comes only from its own frame's own photosites, at the
resolution the mode promises — and, at the end, that a written linear DNG really
does go back into a RAW decoder and come out as the merge that went in.
"""
import os

import numpy as np
import pytest
import tifffile

from trichrome import bake, cli, dng, exif, merge

rawpy = pytest.importorskip("rawpy")

import exiffixture as fx  # noqa: E402
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


def test_full_cli_run_writes_a_readable_dng_and_deletes_on_request(triplet,
                                                                  capsys):
    shoot, paths, expected = triplet
    assert cli.main(["merge", str(shoot), "--delete-originals"]) == 0

    out = str(shoot / "frame1_RGB.dng")
    assert os.path.exists(out)
    data = dng.read_linear_dng(out)
    assert data.shape == (64, 96, 3) and data.dtype == np.uint16
    for ch, want in enumerate(expected):
        assert abs(int(data[8:-8, 8:-8, ch].mean()) - want) <= 1
    assert not any(os.path.exists(p) for p in paths)     # RAWs gone
    assert dng.is_merge_dng(out)


# --------------------------------------------------------------------------- #
# The file, opened the way it exists to be opened
# --------------------------------------------------------------------------- #
def test_a_written_dng_opens_in_libraw_as_raw_with_the_merged_values(triplet):
    """The whole claim of the format: a real RAW decoder ingests the file
    through its RAW pipeline. Decoded with the pipeline switched to identity
    (camera-native colour, gamma 1, unity white balance, no auto-brighten) it
    must hand back exactly the merge that went in — proof that the file says
    what it holds, and that nothing in the container altered it."""
    shoot, _paths, expected = triplet
    assert cli.main(["merge", str(shoot)]) == 0
    out = str(shoot / "frame1_RGB.dng")

    with rawpy.imread(out) as decoded:
        rgb = decoded.postprocess(output_color=rawpy.ColorSpace.raw,
                                  gamma=(1, 1), no_auto_bright=True,
                                  output_bps=16, use_camera_wb=False,
                                  user_wb=[1, 1, 1, 1])
    assert rgb.shape == (64, 96, 3)
    for ch, want in enumerate(expected):
        assert abs(int(rgb[8:-8, 8:-8, ch].mean()) - want) <= 2, f"channel {ch}"


def test_the_dng_survives_a_decoder_that_derives_its_own_white_balance(triplet):
    """The test above pins `user_wb`, which is the one thing a real converter
    does NOT do — so on its own it cannot see the failure this guards against.

    A reader has two ways to find the merge's neutral: read AsShotNeutral, or
    derive a daylight balance from ColorMatrix1. rawpy, libraw and dcraw take the
    second by default, as does any "camera reference" white balance preset, and
    a file whose ColorMatrix1 is stated under the wrong illuminant sends those
    two ways to different answers. Stating it under D50 while AsShotNeutral says
    (1, 1, 1) made the derived multipliers (1.65, 1.34, 1.00): the merge arrived
    two thirds of a stop hot in red, with red and green clipped. Both routes must
    agree, and agree on unity."""
    shoot, _paths, expected = triplet
    assert cli.main(["merge", str(shoot)]) == 0
    out = str(shoot / "frame1_RGB.dng")

    with rawpy.imread(out) as decoded:
        as_shot = np.array(decoded.camera_whitebalance[:3])
        derived = np.array(decoded.daylight_whitebalance[:3])
        # Left to itself: no user_wb, no use_camera_wb — the converter's own
        # reading of the file, which is how FreeCCR and Affinity Photo open it.
        rgb = decoded.postprocess(output_color=rawpy.ColorSpace.raw,
                                  gamma=(1, 1), no_auto_bright=True,
                                  output_bps=16)
    assert np.allclose(as_shot, 1.0, atol=1e-4), as_shot
    assert np.allclose(derived / derived.min(), 1.0, atol=1e-3), derived
    # A relative tolerance, unlike the test above: those multipliers came out of
    # the matrix as it is ENCODED, to six decimal places, so this route carries a
    # rounding the pinned-unity one does not. 0.1% is still two and a half stops
    # clear of the (1.65, 1.34, 1.00) this exists to catch.
    for ch, want in enumerate(expected):
        got = int(rgb[8:-8, 8:-8, ch].mean())
        assert abs(got - want) <= want * 1e-3, f"channel {ch}: {got} vs {want}"


def test_a_dng_run_over_a_folder_twice_does_not_eat_its_own_output(triplet):
    """The .dng-is-also-a-RAW-extension hazard, end to end and with the
    destructive flag on: the second run must find no inputs at all, rather than
    merging the first run's output and deleting it. Sharper now that every
    output is a DNG: the tool writes its own input format every time."""
    shoot, paths, _expected = triplet
    assert cli.main(["merge", str(shoot), "--delete-originals"]) == 0
    merged = str(shoot / "frame1_RGB.dng")
    assert os.path.exists(merged) and not any(os.path.exists(p) for p in paths)

    assert cli.main(["merge", str(shoot)]) == 2
    assert os.path.exists(merged)
    assert not os.path.exists(str(shoot / "frame1_RGB_RGB.dng"))


def test_a_merged_dng_carries_its_first_frame_s_camera_metadata(triplet, tmp_path):
    """The whole point, end to end: three real RAWs go in, and the file that
    comes out still says when it was shot, on what, and through what lens —
    while remaining a file libraw opens as the merge that went in."""
    shoot, paths, expected = triplet
    # Stamp the camera's metadata onto the frames, as a body would have. Only
    # the first frame's is copied, so the other two get a different lens to
    # prove which frame the merged file speaks for.
    exif.copy_into_dng(paths[0],
                       exif.read_source_metadata(
                           fx.write_tiff_source(tmp_path / "body.arw")))
    for other in paths[1:]:
        exif.copy_into_dng(other, exif.read_source_metadata(
            fx.write_tiff_source(tmp_path / "other.arw",
                                 ifd0=[(271, fx._ascii("NOT THIS BODY"))])))

    jobs = bake.plan_jobs(paths, out_dir=str(tmp_path / "out"))
    summary = bake.run_jobs(jobs)
    assert [r.warning for r in summary.written] == [None]
    out = summary.written[0].job.output

    with tifffile.TiffFile(out) as tf:
        tags = {t.code: t.value for t in tf.pages[0].tags.values()}
    assert tags[271] == fx.MAKE and tags[272] == fx.MODEL
    assert tags[34665]["LensModel"] == fx.LENS_MODEL
    assert tags[34665]["DateTimeOriginal"] == fx.DATETIME
    assert tags[50827] == os.path.basename(paths[0])
    assert tags[50708] == dng.UNIQUE_CAMERA_MODEL      # still not a real camera

    # And it is still a raw file, still the merge: libraw reads the same pixels
    # out of it that it did before there was any metadata in it.
    with rawpy.imread(out) as raw:
        rgb = raw.postprocess(output_bps=16, no_auto_bright=True, gamma=(1, 1),
                              user_flip=0, use_camera_wb=False,
                              use_auto_wb=False, no_auto_scale=True,
                              output_color=rawpy.ColorSpace.raw)
    for ch, want in enumerate(expected):
        assert abs(int(rgb[8:-8, 8:-8, ch].mean()) - want) <= 2, f"channel {ch}"
