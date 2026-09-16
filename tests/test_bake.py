"""
Tests for planning, the written file and the destructive delete-originals path.

The RAW decode is monkeypatched (no real trichrome triplet is needed), so these
cover exactly what the safety rules promise: verify before delete, never delete
a failed triplet's sources, never orphan a shared source, cancel cleanly, and
never overwrite an existing file.
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
# collect_raw_files
# --------------------------------------------------------------------------- #
def test_collect_takes_raws_from_a_folder_and_skips_other_files(tmp_path):
    raws(tmp_path, 3)
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "preview.jpg").write_bytes(b"x")
    found = bake.collect_raw_files([str(tmp_path)])
    assert len(found) == 3
    assert all(p.endswith(".arw") for p in found)


def test_collect_keeps_an_explicitly_named_non_raw_so_validation_reports_it(tmp_path):
    raws(tmp_path, 2)
    jpg = tmp_path / "still.jpg"
    jpg.write_bytes(b"x")
    found = bake.collect_raw_files([str(jpg)])
    assert found == [str(jpg)]


def test_collect_recurses_only_when_asked(tmp_path):
    raws(tmp_path, 3)
    raws(tmp_path, 3, folder="sub")
    assert len(bake.collect_raw_files([str(tmp_path)])) == 3
    assert len(bake.collect_raw_files([str(tmp_path)], recursive=True)) == 6


def test_collect_deduplicates_a_file_named_twice(tmp_path):
    files = raws(tmp_path, 3)
    found = bake.collect_raw_files([str(tmp_path), files[0]])
    assert len(found) == 3


# --------------------------------------------------------------------------- #
# plan_jobs
# --------------------------------------------------------------------------- #
def test_plan_groups_in_filename_order_and_names_after_the_first_frame(tmp_path):
    files = raws(tmp_path, 6)
    jobs = bake.plan_jobs(files)
    assert len(jobs) == 2
    assert [os.path.basename(s) for s in jobs[0].sources] == \
        ["img001.arw", "img002.arw", "img003.arw"]
    assert os.path.basename(jobs[0].output) == "img001_RGB.dng"
    assert os.path.basename(jobs[1].output) == "img004_RGB.dng"


def test_plan_rejects_a_count_that_is_not_a_multiple_of_three(tmp_path):
    with pytest.raises(ValueError, match="multiple of 3"):
        bake.plan_jobs(raws(tmp_path, 4))


def test_plan_writes_into_out_dir_when_given(tmp_path):
    files = raws(tmp_path, 3, folder="src")
    out = tmp_path / "merged"
    jobs = bake.plan_jobs(files, out_dir=str(out))
    assert os.path.dirname(jobs[0].output) == str(out)


def test_plan_never_reuses_an_existing_filename(tmp_path):
    files = raws(tmp_path, 3)
    (tmp_path / "img001_RGB.dng").write_bytes(b"already here")
    jobs = bake.plan_jobs(files)
    assert os.path.basename(jobs[0].output) == "img001_RGB_2.dng"


def test_plan_reserves_names_so_two_folders_cannot_collide_in_one_out_dir(tmp_path):
    files = raws(tmp_path, 3, folder="a") + raws(tmp_path, 3, folder="b")
    jobs = bake.plan_jobs(files, out_dir=str(tmp_path / "merged"))
    # Same basenames in both folders -> the second job must claim a new name.
    assert len({j.output for j in jobs}) == 2


# --------------------------------------------------------------------------- #
# run_jobs — the happy path and the written file
# --------------------------------------------------------------------------- #
def test_run_writes_a_verified_uint16_rgb_dng(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    summary = bake.run_jobs(jobs)
    assert len(summary.written) == 1 and not summary.failures
    data = dng.read_linear_dng(jobs[0].output)
    assert data.shape == (6, 8, 3) and data.dtype == np.uint16
    assert data[0, 0, 0] == 1000 and data[0, 0, 2] == 3000


def test_the_written_file_carries_the_freeccr_marker(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    bake.run_jobs(jobs)
    assert dng.is_merge_dng(jobs[0].output)
    with tifffile.TiffFile(jobs[0].output) as tf:
        assert dng.FREECCR_MERGE_MARKER in tf.pages[0].tags["Software"].value


def test_every_output_is_named_dng(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 6))
    assert all(j.output.endswith(".dng") for j in jobs)


def test_run_forwards_demosaic_and_light_order_to_the_decoder(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    bake.run_jobs(jobs, demosaic=False, light_order="BGR")
    assert fake_decode.calls[0]["demosaic"] is False
    assert fake_decode.calls[0]["light_order"] == "BGR"


def test_run_keeps_the_originals_by_default(tmp_path, fake_decode):
    files = raws(tmp_path, 3)
    summary = bake.run_jobs(bake.plan_jobs(files))
    assert summary.deleted == []
    assert all(os.path.exists(f) for f in files)


# --------------------------------------------------------------------------- #
# run_jobs — deletion safety
# --------------------------------------------------------------------------- #
def test_delete_originals_removes_the_sources_after_a_verified_write(tmp_path,
                                                                     fake_decode):
    files = raws(tmp_path, 3)
    jobs = bake.plan_jobs(files)
    summary = bake.run_jobs(jobs, delete_originals=True)
    assert len(summary.deleted) == 3
    assert not any(os.path.exists(f) for f in files)
    assert os.path.exists(jobs[0].output)


def test_a_failed_triplet_keeps_its_sources_and_leaves_no_partial_file(tmp_path,
                                                                       fake_decode):
    files = raws(tmp_path, 6)
    fake_decode.boom = "img005"                 # kills the SECOND triplet
    jobs = bake.plan_jobs(files)
    summary = bake.run_jobs(jobs, delete_originals=True)

    assert len(summary.written) == 1 and len(summary.failures) == 1
    # First triplet: baked and deleted. Second: untouched, no stray output.
    assert not any(os.path.exists(f) for f in files[:3])
    assert all(os.path.exists(f) for f in files[3:])
    assert os.path.exists(jobs[0].output)
    assert not os.path.exists(jobs[1].output)


def test_a_source_shared_with_a_failed_triplet_is_never_deleted(tmp_path,
                                                                fake_decode):
    """Two jobs referencing the same files: the first succeeds, the second
    fails. Nothing may be deleted — a frame's only copy is never orphaned by a
    triplet that still needs it."""
    files = raws(tmp_path, 3)
    good = bake.Job(sources=tuple(files), output=str(tmp_path / "good.dng"))
    bad = bake.Job(sources=tuple(files), output=str(tmp_path / "bad.dng"))
    fail_after = {"n": 0}
    inner = fake_decode

    def flaky(sources, **kw):
        fail_after["n"] += 1
        if fail_after["n"] == 2:
            raise ValueError("simulated failure on the second job")
        return inner(sources, **kw)

    bake.merge_mod.merge_raw_channels = flaky
    summary = bake.run_jobs([good, bad], delete_originals=True)

    assert len(summary.written) == 1 and len(summary.failures) == 1
    assert summary.deleted == []                  # every source is shared
    assert all(os.path.exists(f) for f in files)


def test_a_file_that_fails_verification_blocks_the_delete(tmp_path, fake_decode,
                                                          monkeypatch):
    files = raws(tmp_path, 3)
    monkeypatch.setattr(bake.dng_mod, "verify_linear_dng",
                        lambda *a, **k: (_ for _ in ()).throw(
                            IOError("verification failed")))
    summary = bake.run_jobs(bake.plan_jobs(files), delete_originals=True)
    assert summary.deleted == [] and len(summary.failures) == 1
    assert all(os.path.exists(f) for f in files)


def test_cancel_deletes_nothing_and_removes_what_it_already_wrote(tmp_path,
                                                                  fake_decode):
    files = raws(tmp_path, 6)
    jobs = bake.plan_jobs(files)
    done = {"n": 0}

    def progress(i, total, job):
        done["n"] = i + 1

    summary = bake.run_jobs(jobs, delete_originals=True, progress_cb=progress,
                            cancel_flag=lambda: done["n"] >= 1)
    assert summary.cancelled
    assert summary.deleted == []
    assert all(os.path.exists(f) for f in files)
    assert not any(os.path.exists(j.output) for j in jobs)


def test_dry_run_touches_nothing(tmp_path, fake_decode):
    files = raws(tmp_path, 3)
    jobs = bake.plan_jobs(files)
    summary = bake.run_jobs(jobs, delete_originals=True, dry_run=True)
    assert len(summary.results) == 1 and summary.deleted == []
    assert not os.path.exists(jobs[0].output)
    assert all(os.path.exists(f) for f in files)
    assert fake_decode.calls == []                # no decode was attempted


# --------------------------------------------------------------------------- #
# the written file's own guards
# --------------------------------------------------------------------------- #
def test_verify_rejects_a_wrong_sized_file(tmp_path, fake_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    bake.run_jobs(jobs)
    out = jobs[0].output
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
