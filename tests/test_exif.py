"""
Tests for the camera metadata a merged DNG carries over from its first source.

Two halves, and they are tested against different things on purpose. The READING
half is given hand-built containers (exiffixture: a TIFF, a CR3, a RAF, in both
byte orders) that trichrome.exif had no part in writing, so what is checked is
that it can parse a directory some camera wrote. The WRITING half is read back
with tifffile's own EXIF parser — not with this package's — so a file that only
trichrome can understand fails here, and the structure the DNG already promised
(thumbnail, LinearRaw SubIFD, colour tags, merge marker) is checked to have
survived the surgery, because the deletion of the originals rides on it.
"""
import os
import shutil
import subprocess

import numpy as np
import pytest
import tifffile

from trichrome import bake, cli, dng, exif

import exiffixture as fx
from test_bake import FakeDecoder, raws


@pytest.fixture
def fake_decode(monkeypatch):
    decoder = FakeDecoder()
    monkeypatch.setattr(bake.merge_mod, "merge_raw_channels", decoder)
    return decoder


def sample(h=10, w=14):
    img = np.zeros((h, w, 3), np.uint16)
    img[..., 0], img[..., 1], img[..., 2] = 1000, 40000, 65535
    return img


def written_dng(tmp_path, source, merged=None, name="out.dng"):
    """A merged DNG carrying `source`'s metadata, plus the writer's warning."""
    out = str(tmp_path / name)
    warning = dng.write_linear_dng(out, sample() if merged is None else merged,
                                   source=source)
    return out, warning


def ifd0_tags(path):
    with tifffile.TiffFile(path) as tf:
        return {t.code: t.value for t in tf.pages[0].tags.values()}


def exif_tags(path):
    """The EXIF IFD as TIFFFILE reads it — an independent parser, so this is a
    real check that the appended directory is a standard one."""
    return ifd0_tags(path).get(34665, {})


# --------------------------------------------------------------------------- #
# Reading a source RAW
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("writer", [fx.write_tiff_source, fx.write_cr3_source,
                                    fx.write_raf_source])
@pytest.mark.parametrize("order", ["<", ">"])
def test_every_container_yields_the_same_exposure(tmp_path, writer, order):
    meta = exif.read_source_metadata(writer(tmp_path / "src.raw", order=order))
    tags = {e.tag: e for e in meta.exif}
    assert tags[42036].data.rstrip(b"\x00").decode() == fx.LENS_MODEL
    assert tags[36867].data.rstrip(b"\x00").decode() == fx.DATETIME
    assert {e.tag for e in meta.ifd0} >= {271, 272, 274, 306}
    assert {e.tag for e in meta.gps} == {0, 1, 2}
    assert meta.byteorder == order
    assert meta.filename == "src.raw"


def test_a_tiff_source_is_found_even_behind_a_private_version_number(tmp_path):
    # Panasonic writes 85 where TIFF says 42, Olympus writes 'RO'. Only the byte
    # order and the offsets matter, so neither is a reason to give up.
    path = str(tmp_path / "src.rw2")
    data = bytearray(fx.exif_tiff())
    data[2:4] = (85).to_bytes(2, "little")
    open(path, "wb").write(bytes(data))
    assert exif.read_source_metadata(path).exif


def test_a_file_that_is_not_a_raw_at_all_is_reported_not_guessed(tmp_path):
    path = tmp_path / "img001.arw"
    path.write_bytes(b"not really a raw, just a placeholder")
    with pytest.raises(ValueError):
        exif.read_source_metadata(str(path))


def test_a_source_with_no_exif_in_it_is_reported(tmp_path):
    path = str(tmp_path / "bare.arw")
    open(path, "wb").write(fx.exif_tiff(ifd0=[(305, fx._ascii("nothing"))],
                                        exif=[], gps=[]))
    # IFD0 has a tag, but none this module copies and no EXIF or GPS IFD.
    with pytest.raises(ValueError, match="no camera metadata"):
        exif.read_source_metadata(path)


def test_an_empty_or_missing_file_is_reported(tmp_path):
    empty = tmp_path / "empty.arw"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        exif.read_source_metadata(str(empty))
    with pytest.raises(ValueError):
        exif.read_source_metadata(str(tmp_path / "gone.arw"))


# --------------------------------------------------------------------------- #
# What travels into the DNG, and what does not
# --------------------------------------------------------------------------- #
def test_the_exposure_and_the_lens_arrive_intact(tmp_path):
    out, warning = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    assert warning is None
    tags = exif_tags(out)
    assert tags["ExposureTime"] == fx.EXPOSURE_TIME
    assert tags["FNumber"] == fx.F_NUMBER
    assert tags["ISOSpeedRatings"] == fx.ISO
    assert tags["FocalLength"] == fx.FOCAL_LENGTH
    assert tags["LensModel"] == fx.LENS_MODEL
    assert tags["DateTimeOriginal"] == fx.DATETIME


def test_the_camera_and_the_capture_time_land_where_a_browser_looks(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    tags = ifd0_tags(out)
    assert tags[271] == fx.MAKE and tags[272] == fx.MODEL
    assert tags[306] == fx.DATETIME
    assert tags[315] == fx.ARTIST and tags[33432] == fx.COPYRIGHT
    # Unrotated sensor data, so the source's claim about which way up it goes is
    # exactly as true of the merge.
    assert tags[274] == fx.ORIENTATION


def test_the_gps_position_travels_as_its_own_ifd(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    assert ifd0_tags(out)[34853]["GPSLatitude"] == (51, 1, 30, 1, 2664, 100)


def test_the_maker_note_is_dropped_since_its_offsets_would_not_survive(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    assert 37500 not in exif_tags(out)
    assert fx.MAKER_NOTE not in open(out, "rb").read()


def test_the_interoperability_pointer_is_dropped(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    assert 40965 not in exif_tags(out)


def test_the_pixel_dimensions_describe_the_merge_not_the_source(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"),
                         merged=sample(10, 14))
    tags = exif_tags(out)
    assert (tags["PixelXDimension"], tags["PixelYDimension"]) == (14, 10)


def test_the_dng_spellings_of_the_body_and_the_lens_are_filled_in(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    tags = ifd0_tags(out)
    assert tags[50735] == fx.BODY_SERIAL                      # CameraSerialNumber
    assert tags[50736] == (55, 1, 55, 1, 18, 10, 18, 10)      # LensInfo


def test_the_file_records_which_frame_it_was_made_from(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "img001.arw"))
    assert ifd0_tags(out)[50827] == "img001.arw"          # OriginalRawFileName


def test_a_big_endian_source_reads_the_same_as_a_little_endian_one(tmp_path):
    little, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "le.nef"),
                            name="le.dng")
    big, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "be.nef",
                                                        order=">"),
                         name="be.dng")
    assert exif_tags(little) == exif_tags(big)
    assert ifd0_tags(little)[274] == ifd0_tags(big)[274] == fx.ORIENTATION


# --------------------------------------------------------------------------- #
# When the raw's own EXIF is not the whole EXIF
# --------------------------------------------------------------------------- #
def test_a_sparse_raw_exif_is_filled_from_its_preview_jpeg(tmp_path):
    """Panasonic's layout, reproduced from a DC-G9 RW2: the raw's own ExifIFD
    has no ISO at all, and the full EXIF is in the preview JPEG's APP1."""
    src = fx.write_sparse_raw_with_preview(tmp_path / "IMG_0997.rw2")
    out, warning = written_dng(tmp_path, src)
    assert warning is None
    tags = exif_tags(out)
    assert tags["ISOSpeedRatings"] == fx.ISO
    assert tags["FNumber"] == fx.F_NUMBER
    assert tags["DateTimeOriginal"] == fx.DATETIME


def test_the_raw_s_own_value_wins_where_the_preview_disagrees(tmp_path):
    src = fx.write_sparse_raw_with_preview(tmp_path / "s.rw2",
                                           raw_focal=(45, 1))
    out, _ = written_dng(tmp_path, src)
    assert exif_tags(out)["FocalLength"] == (45, 1)     # not the preview's 44


def test_a_big_endian_raw_merges_its_preview_in_the_same_byte_order(tmp_path):
    src = fx.write_sparse_raw_with_preview(tmp_path / "s.rw2", order=">")
    out, _ = written_dng(tmp_path, src)
    tags = exif_tags(out)
    assert tags["ISOSpeedRatings"] == fx.ISO
    assert tags["ExposureTime"] == fx.EXPOSURE_TIME


def test_the_jpeg_signature_in_image_data_is_not_mistaken_for_exif(tmp_path):
    with_decoy = fx.write_sparse_raw_with_preview(tmp_path / "a.rw2", decoy=True)
    without = fx.write_sparse_raw_with_preview(tmp_path / "b.rw2", decoy=False)
    a = {e.tag: e.data for e in exif.read_source_metadata(with_decoy).exif}
    b = {e.tag: e.data for e in exif.read_source_metadata(without).exif}
    assert a == b


def test_colour_space_is_restated_because_srgb_is_false_of_linear_data(tmp_path):
    """The preview says sRGB, which is true of the preview. Copied onto linear
    camera-native data it would be a claim the file cannot back."""
    out, _ = written_dng(tmp_path,
                         fx.write_sparse_raw_with_preview(tmp_path / "s.rw2"))
    assert exif_tags(out)["ColorSpace"] == 0xFFFF             # Uncalibrated


def test_the_preview_s_compression_tags_do_not_describe_the_dng(tmp_path):
    out, _ = written_dng(tmp_path,
                         fx.write_sparse_raw_with_preview(tmp_path / "s.rw2"))
    tags = exif_tags(out)
    assert "ComponentsConfiguration" not in tags
    assert "CompressedBitsPerPixel" not in tags
    assert (tags["PixelXDimension"], tags["PixelYDimension"]) == (14, 10)


# --------------------------------------------------------------------------- #
# The lens, out of a maker note
# --------------------------------------------------------------------------- #
def lens_source(tmp_path, note, order="<", make="Panasonic", exif=None,
                name="s.raw"):
    ifd0 = [(271, fx._ascii(make)), (272, fx._ascii("Body"))]
    path = tmp_path / name
    path.write_bytes(fx.exif_tiff(order, ifd0=ifd0, gps=[],
                                  exif=fx.exif_without_lens(order)
                                  if exif is None else exif,
                                  note=note))
    return str(path)


@pytest.mark.parametrize("order", ["<", ">"])
def test_a_panasonic_lens_becomes_standard_exif(tmp_path, order):
    out, _ = written_dng(tmp_path,
                         lens_source(tmp_path, fx.panasonic_note(order), order))
    tags = exif_tags(out)
    assert tags["LensModel"] == fx.PANASONIC_LENS
    assert tags["LensSerialNumber"] == fx.PANASONIC_LENS_SERIAL


@pytest.mark.parametrize("order", ["<", ">"])
def test_a_canon_lens_becomes_standard_exif(tmp_path, order):
    src = lens_source(tmp_path, fx.canon_note(order), order, make="Canon")
    out, _ = written_dng(tmp_path, src)
    assert exif_tags(out)["LensModel"] == fx.CANON_LENS


def test_a_canon_cr3_lens_is_read_from_its_own_cmt3_box(tmp_path):
    src = fx.write_cr3_source(tmp_path / "s.cr3", lens_note=True)
    tags = {e.tag: e for e in exif.read_source_metadata(src).exif}
    assert tags[42036].data.rstrip(b"\x00").decode() == fx.LENS_MODEL, \
        "the EXIF LensModel CR3 already has must win"
    bare = fx.write_cr3_source(tmp_path / "bare.cr3", lens_note=True,
                               exif_lens=False)
    tags = {e.tag: e for e in exif.read_source_metadata(bare).exif}
    assert tags[42036].data.rstrip(b"\x00").decode() == fx.CANON_LENS


@pytest.mark.parametrize("note_order", ["<", ">"])
def test_a_nikon_lens_becomes_a_lens_specification_and_dng_lens_info(
        tmp_path, note_order):
    """Nikon's note is big-endian inside a little-endian file as often as not:
    its rationals have to come out in the DNG's order, not the note's."""
    src = lens_source(tmp_path, fx.nikon_note(note_order), "<", make="NIKON")
    out, _ = written_dng(tmp_path, src)
    spec = (105, 1, 105, 1, 28, 10, 28, 10)
    assert exif_tags(out)["LensSpecification"] == spec
    assert ifd0_tags(out)[50736] == spec


def test_an_exif_lens_model_is_never_overridden_by_the_maker_note(tmp_path):
    exif_with_lens = fx.exif_without_lens("<") + [(42036, fx._ascii("FROM EXIF"))]
    src = lens_source(tmp_path, fx.panasonic_note("<"), exif=exif_with_lens)
    out, _ = written_dng(tmp_path, src)
    tags = exif_tags(out)
    assert tags["LensModel"] == "FROM EXIF"
    assert tags["LensSerialNumber"] == fx.PANASONIC_LENS_SERIAL   # still filled


@pytest.mark.parametrize("placeholder", ["", "0000000", "----", "  0 - 0  "])
def test_a_placeholder_is_not_a_lens(tmp_path, placeholder):
    note = fx.panasonic_note("<", lens=placeholder, serial=placeholder)
    out, _ = written_dng(tmp_path, lens_source(tmp_path, note))
    tags = exif_tags(out)
    assert "LensModel" not in tags and "LensSerialNumber" not in tags


def test_an_unknown_lens_specification_is_not_written_as_f0(tmp_path):
    """A manual or adapted lens is recorded as zeros: unknown, not f/0."""
    note = fx.nikon_note(spec=((0, 0), (0, 0), (0, 0), (0, 0)))
    out, _ = written_dng(tmp_path, lens_source(tmp_path, note, make="NIKON"))
    assert "LensSpecification" not in exif_tags(out)
    assert 50736 not in ifd0_tags(out)


def test_a_maker_note_from_a_vendor_it_does_not_know_yields_nothing(tmp_path):
    src = lens_source(tmp_path, fx.canon_note("<"), make="SONY")
    out, warning = written_dng(tmp_path, src)
    assert warning is None
    assert "LensModel" not in exif_tags(out)


def test_the_maker_note_itself_still_does_not_travel(tmp_path):
    out, _ = written_dng(tmp_path,
                         lens_source(tmp_path, fx.panasonic_note("<")))
    assert "MakerNote" not in exif_tags(out)
    assert b"Panasonic\x00\x00\x00" not in open(out, "rb").read()


# --------------------------------------------------------------------------- #
# The DNG is still the DNG it was
# --------------------------------------------------------------------------- #
def test_the_colour_tags_and_the_merge_marker_are_not_disturbed(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    tags = ifd0_tags(out)
    # The camera tags say what took the frames; UniqueCameraModel says what the
    # file is, and a converter resolves its profile through THAT.
    assert tags[50708] == dng.UNIQUE_CAMERA_MODEL
    assert tags[50728] == (1000000, 1000000) * 3               # AsShotNeutral
    assert len(tags[50721]) == 18 and len(tags[50964]) == 18   # both matrices
    assert tags[50940] == dng.IDENTITY_TONE_CURVE              # ProfileToneCurve
    assert tags[51110] == 1                                   # DefaultBlackRender
    assert tifffile.TiffFile(out).pages[0].tags["Software"].value.startswith(
        "FreeCCR:3-way-RGB-merge-linear-v1")
    assert dng.is_merge_dng(out)


def test_the_image_and_its_thumbnail_still_read_back(tmp_path):
    merged = sample(12, 20)
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"),
                         merged=merged)
    assert np.array_equal(dng.read_linear_dng(out), merged)
    with tifffile.TiffFile(out) as tf:
        assert tf.pages[0].asarray().shape[:2] == (12, 20)     # the thumbnail
        assert tf.pages[0].tags[330].value                      # SubIFDs intact


def test_the_subifd_pointer_is_stated_as_the_long_it_is(tmp_path):
    # Tag 330 is SubIFDs, which may be LONG or IFD — and is also Sony's private
    # A100DataOffset, a LONG. With a real Make in the file, a reader that
    # dispatches on it has to be given the unambiguous one.
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"))
    with tifffile.TiffFile(out) as tf:
        subifds = tf.pages[0].tags[330]
        assert subifds.dtype == tifffile.DATATYPE.LONG
        assert dng._raw_page(tf) is not None      # still resolves to the image


def test_verification_still_passes_so_deletion_stays_guarded(tmp_path):
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"),
                         merged=sample(12, 20))
    dng.verify_linear_dng(out, expect_shape=(12, 20))
    with pytest.raises(IOError):
        dng.verify_linear_dng(out, expect_shape=(13, 20))


def test_the_pixels_are_the_same_bytes_with_and_without_metadata(tmp_path):
    merged = sample()
    plain = str(tmp_path / "plain.dng")
    dng.write_linear_dng(plain, merged)
    stamped, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "s.arw"),
                             merged=merged)
    assert np.array_equal(dng.read_linear_dng(plain),
                          dng.read_linear_dng(stamped))
    # Nothing already in the file moved: the metadata is appended after it, and
    # the only edit inside is the header's four-byte pointer to IFD0.
    without = open(plain, "rb").read()
    with_meta = open(stamped, "rb").read()
    assert len(with_meta) > len(without)
    assert with_meta[:4] == without[:4]
    assert with_meta[8:len(without)] == without[8:]


# --------------------------------------------------------------------------- #
# When it cannot be done
# --------------------------------------------------------------------------- #
def test_an_unreadable_source_costs_the_metadata_and_not_the_merge(tmp_path):
    junk = tmp_path / "img001.arw"
    junk.write_bytes(b"not really a raw")
    out, warning = written_dng(tmp_path, str(junk))
    assert warning and "img001.arw" in warning
    dng.verify_linear_dng(out, expect_shape=(10, 14))
    assert np.array_equal(dng.read_linear_dng(out), sample())


def test_no_source_given_writes_the_same_dng_as_before(tmp_path):
    out = str(tmp_path / "bare.dng")
    assert dng.write_linear_dng(out, sample()) is None
    assert 34665 not in ifd0_tags(out)


def test_a_failed_copy_is_reported_by_the_job_and_printed_by_the_cli(
        tmp_path, fake_decode, capsys):
    # test_bake's raws() are placeholders with no metadata in them at all, which
    # is exactly the case that must warn rather than fail.
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert "1 merged, 0 failed" in out
    assert "WARNING img001_RGB.dng" in err and "img001.arw" in err


def test_a_copied_job_warns_about_nothing(tmp_path, fake_decode, capsys):
    for i in (1, 2, 3):
        fx.write_tiff_source(tmp_path / f"img{i:03d}.arw")
    assert cli.main(["merge", str(tmp_path)]) == 0
    assert "WARNING" not in capsys.readouterr().err
    results = bake.run_jobs(bake.plan_jobs(
        [str(tmp_path / f"img{i:03d}.arw") for i in (1, 2, 3)],
        out_dir=str(tmp_path / "again")))
    assert [r.warning for r in results.written] == [None]


# --------------------------------------------------------------------------- #
# An independent reader, when there is one to hand
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("exiftool") is None,
                    reason="exiftool is not installed")
def test_exiftool_reads_the_result_as_an_ordinary_camera_file(tmp_path):
    """The one check that goes outside Python: exiftool parses the appended
    directories the way a converter's own reader would, and validates the file
    it finds."""
    out, _ = written_dng(tmp_path, fx.write_tiff_source(tmp_path / "img001.arw"))
    read = subprocess.run(["exiftool", "-s", "-s", "-s", "-Model", "-LensModel",
                           "-DateTimeOriginal", "-FNumber", "-ISO",
                           "-OriginalRawFileName", out],
                          capture_output=True, text=True, check=True)
    assert read.stdout.split() == [fx.MODEL, "FE", "55mm", "F1.8", "ZA",
                                   "2026:05:12", "14:33:07", "8.0", "100",
                                   "img001.arw"]
