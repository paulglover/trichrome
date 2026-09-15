"""
trichrome — merge red/green/blue-light RAW triplets into linear TIFFs or DNGs.

Shoot one static scene three times, once under each pure light, and this turns
every consecutive triplet of RAWs into one full-colour 16-bit linear image by
taking each frame's own colour channel — no demosaic crosstalk, no white
balance, no colour matrix, no tone curve. Optionally it then deletes the source
RAWs, once each replacement has been written and verified.

The result is written as a linear TIFF (compact and archival) or, with
`fmt="dng"`, a linear DNG that a converter opens through its RAW pipeline —
same pixels either way.

Public API:

    from trichrome import merge_raw_channels, plan_jobs, run_jobs

Dust, hair and scratch removal for the uninverted merge is opt-in and lives in
`trichrome.despeck` (configured with `DespeckOptions`); it is the only part of
this package that changes pixels rather than metadata.
"""
__version__ = "0.3.1"

from .bake import (DEFAULT_FORMAT, OUTPUT_FORMATS, Job, JobResult, Summary,
                   collect_raw_files, plan_jobs, run_jobs)
from .despeck import Options as DespeckOptions
from .despeck import Stats as DespeckStats
from .dng import (is_merge_dng, read_linear_dng, verify_linear_dng,
                  write_linear_dng)
from .icc import linear_rgb_profile
from .merge import (MERGE_GROUP_SIZE, RAW_EXTENSIONS, combine_channels,
                    group_into_triplets, is_raw_path, merge_raw_channels,
                    sort_for_merge, validate_merge_inputs)
from .tiff import (embedded_icc_profile, is_merge_tiff, verify_linear_tiff,
                   write_linear_tiff)

__all__ = [
    "__version__",
    "DEFAULT_FORMAT", "OUTPUT_FORMATS",
    "Job", "JobResult", "Summary", "collect_raw_files", "plan_jobs", "run_jobs",
    "DespeckOptions", "DespeckStats",
    "is_merge_dng", "read_linear_dng", "verify_linear_dng", "write_linear_dng",
    "MERGE_GROUP_SIZE", "RAW_EXTENSIONS", "combine_channels",
    "group_into_triplets", "is_raw_path", "merge_raw_channels",
    "sort_for_merge", "validate_merge_inputs",
    "embedded_icc_profile", "is_merge_tiff", "linear_rgb_profile",
    "verify_linear_tiff", "write_linear_tiff",
]
