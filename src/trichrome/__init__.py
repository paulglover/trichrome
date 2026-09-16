"""
trichrome — merge red/green/blue-light RAW triplets into linear TIFFs or DNGs.

Shoot one static scene three times, once under each pure light, and this turns
every consecutive triplet of RAWs into one full-colour 16-bit linear image by
taking each frame's own colour channel — no demosaic crosstalk, no white
balance, no colour matrix, no tone curve. Optionally it then deletes the source
RAWs, once each replacement has been written and verified.

The result is written as a linear TIFF (compact and archival) or, with
`fmt="dng"`, a linear DNG that a converter opens through its RAW pipeline —
same pixels either way. A DNG also carries the camera metadata of the triplet's
first frame (date, body, lens, exposure), so a merged file is still filed and
sorted like a photograph once the RAWs are gone.

Public API:

    from trichrome import merge_raw_channels, plan_jobs, run_jobs
"""
__version__ = "0.4.0"

from .bake import (DEFAULT_FORMAT, OUTPUT_FORMATS, Job, JobResult, Summary,
                   collect_raw_files, plan_jobs, run_jobs)
from .dng import (is_merge_dng, read_linear_dng, verify_linear_dng,
                  write_linear_dng)
from .exif import read_source_metadata
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
    "is_merge_dng", "read_linear_dng", "verify_linear_dng", "write_linear_dng",
    "read_source_metadata",
    "MERGE_GROUP_SIZE", "RAW_EXTENSIONS", "combine_channels",
    "group_into_triplets", "is_raw_path", "merge_raw_channels",
    "sort_for_merge", "validate_merge_inputs",
    "embedded_icc_profile", "is_merge_tiff", "linear_rgb_profile",
    "verify_linear_tiff", "write_linear_tiff",
]
