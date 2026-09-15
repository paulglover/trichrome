"""
Dust, hair and scratch removal for an UNINVERTED negative scan.

This runs on the merged linear RGB image, before any inversion, and it is
deliberately conservative: it would rather leave a speck than eat real detail.

Why it works, in four steps:

1. DENSITY, NOT LINEAR. A speck of dust is a *multiplicative* attenuation of
   transmitted light, so in the linear data it costs thousands of ADU in a thin
   (bright) part of the negative and a few dozen in a dense (dark) one — no
   single threshold can catch both. In density, D = -log10(value/white), the
   same speck adds a roughly CONSTANT offset anywhere in the frame, so the
   detector gets one image-independent threshold. Grain sits near dD 0.02-0.05;
   real dust is usually dD > 0.3.

2. A LOCAL BASELINE for FINDING a defect. On its own, "darker than the darkest
   exposed area" fails on three counts:
   specular highlights and light sources in the scene legitimately reach Dmax;
   a small speck is blurred by the lens PSF and never reaches full opacity; and
   illumination falloff shifts the levels across the frame. Instead a grayscale
   morphological OPENING with a square structuring element removes every
   structure narrower than the element, in either dimension, and the white
   top-hat `D - opening(D)` is what the opening stripped off. That catches round
   specks and — because an opening removes anything thin, however long — hairs
   and fine scratches too.

   Mind the polarity throughout: density runs opposite to the image, so a speck
   is a local MAXIMUM in D and it takes an opening to isolate it. Reaching for
   the closing that suits the linear image would find the picture's own dark
   detail instead.

   But local is only half of it. The global rule above is not wrong, merely
   unusable on its own — it is wrong as a DETECTOR and right as a CONFIRMATION,
   which is what step 3 does with it.

3. AN ABSOLUTE DENSITY FLOOR. The top-hat alone is not enough, and this is the
   trap the detector originally fell into. On an uninverted negative a speck of
   dust and a small bright HIGHLIGHT in the scene look alike: both are small,
   both are neutral, and both are locally much denser than what surrounds them.
   A purely local measurement cannot tell them apart, and a detector built on
   one will happily "repair" every catchlight and specular glint in the frame.

   What separates them is the absolute level the local measurement throws away.
   A highlight is exposure, so it is bounded by what the film can record; dust
   is an obstruction, so it goes denser than any exposed part of the negative
   can. Nothing is repaired unless it is denser than `floor_percentile` of the
   frame — the densest real film in the picture.

   The cost is honest: dust lying on a THIN part of the negative adds its
   density to a low base and may not clear the floor, so faint dust in the
   shadows of the scene is left alone. That is the intended direction to err.

4. NEUTRALITY ACROSS THE THREE CHANNELS. This is the part a trichrome merge
   gets for free. Dust and surface dirt are neutral: they attenuate R, G and B
   equally, so the three top-hat residuals agree. Real colour-negative detail
   almost never does, because the dye layers respond independently. Taking
   min(r_R, r_G, r_B) therefore passes only the neutral part of any dip, and
   requiring min/max above `neutrality` rejects the rest. Note the local
   residual cancels any per-channel gain, so this needs no white balance.

   Two honest limits. On a B&W negative the silver image is itself neutral, so
   the test goes vacuous — it passes real detail as readily as dust, and
   detection falls back entirely on morphology and shape. It does no harm there
   (leaving it on still rejects non-neutral noise), it simply stops helping, so
   `threshold` is the only knob that really bites. And a scratch
   that has gouged emulsion away has removed DYE, which is not neutral and is
   *less* dense than its surroundings, so it is not detected here at all (see
   the module TODO).

5. SUBTRACT WHERE YOU CAN, FILL ONLY WHERE YOU MUST. Under partial occlusion
   the image is still there, just attenuated, so subtracting the measured
   neutral density offset recovers real detail and keeps the grain. Only where
   attenuation is heavy enough to have destroyed the signal is a pixel replaced
   by a diffusion fill, and then synthetic grain matched to the local noise is
   added back — an unfilled diffusion patch is glassy-smooth and obvious at
   100%.

Everything here is pure numpy, on purpose: morphology is a separable
shift-and-max, and connected components are a union-find over only the handful
of masked pixels rather than the whole frame. That keeps the package's
dependencies as they are (numpy, rawpy, tifffile, imagecodecs).

Both stages are also sized for a big scan rather than a small one, because the
working set, not the picture, is what costs. Detection sweeps the frame in
horizontal strips (`_scan_tophat`), and the repair never converts the frame to
density at all: its subtraction has an exact linear equivalent, and each fill
builds density for its own window. An 80MP frame costs about 23 s and 2.9 GB
peak, a 24MP one about 9 s and 1.4 GB, both scaling linearly.

TODO, in rough order of value: a multiscale Hessian ridge filter (Frangi) for
long scratches, which the closing only catches while they stay thinner than the
structuring element; detection of the opposite polarity, for scratches that
removed emulsion and read lighter than their surroundings; and an exemplar or
coherence-transport fill to replace the diffusion one where a defect crosses
real structure.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

# The merge normalises every channel to 16-bit full scale, so white is fixed.
WHITE = 65535.0

# Densities are measured against a 1 ADU floor, capping D at ~4.8 — deep enough
# for any real negative and finite, which -log10(0) is not.
_FLOOR = 1.0

# Structuring element half-width. This is the real size limit of the
# detector: a defect much wider than this survives the opening in its
# middle and is found only by its rim (which _close_interiors then
# completes); wider still and it is not found at all. Raise it for big
# dust, at the cost of more false positives on small real detail.
DEFAULT_RADIUS = 4
# Minimum neutral density offset to call a defect. Real dust blocks most of
# the light reaching it; scene highlights and film detail routinely produce
# local swings of 0.2-0.4 D, so a lower bar repairs the picture instead. On a
# real 80MP scan carrying no dust at all, 0.15 found 211 "defects" and 0.5
# found none, while still catching planted dust down to 0.5 D attenuation.
DEFAULT_THRESHOLD = 0.50
DEFAULT_NEUTRALITY = 0.7    # required min(r)/max(r) across the three channels.
#                             Secondary only: taking min(r) as the offset
#                             already rejects a single-layer defect, and that
#                             part is not switchable.
DEFAULT_GROW = 2            # mask dilation, px — covers the defect's PSF penumbra
# At or below this density offset a defect is thinned by subtraction; above it
# the signal underneath is gone and the pixels are replaced. 1.0 D is 90% of the
# light blocked. This MUST stay above DEFAULT_THRESHOLD — below it, every defect
# that can be detected is by definition too heavy to subtract, and the
# detail-preserving path becomes unreachable.
DEFAULT_PARTIAL_MAX = 1.00
DEFAULT_MAX_AREA = 4000     # px; a blob bigger than this is presumed real content
DEFAULT_MAX_THICKNESS = 6.0  # px; ...unless it is this thin, i.e. a hair

# A mask larger than this is a misconfiguration, not a dirty negative (a B&W
# scan left at the default neutrality will do it). Refuse rather than quietly
# repaint the picture.
MAX_MASK_FRACTION = 0.05

# Frame percentile taken as "the densest exposed film in this picture"; nothing
# below it is repaired. Dust typically covers far less than 0.1% of a frame, so
# the percentile sits above the picture and below any obstruction. A negative
# that really is 1% dust would pull it upward — set `floor` explicitly there.
DEFAULT_FLOOR_PERCENTILE = 99.9

# Stride for estimating that percentile. Subsampling is not just cheaper, it is
# better: it steps over most dust, so the estimate describes the film.
_FLOOR_STRIDE = 4


@dataclass
class Options:
    """Detector and repair tuning. The defaults are the conservative end: they
    aim to leave a faint speck rather than touch real detail."""
    radius: int = DEFAULT_RADIUS
    threshold: float = DEFAULT_THRESHOLD
    # Absolute density a defect must reach before it is touched at all, as a
    # frame percentile; `floor` overrides it with a fixed density. This is what
    # keeps specular highlights out of the mask — see the module docstring.
    floor_percentile: float = DEFAULT_FLOOR_PERCENTILE
    floor: Optional[float] = None
    neutrality: float = DEFAULT_NEUTRALITY
    grow: int = DEFAULT_GROW
    partial_max: float = DEFAULT_PARTIAL_MAX
    max_area: int = DEFAULT_MAX_AREA
    max_thickness: float = DEFAULT_MAX_THICKNESS
    grain: bool = True
    seed: int = 0               # fixed, so a repeated run is bit-identical
    want_mask: bool = False     # also return the mask, for tuning by eye


@dataclass
class Stats:
    """What a despeck pass actually did, for the CLI to report."""
    defects: int = 0            # connected components repaired
    pixels: int = 0             # pixels in the grown mask
    subtracted: int = 0         # components repaired by subtracting the offset
    filled: int = 0             # components whose signal was gone, so replaced
    # Defects too wide for the structuring element, left ALONE rather than
    # repaired into a ring. A non-zero count means: raise `radius`.
    oversize: int = 0
    total_pixels: int = 0

    @property
    def fraction(self) -> float:
        return self.pixels / self.total_pixels if self.total_pixels else 0.0


# ---------------------------------------------------------------------------
# Separable grayscale morphology.
#
# A square structuring element is separable, so a 2-D max over (2r+1)^2 is a
# 1-D max along each axis. Each 1-D pass grows the window by doubling — radius
# w becomes w+d for d <= w+1, which leaves no gap — so a radius-r filter costs
# O(log r) passes rather than O(r).
# ---------------------------------------------------------------------------

def _shift(a: np.ndarray, d: int, axis: int) -> np.ndarray:
    """`a` shifted by `d` along `axis`, with the edge value replicated.

    Replication (rather than treating the outside as -inf/+inf) is what keeps a
    closing from inventing a defect along the frame border."""
    n = a.shape[axis]
    if d == 0 or n == 1:
        return a
    d = max(-(n - 1), min(n - 1, d))
    out = np.empty_like(a)
    src: List[slice] = [slice(None)] * a.ndim
    dst: List[slice] = [slice(None)] * a.ndim
    if d > 0:
        dst[axis], src[axis] = slice(d, None), slice(0, n - d)
        out[tuple(dst)] = a[tuple(src)]
        dst[axis], src[axis] = slice(0, d), slice(0, 1)      # replicate the low edge
        out[tuple(dst)] = a[tuple(src)]
    else:
        k = -d
        dst[axis], src[axis] = slice(0, n - k), slice(k, None)
        out[tuple(dst)] = a[tuple(src)]
        dst[axis], src[axis] = slice(n - k, None), slice(n - 1, n)
        out[tuple(dst)] = a[tuple(src)]
    return out


def _rank_1d(a: np.ndarray, r: int, axis: int, op) -> np.ndarray:
    """`op` (np.maximum or np.minimum) over a centred window of 2r+1 samples."""
    if r <= 0:
        return a
    cur = a
    w = 0
    while w < r:
        d = min(w + 1, r - w)       # never exceeds 2w+1, so the window stays gapless
        cur = op(op(cur, _shift(cur, d, axis)), _shift(cur, -d, axis))
        w += d
    return cur


def _rank_2d(a: np.ndarray, r: int, op) -> np.ndarray:
    return _rank_1d(_rank_1d(a, r, 0, op), r, 1, op)


def dilate(a: np.ndarray, r: int) -> np.ndarray:
    """Grayscale dilation by a (2r+1)-square: the local maximum."""
    return _rank_2d(a, r, np.maximum)


def erode(a: np.ndarray, r: int) -> np.ndarray:
    """Grayscale erosion by a (2r+1)-square: the local minimum."""
    return _rank_2d(a, r, np.minimum)


def opening(a: np.ndarray, r: int) -> np.ndarray:
    """Grayscale opening: erosion then dilation. Removes structures BRIGHTER
    than their surroundings and narrower than the structuring element in either
    dimension — which in density is exactly the family of defects we are after,
    specks and anything thin.

    Mind the polarity: density runs opposite to the image, so a speck of dust is
    a local MAXIMUM here, and it takes an opening (and a white top-hat) to pull
    it out. A closing would find the picture's dark detail instead."""
    return dilate(erode(a, r), r)


def _box3(a: np.ndarray) -> np.ndarray:
    """3x3 mean, edges replicated. Only ever called on a small patch."""
    acc = np.zeros_like(a)
    for dy in (-1, 0, 1):
        row = _shift(a, dy, 0)
        for dx in (-1, 0, 1):
            acc += _shift(row, dx, 1)
    return acc / 9.0


# ---------------------------------------------------------------------------
# Connected components over a sparse mask.
#
# Labelling the whole frame would mean a full-size int array and a pass per
# iteration; the mask is expected to cover well under 1% of it, so instead the
# masked pixels are pulled out and unioned in a dict. np.nonzero returns them in
# raster order, so unioning against the four already-seen 8-neighbours is
# enough to connect every component.
# ---------------------------------------------------------------------------

def _components(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Label the 8-connected components of `mask`.

    Returns (labels, ys, xs, count) where ys/xs are the masked pixel coordinates
    and labels[i] is the component index of pixel i."""
    ys, xs = np.nonzero(mask)
    n = ys.size
    if n == 0:
        return np.empty(0, np.intp), ys, xs, 0

    parent = list(range(n))

    def find(i: int) -> int:
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:        # path compression
            parent[i], i = root, parent[i]
        return root

    pos = {}
    ys_l, xs_l = ys.tolist(), xs.tolist()
    for i in range(n):
        pos[(ys_l[i], xs_l[i])] = i
    for i in range(n):
        y, x = ys_l[i], xs_l[i]
        for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1)):
            j = pos.get((y + dy, x + dx))
            if j is not None:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    roots = np.fromiter((find(i) for i in range(n)), np.intp, n)
    _, labels = np.unique(roots, return_inverse=True)
    return labels.astype(np.intp), ys, xs, int(labels.max()) + 1


def _component_bounds(labels, ys, xs, count):
    """Per-component pixel count and bounding box."""
    area = np.bincount(labels, minlength=count)
    y0 = np.full(count, 1 << 30, np.intp)
    x0 = np.full(count, 1 << 30, np.intp)
    y1 = np.zeros(count, np.intp)
    x1 = np.zeros(count, np.intp)
    np.minimum.at(y0, labels, ys)
    np.minimum.at(x0, labels, xs)
    np.maximum.at(y1, labels, ys)
    np.maximum.at(x1, labels, xs)
    return area, y0, y1, x0, x1


# A component whose bounding box is larger than this is not dust under any
# reading, so its interior is not worth flood-filling; the shape gate drops it
# a moment later anyway.
_MAX_INTERIOR_BBOX = 1 << 20

# Coverage past which consolidation is skipped; see _consolidate.
_MAX_CONSOLIDATE_COVERAGE = 0.10

# A fill is solved over its defect's bounding box, which for a long DIAGONAL
# hair is most of the frame even though the hair itself is a few pixels wide.
# Past this span the component is filled in tiles instead. Diffusion is local,
# so each tile reaches the same answer from its own surrounding film, at a
# fraction of the memory and time.
_FILL_TILE = 128

# The coarse classifying scale, as a multiple of `radius`, and how much larger
# its answer must be than a component before that component is read as a rim
# rather than as dust. See _reject_oversize.
_COARSE_FACTOR = 4
_OVERSIZE_RATIO = 6

# Rows per detection strip. An opening is local, so the top-hat can be swept in
# horizontal bands and never has to hold a frame-sized stack of float planes.
# At 80MP this is the difference between ~3 GB of working set and well under 1.
_STRIP_ROWS = 512


def _consolidate(mask: np.ndarray, r: int) -> np.ndarray:
    """Make a defect that outgrew the structuring element whole again.

    A blob wider than `r` survives the opening in its middle, so the top-hat
    fires only around its edge — and not even as a closed ring: the response
    breaks into arcs with gaps wherever the element fits into the curve. Left
    alone, the repair paints those arcs and leaves the core, which reads as a
    dark ring round an untouched speck. Strictly worse than not trying.

    The cure is a binary closing with the hole fill done BETWEEN the dilation
    and the erosion. Dilating bridges the gaps, but it can leave one unreached
    pixel at the blob's centre, and eroding straight back would then scrub an
    element-sized region around that pixel and hand back the arcs unchanged.
    Filling first removes the pinhole, so the erosion returns the solid blob.

    On an isolated speck this is a no-op: nothing is enclosed, and a closing
    only ever fills concavities. So the ordinary case pays nothing for it."""
    grown = _rank_2d(mask, r, np.logical_or)
    # Interior-filling is linear in masked pixels, and dilation can multiply
    # those by the element's area. Past this coverage the frame is pathological
    # anyway and "interior" has stopped meaning anything, so skip the work.
    if grown.mean() <= _MAX_CONSOLIDATE_COVERAGE:
        labels, ys, xs, count = _components(grown)
        if count:
            _close_interiors(grown, _component_bounds(labels, ys, xs, count))
    return _rank_2d(grown, r, np.logical_and)


def _close_interiors(mask: np.ndarray, bounds) -> bool:
    """Fill background enclosed by a component. Returns True if anything moved.

    Why this is needed at all: an opening only removes what is NARROWER than the
    structuring element, so a defect wider than `radius` survives in its middle
    and the top-hat fires on its rim alone. Left that way the repair paints a
    dark ring and leaves the core untouched, which is visibly worse than doing
    nothing. Closing the interior turns that annulus back into the blob it came
    from.

    It also has to happen BEFORE the shape gate: a ring is thin, so it would
    otherwise slip through the thinness escape meant for hairs, carrying its
    whole enclosed blob with it."""
    _, y0, y1, x0, x1 = bounds
    moved = False
    for k in range(len(y0)):
        h, w = int(y1[k] - y0[k]) + 1, int(x1[k] - x0[k]) + 1
        if h < 3 or w < 3 or h * w > _MAX_INTERIOR_BBOX:
            continue        # nothing can be enclosed, or far too big to be dust
        sub = mask[y0[k]:y1[k] + 1, x0[k]:x1[k] + 1]
        # Pad with a ring of background, so the flood always has somewhere to
        # start and "outside" is unambiguous.
        pad = np.zeros((h + 2, w + 2), bool)
        pad[1:-1, 1:-1] = sub
        free = ~pad
        reach = np.zeros_like(free)
        reach[0, :] = reach[-1, :] = True
        reach[:, 0] = reach[:, -1] = True
        while True:
            grown = (_shift(reach, 1, 0) | _shift(reach, -1, 0)
                     | _shift(reach, 1, 1) | _shift(reach, -1, 1))
            nxt = reach | (free & grown)
            if np.array_equal(nxt, reach):
                break
            reach = nxt
        holes = free & ~reach
        if holes.any():
            sub |= holes[1:-1, 1:-1]
            moved = True
    return moved


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def to_density(rgb: np.ndarray) -> np.ndarray:
    """Linear 16-bit RGB -> density, D = -log10(value/white). Multiplicative
    attenuation becomes additive here, which is the whole point."""
    lin = np.maximum(rgb.astype(np.float32), _FLOOR) / WHITE
    return -np.log10(lin, dtype=np.float32)


def from_density(d: np.ndarray) -> np.ndarray:
    """Density back to linear 16-bit RGB."""
    lin = np.power(10.0, -d.astype(np.float32), dtype=np.float32) * WHITE
    return np.clip(lin, 0.0, 65535.0).astype(np.uint16)


def neutral_residual(density: np.ndarray,
                     radius: int) -> Tuple[np.ndarray, np.ndarray]:
    """Per-pixel neutral density offset, and the channel agreement behind it.

    Returns (offset, agree): `offset` is min over channels of the white top-hat
    — how much excess density the opening stripped off — and `agree` is
    min(r)/max(r), the cross-channel neutrality score in [0, 1]."""
    # Accumulated a channel at a time rather than stacked into one (H, W, 3)
    # residual: only the running min and max are ever needed, and on a 24MP
    # frame the stacked version is a needless 290 MB at the pipeline's peak.
    offset = peak = None
    for c in range(3):
        chan = density[..., c]
        resid = chan - opening(chan, radius)
        np.maximum(resid, 0.0, out=resid)   # opening <= original, bar rounding
        if offset is None:
            offset, peak = resid, resid.copy()
        else:
            np.minimum(offset, resid, out=offset)
            np.maximum(peak, resid, out=peak)
    with np.errstate(invalid="ignore", divide="ignore"):
        agree = np.where(peak > 0, offset / np.maximum(peak, 1e-6), 0.0)
    return offset, agree.astype(np.float32)


def density_floor(rgb: np.ndarray, percentile: float = DEFAULT_FLOOR_PERCENTILE,
                  radius: int = DEFAULT_RADIUS,
                  stride: int = _FLOOR_STRIDE) -> float:
    """The density of the densest real film in this frame.

    Measured as the `percentile` of per-pixel density taken as the MINIMUM over
    the three channels: dust blocks every wavelength, so all three go dark
    together, where a saturated single dye layer does not. Subsampling keeps it
    cheap and, more usefully, steps over most dust, so the answer describes the
    picture rather than what is sitting on it."""
    sub = to_density(rgb[::stride, ::stride]).min(axis=2)
    # Open it first. An opening removes whatever is narrower than its element —
    # which is precisely the defects — so the percentile describes the PICTURE
    # and not the dust sitting on it. Without this the estimate rises with the
    # dirt on the negative, and a filthy frame quietly stops being cleaned.
    #
    # The element has to cover the largest defect the detector will accept,
    # scaled into the subsampled grid, with margin. Erring large only lowers the
    # floor, which costs a little protection; erring small lets the defects back
    # into their own estimate, which is the failure that matters.
    element = max(2, -(-radius // stride) + 2)
    return float(np.percentile(opening(sub, element), percentile))


def _scan_tophat(rgb: np.ndarray, radius: int, threshold: float,
                 neutrality: float, floor: Optional[float] = None,
                 offset_out: Optional[np.ndarray] = None) -> np.ndarray:
    """The neutral top-hat mask for a whole frame, swept in horizontal strips.

    An opening reaches exactly `2 * radius` rows (it erodes by radius, then
    dilates the result by radius), so a strip padded by that much computes
    interior rows identical to a whole-frame pass — this is a memory strategy,
    not an approximation. Padding is clipped at the frame edges, where `_shift`
    replicates and the whole-frame pass would do the same.

    Worth doing because the detector's working set, not the picture, is what
    costs: density, the running residual, min, max and the opening's own
    temporaries are five or six float planes at once. Frame-sized, an 80MP scan
    spends about 3 GB on them; a strip at a time it is a few hundred MB.

    `floor` additionally requires a pixel's own density to clear an absolute
    level, which is what keeps scene highlights out; see the module docstring.

    Fills `offset_out` with the per-pixel neutral offset when one is given."""
    h, w, _ = rgb.shape
    margin = 2 * radius
    step = max(_STRIP_ROWS, 4 * margin)
    mask = np.empty((h, w), bool)
    for y0 in range(0, h, step):
        y1 = min(h, y0 + step)
        a0, a1 = max(0, y0 - margin), min(h, y1 + margin)
        strip = to_density(rgb[a0:a1])
        offset, agree = neutral_residual(strip, radius)
        band = offset >= threshold
        if neutrality > 0:
            band &= agree >= neutrality
        if floor is not None:
            # The absolute-level confirmation: an obstruction is denser than any
            # exposed film in the frame, a highlight never is.
            band &= strip.min(axis=2) >= floor
        del strip
        inner = slice(y0 - a0, y1 - a0)
        mask[y0:y1] = band[inner]
        if offset_out is not None:
            offset_out[y0:y1] = offset[inner]
    return mask


def detect(rgb: np.ndarray, opt: Options) -> Tuple[np.ndarray, np.ndarray, int]:
    """Find defects.

    Returns (mask, offset, oversize) — a boolean defect mask grown by
    `opt.grow`, the per-pixel neutral density offset behind it, and the number
    of defects declined as too wide for the structuring element."""
    h, w, _ = rgb.shape
    floor = opt.floor if opt.floor is not None else (
        density_floor(rgb, opt.floor_percentile, opt.radius)
        if opt.floor_percentile else None)
    offset = np.empty((h, w), np.float32)
    mask = _scan_tophat(rgb, opt.radius, opt.threshold, opt.neutrality, floor,
                        offset)

    covered = float(mask.mean())
    if covered > MAX_MASK_FRACTION:
        raise ValueError(
            f"despeck matched {covered:.1%} of the frame, over the "
            f"{MAX_MASK_FRACTION:.0%} sanity limit \u2014 raise the threshold, or "
            f"lower the radius if the structuring element is eating real detail")

    mask, oversize = _reject_oversize(_consolidate(mask, opt.radius), rgb, opt,
                                      floor)
    mask = _gate_shape(mask, opt)
    if opt.grow > 0:
        mask = _rank_2d(mask, opt.grow, np.logical_or)
    return mask, offset, oversize


def _reject_oversize(mask: np.ndarray, rgb: np.ndarray, opt: Options,
                     floor: Optional[float] = None) -> Tuple[np.ndarray, int]:
    """Drop components that are only the RIM of a defect too wide to detect.

    Consolidation stretches the usable range to roughly 1.5x `radius`, but past
    that the top-hat response round a big blob is too sparse to bridge, and what
    survives is an arc — or, wider still, four lone pixels — around a core the
    detector never saw. Repairing that paints a dark ring about an untouched
    speck: the one outcome worse than leaving the dust alone.

    A second top-hat at `_COARSE_FACTOR` times the radius settles it. That scale
    sees the whole blob where the fine one saw only its rim, so a component
    whose neighbourhood answers far larger at the coarse scale than the
    component itself is a rim, and is dropped. Ordinary dust is narrow at every
    scale and answers about the same size at both, so it is kept.

    The coarse pass only ever CLASSIFIES — it never adds to the mask. Detecting
    at that scale would repair any neutral scene detail narrower than the coarse
    element, which is exactly the real content this tool is meant to leave
    alone.

    Two approaches that look reasonable and are not: judging a component by the
    density just outside it reads a false step off the picture's own gradient,
    and judging it by geometry cannot work either — a diagonal hair has the same
    thin, low-solidity, square-ish bounding box as a ring, and a broken rim
    encloses nothing for a flood fill to find."""
    labels, ys, xs, count = _components(mask)
    if count == 0:
        return mask, 0
    area, y0, y1, x0, x1 = _component_bounds(labels, ys, xs, count)

    coarse = _scan_tophat(rgb, opt.radius * _COARSE_FACTOR, opt.threshold,
                          opt.neutrality, floor)

    h_img, w_img = mask.shape
    pad = opt.radius * _COARSE_FACTOR
    floor = (2 * opt.radius + 1) ** 2        # a blob must at least outgrow the element

    drop = np.zeros(count, bool)
    for k in range(count):
        wy0, wy1 = max(0, int(y0[k]) - pad), min(h_img, int(y1[k]) + 1 + pad)
        wx0, wx1 = max(0, int(x0[k]) - pad), min(w_img, int(x1[k]) + 1 + pad)
        seen = int(coarse[wy0:wy1, wx0:wx1].sum())
        drop[k] = seen > floor and seen > _OVERSIZE_RATIO * int(area[k])

    if not drop.any():
        return mask, 0
    out = np.zeros_like(mask)
    keep = ~drop[labels]
    out[ys[keep], xs[keep]] = True
    return out, int(drop.sum())


def _gate_shape(mask: np.ndarray, opt: Options) -> np.ndarray:
    """Drop components too large to be a defect — unless they are thin, which is
    what a hair or a scratch looks like however long it runs."""
    labels, ys, xs, count = _components(mask)
    if count == 0:
        return mask
    area, y0, y1, x0, x1 = _component_bounds(labels, ys, xs, count)

    span = np.maximum(y1 - y0, x1 - x0) + 1
    thickness = area / np.maximum(span, 1)
    keep = (area <= opt.max_area) | (thickness <= opt.max_thickness)
    if keep.all():
        return mask

    out = np.zeros_like(mask)
    sel = keep[labels]
    out[ys[sel], xs[sel]] = True
    return out


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def _fill(rgb: np.ndarray, mask: np.ndarray, ys, xs, thickness: float,
          grain: bool, rng) -> None:
    """Replace a component's pixels by a diffusion fill, in place.

    Laplace on the hole, seeded from the surrounding known pixels: Jacobi
    iterations converge in roughly the square of the hole's THIN dimension, not
    its length, so a long hair costs the same as a small speck."""
    h, w, _ = rgb.shape
    margin = max(3, int(2 * thickness))
    ay0 = max(0, int(ys.min()) - margin)
    ay1 = min(h, int(ys.max()) + 1 + margin)
    ax0 = max(0, int(xs.min()) - margin)
    ax1 = min(w, int(xs.max()) + 1 + margin)

    # Density for this window only. The diffusion has to run in density, where
    # the defect is an additive offset, but nothing outside the patch does.
    patch = to_density(rgb[ay0:ay1, ax0:ax1])
    # Solve over EVERY masked pixel in the window, not just this component's: a
    # neighbouring defect is unknown too, and must never be read as a source.
    hole = mask[ay0:ay1, ax0:ax1]
    known = ~hole
    if not known.any():
        return
    # ...but write back only our own pixels, so a component sharing this window
    # still gets the repair its own peak attenuation called for.
    target = np.zeros_like(hole)
    target[ys - ay0, xs - ax0] = True

    if grain:
        # Estimate grain from the known neighbourhood before the fill smooths it.
        detail = patch - _box3(patch)
        sigma = np.array([detail[..., c][known].std() for c in range(3)],
                         np.float32)
    patch[hole] = patch[known].mean(axis=0)

    # Jacobi on a once-padded buffer. The border replicates known film and no
    # hole pixel ever touches it (that is what `margin` is for), so the padding
    # is written once and the loop is four slice adds over views — no per-
    # iteration allocation. A frame carrying a thousand defects spends most of
    # its time in here, and the shift-based version cost several times as much.
    iters = int(min(600, 4 * thickness * thickness + 20))
    hole3 = hole[..., None]
    buf = np.pad(patch, ((1, 1), (1, 1), (0, 0)), mode="edge")
    patch = buf[1:-1, 1:-1]                  # a view: updating it updates buf
    nb = np.empty_like(patch)
    # `margin` normally keeps the hole clear of the patch border, so the padding
    # replicates known film and can be written once. A defect running off the
    # edge of the FRAME has no margin to be given, and there the border tracks
    # pixels the loop is still changing, so it has to be refreshed.
    at_edge = bool(hole[0].any() or hole[-1].any()
                   or hole[:, 0].any() or hole[:, -1].any())
    for _ in range(iters):
        np.add(buf[:-2, 1:-1], buf[2:, 1:-1], out=nb)
        nb += buf[1:-1, :-2]
        nb += buf[1:-1, 2:]
        nb *= 0.25
        np.copyto(patch, nb, where=hole3)
        if at_edge:                          # corners are never read by the stencil
            buf[0, 1:-1] = patch[0]
            buf[-1, 1:-1] = patch[-1]
            buf[1:-1, 0] = patch[:, 0]
            buf[1:-1, -1] = patch[:, -1]

    if grain and sigma.max() > 0:
        noise = rng.standard_normal((int(hole.sum()), 3)).astype(np.float32)
        patch[hole] += noise * sigma

    view = rgb[ay0:ay1, ax0:ax1]
    view[target] = from_density(patch[target])


def repair(rgb: np.ndarray, mask: np.ndarray, offset: np.ndarray,
           opt: Options) -> Stats:
    """Remove the detected defects from `rgb`, in place.

    Every masked pixel first has its neutral density offset taken off, which is
    the physically correct removal of a partial occlusion and keeps the detail
    and grain underneath. A component whose peak offset says the signal was
    destroyed is then overwritten by a fill.

    Both steps are local, so the frame is never converted to density as a whole:
    the subtraction has an exact linear equivalent (below), and each fill builds
    density for its own small window. On an 80MP scan that is a gigabyte of
    float planes not allocated."""
    stats = Stats(total_pixels=int(mask.size))
    w_img = mask.shape[1]
    labels, ys, xs, count = _components(mask)
    if count == 0:
        return stats

    stats.defects = count
    stats.pixels = int(ys.size)

    # Take the neutral part of the attenuation off all three channels; the
    # colour that remains is the film's, not the dust's. Subtracting a density
    # offset IS multiplying by 10**offset in the linear data, so this needs no
    # density conversion — and it touches only the masked pixels.
    gain = np.power(10.0, offset[ys, xs], dtype=np.float32)[:, None]
    rgb[ys, xs, :] = np.clip(
        rgb[ys, xs, :].astype(np.float32) * gain, 0.0, 65535.0).astype(np.uint16)

    peak = np.zeros(count, np.float32)
    np.maximum.at(peak, labels, offset[ys, xs])
    area, y0, y1, x0, x1 = _component_bounds(labels, ys, xs, count)
    span = np.maximum(y1 - y0, x1 - x0) + 1
    thickness = area / np.maximum(span, 1)

    rng = np.random.default_rng(opt.seed)
    order = np.argsort(labels, kind="stable")
    bounds = np.searchsorted(labels[order], np.arange(count + 1))
    for k in range(count):
        if peak[k] <= opt.partial_max:
            stats.subtracted += 1
            continue
        idx = order[bounds[k]:bounds[k + 1]]
        cys, cxs = ys[idx], xs[idx]
        if max(span[k], 1) > _FILL_TILE:
            # Tile it: see _FILL_TILE. Grouping by tile coordinate keeps each
            # patch small no matter how the defect is angled across the frame.
            key = (cys // _FILL_TILE) * (w_img // _FILL_TILE + 2) \
                + (cxs // _FILL_TILE)
            for t in np.unique(key):
                sel = key == t
                _fill(rgb, mask, cys[sel], cxs[sel], float(thickness[k]),
                      opt.grain, rng)
        else:
            _fill(rgb, mask, cys, cxs, float(thickness[k]), opt.grain, rng)
        stats.filled += 1
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def despeck(rgb: np.ndarray, opt: Optional[Options] = None
            ) -> Tuple[np.ndarray, Stats, Optional[np.ndarray]]:
    """Detect and repair dust, hairs and fine scratches on an uninverted merged
    negative scan.

    `rgb` is the (H, W, 3) uint16 linear image a merge produced. Returns
    (cleaned, stats, mask) — a new image, what was done, and the defect mask if
    `opt.want_mask` asked for it (otherwise None).

    Raises ValueError if the mask covers more than MAX_MASK_FRACTION of the
    frame, which means the settings are wrong for this negative rather than that
    the negative is filthy."""
    opt = opt or Options()
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"despeck needs an (H, W, 3) image, got {rgb.shape}")
    if opt.radius < 1:
        raise ValueError("despeck radius must be at least 1 px")
    if opt.threshold <= 0:
        raise ValueError("despeck threshold must be above 0 density")

    mask, offset, oversize = detect(rgb, opt)
    # Repair a copy, in place. Only masked pixels are ever written, so the rest
    # of the frame comes through bit-exact — despeckling is surgery on an
    # archival merge, not a re-render of it.
    out = rgb.copy()
    stats = repair(out, mask, offset, opt)
    stats.oversize = oversize
    return out, stats, (mask if opt.want_mask else None)


def write_mask(path: str, mask: np.ndarray) -> None:
    """Write a defect mask as an 8-bit TIFF, for checking the detector by eye
    before trusting the repair."""
    import tifffile
    tifffile.imwrite(path, (mask.astype(np.uint8) * 255), compression="deflate")
