"""
Tests for the linear DNG output: the file's structure, the colour metadata it is
forced to carry, the verification that guards deletion, and the format plumbing
through plan_jobs/run_jobs and the CLI.

The DNG's whole reason for existing is that a converter opens it through the RAW
pipeline, so the claims that matter here are structural — LinearRaw in a SubIFD,
black at 0, white at 65535, a neutral of (1, 1, 1) — plus the one arithmetic
claim the file makes about itself: that its two colour matrices are consistent
with each other and with that neutral. test_integration.py adds the end-to-end
proof that libraw actually reads the result.
"""
import os

import numpy as np
import pytest
import tifffile

from trichrome import bake, cli, dng, icc, tiff

from test_bake import FakeDecoder, raws


@pytest.fixture
def fake_decode(monkeypatch):
    decoder = FakeDecoder()
    monkeypatch.setattr(bake.merge_mod, "merge_raw_channels", decoder)
    return decoder


def sample(h=12, w=20):
    """A small merge-shaped frame with a distinct level per channel."""
    img = np.zeros((h, w, 3), np.uint16)
    img[..., 0] = 1000
    img[..., 1] = 40000
    img[..., 2] = 65535
    return img


def tag_values(tags):
    """A tag dict keyed by BOTH name and number, so a test can ask for whichever
    is clearer (tifffile knows the DNG tag names it has heard of, and not the
    others)."""
    out = {}
    for tag in tags.values():
        out[tag.name] = tag.value
        out[tag.code] = tag.value
    return out


def raw_page(path):
    """(LinearRaw page, IFD0 tags, LinearRaw tags) of a DNG this tool wrote."""
    with tifffile.TiffFile(path) as tf:
        page = dng._raw_page(tf)
        assert page is not None, "no LinearRaw page"
        return page, tag_values(tf.pages[0].tags), tag_values(page.tags)


# --------------------------------------------------------------------------- #
# The written file
# --------------------------------------------------------------------------- #
def test_the_image_survives_the_round_trip_exactly(tmp_path):
    """The point of the format is the container, not the numbers: a DNG must
    carry the merge unchanged, every pixel."""
    out = str(tmp_path / "m.dng")
    img = sample()
    dng.write_linear_dng(out, img)
    assert np.array_equal(dng.read_linear_dng(out), img)


def test_a_dng_and_a_tiff_of_the_same_merge_hold_identical_pixels(tmp_path):
    img = sample()
    d, t = str(tmp_path / "m.dng"), str(tmp_path / "m.tif")
    dng.write_linear_dng(d, img)
    tiff.write_linear_tiff(t, img)
    assert np.array_equal(dng.read_linear_dng(d), tifffile.imread(t))


def test_the_full_image_is_linear_raw_in_a_subifd_not_ifd0(tmp_path):
    """DNG's prescribed layout: IFD0 is a reduced-resolution preview, the real
    image hangs off it. A reader that stopped at IFD0 must not find the data
    there and mistake the thumbnail for the merge."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample(64, 96))
    page, ifd0, raw = raw_page(out)
    assert ifd0["NewSubfileType"] == 1                 # IFD0 = thumbnail
    assert ifd0["PhotometricInterpretation"] == 2      # ordinary RGB preview
    assert raw["NewSubfileType"] == 0                  # the real image
    assert raw["PhotometricInterpretation"] == dng.PHOTOMETRIC_LINEAR_RAW
    assert raw["SamplesPerPixel"] == 3
    assert tuple(raw["BitsPerSample"]) == (16, 16, 16)
    assert page.shape == (64, 96, 3) and np.dtype(page.dtype) == np.uint16


def test_the_thumbnail_is_small_8_bit_and_brighter_than_the_linear_data(tmp_path):
    """The preview is sRGB-encoded so browsers show something recognisable; that
    encoding must land on the preview ONLY, which is why it is checked as
    brighter than the linear values it came from."""
    out = str(tmp_path / "m.dng")
    img = np.full((600, 800, 3), 11800, np.uint16)     # ~0.18 linear
    dng.write_linear_dng(out, img)
    with tifffile.TiffFile(out) as tf:
        thumb = tf.pages[0].asarray()
    assert thumb.dtype == np.uint8
    assert max(thumb.shape[:2]) <= dng._THUMBNAIL_MAX_EDGE
    linear_8bit = 11800 / 65535 * 255                  # ~46, the dark reading
    assert thumb.mean() > linear_8bit * 2              # ~118 after encoding


def test_black_and_white_levels_describe_the_normalised_merge(tmp_path):
    """combine_channels already subtracted each sensor's pedestal and scaled to
    the full 16-bit range, so these two tags are simply true — the one piece of
    the colour spec that is not a convention."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    _page, _ifd0, raw = raw_page(out)
    assert tuple(raw["BlackLevel"]) == (0, 0, 0)
    assert tuple(raw["WhiteLevel"]) == (65535, 65535, 65535)
    assert tuple(raw["BlackLevelRepeatDim"]) == (1, 1)


def test_as_shot_neutral_is_unity_because_the_merge_applies_no_white_balance(
        tmp_path):
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    _page, ifd0, _raw = raw_page(out)
    num_den = ifd0[50728]
    assert [num_den[i] / num_den[i + 1] for i in range(0, 6, 2)] == [1.0, 1.0, 1.0]


def test_it_names_itself_rather_than_impersonating_a_camera(tmp_path):
    """UniqueCameraModel must not collide with a real camera, or a reader could
    apply that camera's profile to data it has nothing to do with."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    _page, ifd0, _raw = raw_page(out)
    assert ifd0["UniqueCameraModel"] == dng.UNIQUE_CAMERA_MODEL
    assert bytes(ifd0["DNGVersion"]) == bytes((1, 4, 0, 0))
    assert bytes(ifd0["DNGBackwardVersion"]) == bytes((1, 2, 0, 0))
    assert tiff.FREECCR_MERGE_TIFF_MARKER in ifd0["Software"]


# --------------------------------------------------------------------------- #
# The colour spec — a convention, but an internally consistent one
# --------------------------------------------------------------------------- #
def test_the_forward_matrix_maps_the_merge_neutral_onto_d50(tmp_path):
    """DNG's consistency requirement, and the reason AsShotNeutral (1, 1, 1) and
    ForwardMatrix1 have to be chosen together: the camera neutral must land
    exactly on the profile connection space's white."""
    forward, _color = dng.color_matrices()
    white = np.array(forward) @ np.array([1.0, 1.0, 1.0])
    x, y = icc.D50_XY
    expected = np.array([x / y, 1.0, (1.0 - x - y) / y])
    assert np.allclose(white, expected, atol=1e-6)


def test_the_two_matrices_are_inverses_of_each_other():
    forward, color = dng.color_matrices()
    assert np.allclose(np.array(forward) @ np.array(color), np.eye(3), atol=1e-9)


def test_the_matrices_state_the_same_primaries_the_icc_profile_does():
    """The TIFF and the DNG must not disagree about what the merge is. icc.py
    assumes sRGB primaries; the forward matrix here has to be that same
    RGB->XYZ(D50) transform, or the two formats would grade differently."""
    forward, _color = dng.color_matrices()
    chad = np.array(icc.bradford_adaptation(icc.D65_XY, icc.D50_XY))
    srgb = np.array(icc.rgb_to_xyz_matrix(icc.SRGB_PRIMARIES_XY, icc.D65_XY))
    assert np.allclose(np.array(forward), chad @ srgb)


def test_the_written_matrices_match_the_computed_ones(tmp_path):
    """The rationals in the file are what a reader acts on, so the encoding is
    checked, not just the arithmetic."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    _page, ifd0, _raw = raw_page(out)
    forward, color = dng.color_matrices()
    for tag, want in ((50964, forward), (50721, color)):
        pairs = ifd0[tag]
        got = [pairs[i] / pairs[i + 1] for i in range(0, 18, 2)]
        assert np.allclose(got, np.array(want).reshape(-1), atol=1e-6), tag


# --------------------------------------------------------------------------- #
# Verification — this is what stands between a bad write and a deleted original
# --------------------------------------------------------------------------- #
def test_verify_accepts_a_file_this_module_wrote(tmp_path):
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample(12, 20))
    dng.verify_linear_dng(out, expect_shape=(12, 20))


def test_verify_rejects_a_missing_or_empty_file(tmp_path):
    with pytest.raises(IOError, match="not written or empty"):
        dng.verify_linear_dng(str(tmp_path / "nope.dng"))
    empty = tmp_path / "empty.dng"
    empty.write_bytes(b"")
    with pytest.raises(IOError, match="not written or empty"):
        dng.verify_linear_dng(str(empty))


def test_verify_rejects_a_size_that_is_not_the_one_the_merge_produced(tmp_path):
    """A truncated or mis-sized write must never pass for a good replacement of
    files that are about to be deleted."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample(12, 20))
    with pytest.raises(IOError, match=r"expected \(12, 40\)"):
        dng.verify_linear_dng(out, expect_shape=(12, 40))


def test_verify_rejects_a_plain_tiff(tmp_path):
    out = str(tmp_path / "not.dng")
    tiff.write_linear_tiff(out, sample())
    with pytest.raises(IOError, match="no DNGVersion"):
        dng.verify_linear_dng(out)


def test_verify_rejects_a_dng_whose_thumbnail_survived_but_image_did_not(tmp_path):
    """The failure mode the SubIFD layout makes possible: IFD0 reads fine and
    looks like a picture, while the image it points at is gone."""
    out = str(tmp_path / "thumb_only.dng")
    tifffile.imwrite(out, np.zeros((16, 16, 3), np.uint8), photometric="rgb",
                     extratags=[(50706, 'B', 4, (1, 4, 0, 0), True)])
    with pytest.raises(IOError, match="no LinearRaw image"):
        dng.verify_linear_dng(out)


@pytest.mark.parametrize("bad", [
    np.zeros((4, 4), np.uint16),            # not three channels
    np.zeros((4, 4, 3), np.uint8),          # not 16-bit
    np.zeros((4, 4, 4), np.uint16),         # four channels
])
def test_write_refuses_anything_that_is_not_a_uint16_rgb_frame(tmp_path, bad):
    with pytest.raises(ValueError, match="expected an"):
        dng.write_linear_dng(str(tmp_path / "m.dng"), bad)


# --------------------------------------------------------------------------- #
# is_merge_dng — and why it exists
# --------------------------------------------------------------------------- #
def test_a_written_dng_is_recognised_as_this_tools_own_output(tmp_path):
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    assert dng.is_merge_dng(out)
    assert tiff.carries_merge_marker(out)
    assert not tiff.is_merge_tiff(out)          # marker yes, .tif no


def test_an_unmarked_dng_and_a_non_dng_are_not(tmp_path):
    plain = tmp_path / "camera.dng"
    tifffile.imwrite(str(plain), np.zeros((8, 8), np.uint16), software="a camera")
    assert not dng.is_merge_dng(str(plain))
    assert not dng.is_merge_dng(str(tmp_path / "missing.dng"))
    marked_tif = str(tmp_path / "m.tif")
    tiff.write_linear_tiff(marked_tif, sample())
    assert not dng.is_merge_dng(marked_tif)     # marker yes, .dng no


def test_a_merged_dng_left_beside_its_sources_is_not_picked_up_as_an_input(
        tmp_path):
    """.dng is a supported RAW extension, so without the exclusion a second
    `--format dng` run over the same folder would try to merge its own output —
    and, with --delete-originals, would then be deleting merges."""
    sources = raws(tmp_path, 3, ext=".dng")
    dng.write_linear_dng(str(tmp_path / "img001_RGB.dng"), sample())
    found = bake.collect_raw_files([str(tmp_path)])
    assert sorted(os.path.basename(p) for p in found) == \
        ["img001.dng", "img002.dng", "img003.dng"]
    assert len(sources) == 3


def test_the_exclusion_also_applies_to_a_recursive_scan(tmp_path):
    raws(tmp_path, 3, ext=".dng", folder="shoot")
    dng.write_linear_dng(str(tmp_path / "shoot" / "img001_RGB.dng"), sample())
    found = bake.collect_raw_files([str(tmp_path)], recursive=True)
    assert len(found) == 3


def test_an_explicitly_named_merge_output_is_still_taken_as_given(tmp_path):
    """Naming a file is an instruction; only directory scans get filtered."""
    out = str(tmp_path / "m.dng")
    dng.write_linear_dng(out, sample())
    assert bake.collect_raw_files([out]) == [out]


# --------------------------------------------------------------------------- #
# Format plumbing: plan_jobs, run_jobs, the CLI
# --------------------------------------------------------------------------- #
def test_planning_for_dng_names_the_outputs_dng(tmp_path):
    jobs = bake.plan_jobs(raws(tmp_path, 6), fmt="dng")
    assert [os.path.basename(j.output) for j in jobs] == \
        ["img001_RGB.dng", "img004_RGB.dng"]
    assert all(j.fmt == "dng" for j in jobs)


def test_planning_never_overwrites_an_existing_dng_either(tmp_path):
    files = raws(tmp_path, 3)
    (tmp_path / "img001_RGB.dng").write_bytes(b"already here")
    jobs = bake.plan_jobs(files, fmt="dng")
    assert os.path.basename(jobs[0].output) == "img001_RGB_2.dng"


def test_a_tiff_and_a_dng_plan_do_not_collide_with_each_other(tmp_path):
    files = raws(tmp_path, 3)
    tif = bake.plan_jobs(files)[0]
    dng_job = bake.plan_jobs(files, fmt="dng")[0]
    assert tif.output.endswith("img001_RGB.tif")
    assert dng_job.output.endswith("img001_RGB.dng")


def test_an_unknown_format_is_rejected_while_planning(tmp_path):
    with pytest.raises(ValueError, match="unknown output format"):
        bake.plan_jobs(raws(tmp_path, 3), fmt="jpeg")


def test_running_a_dng_plan_writes_verified_dngs(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3), fmt="dng")
    summary = bake.run_jobs(jobs)
    assert len(summary.written) == 1 and not summary.failures
    assert summary.written[0].size == (6, 8)
    assert np.array_equal(dng.read_linear_dng(jobs[0].output)[0, 0],
                          [1000, 2000, 3000])


def test_the_job_decides_the_format_so_a_plan_cannot_be_run_as_the_wrong_one(
        tmp_path, fake_decode):
    """The extension is chosen at plan time; if the writer were chosen separately
    at run time the two could disagree and put TIFF bytes in a .dng."""
    jobs = bake.plan_jobs(raws(tmp_path, 3), fmt="dng")
    bake.run_jobs(jobs, icc=False)              # a TIFF-only flag
    dng.verify_linear_dng(jobs[0].output, expect_shape=(6, 8))


def test_deleting_originals_works_the_same_on_the_dng_path(tmp_path,
                                                           fake_decode):
    files = raws(tmp_path, 3)
    jobs = bake.plan_jobs(files, fmt="dng")
    summary = bake.run_jobs(jobs, delete_originals=True)
    assert len(summary.deleted) == 3
    assert not any(os.path.exists(f) for f in files)
    dng.verify_linear_dng(jobs[0].output)


def test_a_failed_dng_job_leaves_no_partial_file_and_no_deletions(tmp_path,
                                                                  fake_decode):
    files = raws(tmp_path, 3)
    fake_decode.boom = "img002"
    jobs = bake.plan_jobs(files, fmt="dng")
    summary = bake.run_jobs(jobs, delete_originals=True)
    assert len(summary.failures) == 1 and not summary.deleted
    assert not os.path.exists(jobs[0].output)
    assert all(os.path.exists(f) for f in files)


def test_cli_format_dng_writes_a_dng_and_says_so(tmp_path, capsys, fake_decode):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--format", "dng"]) == 0
    out = capsys.readouterr().out
    assert "linear DNG" in out
    assert dng.is_merge_dng(str(tmp_path / "img001_RGB.dng"))


def test_cli_defaults_to_tiff(tmp_path, fake_decode):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path)]) == 0
    assert os.path.exists(str(tmp_path / "img001_RGB.tif"))
    assert not os.path.exists(str(tmp_path / "img001_RGB.dng"))


def test_cli_no_icc_is_inert_on_the_dng_path(tmp_path, capsys, fake_decode):
    """DNG carries no ICC profile at all, so the flag has nothing to switch off
    and the header must not claim otherwise."""
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--format", "dng",
                     "--no-icc"]) == 0
    assert "no ICC" not in capsys.readouterr().out
    dng.verify_linear_dng(str(tmp_path / "img001_RGB.dng"))


def test_cli_list_and_dry_run_report_the_chosen_format(tmp_path, capsys):
    raws(tmp_path, 3)
    assert cli.main(["list", str(tmp_path), "--format", "dng"]) == 0
    assert "img001_RGB.dng" in capsys.readouterr().out
    assert cli.main(["merge", str(tmp_path), "--format", "dng", "-n"]) == 0
    out = capsys.readouterr().out
    assert "1 DNG(s) would be created" in out
    assert not os.path.exists(str(tmp_path / "img001_RGB.dng"))


def test_cli_rejects_an_unknown_format(tmp_path, capsys):
    raws(tmp_path, 3)
    with pytest.raises(SystemExit):
        cli.main(["merge", str(tmp_path), "--format", "jpeg"])
