"""
Command-line interface.

    trichrome merge ./shoot                     # write TIFFs, keep the RAWs
    trichrome merge ./shoot --format dng        # linear DNGs instead
    trichrome merge ./shoot --out ./merged
    trichrome merge ./shoot --despeck            # also remove dust and hairs
    trichrome merge ./shoot --delete-originals  # destructive; no prompt
    trichrome merge ./shoot --dry-run           # show the plan only
    trichrome list ./shoot                      # show the triplet grouping
"""
import argparse
import os
import sys
from typing import List

from . import __version__
from . import bake as bake_mod
from . import despeck as despeck_mod
from . import merge as merge_mod


def _fmt_triplet(job: bake_mod.Job) -> str:
    return "  " + " + ".join(os.path.basename(s) for s in job.sources)


def _plan(args) -> List[bake_mod.Job]:
    paths = bake_mod.collect_raw_files(args.inputs, recursive=args.recursive)
    if not paths:
        raise ValueError("No RAW files found. Supported: "
                         + ", ".join(sorted(merge_mod.RAW_EXTENSIONS)))
    return bake_mod.plan_jobs(paths, out_dir=args.out, suffix=args.suffix,
                              fmt=args.format)


def cmd_list(args) -> int:
    jobs = _plan(args)
    print(f"{len(jobs)} triplet(s), light order {args.order.upper()}:")
    for job in jobs:
        print(f"{_fmt_triplet(job)}  ->  {job.output}")
    return 0


def _despeck_options(args) -> "despeck_mod.Options | None":
    """Build the despeck settings, or None when the pass is off.

    The tuning flags default to None rather than to their real values so that
    passing one WITHOUT --despeck is an error instead of a silent no-op."""
    tuned = {name: getattr(args, "despeck_" + name)
             for name in ("threshold", "radius", "neutrality",
                          "floor_percentile")}
    given = [n for n, v in tuned.items() if v is not None]
    if not args.despeck:
        if given or args.despeck_mask:
            flags = ", ".join("--despeck-" + n.replace("_", "-")
                              for n in given)
            if args.despeck_mask:
                flags = ", ".join(filter(None, [flags, "--despeck-mask"]))
            raise ValueError(f"{flags} only applies with --despeck")
        return None
    opt = despeck_mod.Options(want_mask=args.despeck_mask)
    for name, value in tuned.items():
        if value is not None:
            setattr(opt, name, value)
    return opt


def cmd_merge(args) -> int:
    jobs = _plan(args)
    despeck = _despeck_options(args)
    mode = "demosaic (full resolution)" if args.demosaic else \
           "single photosite (half resolution)"
    fmt = bake_mod.normalise_format(args.format)
    is_dng = fmt == bake_mod.FORMAT_DNG
    label = "linear DNG" if is_dng else "linear TIFF"
    print(f"{len(jobs)} triplet(s) · light order {args.order.upper()} · {mode}"
          f" · {label}"
          # --no-icc has nothing to switch off on the DNG path.
          + ("" if args.icc or is_dng else " · untagged (no ICC)")
          + ("" if despeck is None else
             f" · despeck (threshold {despeck.threshold:g} D, "
             f"radius {despeck.radius} px, floor "
             + (f"p{despeck.floor_percentile:g})"
                if despeck.floor_percentile else "off)")))

    def progress(i, total, job):
        print(f"[{i + 1}/{total}] {_fmt_triplet(job).strip()}  ->  "
              f"{os.path.basename(job.output)}", flush=True)

    summary = bake_mod.run_jobs(
        jobs, demosaic=args.demosaic, light_order=args.order,
        delete_originals=args.delete_originals, dry_run=args.dry_run,
        icc=args.icc, despeck=despeck, progress_cb=progress)

    if args.dry_run:
        print(f"\nDry run — nothing written. {len(jobs)} "
              f"{'DNG' if is_dng else 'TIFF'}(s) would be created"
              + (f", {len(jobs) * 3} RAW(s) deleted."
                 if args.delete_originals else "."))
        return 0

    print()
    for r in summary.written:
        h, w = r.size or (0, 0)
        print(f"wrote {r.job.output}  ({w}x{h}, uint16)")
        if r.despeck:
            d = r.despeck
            print(f"  despeck: {d.defects} defect(s), {d.fraction:.3%} of pixels"
                  f"  ({d.subtracted} thinned, {d.filled} filled)")
            if d.oversize:
                print(f"           {d.oversize} too wide for the current "
                      f"--despeck-radius and left alone; raise it to catch them")
        if r.mask:
            print(f"  mask:    {r.mask}")
    for r in summary.failures:
        print(f"FAILED {r.job.name}: {r.error}", file=sys.stderr)
    for path, reason in summary.delete_errors:
        print(f"NOT DELETED {os.path.basename(path)}: {reason}", file=sys.stderr)

    if summary.deleted:
        print(f"deleted {len(summary.deleted)} source RAW(s)")
    elif args.delete_originals:
        print("deleted nothing" +
              (" (no triplet succeeded)" if not summary.written else ""))
    print(f"\n{len(summary.written)} merged, {len(summary.failures)} failed")
    return 1 if (summary.failures or summary.delete_errors) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trichrome",
        description="Merge red/green/blue-light RAW triplets into 16-bit "
                    "linear TIFFs or DNGs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version",
                   version=f"trichrome {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("inputs", nargs="+",
                        help="RAW files, and/or folders of RAW files")
        sp.add_argument("-r", "--recursive", action="store_true",
                        help="descend into subfolders of any input folder")
        sp.add_argument("-o", "--out", metavar="DIR",
                        help="write the merged files here (default: beside each "
                             "triplet's first frame)")
        sp.add_argument("--format", choices=bake_mod.OUTPUT_FORMATS,
                        default=bake_mod.DEFAULT_FORMAT,
                        help="tiff: compact, archival, opens as a rendered "
                             "image (default). dng: uncompressed and larger, "
                             "but opens through a converter's RAW pipeline "
                             "(raw white balance, camera profile)")
        sp.add_argument("--suffix", default=bake_mod.DEFAULT_NAME_SUFFIX,
                        help="appended to the first frame's name, before the "
                             "extension "
                             f"(default: {bake_mod.DEFAULT_NAME_SUFFIX})")
        sp.add_argument("--order", default=merge_mod.DEFAULT_LIGHT_ORDER,
                        metavar="RGB",
                        help="which light each frame of a triplet was shot "
                             "under, in filename order (default: RGB)")

    sp = sub.add_parser("merge",
                        help="merge triplets into linear TIFFs or DNGs")
    common(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--demosaic", dest="demosaic", action="store_true",
                   default=True,
                   help="full-resolution linear demosaic of each frame's own "
                        "channel (default)")
    g.add_argument("--photosite", dest="demosaic", action="store_false",
                   help="no demosaic at all: bare Bayer photosites, half "
                        "resolution")
    sp.add_argument("--delete-originals", action="store_true",
                    help="PERMANENTLY delete each triplet's source RAWs after "
                         "its merged file is written and verified")
    sp.add_argument("--no-icc", dest="icc", action="store_false",
                    help="write a strictly untagged TIFF, with no linear ICC "
                         "profile (pixels are the same either way; without it "
                         "viewers assume sRGB and show the file dark). No "
                         "effect on --format dng, which carries no ICC profile")
    sp.add_argument("--despeck", action="store_true",
                    help="detect and repair dust, hairs and fine scratches on "
                         "the merged UNINVERTED negative. Unlike every other "
                         "option here this CHANGES PIXELS — see despeck.py for "
                         "what it does and when it does not work")
    sp.add_argument("--despeck-threshold", type=float, metavar="D",
                    help="minimum neutral density a defect must add before it "
                         "is repaired; lower catches more and risks real detail "
                         f"(default: {despeck_mod.DEFAULT_THRESHOLD})")
    sp.add_argument("--despeck-radius", type=int, metavar="PX",
                    help="half-width of the structuring element: the largest "
                         "defect that can be detected "
                         f"(default: {despeck_mod.DEFAULT_RADIUS})")
    sp.add_argument("--despeck-neutrality", type=float, metavar="F",
                    help="how equally a defect must attenuate R, G and B to "
                         "count as dust, 0 to 1. 0 drops only this agreement "
                         "check; the detector always measures the SMALLEST of "
                         "the three attenuations, so a defect in one dye layer "
                         "alone is never repaired either way "
                         f"(default: {despeck_mod.DEFAULT_NEUTRALITY})")
    sp.add_argument("--despeck-floor-percentile", type=float, metavar="P",
                    dest="despeck_floor_percentile",
                    help="frame percentile taken as the densest real film; "
                         "nothing below it is repaired, which is what keeps "
                         "specular highlights out of the mask. 0 disables the "
                         "test — useful for faint dust on a thin, contrasty "
                         "negative, at the cost of that protection "
                         f"(default: {despeck_mod.DEFAULT_FLOOR_PERCENTILE})")
    sp.add_argument("--despeck-mask", action="store_true",
                    help="also write the defect mask beside each output, to "
                         "check the detector by eye before trusting it")
    sp.add_argument("-n", "--dry-run", action="store_true",
                    help="show what would happen; decode, write and delete "
                         "nothing")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("list", help="show how files group into triplets")
    common(sp)
    sp.set_defaults(func=cmd_list)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        merge_mod.parse_light_order(args.order)      # fail fast on a bad order
        return args.func(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
