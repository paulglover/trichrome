"""
Tests for dust/hair/scratch detection and repair on an uninverted negative.

There is no sample scan here: every case is a synthetic negative built in
density — a smooth, orange-masked, limited-range image — with defects added at
known coordinates, so each test can assert on exactly the pixels it planted.

The properties that matter, and that the detector is easy to get wrong on:
a clean frame must come back BIT-IDENTICAL; a defect must be caught wherever it
sits on the density scale, which is the whole reason for working in log space;
non-neutral picture detail must survive; and partial occlusion must be thinned
rather than painted over.
"""
import numpy as np
import pytest

from trichrome import bake, despeck

H, W = 220, 300


def negative(h=H, w=W, mono=False, seed=1):
    """A synthetic UNINVERTED negative, in density.

    The density RANGE matters as much as the content. An earlier version of this
    fixture spanned barely 0.35 D, which is narrower than a sensible detection
    threshold — so a bright highlight could not even be represented in it, and
    the detector was tuned against a picture where its worst failure was
    impossible. A real scan measured about 1.0 D of range in its thinnest
    channel, so that is what this builds.

    Density rises left to right, which gives every test a known base level to
    place a defect against: `base_density(x)` returns it."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ramp = xx / max(w - 1, 1)
    texture = 0.05 * np.sin(xx / 37.0) * np.cos(yy / 29.0)
    d = np.empty((h, w, 3), np.float32)
    if mono:
        for c in range(3):
            d[..., c] = 0.35 + 0.95 * ramp + texture
    else:
        # The thinnest channel is the one the floor is measured from, so it is
        # the one whose range has to be realistic.
        d[..., 0] = 0.35 + 0.95 * ramp + texture
        d[..., 1] = 0.50 + 0.85 * ramp + texture
        d[..., 2] = 0.65 + 0.75 * ramp + texture
    return d + rng.normal(0, 0.012, d.shape).astype(np.float32)


def base_density(x, w=W):
    """The thinnest channel's density at column `x` of `negative()` — what a
    defect planted there adds to, and what decides whether it clears the floor."""
    return 0.35 + 0.95 * (x / max(w - 1, 1))


def disc(h, w, cy, cx, r):
    yy, xx = np.mgrid[0:h, 0:w]
    return (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r


def run(density, **kw):
    """Despeck a density image, returning (out_rgb, stats, mask, in_rgb)."""
    rgb = despeck.from_density(density)
    kw.setdefault("want_mask", True)
    out, stats, mask = despeck.despeck(rgb, despeck.Options(**kw))
    return out, stats, mask, rgb


# --- morphology primitives -------------------------------------------------

def test_opening_removes_a_narrow_peak_and_keeps_a_wide_plateau():
    a = np.zeros((21, 21), np.float32)
    a[10, 10] = 1.0                 # a speck, narrower than the element
    a[2:6, 2:6] += 0.0
    wide = np.zeros((21, 21), np.float32)
    wide[4:17, 4:17] = 1.0          # a plateau wider than the element

    assert despeck.opening(a, 3).max() == pytest.approx(0.0)
    # The plateau survives except for the element-width bite at its border.
    assert despeck.opening(wide, 3)[10, 10] == pytest.approx(1.0)


def test_opening_removes_a_thin_line_however_long_it_runs():
    """A hair is thin but not small — an opening removes it on thinness alone,
    which is why one filter catches specks and hairs both."""
    a = np.zeros((41, 41), np.float32)
    a[20, :] = 1.0                  # full-width, 1 px thick
    assert despeck.opening(a, 3).max() == pytest.approx(0.0)


def test_shift_replicates_the_edge():
    """Edge replication is what stops a top-hat inventing a defect at the frame
    border; a zero-filled shift would show one."""
    a = np.arange(5, dtype=np.float32).reshape(1, 5)
    assert despeck._shift(a, 2, 1).ravel().tolist() == [0, 0, 0, 1, 2]
    assert despeck._shift(a, -2, 1).ravel().tolist() == [2, 3, 4, 4, 4]


def test_density_round_trip():
    rgb = np.array([[[65535, 6554, 655]]], np.uint16)
    d = despeck.to_density(rgb)
    assert d[0, 0].tolist() == pytest.approx([0.0, 1.0, 2.0], abs=2e-3)
    back = despeck.from_density(d)
    assert back.ravel().tolist() == pytest.approx(rgb.ravel().tolist(), abs=1)


# --- detection -------------------------------------------------------------

def test_clean_negative_is_returned_bit_identical():
    out, stats, _, rgb = run(negative())
    assert stats.defects == 0
    assert np.array_equal(out, rgb)


def test_speck_is_detected_and_repaired():
    d = negative()
    spot = disc(H, W, 90, 140, 3)
    d[spot] += 1.6
    out, stats, mask, _ = run(d)

    assert stats.defects == 1
    assert mask[spot].all()
    # The repair lands within a tenth of a stop of the surrounding density.
    err = np.abs(despeck.to_density(out) - negative())[spot].max()
    assert err < 0.3


def test_a_defect_is_caught_at_both_ends_of_the_density_scale():
    """The point of working in density: one threshold holds in a thin part of
    the negative and a dense one, where in linear the same speck would cost
    wildly different numbers of ADU."""
    d = negative()
    d[:, :150, :] += 1.4                    # make the left half much denser
    thin = disc(H, W, 90, 220, 3)
    dense = disc(H, W, 90, 70, 3)
    d[thin] += 1.5
    d[dense] += 1.5

    _, stats, mask, rgb = run(d)
    assert mask[thin].all() and mask[dense].all()
    assert stats.defects == 2
    # ...and the two really were different in the linear data.
    assert rgb[90, 220, 1] > 4 * rgb[90, 70, 1]


def test_non_neutral_detail_survives():
    """A small dark spot in one dye layer only is picture, not dust, and the
    cross-channel neutrality test is the only thing that can tell them apart.

    The spot is deliberately SMALLER than the structuring element, so the
    opening does fire on it and neutrality is genuinely what saves it — a
    larger patch would survive on size alone and prove nothing."""
    d = negative()
    spot = disc(H, W, 90, 140, 3)
    d[spot, 1] += 1.2                        # green layer alone
    out, stats, mask, rgb = run(d)

    assert not mask[spot].any()
    assert stats.defects == 0
    assert np.array_equal(out, rgb)


def test_neutrality_only_governs_the_agreement_check():
    """`neutrality` is the SECONDARY gate. Taking min(r_R, r_G, r_B) as the
    offset is the primary one, and it is not switchable — so a mostly-coloured
    defect is what the ratio decides, and a single-layer one stays rejected
    either way."""
    spot = disc(H, W, 90, 140, 3)

    mixed = negative()
    mixed[spot] += 0.7                   # neutral part, above threshold
    mixed[spot, 1] += 1.6                # ...plus a much larger green part
    assert not run(mixed, neutrality=0.7)[2][spot].any()
    assert run(mixed, neutrality=0.0)[2][spot].any()

    green_only = negative()
    green_only[spot, 1] += 2.3
    assert not run(green_only, neutrality=0.0)[2][spot].any()


def test_mono_negative_still_finds_dust_by_shape_alone():
    d = negative(mono=True)
    spot = disc(H, W, 110, 150, 3)
    d[spot] += 1.6
    _, stats, mask, _ = run(d, neutrality=0.0)
    assert stats.defects == 1
    assert mask[spot].all()


# --- telling dust from a bright highlight ----------------------------------
#
# The failure a real 80MP scan exposed. On an uninverted negative a speck of
# dust and a small specular highlight are both small, both neutral and both
# locally much denser than their surroundings, so the top-hat cannot separate
# them. On that scan — which held no dust at all — the local detector alone
# found 211 "defects", every one of them a catchlight in the picture.

def test_a_bright_neutral_highlight_is_not_repaired():
    """A specular highlight is exposure, so it is bounded by what the film can
    record. It is neutral, it is small, and the top-hat sees it as plainly as it
    sees dust — staying inside the frame's own density range is the ONLY thing
    that marks it as picture.

    Planted on thin film (x=40) so that even a strong highlight lands under the
    floor, which is where a real one lives."""
    d = negative()
    spot = disc(H, W, 90, 40, 3)
    d[spot] += 0.7                       # neutral, and well over the threshold
    assert base_density(40) + 0.7 < despeck.density_floor(despeck.from_density(d))

    out, stats, mask, rgb = run(d)
    assert stats.defects == 0
    assert not mask[spot].any()
    assert np.array_equal(out, rgb)


def test_an_obstruction_denser_than_any_film_is_repaired():
    """The same spot on the same thin film, but denser than anything exposed in
    the frame. Nothing optical can be denser than the film can record, so this
    is something lying on the negative — and it is repaired."""
    d = negative()
    spot = disc(H, W, 90, 40, 3)
    d[spot] += 1.9
    assert base_density(40) + 1.9 > despeck.density_floor(despeck.from_density(d))

    _, stats, mask, _ = run(d)
    assert stats.defects == 1
    assert mask[spot].all()


def test_the_floor_can_be_turned_off():
    """Disabling it is the knob for a scan with no highlights to protect, and for
    dust lying on a thin part of a contrasty negative, which may never clear the
    floor. The highlight above is repaired once it is off — that cost is the
    whole reason the floor is on by default."""
    d = negative()
    spot = disc(H, W, 90, 40, 3)
    d[spot] += 0.7
    assert run(d)[1].defects == 0
    assert run(d, floor_percentile=0)[1].defects == 1


def test_the_floor_is_measured_from_the_film_not_the_dirt():
    """The estimate opens the frame first, so the defects are removed from their
    own reference level. Without that a filthy negative raises its own floor
    above its dust and quietly stops being cleaned."""
    clean = negative()
    dirty = clean.copy()
    rng = np.random.default_rng(4)
    # Over 1% of the frame in specks — ten times the 0.1% the floor percentile
    # sits at, so an unopened estimate would be measuring the dirt itself. Kept
    # under the runaway limit so the repair still runs.
    centres = []
    for _ in range(30):
        cy, cx = int(rng.integers(6, H - 6)), int(rng.integers(6, W - 6))
        dirty[disc(H, W, cy, cx, 3)] += 1.8
        centres.append((cy, cx))

    floor_clean = despeck.density_floor(despeck.from_density(clean))
    floor_dirty = despeck.density_floor(despeck.from_density(dirty))
    assert abs(floor_dirty - floor_clean) < 0.1

    # Counted by planted centre rather than by component: neighbouring specks
    # merge, so a component count would understate the recall being asserted.
    mask = run(dirty)[2]
    assert sum(mask[cy, cx] for cy, cx in centres) >= 28


# --- shape gating ----------------------------------------------------------

def test_a_large_neutral_area_is_not_mistaken_for_dust():
    """Real content can be neutral and much denser than its surroundings. Size
    is what rules it out — but only size, so the next test still passes."""
    d = negative()
    d[40:180, 60:240, :] += 0.9
    _, stats, mask, _ = run(d)
    assert stats.defects == 0
    assert not mask[70:150, 90:210].any()


def test_a_long_hair_is_kept_despite_its_area():
    """A hair covers far more pixels than max_area, so only the thinness escape
    keeps it. Without that, the shape gate would throw away exactly the defect
    it is hardest to retouch by hand."""
    d = negative()
    yy, xx = np.mgrid[0:H, 0:W]
    hair = (np.abs(yy - 0.3 * xx - 40) < 1.0) & (xx > 20) & (xx < 280)
    d[hair] += 1.5

    # max_area is set below the hair's own area, so the gate WOULD drop it and
    # only the thinness escape can keep it.
    area = int(hair.sum())
    _, stats, kept, _ = run(d, max_area=area // 4)
    assert stats.defects >= 1
    assert kept[hair].mean() > 0.9

    # Remove the escape and the same hair is thrown away — which is what makes
    # the assertion above about thinness rather than about area.
    _, _, dropped, _ = run(d, max_area=area // 4, max_thickness=0.0)
    assert not dropped[hair].any()


# --- defects wider than the structuring element ----------------------------
#
# These are the regression tests for the detector's nastiest failure. An
# opening only removes what is narrower than its element, so a blob wider than
# `radius` survives in its middle and the top-hat sees its rim alone — as arcs,
# not even a closed ring. Repairing those arcs paints a dark ring around an
# untouched core, which is worse than leaving the dust alone.

@pytest.mark.parametrize("r", [1, 2, 3, 4, 6])
def test_defects_up_to_the_element_are_fully_repaired(r):
    """Consolidation carries the detector past its nominal size limit: a blob half
    again as wide as the element still comes out solid, not ringed."""
    d = negative()
    spot = disc(H, W, 110, 150, r)
    d[spot] += 1.9
    out, stats, mask, _ = run(d)

    core = disc(H, W, 110, 150, max(1, r - 2))
    assert stats.defects == 1 and stats.oversize == 0
    assert mask[core].all()
    assert np.abs(despeck.to_density(out) - negative())[core].max() < 0.4


@pytest.mark.parametrize("r", [9, 14, 20])
def test_an_oversized_defect_is_left_alone_not_ringed(r):
    """Too wide to detect, so the frame must come back untouched and the count
    must say why — never a rim repaired around a core that was not."""
    d = negative()
    d[disc(H, W, 110, 150, r)] += 1.9
    out, stats, _, rgb = run(d)

    assert stats.oversize >= 1
    assert stats.defects == 0
    assert np.array_equal(out, rgb)


@pytest.mark.parametrize("r", [9, 14, 20])
def test_raising_the_radius_rescues_an_oversized_defect(r):
    """Which makes the oversize report actionable rather than just a refusal."""
    d = negative()
    d[disc(H, W, 110, 150, r)] += 1.9
    out, stats, _, _ = run(d, radius=r)

    core = disc(H, W, 110, 150, r - 2)
    assert stats.defects == 1 and stats.oversize == 0
    assert np.abs(despeck.to_density(out) - negative())[core].max() < 0.4


@pytest.mark.parametrize("slope, x0, x1", [(0.3, 20, 280), (1.0, 70, 200)])
def test_a_hair_is_never_mistaken_for_an_oversized_defect(slope, x0, x1):
    """A diagonal hair has the same thin, square-ish, low-solidity bounding box as
    a ring, so only the density step inside it tells the two apart. If that test
    ever regresses to pure geometry, this is what catches it."""
    d = negative()
    yy, xx = np.mgrid[0:H, 0:W]
    hair = (np.abs(yy - slope * xx - (40 if slope < 1 else -60)) < 1.0) \
        & (xx > x0) & (xx < x1)
    d[hair] += 1.5
    _, stats, mask, _ = run(d)

    assert stats.oversize == 0
    assert mask[hair].mean() > 0.9


def test_nearby_specks_stay_separate_defects():
    """The oversize grouping dilates generously to collect a broken rim; it must
    not merge ordinary neighbouring dust into one rejected cluster."""
    d = negative()
    for cy, cx in [(40, 40), (40, 60), (60, 40), (160, 220), (170, 235)]:
        d[disc(H, W, cy, cx, 3)] += 1.8
    _, stats, _, _ = run(d)
    assert stats.defects == 5 and stats.oversize == 0


def test_an_oversized_blob_beside_a_hair_is_still_rejected():
    """The oversize test classifies each component against a COARSE top-hat of its
    own neighbourhood. An earlier version judged whole clusters of fragments
    instead, and an unrelated hair passing nearby was enough to balloon the
    cluster and get the rim repaired after all. This is that regression."""
    yy, xx = np.mgrid[0:H, 0:W]
    blob = disc(H, W, 50, 60, 12)
    hair = (np.abs(yy - 0.2 * xx - 150) < 1.2) & (xx > 30) & (xx < 280)
    assert not (hair & blob).any()

    d = negative()
    d[hair] += 1.5
    d[blob] += 1.9
    _, stats, mask, _ = run(d)

    assert stats.oversize >= 1
    assert not mask[blob].any()          # the blob's rim: untouched
    assert mask[hair].mean() > 0.9       # the hair: still repaired


def test_the_coarse_pass_never_widens_the_mask():
    """The coarse top-hat exists only to CLASSIFY. If it ever fed the mask, any
    neutral scene detail narrower than the coarse element would be repaired —
    exactly the real content this tool is meant to leave alone."""
    d = negative()
    # A neutral patch wider than the fine element but narrower than the coarse
    # one: invisible to detection, and it must stay that way.
    d[100:120, 140:160, :] += 0.9
    out, stats, mask, rgb = run(d)
    assert stats.defects == 0
    assert not mask[100:120, 140:160].any()
    assert np.array_equal(out, rgb)


# --- repair ----------------------------------------------------------------

def test_partial_occlusion_is_thinned_not_filled():
    """Light attenuation leaves the picture underneath intact, so subtracting
    the measured density recovers real detail instead of inventing it."""
    d = negative()
    spot = disc(H, W, 90, 140, 4)
    d[spot] += 0.7                       # above threshold, under partial_max
    out, stats, _, _ = run(d)

    assert stats.subtracted == 1 and stats.filled == 0
    err = np.abs(despeck.to_density(out) - negative())[spot].max()
    assert err < 0.1


def test_the_subtract_path_is_reachable_at_all():
    """The thinning path only exists for defects between `threshold` and
    `partial_max`. If `partial_max` ever drops below `threshold` the window
    closes, every detectable defect is heavy enough to be replaced, and the
    detail-preserving half of the repair becomes dead code without a single
    test failing."""
    assert despeck.DEFAULT_PARTIAL_MAX > despeck.DEFAULT_THRESHOLD


def test_opaque_defect_is_filled():
    d = negative()
    spot = disc(H, W, 90, 140, 4)
    d[spot] += 1.9                       # over partial_max: signal is gone
    _, stats, _, _ = run(d)
    assert stats.filled == 1 and stats.subtracted == 0


def test_fill_reinjects_grain():
    """A pure diffusion fill is glassy-smooth and obvious at 100%; the filled
    patch should carry noise of the same order as its surroundings."""
    d = negative()
    spot = disc(H, W, 110, 150, 6)
    d[spot] += 1.9

    # Measure INSIDE the filled area only: a window that also takes in the
    # untouched surroundings is dominated by their grain and shows nothing.
    inner = disc(H, W, 110, 150, 4)

    def roughness(img):
        p = despeck.to_density(img)[..., 1:2]
        return float((p - despeck._box3(p))[..., 0][inner].std())

    grainy, _, _, _ = run(d, grain=True)
    flat, _, _, _ = run(d, grain=False)
    # Without reinjection the fill really is glassy next to untouched film.
    assert roughness(flat) < 0.25 * roughness(despeck.from_density(negative()))
    assert roughness(grainy) > 2 * roughness(flat)


@pytest.mark.parametrize("cy, cx", [(0, 0), (0, 150), (110, 0),
                                   (H - 1, W - 1), (3, 150)])
def test_a_defect_running_off_the_frame_edge_is_repaired(cy, cx):
    """At the frame edge there is no margin to keep the hole away from its patch
    border, so the diffusion's padding tracks pixels the solver is still
    changing and has to be refreshed each sweep. Freezing it instead pulls the
    fill back toward the seed the hole started at.

    The bound is tight on purpose. A loose one passes either way — the defect is
    still broadly removed — and the regression this guards against is a
    degradation of the fill, not its failure."""
    d = negative()
    spot = disc(H, W, cy, cx, 4)
    d[spot] += 1.9
    out, stats, mask, _ = run(d)

    assert stats.defects == 1
    assert mask[spot].all()
    err = np.abs(despeck.to_density(out) - negative())[spot].max()
    assert err < 0.09


def test_pixels_outside_the_mask_are_untouched():
    """Despeckling is opt-in surgery, not a re-render: everything it did not
    repair must survive the density round trip bit-for-bit."""
    d = negative()
    d[disc(H, W, 90, 140, 3)] += 1.6
    out, _, mask, rgb = run(d)
    assert np.array_equal(out[~mask], rgb[~mask])
    assert not np.array_equal(out[mask], rgb[mask])


def test_repeated_runs_are_identical():
    """The fill draws synthetic grain, so it is seeded — a re-merge of the same
    triplet must not produce a different file."""
    d = negative()
    d[disc(H, W, 90, 140, 5)] += 1.9
    assert np.array_equal(run(d)[0], run(d)[0])


# --- guards ----------------------------------------------------------------

def test_a_runaway_mask_is_refused():
    """Better to fail the job than to quietly repaint the picture."""
    # A neutral high-frequency pattern: every other pixel is a peak the opening
    # strips, all three channels agree, and it stays inside the density range so
    # nothing is lost to clipping. The detector should see ~half the frame.
    d = negative()
    yy, xx = np.mgrid[0:H, 0:W]
    d += (0.7 * ((yy + xx) % 2)).astype(np.float32)[..., None]
    with pytest.raises(ValueError, match="sanity limit"):
        despeck.despeck(despeck.from_density(d), despeck.Options())


@pytest.mark.parametrize("kw, match", [
    ({"radius": 0}, "radius"),
    ({"threshold": 0.0}, "threshold"),
])
def test_bad_options_are_rejected(kw, match):
    rgb = despeck.from_density(negative(20, 20))
    with pytest.raises(ValueError, match=match):
        despeck.despeck(rgb, despeck.Options(**kw))


def test_wrong_shape_is_rejected():
    with pytest.raises(ValueError, match=r"\(H, W, 3\)"):
        despeck.despeck(np.zeros((8, 8), np.uint16))


def test_mask_is_only_returned_when_asked_for():
    rgb = despeck.from_density(negative(40, 40))
    assert despeck.despeck(rgb, despeck.Options(want_mask=False))[2] is None


# --- batch integration -----------------------------------------------------

class SpeckDecoder:
    """Stands in for merge_raw_channels with a synthetic negative carrying one
    opaque speck, so a real repair runs through the batch path."""

    def __call__(self, sources, preview=False, demosaic=True, light_order="RGB"):
        d = negative(60, 80)
        d[disc(60, 80, 30, 40, 3)] += 1.8
        return despeck.from_density(d), (60, 80)


@pytest.fixture
def speck_decode(monkeypatch):
    monkeypatch.setattr(bake.merge_mod, "merge_raw_channels", SpeckDecoder())


def raws(tmp_path, n):
    out = []
    for i in range(1, n + 1):
        p = tmp_path / f"img{i:03d}.arw"
        p.write_bytes(b"not really a raw")
        out.append(str(p))
    return out


def test_run_jobs_despeckles_and_reports(tmp_path, speck_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    summary = bake.run_jobs(jobs, despeck=despeck.Options())
    assert len(summary.written) == 1
    assert summary.written[0].despeck.defects == 1
    assert summary.written[0].mask is None


def test_run_jobs_without_despeck_leaves_pixels_alone(tmp_path, speck_decode):
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    summary = bake.run_jobs(jobs)
    assert summary.written[0].despeck is None


def test_mask_is_written_beside_the_output(tmp_path, speck_decode):
    import tifffile
    jobs = bake.plan_jobs(raws(tmp_path, 3))
    summary = bake.run_jobs(jobs, despeck=despeck.Options(want_mask=True))
    mask_path = summary.written[0].mask
    assert mask_path and mask_path.endswith("_RGB_mask.tif")
    assert tifffile.imread(mask_path).max() == 255


def test_cancelling_removes_the_mask_too(tmp_path, speck_decode):
    """bake promises a cancel leaves the folder exactly as it was, and the mask
    is a file this tool wrote like any other."""
    import os
    sources = raws(tmp_path, 6)
    before = sorted(os.listdir(tmp_path))
    jobs = bake.plan_jobs(sources)
    seen = []
    summary = bake.run_jobs(jobs, despeck=despeck.Options(want_mask=True),
                            progress_cb=lambda i, t, j: seen.append(i),
                            cancel_flag=lambda: len(seen) >= 1)
    assert summary.cancelled
    assert sorted(os.listdir(tmp_path)) == before


def test_a_refused_despeck_fails_the_job_and_keeps_its_sources(tmp_path,
                                                               speck_decode):
    """A detector that refuses a frame must behave like any other job failure:
    no output, no mask, and the source RAWs untouched."""
    import os
    sources = raws(tmp_path, 3)
    jobs = bake.plan_jobs(sources)
    summary = bake.run_jobs(jobs, delete_originals=True,
                            despeck=despeck.Options(threshold=0.001,
                                                    floor_percentile=0,
                                                    neutrality=0,
                                                    want_mask=True))
    assert len(summary.failures) == 1
    assert "sanity limit" in summary.failures[0].error
    assert summary.deleted == []
    assert all(os.path.exists(s) for s in sources)
    assert not any(f.endswith(".tif") for f in os.listdir(tmp_path))
