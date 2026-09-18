"""
Tests for planning, the written file and the destructive delete-originals path.

The RAW decode is monkeypatched (no real trichrome triplet is needed), so these
cover exactly what the safety rules promise: verify before delete, never delete
a failed merge's sources, and never overwrite an existing file.
"""
import os

import numpy as np
import pytest
import tifffile

from trichrome import bake, dng, merge


class FakeDecoder:
    """Stands in for merge_raw_channels with a cheap synthetic 6x8 merge.

    `calls` records every invocation. Set `boom` to a filename substring to make
    any triplet containing that file fail the decode."""

    def __init__(self):
        self.calls = []
        self.boom = None

    def __call__(self, sources, preview=False, demosaic=True, light_order="RGB"):
        self.calls.append({"sources": tuple(sources), "demosaic": demosaic,
                           "light_order": light_order})
        if self.boom and any(self.boom in os.path.basename(s) for s in sources):
            raise ValueError("simulated decode failure")
        img = np.zeros((6, 8, 3), np.uint16)
        img[..., 0] = 1000
        img[..., 1] = 2000
        img[..., 2] = 3000
        return img, (6, 8)


@pytest.fixture
def fake_decode(monkeypatch):
    decoder = FakeDecoder()
    monkeypatch.setattr(bake.merge_mod, "merge_raw_channels", decoder)
    return decoder


def raws(tmp_path, n, ext=".arw", folder=""):
    """n placeholder RAW files named 001..n (content is never read)."""
    d = tmp_path / folder if folder else tmp_path
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(1, n + 1):
        p = d / f"img{i:03d}{ext}"
        p.write_bytes(b"not really a raw")
        out.append(str(p))
    return out


# --------------------------------------------------------------------------- #
# plan_job
# --------------------------------------------------------------------------- #
def test_plan_orders_by_filename_and_names_after_the_film_id(tmp_path):
    files = raws(tmp_path, 3)
    job = bake.plan_job(list(reversed(files)), "S0123-10")
    assert [os.path.basename(s) for s in job.sources] == \
        ["img001.arw", "img002.arw", "img003.arw"]
    assert job.output == str(tmp_path / "S0123-10.dng")
    assert job.film_id == "S0123-10"


@pytest.mark.parametrize("n", [1, 2, 4, 6])
def test_plan_rejects_anything_but_exactly_three_files(tmp_path, n):
    with pytest.raises(ValueError, match="exactly 3"):
        bake.plan_job(raws(tmp_path, n), "S0123-10")


def test_plan_rejects_a_folder(tmp_path):
    raws(tmp_path, 3, folder="shoot")
    with pytest.raises(ValueError, match="not folders"):
        bake.plan_job([str(tmp_path / "shoot")], "S0123-10")


def test_plan_rejects_a_file_named_twice(tmp_path):
    files = raws(tmp_path, 2)
    with pytest.raises(ValueError, match="more than once"):
        bake.plan_job(files + [files[0]], "S0123-10")


def test_plan_rejects_a_non_raw(tmp_path):
    files = raws(tmp_path, 2)
    jpg = tmp_path / "still.jpg"
    jpg.write_bytes(b"x")
    with pytest.raises(ValueError, match="still.jpg"):
        bake.plan_job(files + [str(jpg)], "S0123-10")


def test_plan_rejects_a_missing_file(tmp_path):
    files = raws(tmp_path, 2)
    with pytest.raises(ValueError, match="no such file"):
        bake.plan_job(files + [str(tmp_path / "img003.arw")], "S0123-10")


def test_plan_writes_into_out_dir_when_given(tmp_path):
    files = raws(tmp_path, 3, folder="src")
    out = tmp_path / "merged"
    job = bake.plan_job(files, "S0123-10", out_dir=str(out))
    assert job.output == str(out / "S0123-10.dng")


def test_plan_refuses_to_overwrite_an_existing_file(tmp_path):
    files = raws(tmp_path, 3)
    (tmp_path / "S0123-10.dng").write_bytes(b"already here")
    with pytest.raises(ValueError, match="already exists"):
        bake.plan_job(files, "S0123-10")
    assert (tmp_path / "S0123-10.dng").read_bytes() == b"already here"


@pytest.mark.parametrize("bad", ["", "   ", "a/b", ".", ".."])
def test_plan_rejects_a_film_id_that_is_not_a_file_name(tmp_path, bad):
    with pytest.raises(ValueError, match="film ID"):
        bake.plan_job(raws(tmp_path, 3), bad)


def test_the_film_id_is_trimmed(tmp_path):
    job = bake.plan_job(raws(tmp_path, 3), "  S0123-10 ")
    assert job.film_id == "S0123-10"
    assert os.path.basename(job.output) == "S0123-10.dng"


# --------------------------------------------------------------------------- #
# run_job — the happy path and the written file
# --------------------------------------------------------------------------- #
def test_run_writes_a_verified_uint16_rgb_dng(tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    result = bake.run_job(job)
    assert result.ok and result.size == (6, 8)
    data = dng.read_linear_dng(job.output)
    assert data.shape == (6, 8, 3) and data.dtype == np.uint16
    assert data[0, 0, 0] == 1000 and data[0, 0, 2] == 3000


def test_the_written_file_carries_the_freeccr_marker(tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    bake.run_job(job)
    assert dng.is_merge_dng(job.output)
    with tifffile.TiffFile(job.output) as tf:
        assert dng.FREECCR_MERGE_MARKER in tf.pages[0].tags["Software"].value


def test_the_written_file_states_the_film_id_as_its_dc_identifier(
        tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    bake.run_job(job)
    with tifffile.TiffFile(job.output) as tf:
        xmp = bytes(tf.pages[0].tags[700].value).decode("utf-8")
    assert "<dc:identifier>S0123-10</dc:identifier>" in xmp


def test_run_forwards_demosaic_and_light_order_to_the_decoder(tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    bake.run_job(job, demosaic=False, light_order="BGR")
    assert fake_decode.calls[0]["demosaic"] is False
    assert fake_decode.calls[0]["light_order"] == "BGR"


def test_run_keeps_the_originals_by_default(tmp_path, fake_decode):
    files = raws(tmp_path, 3)
    result = bake.run_job(bake.plan_job(files, "S0123-10"))
    assert result.deleted == []
    assert all(os.path.exists(f) for f in files)


def test_run_will_not_overwrite_a_file_that_appeared_after_planning(
        tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    (tmp_path / "S0123-10.dng").write_bytes(b"got here first")
    result = bake.run_job(job, delete_originals=True)
    assert "already exists" in result.error and result.deleted == []
    assert (tmp_path / "S0123-10.dng").read_bytes() == b"got here first"
    assert fake_decode.calls == []


# --------------------------------------------------------------------------- #
# run_job — deletion safety
# --------------------------------------------------------------------------- #
def test_delete_originals_removes_the_sources_after_a_verified_write(tmp_path,
                                                                     fake_decode):
    files = raws(tmp_path, 3)
    job = bake.plan_job(files, "S0123-10")
    result = bake.run_job(job, delete_originals=True)
    assert len(result.deleted) == 3
    assert not any(os.path.exists(f) for f in files)
    assert os.path.exists(job.output)


def test_a_failed_merge_keeps_its_sources_and_leaves_no_partial_file(tmp_path,
                                                                     fake_decode):
    files = raws(tmp_path, 3)
    fake_decode.boom = "img002"
    job = bake.plan_job(files, "S0123-10")
    result = bake.run_job(job, delete_originals=True)
    assert not result.ok and result.deleted == []
    assert all(os.path.exists(f) for f in files)
    assert not os.path.exists(job.output)


def test_a_file_that_fails_verification_blocks_the_delete(tmp_path, fake_decode,
                                                          monkeypatch):
    files = raws(tmp_path, 3)
    monkeypatch.setattr(bake.dng_mod, "verify_linear_dng",
                        lambda *a, **k: (_ for _ in ()).throw(
                            IOError("verification failed")))
    job = bake.plan_job(files, "S0123-10")
    result = bake.run_job(job, delete_originals=True)
    assert result.deleted == [] and not result.ok
    assert all(os.path.exists(f) for f in files)
    assert not os.path.exists(job.output)


def test_dry_run_touches_nothing(tmp_path, fake_decode):
    files = raws(tmp_path, 3)
    job = bake.plan_job(files, "S0123-10")
    result = bake.run_job(job, delete_originals=True, dry_run=True)
    assert result.ok and result.deleted == []
    assert not os.path.exists(job.output)
    assert all(os.path.exists(f) for f in files)
    assert fake_decode.calls == []                # no decode was attempted


# --------------------------------------------------------------------------- #
# the written file's own guards
# --------------------------------------------------------------------------- #
def test_verify_rejects_a_wrong_sized_file(tmp_path, fake_decode):
    job = bake.plan_job(raws(tmp_path, 3), "S0123-10")
    bake.run_job(job)
    out = job.output
    dng.verify_linear_dng(out, expect_shape=(6, 8))
    with pytest.raises(IOError):
        dng.verify_linear_dng(out, expect_shape=(6, 9))


def test_verify_rejects_a_file_that_is_not_one_of_ours(tmp_path):
    plain = str(tmp_path / "plain.dng")
    tifffile.imwrite(plain, np.zeros((4, 4, 3), np.uint16), photometric="rgb")
    with pytest.raises(IOError):
        dng.verify_linear_dng(plain)


def test_verify_rejects_a_missing_or_empty_file(tmp_path):
    with pytest.raises(IOError):
        dng.verify_linear_dng(str(tmp_path / "nope.dng"))
    empty = tmp_path / "empty.dng"
    empty.write_bytes(b"")
    with pytest.raises(IOError):
        dng.verify_linear_dng(str(empty))


def test_writing_rejects_an_array_that_is_not_a_merge(tmp_path):
    with pytest.raises(ValueError):
        dng.write_linear_dng(str(tmp_path / "x.dng"),
                             np.zeros((4, 4, 3), np.uint8))


def test_is_merge_dng_is_false_for_a_plain_dng_and_a_non_dng(tmp_path):
    plain = str(tmp_path / "plain.dng")
    tifffile.imwrite(plain, np.zeros((4, 4, 3), np.uint16), photometric="rgb")
    assert not dng.is_merge_dng(plain)
    other = tmp_path / "notes.txt"
    other.write_text("hi")
    assert not dng.is_merge_dng(str(other))
