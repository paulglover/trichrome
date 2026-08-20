"""
Trichrome (3-way RGB-light) merge core.

The photographer shoots one static scene three times under a single pure light
each — red, then green, then blue — and every consecutive triplet of source RAWs
is merged into one full-colour image by taking each frame's OWN colour channel
and discarding the other two.

Two sensor kinds are supported:

* **Bayer (RGGB)** — read the RAW Bayer mosaic directly and take ONLY the
  wanted colour's photosites, with NO demosaic and NO libraw colour pipeline, and
  never mixing in the OTHER colours, so there is zero inter-channel crosstalk.
  R and B use their single site per quad; green averages its two same-colour
  sites (not crosstalk — both are green — and it preserves the green SNR). A
  photosite Bayer merge is half-sensor resolution (one site per quad = its full
  resolution). NB: libraw `half_size=True` gives a numerically similar result,
  but it goes through the postprocess pipeline; reading the mosaic directly keeps
  full control and guarantees no hidden colour operation.

  With `demosaic=True` (the default) the same frames instead go through a LINEAR
  (bilinear) demosaic and the frame's channel is taken from that, at FULL sensor
  resolution. Bilinear interpolates each colour plane only from its OWN
  photosites, so the "no inter-channel crosstalk" guarantee still holds.

* **Monochrome** — no CFA at all, so there is nothing to demosaic: every
  photosite measured the (single) light's intensity. The whole grayscale frame
  IS that frame's channel, at FULL sensor resolution. A monochrome sensor is in
  fact the ideal trichrome sensor (no wasted photosites, full resolution).

Either way each frame contributes exactly one channel (R from the red-light
frame, G from green, B from blue). Every plane reaches `combine_channels`
already black-subtracted (by libraw on the demosaic path, per site on the
photosite one), so it is normalised to 16-bit by 65535/(white_level -
black_level) — the sensor's USABLE range, not its raw code range. Dividing by
the un-subtracted white_level would leave every channel short by
black_level/white_level (~3% on a 512/16383 sensor, ~12.5% on a 2048/16383 one)
and, because the pedestal can differ per frame, would tint the merge as well as
darken it. The result is camera-native linear RGB — no white balance, no colour
matrix, no gamma, no tone curve.

Everything except `merge_raw_channels` is pure and unit-testable without rawpy.
"""
import os
from typing import List, Optional, Sequence, Tuple

import numpy as np

# RAW extensions whose Bayer mosaic rawpy can decode. Deliberately excludes
# .fff (Hasselblad 3FR-in-TIFF wrapper, not a bare CFA) and every non-RAW
# format — only files this set covers can be channel-extracted.
RAW_EXTENSIONS = frozenset({
    ".cr3", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".raf",
    ".srw", ".pef", ".3fr",
})

# Number of source frames per merged image (red, green, blue).
MERGE_GROUP_SIZE = 3

# The light order the frames were shot in, as a 3-letter string. "RGB" means the
# first frame of each triplet was lit red, the second green, the third blue.
DEFAULT_LIGHT_ORDER = "RGB"


def is_raw_path(path: str) -> bool:
    """True when the file extension is a RAW format this module can merge."""
    return os.path.splitext(str(path))[1].lower() in RAW_EXTENSIONS


def sort_for_merge(paths: Sequence[str]) -> List[str]:
    """Order paths by case-insensitive basename (directory ignored), so the
    triplet ordering is determined purely by filename — which is how a camera
    names a sequential burst. Ties (same basename in different dirs) keep a
    stable order via the full normalized path as a secondary key."""
    return sorted(paths, key=lambda p: (os.path.basename(p).lower(),
                                        os.path.normcase(p)))


def group_into_triplets(sorted_paths: Sequence[str]) -> List[Tuple[str, str, str]]:
    """Group an already-sorted path list into consecutive (frame 1, 2, 3)
    triplets. The caller validates the count is a multiple of 3 first; any
    trailing remainder (< 3) is dropped here."""
    n = (len(sorted_paths) // MERGE_GROUP_SIZE) * MERGE_GROUP_SIZE
    return [tuple(sorted_paths[i:i + MERGE_GROUP_SIZE])  # type: ignore[misc]
            for i in range(0, n, MERGE_GROUP_SIZE)]


def parse_light_order(order: str) -> Tuple[int, int, int]:
    """Turn a light-order string like "RGB" or "BGR" into, for each output
    channel (R, G, B), the index of the frame in the triplet that carries it.

    "RGB" -> (0, 1, 2): frame 0 was the red light, so it supplies output R.
    "BGR" -> (2, 1, 0): the BLUE light was shot first, so frame 0 supplies B.

    Raises ValueError unless the string is a permutation of R, G and B."""
    s = str(order).upper().strip()
    if len(s) != 3 or set(s) != set("RGB"):
        raise ValueError(f"light order must be a permutation of R, G and B "
                         f"(e.g. RGB, BGR); got {order!r}")
    return (s.index("R"), s.index("G"), s.index("B"))


def validate_merge_inputs(paths: Sequence[str]) -> Tuple[bool, Optional[str]]:
    """Pre-decode validation for a merge batch. Returns (ok, error_message).

    Checks, in order: non-empty, every file is a supported RAW, count is a
    multiple of 3. The sensor check (Bayer vs monochrome vs unsupported) can only
    happen at decode time (see merge_raw_channels), so it is NOT done here."""
    if not paths:
        return False, "No files given to merge."
    non_raw = [p for p in paths if not is_raw_path(p)]
    if non_raw:
        shown = "\n".join(os.path.basename(p) for p in non_raw[:8])
        if len(non_raw) > 8:
            shown += f"\n… and {len(non_raw) - 8} more"
        return False, ("Trichrome merge requires RAW files. "
                       f"These are not supported RAW:\n{shown}")
    if len(paths) % MERGE_GROUP_SIZE != 0:
        return False, (f"Trichrome merge needs a multiple of 3 images "
                       f"(got {len(paths)}). Three frames per shot: "
                       f"one per light.")
    return True, None


def bayer_channel_indices(color_desc) -> Tuple[int, int, int]:
    """Map (R, G, B) to the output-channel indices a camera-native (output_color
    =raw) decode emits, which follow libraw's internal colour order == the
    `color_desc` string. For the canonical b'RGBG' this is (0, 1, 2); a permuted
    desc is honoured. Raises ValueError when the sensor is not an R/G/B Bayer
    (e.g. monochrome b'G', or 4-colour b'RGBE'/CYGM), which must be rejected."""
    if isinstance(color_desc, (bytes, bytearray)):
        s = bytes(color_desc).decode("ascii", "ignore").upper()
    else:
        s = str(color_desc).upper()
    r, g, b = s.find("R"), s.find("G"), s.find("B")
    if -1 in (r, g, b):
        raise ValueError(
            f"trichrome merge requires a Bayer (RGGB) sensor; color_desc "
            f"{color_desc!r} is not R/G/B (X-Trans, monochrome or 4-colour).")
    return r, g, b


def combine_channels(plane_r: np.ndarray, plane_g: np.ndarray, plane_b: np.ndarray,
                     white_levels: Sequence[float],
                     black_levels: Optional[Sequence[float]] = None) -> np.ndarray:
    """Pure merge core: take the red frame's R-plane, the green frame's G-plane,
    and the blue frame's B-plane (each a 2-D array, already the correct channel),
    normalise each to 16-bit, and stack into one (H, W, 3) uint16 RGB image.

    `black_levels` is the pedestal ALREADY REMOVED from each plane before it got
    here — nothing further is subtracted. It only fixes the divisor: a
    black-subtracted plane spans 0..(white_level - black_level), so that, not
    white_level, is what maps to 65535. Passing no black levels reproduces the
    old 65535/white_level scaling, which under-exposes every channel by
    black_level/white_level.

    Planes may differ slightly in size; all are cropped to the common (min H,
    min W). Returns linear RGB in [0, 65535]."""
    planes = [plane_r, plane_g, plane_b]
    if len(white_levels) != 3:
        raise ValueError("white_levels must have 3 entries (R, G, B)")
    if black_levels is None:
        black_levels = (0.0, 0.0, 0.0)
    elif len(black_levels) != 3:
        raise ValueError("black_levels must have 3 entries (R, G, B)")
    h = min(p.shape[0] for p in planes)
    w = min(p.shape[1] for p in planes)
    out = np.empty((h, w, 3), dtype=np.uint16)
    for i, (plane, wl, bl) in enumerate(zip(planes, white_levels, black_levels)):
        cropped = plane[:h, :w].astype(np.float32)
        span = (wl or 0.0) - (bl or 0.0)
        scale = 65535.0 / span if span > 0 else 1.0
        out[..., i] = np.clip(cropped * scale, 0, 65535).astype(np.uint16)
    return out


def _desc_bytes(color_desc) -> bytes:
    if isinstance(color_desc, (bytes, bytearray)):
        return bytes(color_desc)
    return str(color_desc).encode("ascii", "ignore")


def is_monochrome_sensor(num_colors, color_desc, raw_pattern=None) -> bool:
    """Whether a RAW comes from a monochrome (no-CFA) sensor: num_colors == 1, a
    grey color_desc, or an RGBG desc whose CFA pattern is all one colour index.
    Pure (takes the rawpy primitives, not a raw object), so it is
    unit-testable."""
    if num_colors == 1:
        return True
    cd = _desc_bytes(color_desc)
    if cd in (b"G", b"GRAY", b"GREY"):
        return True
    if cd == b"RGBG" and raw_pattern is not None:
        try:
            if int(np.asarray(raw_pattern).max()) == 0:
                return True
        except Exception:
            pass
    return False


def channel_black_level(color_desc, letter: str, black_levels,
                        colors=None) -> float:
    """The black pedestal that has ALREADY been removed from the `letter` plane,
    so `combine_channels` can divide by the usable range instead of the raw one.

    A plane is the mean of the sites that carry `letter`, so its pedestal is the
    mean of THEIR black levels — which matters for green, whose two sites may sit
    at different libraw colour indices (b'RGBG' -> 1 and 3) with different
    pedestals. Sites are counted with multiplicity, exactly as
    `extract_cfa_channel` averages them.

    `colors` is a CFA colour-index map (`raw_colors_visible`, or the 2x2
    `raw_pattern` — only the 2x2 tile is read, and only WHICH indices it holds,
    not where they sit, so either origin phase gives the same answer). Without it
    every index in `color_desc` spelling `letter` is used. Returns 0.0 when the black
    levels are unknown or none of them apply.

    NB this sees only libraw's per-channel pedestal. A camera that encodes black
    as a 2-D cblack PATTERN instead reports zeros here — but rawpy leaves that
    pedestal in `raw_image_visible` too, so both paths then agree on 0 and the
    merge stays self-consistent; it is simply uncorrected, exactly as before.

    Pure — unit-testable without rawpy."""
    if black_levels is None:
        return 0.0
    desc = _desc_bytes(color_desc).decode("ascii", "ignore").upper()
    letter = letter.upper()
    if colors is not None:
        tile = np.asarray(colors)[:2, :2].ravel()
        indices = [int(v) for v in tile
                   if 0 <= int(v) < len(desc) and desc[int(v)] == letter]
    else:
        indices = [i for i, ch in enumerate(desc) if ch == letter]
    vals = [float(black_levels[i]) for i in indices if i < len(black_levels)]
    return sum(vals) / len(vals) if vals else 0.0


def extract_cfa_channel(mosaic: np.ndarray, colors: np.ndarray, color_desc,
                        letter: str, black_levels=None) -> np.ndarray:
    """From a raw Bayer mosaic (2-D sensor read-out) and its per-pixel CFA colour
    indices, return the half-resolution plane for ONE colour `letter` (R/G/B) by
    phase-slicing the 2x2 lattice — NO demosaic, NO colour matrix, and NO mixing
    with the OTHER colours, so there is zero inter-channel crosstalk.

    R and B have a single site per quad → that bare site. Green has two sites per
    quad (same colour, not crosstalk) → their per-site-black-subtracted AVERAGE,
    which keeps the green SNR. Each contributing site is matched by its CFA letter
    via `color_desc` (so it works whether the two greens share one colour index or
    use indices 1 and 3), located by its phase in the tile read from the actual
    `colors` at the visible origin (offset-safe; the CFA is period-2 so
    `mosaic[dy::2, dx::2]` is exactly that phase's sites), and black-subtracted by
    its own colour index when `black_levels` is given. Pure — unit-testable
    without rawpy."""
    eff = np.asarray(colors)[:2, :2]
    desc = _desc_bytes(color_desc).decode("ascii", "ignore").upper()
    letter = letter.upper()
    phases = [(dy, dx, int(eff[dy, dx]))
              for dy in range(eff.shape[0]) for dx in range(eff.shape[1])
              if 0 <= int(eff[dy, dx]) < len(desc) and desc[int(eff[dy, dx])] == letter]
    if not phases:
        raise ValueError(f"CFA colour {letter!r} not present in the 2x2 tile "
                         f"{eff.tolist()} (color_desc {desc!r})")
    m = np.asarray(mosaic)
    subs = [m[dy::2, dx::2].astype(np.float32) for dy, dx, _ in phases]
    h = min(s.shape[0] for s in subs)
    w = min(s.shape[1] for s in subs)
    acc = np.zeros((h, w), dtype=np.float32)
    for (dy, dx, idx), s in zip(phases, subs):
        black = 0.0
        if black_levels is not None and idx < len(black_levels):
            black = float(black_levels[idx])
        acc += s[:h, :w] - black
    return acc / len(phases)            # 1 site for R/B; average of 2 for green


def _decode_frame_plane(path: str, letter: str, preview: bool = False,
                        demosaic: bool = True):
    """Decode one source RAW and return (plane_2d, white_level, black_level,
    is_mono, sensor_full) for the single colour `letter` (R/G/B) this frame
    contributes.

    `plane_2d` is ALWAYS black-subtracted, and `black_level` is the pedestal that
    was taken off it, so `combine_channels` can normalise by the usable range
    (white_level - black_level) rather than the raw code range.

    * Bayer, demosaic=False: read the RAW Bayer mosaic directly
      (`raw.raw_image_visible`) and take ONLY this frame's colour photosites — no
      demosaic, no libraw colour pipeline, and never mixing in the OTHER colours.
      R/B use their single site per quad; green averages its two same-colour
      sites. The black pedestal is subtracted per site manually (raw_image
      carries it). Half-sensor resolution is this mode's full resolution;
      `preview` is irrelevant (the read is already cheap).
    * Bayer, demosaic=True: a LINEAR (bilinear) demosaic at full sensor
      resolution, then take this frame's channel. Bilinear interpolates each
      plane from its own sites only, so there is still no inter-channel mixing.
    * Monochrome: no CFA — the whole grayscale frame IS the channel, at FULL
      sensor resolution. `preview` may decode at half size, but the canonical
      full resolution reported is always the full sensor.

    Raises ValueError on an unsupported sensor (X-Trans, 4-colour)."""
    import rawpy

    with rawpy.imread(path) as raw:
        white_level = float(raw.white_level)
        sensor_full = (int(raw.sizes.height), int(raw.sizes.width))
        num_colors = int(getattr(raw, "num_colors", 0) or 0)
        color_desc = getattr(raw, "color_desc", b"")
        pattern = getattr(raw, "raw_pattern", None)

        # Read before postprocess, which invalidates libraw's raw buffers. Both
        # paths need it: the photosite one to subtract the pedestal itself, the
        # demosaic one to know what libraw already subtracted.
        try:
            black_levels = list(raw.black_level_per_channel)
        except Exception:
            black_levels = None

        # Identical decode kwargs for both postprocess paths: linear, absolute
        # sensor values (no_auto_scale — combine_channels normalises by the usable
        # white_level - black_level range), black-subtracted by libraw,
        # camera-native primaries, no WB.
        linear_kwargs = dict(
            output_bps=16,
            no_auto_bright=True,
            gamma=(1, 1),                                  # linear
            user_flip=0,                                   # no rotation
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            half_size=preview,         # full res unless a fast preview decode
            use_camera_wb=False,
            use_auto_wb=False,
            output_color=rawpy.ColorSpace.raw,
            no_auto_scale=True,        # absolute sensor values; scale manually
            adjust_maximum_thr=0.0,
            four_color_rgb=False,
        )

        if is_monochrome_sensor(num_colors, color_desc, pattern):
            # One colour, so one pedestal: libraw's colour index 0.
            black_level = float(black_levels[0]) if black_levels else 0.0
            rgb = raw.postprocess(**linear_kwargs)
            plane = rgb if rgb.ndim == 2 else rgb[..., 0]   # channels are equal
            return (np.ascontiguousarray(plane), white_level, black_level, True,
                    sensor_full)

        # Bayer (RGGB): require a 3-colour 2x2 R/G/B mosaic.
        if num_colors != 3:
            raise ValueError(
                f"trichrome merge requires a Bayer (RGGB) or monochrome sensor; "
                f"{os.path.basename(path)} reports {num_colors} colours "
                f"(e.g. 4-colour CYGM/RGBE).")
        if pattern is not None and tuple(np.asarray(pattern).shape) != (2, 2):
            raise ValueError(
                f"trichrome merge requires a 2x2 Bayer mosaic or a monochrome "
                f"sensor; {os.path.basename(path)} is non-Bayer (e.g. X-Trans).")
        channel_indices = bayer_channel_indices(color_desc)  # raises if not R/G/B

        if demosaic:
            # libraw subtracts the pedestal itself here; `pattern` (2x2) names the
            # colour indices whose pedestals ended up in this plane, without
            # materialising a full-frame raw_colors_visible.
            black_level = channel_black_level(color_desc, letter, black_levels,
                                              colors=pattern)
            rgb = raw.postprocess(**linear_kwargs)
            plane = rgb[..., channel_indices["RGB".index(letter.upper())]]
            return (np.ascontiguousarray(plane), white_level, black_level, False,
                    sensor_full)

        mosaic = np.asarray(raw.raw_image_visible)
        colors = np.asarray(raw.raw_colors_visible)
        black_level = channel_black_level(color_desc, letter, black_levels,
                                          colors=colors)
        plane = extract_cfa_channel(mosaic, colors, color_desc, letter,
                                    black_levels=black_levels)
        plane = np.clip(plane, 0, None)
        return (np.ascontiguousarray(plane), white_level, black_level, False,
                (plane.shape[0], plane.shape[1]))


def merge_raw_channels(sources: Sequence[str], preview: bool = False,
                       demosaic: bool = True,
                       light_order: str = DEFAULT_LIGHT_ORDER
                       ) -> Tuple[np.ndarray, Tuple[int, int]]:
    """Merge a triplet of RAW files into one (H, W, 3) uint16 linear-RGB image,
    taking only each frame's own colour channel.

    `light_order` says which light each frame in the triplet was shot under
    (default "RGB": first frame red, second green, third blue).

    `demosaic=True` (default): Bayer frames go through a LINEAR (bilinear,
    per-channel — still no inter-channel mixing) demosaic at FULL sensor
    resolution. `demosaic=False` (single photosite): Bayer frames are
    mosaic-phase-sliced — no demosaic at all — at half-sensor resolution (2x2
    bin). Monochrome sensors have no CFA and decode identically in both modes.
    `preview` lets a monochrome or demosaic decode run at half size for a fast
    check (the photosite read is already cheap and ignores it).

    Each plane is black-subtracted and then normalised to 16-bit by
    65535/(white_level - black_level) — its frame's own usable range, read from
    that frame's own metadata.

    Returns (merged_rgb, full_size=(H, W)) where full_size is the merged image's
    canonical FULL (export) resolution. Raises ValueError on an unsupported
    sensor or a decode failure."""
    if len(sources) != MERGE_GROUP_SIZE:
        raise ValueError(f"merge_raw_channels needs exactly {MERGE_GROUP_SIZE} "
                         f"sources, got {len(sources)}")
    for p in sources:
        if not os.path.exists(p):
            raise ValueError(f"trichrome merge source missing: {p}")

    frame_for = parse_light_order(light_order)   # output channel -> frame index

    planes: List[np.ndarray] = []
    white_levels: List[float] = []
    black_levels: List[float] = []
    monos: List[bool] = []
    sensor_full: Optional[Tuple[int, int]] = None
    for out_ch, letter in enumerate("RGB"):
        path = sources[frame_for[out_ch]]
        plane, white_level, black_level, mono, sfull = _decode_frame_plane(
            path, letter, preview, demosaic=demosaic)
        planes.append(plane)
        white_levels.append(white_level)
        black_levels.append(black_level)
        monos.append(mono)
        if sensor_full is None:
            sensor_full = sfull

    # All three frames must be the same sensor type — mixing a monochrome
    # (full-res) frame with Bayer (half-res) ones would silently min-crop into a
    # misaligned merge. A real trichrome set is always one body, so reject.
    any_mono = any(monos)
    if any_mono and not all(monos):
        raise ValueError("trichrome merge sources must all be the same sensor "
                         "type (all Bayer, or all monochrome).")

    merged = combine_channels(planes[0], planes[1], planes[2], white_levels,
                              black_levels)
    # Canonical FULL resolution: full sensor for monochrome and for a demosaiced
    # Bayer merge (both may decode a half-size preview while their real
    # resolution is the full sensor); the 2x2-binned size for a photosite Bayer
    # merge (its only resolution).
    full_size = (sensor_full if (any_mono or demosaic)
                 else (merged.shape[0], merged.shape[1]))
    return merged, full_size
