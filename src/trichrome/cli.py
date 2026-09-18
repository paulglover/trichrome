"""
Command-line interface.

    trichrome -i S0123-10 img001.arw img002.arw img003.arw   # -> S0123-10.dng
    trichrome -i S0123-10 img00[1-3].arw --out ./merged
    trichrome -i S0123-10 img00[1-3].arw --delete-originals  # destructive; no prompt
    trichrome -i S0123-10 img00[1-3].arw --dry-run           # show the plan only

Exactly three RAW files, the frames of one shot, one per light. They are merged
into one linear DNG named FILMID.dng, written beside the first frame unless
`--out` says otherwise, with the film ID also stated as its XMP dc:identifier.
Selection order does not matter: the frames are taken in filename order.
"""
import argparse
import os
import sys

from . import __version__
from . import bake as bake_mod
from . import merge as merge_mod


def cmd_merge(args) -> int:
    job = bake_mod.plan_job(args.inputs, args.film_id, out_dir=args.out)
    mode = "demosaic (full resolution)" if args.demosaic else \
           "single photosite (half resolution)"
    print(f"{job.film_id}: light order {args.order.upper()} · {mode}"
          f" · linear DNG")
    print("  " + " + ".join(os.path.basename(s) for s in job.sources))
    print(f"  -> {job.output}", flush=True)

    r = bake_mod.run_job(
        job, demosaic=args.demosaic, light_order=args.order,
        delete_originals=args.delete_originals, dry_run=args.dry_run)

    if args.dry_run:
        print("\nDry run — nothing written"
              + (", nothing deleted." if args.delete_originals else "."))
        return 0

    if r.ok:
        h, w = r.size or (0, 0)
        print(f"\nwrote {job.output}  ({w}x{h}, uint16)")
    # The file is written and verified; something about it is worth knowing —
    # today, a DNG whose source's camera metadata could not be read. Said before
    # the deletion count, because that is the decision it bears on.
    if r.warning:
        print(f"WARNING {job.name}: {r.warning}", file=sys.stderr)
    if r.error:
        print(f"FAILED {job.film_id}: {r.error}", file=sys.stderr)
    for path, reason in r.delete_errors:
        print(f"NOT DELETED {os.path.basename(path)}: {reason}", file=sys.stderr)

    if r.deleted:
        print(f"deleted {len(r.deleted)} source RAW(s)")
    elif args.delete_originals:
        print("deleted nothing" + (" (the merge failed)" if not r.ok else ""))
    return 1 if (r.error or r.delete_errors) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trichrome",
        description="Merge a red/green/blue-light RAW triplet into one 16-bit "
                    "linear DNG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version",
                   version=f"trichrome {__version__}")
    p.add_argument("inputs", nargs="+", metavar="RAW",
                   help="the three RAW frames of one shot, one per light")
    p.add_argument("-i", "--filmid", "--film-id", dest="film_id",
                   required=True, metavar="FILMID",
                   help="the film ID: the merged file is FILMID.dng, and the "
                        "ID is written into its XMP dc:identifier")
    p.add_argument("-o", "--out", metavar="DIR",
                   help="write the merged file here (default: beside the "
                        "first frame)")
    p.add_argument("--order", default=merge_mod.DEFAULT_LIGHT_ORDER,
                   metavar="RGB",
                   help="which light each frame was shot under, in filename "
                        "order (default: RGB)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--demosaic", dest="demosaic", action="store_true",
                   default=True,
                   help="full-resolution linear demosaic of each frame's own "
                        "channel (default)")
    g.add_argument("--photosite", dest="demosaic", action="store_false",
                   help="no demosaic at all: bare Bayer photosites, half "
                        "resolution")
    p.add_argument("--delete-originals", action="store_true",
                   help="PERMANENTLY delete the three source RAWs after the "
                        "merged file is written and verified")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show what would happen; decode, write and delete "
                        "nothing")
    p.set_defaults(func=cmd_merge)
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
