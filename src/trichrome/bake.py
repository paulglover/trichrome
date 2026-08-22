"""
Batch orchestration: plan triplets, merge each, write a verified linear file,
and (opt-in) permanently delete the source RAWs.

Two output formats, chosen per plan: a linear TIFF (the compact archival form,
tiff.py) or a linear DNG (opens through a converter's RAW pipeline, dng.py).
They carry identical pixels; only the container and its metadata differ. This
module treats them interchangeably — the deletion rules below hold for both.

Deletion safety is the whole point of this module, so the rules are explicit:

* A source RAW is deleted ONLY after its replacement exists on disk and reads
  back as a valid uint16 RGB image of the expected size.
* If any triplet fails, none of ITS sources are deleted — and neither are those
  sources' copies in any other triplet that happened to reference them
  (shared-source safety: a frame's only copy is never orphaned).
* Cancelling deletes nothing and removes the files written so far, leaving the
  folder exactly as it was.
* `dry_run` decodes and merges nothing, deletes nothing — it just reports the
  plan.
"""
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from . import dng as dng_mod
from . import merge as merge_mod
from . import tiff as tiff_mod

DEFAULT_NAME_SUFFIX = "_RGB"

# Output formats, in the spelling the CLI's --format takes.
FORMAT_TIFF = "tiff"
FORMAT_DNG = "dng"
DEFAULT_FORMAT = FORMAT_TIFF
OUTPUT_FORMATS = (FORMAT_TIFF, FORMAT_DNG)

_EXTENSIONS = {FORMAT_TIFF: tiff_mod.OUTPUT_EXTENSION,
               FORMAT_DNG: dng_mod.OUTPUT_EXTENSION}


def normalise_format(fmt: str) -> str:
    """Validate an output format name and return its canonical spelling.

    Raises ValueError with a user-facing message for anything else, so a typo is
    caught while planning rather than after the first decode."""
    name = str(fmt).lower().strip()
    if name not in _EXTENSIONS:
        raise ValueError(f"unknown output format {fmt!r}; expected one of "
                         + ", ".join(OUTPUT_FORMATS))
    return name


def output_extension(fmt: str = DEFAULT_FORMAT) -> str:
    """The file extension written for an output format."""
    return _EXTENSIONS[normalise_format(fmt)]


def unique_output_path(folder: str, stem: str,
                       ext: str = tiff_mod.OUTPUT_EXTENSION) -> str:
    """`folder/stem<ext>`, with a numeric suffix if that name is already taken —
    this tool NEVER overwrites an existing file."""
    out = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return out


def _write_output(path: str, merged, fmt: str, icc: bool) -> None:
    """Write a merged frame in `fmt`. `icc` is a TIFF-only concern — DNG states
    linearity in its own tags and ignores embedded ICC profiles entirely."""
    if fmt == FORMAT_DNG:
        dng_mod.write_linear_dng(path, merged)
    else:
        tiff_mod.write_linear_tiff(path, merged, icc=icc)


def _verify_output(path: str, fmt: str, expect_shape) -> None:
    """Confirm a just-written file really is the image it claims, BEFORE its
    sources can be deleted. Raises IOError on any mismatch."""
    if fmt == FORMAT_DNG:
        dng_mod.verify_linear_dng(path, expect_shape=expect_shape)
    else:
        tiff_mod.verify_linear_tiff(path, expect_shape=expect_shape)


@dataclass
class Job:
    """One planned merge: three source RAWs → one output file.

    `fmt` is settled at plan time because it decides the extension, and so the
    output path — carrying it on the job is what keeps the two from drifting
    apart between planning and writing."""
    sources: Tuple[str, str, str]
    output: str
    fmt: str = DEFAULT_FORMAT

    @property
    def name(self) -> str:
        return os.path.basename(self.output)


@dataclass
class JobResult:
    job: Job
    size: Optional[Tuple[int, int]] = None      # (H, W) of the written image
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Summary:
    results: List[JobResult] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    # (path, reason) for a source that was due for deletion but could not be
    # removed — the output is fine, the original is simply still there.
    delete_errors: List[Tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def written(self) -> List[JobResult]:
        return [r for r in self.results if r.ok]

    @property
    def failures(self) -> List[JobResult]:
        return [r for r in self.results if not r.ok]


def _is_mergeable_source(path: str) -> bool:
    """True for a RAW file a directory scan should offer as merge input: a
    supported extension, and not a merged DNG this tool wrote itself."""
    return merge_mod.is_raw_path(path) and not dng_mod.is_merge_dng(path)


def collect_raw_files(inputs: Sequence[str], recursive: bool = False) -> List[str]:
    """Expand `inputs` (files and/or directories) into a list of RAW file paths.

    Directories contribute their RAW files (recursively with `recursive=True`),
    EXCEPT this tool's own merged DNGs — `.dng` is a supported RAW extension, so
    without that exclusion a `--format dng` run would pick its own output back up
    as a source the second time it was run over a folder. Explicitly named files
    are taken as given, RAW or not, so that a wrong file is REPORTED by
    validation rather than silently skipped."""
    out: List[str] = []
    for item in inputs:
        if os.path.isdir(item):
            if recursive:
                for root, _dirs, files in os.walk(item):
                    out.extend(os.path.join(root, f) for f in files
                               if _is_mergeable_source(os.path.join(root, f)))
            else:
                out.extend(os.path.join(item, f) for f in sorted(os.listdir(item))
                           if os.path.isfile(os.path.join(item, f))
                           and _is_mergeable_source(os.path.join(item, f)))
        else:
            out.append(item)
    # De-duplicate (a file named twice, or caught by two inputs) while keeping
    # first-seen order; sort_for_merge orders the batch afterwards.
    seen = set()
    unique = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def plan_jobs(paths: Sequence[str], out_dir: Optional[str] = None,
              suffix: str = DEFAULT_NAME_SUFFIX,
              fmt: str = DEFAULT_FORMAT) -> List[Job]:
    """Sort, validate and group `paths` into merge jobs with output paths.

    Output goes next to the triplet's first frame unless `out_dir` is given, and
    is named `<first frame stem><suffix>` plus `fmt`'s extension (`.tif` or
    `.dng`). Existing files are never overwritten: a numeric suffix is added, and
    names claimed earlier in this same plan are reserved too (so two triplets
    from different folders cannot collide when writing into one `out_dir`).

    Raises ValueError with a user-facing message when the batch is invalid
    (empty, non-RAW files present, not a multiple of 3, or an unknown `fmt`)."""
    fmt = normalise_format(fmt)
    ext = _EXTENSIONS[fmt]
    ordered = merge_mod.sort_for_merge(paths)
    ok, err = merge_mod.validate_merge_inputs(ordered)
    if not ok:
        raise ValueError(err)

    jobs: List[Job] = []
    claimed = set()
    for triplet in merge_mod.group_into_triplets(ordered):
        first = triplet[0]
        folder = out_dir or os.path.dirname(os.path.abspath(first))
        stem = os.path.splitext(os.path.basename(first))[0] + suffix
        out = unique_output_path(folder, stem, ext)
        n = 2
        while os.path.normcase(out) in claimed:      # reserved by an earlier job
            out = os.path.join(folder, f"{stem}_{n}{ext}")
            n += 1
        claimed.add(os.path.normcase(out))
        jobs.append(Job(sources=triplet, output=out, fmt=fmt))
    return jobs


def run_jobs(jobs: Sequence[Job], demosaic: bool = True,
             light_order: str = merge_mod.DEFAULT_LIGHT_ORDER,
             delete_originals: bool = False, dry_run: bool = False,
             icc: bool = True,
             progress_cb: Optional[Callable[[int, int, Job], None]] = None,
             cancel_flag: Optional[Callable[[], bool]] = None) -> Summary:
    """Merge every job, write + verify its linear output, then — only with
    `delete_originals` — permanently delete the source RAWs of the jobs that
    succeeded. See the module docstring for the exact deletion rules.

    Each job carries the format it was planned for (`Job.fmt`), so a plan made
    for DNGs cannot be run as TIFFs into `.dng` filenames.

    `icc` embeds the linear ICC profile in each TIFF (see tiff.py); it changes
    tags only, never pixels, so it has no bearing on deletion safety. DNG output
    ignores it — that format states its own linearity (see dng.py).

    `progress_cb(index, total, job)` is called before each job starts.
    `cancel_flag()` is polled between jobs; returning True aborts cleanly."""
    summary = Summary()
    total = len(jobs)
    bad_sources = set()          # sources of FAILED jobs — never delete these

    for i, job in enumerate(jobs):
        if cancel_flag and cancel_flag():
            summary.cancelled = True
            break
        if progress_cb:
            progress_cb(i, total, job)
        if dry_run:
            summary.results.append(JobResult(job=job))
            continue
        try:
            os.makedirs(os.path.dirname(os.path.abspath(job.output)), exist_ok=True)
            merged, full_size = merge_mod.merge_raw_channels(
                job.sources, preview=False, demosaic=demosaic,
                light_order=light_order)
            _write_output(job.output, merged, job.fmt, icc)
            del merged                        # a full-res 16-bit RGB frame
            _verify_output(job.output, job.fmt, full_size)
        except Exception as e:
            summary.results.append(JobResult(job=job, error=str(e)))
            bad_sources.update(os.path.normcase(os.path.abspath(s))
                               for s in job.sources)
            # A partial/corrupt file must not be left behind claiming the name.
            try:
                if os.path.exists(job.output):
                    os.remove(job.output)
            except OSError:
                pass
            continue
        summary.results.append(JobResult(job=job, size=tuple(full_size)))

    if summary.cancelled:
        # Abort cleanly: nothing is ever deleted, and the files written so far
        # are removed so the folder is left untouched.
        for r in summary.written:
            try:
                os.remove(r.job.output)
            except OSError:
                pass
        summary.results = [r for r in summary.results if not r.ok]
        return summary

    if delete_originals and not dry_run:
        to_delete = []
        for r in summary.written:
            for s in r.job.sources:
                if os.path.normcase(os.path.abspath(s)) not in bad_sources:
                    to_delete.append(os.path.abspath(s))
        for path in dict.fromkeys(to_delete):        # unique, order-preserving
            try:
                if os.path.exists(path):
                    os.remove(path)
                    summary.deleted.append(path)
            except OSError as e:
                summary.delete_errors.append((path, str(e)))
    return summary
