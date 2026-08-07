"""
trichrome — merge red/green/blue-light RAW triplets into linear TIFFs.

Shoot one static scene three times, once under each pure light, and this turns
every consecutive triplet of RAWs into one full-colour 16-bit linear TIFF by
taking each frame's own colour channel — no demosaic crosstalk, no white
balance, no colour matrix, no tone curve. Optionally it then deletes the source
RAWs, once each replacement has been written and verified.

Public API:

    from trichrome import merge_raw_channels, plan_jobs, run_jobs
"""
__version__ = "0.2.0"

from .bake import Job, JobResult, Summary, collect_raw_files, plan_jobs, run_jobs
from .merge import (MERGE_GROUP_SIZE, RAW_EXTENSIONS, combine_channels,
                    group_into_triplets, is_raw_path, merge_raw_channels,
                    sort_for_merge, validate_merge_inputs)
from .tiff import (is_merge_tiff, verify_linear_tiff, write_linear_tiff)

__all__ = [
    "__version__",
    "Job", "JobResult", "Summary", "collect_raw_files", "plan_jobs", "run_jobs",
    "MERGE_GROUP_SIZE", "RAW_EXTENSIONS", "combine_channels",
    "group_into_triplets", "is_raw_path", "merge_raw_channels",
    "sort_for_merge", "validate_merge_inputs",
    "is_merge_tiff", "verify_linear_tiff", "write_linear_tiff",
]
