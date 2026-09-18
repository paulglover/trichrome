"""
trichrome — merge a red/green/blue-light RAW triplet into one linear DNG.

Shoot one static scene three times, once under each pure light, and this turns
the three RAWs into one full-colour 16-bit linear image by
taking each frame's own colour channel — no demosaic crosstalk, no white
balance, no colour matrix, no tone curve. Optionally it then deletes the source
RAWs, once the replacement has been written and verified.

The result is written as a linear DNG, which a converter opens through its RAW
pipeline — raw white balance, exposure in stops ahead of the tone curve — and
which carries the camera metadata of the triplet's first frame (date, body,
lens, exposure), so a merged file is still filed and sorted like a photograph
once the RAWs are gone. It is named after its film ID, which is also written
into it as XMP dc:identifier.

Public API:

    from trichrome import merge_raw_channels, plan_job, run_job
"""
__version__ = "0.5.0"

from .bake import Job, JobResult, plan_job, run_job, validate_film_id
from .dng import (is_merge_dng, read_linear_dng, verify_linear_dng,
                  write_linear_dng)
from .exif import read_source_metadata
from .merge import (MERGE_GROUP_SIZE, RAW_EXTENSIONS, combine_channels,
                    group_into_triplets, is_raw_path, merge_raw_channels,
                    sort_for_merge, validate_merge_inputs)

__all__ = [
    "__version__",
    "Job", "JobResult", "plan_job", "run_job", "validate_film_id",
    "is_merge_dng", "read_linear_dng", "verify_linear_dng", "write_linear_dng",
    "read_source_metadata",
    "MERGE_GROUP_SIZE", "RAW_EXTENSIONS", "combine_channels",
    "group_into_triplets", "is_raw_path", "merge_raw_channels",
    "sort_for_merge", "validate_merge_inputs",
]
