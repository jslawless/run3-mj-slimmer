#!/usr/bin/env python3
"""split_fileset.py - Split a combined coffea fileset JSON into per-dataset files.

The slimmer's run_all.sh expects ONE filelist JSON per dataset in a directory
(it derives the job tag/logdir from the filename). Reference samples such as
run3-scouting-multijets/filelists/file_list_mc.json instead bundle many datasets
(TTto2L2Nu, TTtoLNu2Q, TTto4Q, DY*, W*, diboson) in a single file. Both use the
SAME coffea schema:

    { "<dataset>": { "files": { "root://.../file.root": "Events", ... } }, ... }

so they are already format-compatible with the slimmer; this just splits the
combined file into the per-dataset layout the slimmer's filelists/ dir uses, and
optionally filters to selected datasets and/or rewrites the XRootD redirector.

Usage:
    # every dataset -> one <dataset>.json each
    python scripts/split_fileset.py file_list_mc.json -o filelists/

    # only ttbar, swapping the (group-space-unfriendly) xcache redirector
    python scripts/split_fileset.py file_list_mc.json -o filelists/ \\
        --only TT --redirector root://cms-xrd-global.cern.ch/
"""

import argparse
import json
import os
import re
import sys


def reredirect(path, host):
    """Replace the leading 'root://<host>//' of an XRootD URL with a new host.

    Keeps the double slash before the absolute /store path (single slash makes
    it relative and the server rejects it).
    """
    return re.sub(r"^root://[^/]+/+", host.rstrip("/") + "//", path)


def main():
    p = argparse.ArgumentParser(
        description="Split a combined coffea fileset JSON into one JSON per "
                    "dataset (the slimmer's per-dataset filelist layout).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("infile", help="Combined fileset JSON: {dataset: {files: {path: tree}}}")
    p.add_argument("-o", "--outdir", default=".", help="Output dir for per-dataset JSONs.")
    p.add_argument("--only", action="append", default=[], metavar="SUBSTR",
                   help="Keep only datasets whose name contains SUBSTR (repeatable). "
                        "Default: all datasets.")
    p.add_argument("--redirector", default=None, metavar="ROOT_URL",
                   help="If set, rewrite every file's XRootD redirector to this host, "
                        "e.g. root://cms-xrd-global.cern.ch/ (xcache does not serve "
                        "/store/group space). Default: leave paths unchanged.")
    args = p.parse_args()

    with open(args.infile) as f:
        fileset = json.load(f)

    # Guard against accidentally feeding the analyzer's datasets.json (different
    # schema: {"metadata": ..., "datasets": {name: [paths]}}).
    if isinstance(fileset, dict) and "datasets" in fileset and "metadata" in fileset:
        sys.exit("Input looks like an analyzer datasets.json (metadata/datasets); "
                 "this expects a coffea fileset {dataset: {files: {path: tree}}}.")

    names = sorted(fileset)
    if args.only:
        names = [n for n in names if any(s in n for s in args.only)]
        if not names:
            sys.exit(f"No datasets matched --only {args.only}")

    os.makedirs(args.outdir, exist_ok=True)
    total_files = 0
    written = 0
    for name in names:
        entry = fileset[name]
        if not isinstance(entry, dict) or "files" not in entry:
            print(f"  skip (no 'files' key): {name}", file=sys.stderr)
            continue
        files = entry["files"]
        if args.redirector:
            files = {reredirect(path, args.redirector): tree
                     for path, tree in files.items()}
        outpath = os.path.join(args.outdir, f"{name}.json")
        with open(outpath, "w") as f:
            json.dump({name: {"files": files}}, f, indent=4)
        total_files += len(files)
        written += 1
        print(f"  {name}: {len(files)} files -> {outpath}")

    print(f"\nWrote {written} dataset file(s) ({total_files} files) to {args.outdir}/")


if __name__ == "__main__":
    main()
