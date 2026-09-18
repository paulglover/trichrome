"""
Orchestration: plan the merge of one triplet, write a verified linear file,
and (opt-in) permanently delete the three source RAWs.

One output format: a linear DNG (dng.py), which a converter opens through its
RAW pipeline and which carries the triplet's first frame's camera metadata, so
that deleting the originals does not take the capture data with them. It is
named after the film ID, which is also written into it as XMP dc:identifier.

Deletion safety is the whole point of this module, so the rules are explicit:

* A source RAW is deleted ONLY after its replacement exists on disk and reads
  back as a valid uint16 RGB image of the expected size.
* If the merge fails, nothing is deleted and no partial file is left behind.
* An existing file is never overwritten: planning refuses a film ID whose
  output is already there, and the write checks again.
* `dry_run` decodes and merges nothing, deletes nothing — it just reports the
  plan.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import dng as dng_mod
from . import merge as merge_mod


@dataclass
class Job:
    """One planned merge: three source RAWs → one output file."""
    sources: Tuple[str, str, str]
    film_id: str
    output: str

    @property
    def name(self) -> str:
        return os.path.basename(self.output)


@dataclass
class JobResult:
    job: Job
    size: Optional[Tuple[int, int]] = None      # (H, W) of the written image
    error: Optional[str] = None
    # Written, verified and safe to delete the sources of — but with something
    # worth saying about it (a DNG that could not carry its source's metadata).
    warning: Optional[str] = None
    deleted: List[str] = field(default_factory=list)
    # (path, reason) for a source that was due for deletion but could not be
    # removed — the output is fine, the original is simply still there.
    delete_errors: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def validate_film_id(film_id: Optional[str]) -> str:
    """The film ID, stripped of surrounding whitespace, once it is known to be
    usable as a file name. Raises ValueError when it is not: blank, a path
    rather than a name, or a name the filesystem reserves."""
    fid = (film_id or "").strip()
    if not fid:
        raise ValueError("the film ID is blank — it names the merged file, so "
                         "it cannot be")
    if "/" in fid or "\0" in fid:
        raise ValueError(f"film ID {fid!r} contains a '/' — it is a file name, "
                         "not a path (use --out to choose the folder)")
    if fid in (".", ".."):
        raise ValueError(f"film ID {fid!r} is not a usable file name")
    return fid


def output_path(film_id: str, first_source: str,
                out_dir: Optional[str] = None) -> str:
    """Where the merge is written: `FILMID.dng`, beside the first frame unless
    `out_dir` says otherwise."""
    directory = out_dir if out_dir else os.path.dirname(
        os.path.abspath(first_source))
    return os.path.join(os.path.abspath(os.path.expanduser(directory)),
                        film_id + dng_mod.OUTPUT_EXTENSION)


def plan_job(paths: Sequence[str], film_id: str,
             out_dir: Optional[str] = None) -> Job:
    """Sort and validate exactly three RAW files, and settle the output path.

    Raises ValueError with a user-facing message when they cannot be merged:
    not exactly three files, a folder among them, a file that is not a
    supported RAW or is itself a merge, a film ID that is not a usable file
    name, or an output that already exists."""
    fid = validate_film_id(film_id)
    folders = [p for p in paths if os.path.isdir(p)]
    if folders:
        raise ValueError("trichrome takes three RAW files, not folders: "
                         + ", ".join(folders))
    unique = list(dict.fromkeys(os.path.normcase(os.path.abspath(p))
                                for p in paths))
    if len(unique) != len(paths):
        raise ValueError("the same file is named more than once — give the "
                         "three frames of one shot, one per light")
    if len(paths) != merge_mod.MERGE_GROUP_SIZE:
        raise ValueError(f"trichrome merges exactly 3 RAW files (got "
                         f"{len(paths)}): the three frames of one shot, one "
                         "per light")
    ordered = merge_mod.sort_for_merge(paths)
    ok, err = merge_mod.validate_merge_inputs(ordered)
    if not ok:
        raise ValueError(err)
    missing = [p for p in ordered if not os.path.isfile(p)]
    if missing:
        raise ValueError("no such file: " + ", ".join(missing))
    merges = [p for p in ordered if dng_mod.is_merge_dng(p)]
    if merges:
        raise ValueError("already a trichrome merge, not a source frame: "
                         + ", ".join(os.path.basename(p) for p in merges))

    out = output_path(fid, ordered[0], out_dir)
    if os.path.exists(out):
        raise ValueError(f"{out} already exists — not overwriting it. Choose "
                         "another film ID or output folder, or move the "
                         "existing file")
    return Job(sources=tuple(ordered), film_id=fid, output=out)


def run_job(job: Job, demosaic: bool = True,
            light_order: str = merge_mod.DEFAULT_LIGHT_ORDER,
            delete_originals: bool = False,
            dry_run: bool = False) -> JobResult:
    """Merge the triplet, write + verify its linear DNG, then — only with
    `delete_originals` — permanently delete the three source RAWs. See the
    module docstring for the exact deletion rules. Never raises: the outcome,
    good or bad, is in the result."""
    result = JobResult(job=job)
    if dry_run:
        return result
    if os.path.exists(job.output):
        # Planning checked; this is the file appearing since.
        result.error = f"{job.output} already exists — not overwriting it"
        return result
    try:
        os.makedirs(os.path.dirname(os.path.abspath(job.output)), exist_ok=True)
        merged, full_size = merge_mod.merge_raw_channels(
            job.sources, preview=False, demosaic=demosaic,
            light_order=light_order)
        # The first frame's camera metadata rides along with the pixels;
        # `warning` is set when it could not be read, which is worth saying
        # but never worth failing a merge over (dng.py).
        result.warning = dng_mod.write_linear_dng(
            job.output, merged, source=job.sources[0], identifier=job.film_id)
        del merged                        # a full-res 16-bit RGB frame
        # Before anything can be deleted: the file on disk really is the
        # image it claims, at the size the merge produced.
        dng_mod.verify_linear_dng(job.output, expect_shape=full_size)
    except Exception as e:
        result.error = str(e)
        result.warning = None
        # A partial/corrupt file must not be left behind claiming the name.
        try:
            if os.path.exists(job.output):
                os.remove(job.output)
        except OSError:
            pass
        return result
    result.size = tuple(full_size)

    if delete_originals:
        for path in job.sources:
            path = os.path.abspath(path)
            try:
                if os.path.exists(path):
                    os.remove(path)
                    result.deleted.append(path)
            except OSError as e:
                result.delete_errors.append((path, str(e)))
    return result
