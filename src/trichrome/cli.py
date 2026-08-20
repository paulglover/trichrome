"""
Command-line interface.

    trichrome merge ./shoot                     # write TIFFs, keep the RAWs
    trichrome merge ./shoot --out ./merged
    trichrome merge ./shoot --delete-originals  # destructive; asks first
    trichrome merge ./shoot --dry-run           # show the plan only
    trichrome list ./shoot                      # show the triplet grouping
"""
import argparse
import os
import sys
from typing import List

from . import __version__
from . import bake as bake_mod
from . import merge as merge_mod


def _fmt_triplet(job: bake_mod.Job) -> str:
    return "  " + " + ".join(os.path.basename(s) for s in job.sources)


def _plan(args) -> List[bake_mod.Job]:
    paths = bake_mod.collect_raw_files(args.inputs, recursive=args.recursive)
    if not paths:
        raise ValueError("No RAW files found. Supported: "
                         + ", ".join(sorted(merge_mod.RAW_EXTENSIONS)))
    return bake_mod.plan_jobs(paths, out_dir=args.out, suffix=args.suffix)


def _confirm(jobs: List[bake_mod.Job]) -> bool:
    """Ask before a destructive run. Non-interactive stdin declines rather than
    guessing — --yes is the way to say yes without a terminal."""
    n_raw = len(jobs) * merge_mod.MERGE_GROUP_SIZE
    print(f"\n!! --delete-originals will PERMANENTLY delete {n_raw} source RAW "
          f"file(s)\n   after each of the {len(jobs)} TIFF(s) is written and "
          f"verified. This cannot be undone.")
    if not sys.stdin.isatty():
        print("   stdin is not a terminal — re-run with --yes to confirm.")
        return False
    try:
        return input("   Type 'delete' to proceed: ").strip().lower() == "delete"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_list(args) -> int:
    jobs = _plan(args)
    print(f"{len(jobs)} triplet(s), light order {args.order.upper()}:")
    for job in jobs:
        print(f"{_fmt_triplet(job)}  ->  {job.output}")
    return 0


def cmd_merge(args) -> int:
    jobs = _plan(args)
    mode = "demosaic (full resolution)" if args.demosaic else \
           "single photosite (half resolution)"
    print(f"{len(jobs)} triplet(s) · light order {args.order.upper()} · {mode}"
          + ("" if args.icc else " · untagged (no ICC)"))

    if args.delete_originals and not args.dry_run and not args.yes:
        if not _confirm(jobs):
            print("Aborted — nothing was written or deleted.")
            return 1

    def progress(i, total, job):
        print(f"[{i + 1}/{total}] {_fmt_triplet(job).strip()}  ->  "
              f"{os.path.basename(job.output)}", flush=True)

    summary = bake_mod.run_jobs(
        jobs, demosaic=args.demosaic, light_order=args.order,
        delete_originals=args.delete_originals, dry_run=args.dry_run,
        icc=args.icc, progress_cb=progress)

    if args.dry_run:
        print(f"\nDry run — nothing written. {len(jobs)} TIFF(s) would be "
              f"created" + (f", {len(jobs) * 3} RAW(s) deleted."
                            if args.delete_originals else "."))
        return 0

    print()
    for r in summary.written:
        h, w = r.size or (0, 0)
        print(f"wrote {r.job.output}  ({w}x{h}, uint16)")
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
                    "linear TIFFs.",
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
                        help="write TIFFs here (default: beside each triplet's "
                             "first frame)")
        sp.add_argument("--suffix", default=bake_mod.DEFAULT_NAME_SUFFIX,
                        help="appended to the first frame's name "
                             f"(default: {bake_mod.DEFAULT_NAME_SUFFIX})")
        sp.add_argument("--order", default=merge_mod.DEFAULT_LIGHT_ORDER,
                        metavar="RGB",
                        help="which light each frame of a triplet was shot "
                             "under, in filename order (default: RGB)")

    sp = sub.add_parser("merge", help="merge triplets into linear TIFFs")
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
                         "its TIFF is written and verified")
    sp.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt for --delete-originals")
    sp.add_argument("--no-icc", dest="icc", action="store_false",
                    help="write a strictly untagged TIFF, with no linear ICC "
                         "profile (pixels are the same either way; without it "
                         "viewers assume sRGB and show the file dark)")
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
