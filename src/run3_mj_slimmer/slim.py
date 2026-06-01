#!/usr/bin/env python3
"""slim.py - ScoutingNanoAOD slimmer for the 6-jet multijet analysis.

Reads a ScoutingNanoAOD ROOT file, applies cuts defined in a JSON config file,
and writes a slimmed ROOT file containing:
  - TTree 'events'  : selected events with ScoutingPFJet branches + derived HT
  - TH1  'cutflow'  : event counts after each selection stage
  - TTree 'meta'    : slimmer version, config version, input file, cut parameters

Usage:
    python slim.py input.root config.json
    python slim.py input.root config.json --tree Events --chunk-size 100000

Output file is written to the current directory as slimmed_<input basename>,
e.g. input /path/to/ScoutingNanoAOD.root -> ./slimmed_ScoutingNanoAOD.root

Config JSON format:
    {
        "metadata": {
            "version": "v1"
        },
        "cuts": {
            "ht_cut":      550.0,
            "jet_pt_cut":  30.0,
            "jet_eta_cut": 2.4,
            "min_jets":    6
        }
    }
"""

import argparse
import json
import os
import sys

import awkward as ak
import boost_histogram as bh
import numpy as np
import uproot

VERSION = "v1"

# All per-jet ScoutingPFJet branches as they appear in ScoutingNanoAOD.
# px, py, pz, e are NOT stored in the file; they are computed and added.
SCOUTING_PF_JET_BRANCHES = [
    "ScoutingPFJet_pt",
    "ScoutingPFJet_eta",
    "ScoutingPFJet_phi",
    "ScoutingPFJet_m",
    "ScoutingPFJet_jetArea",
    "ScoutingPFJet_chargedHadronEnergy",
    "ScoutingPFJet_neutralHadronEnergy",
    "ScoutingPFJet_photonEnergy",
    "ScoutingPFJet_electronEnergy",
    "ScoutingPFJet_muonEnergy",
    "ScoutingPFJet_HFEMEnergy",
    "ScoutingPFJet_HFHadronEnergy",
    "ScoutingPFJet_HOEnergy",
    "ScoutingPFJet_chargedHadronMultiplicity",
    "ScoutingPFJet_neutralHadronMultiplicity",
    "ScoutingPFJet_photonMultiplicity",
    "ScoutingPFJet_electronMultiplicity",
    "ScoutingPFJet_muonMultiplicity",
    "ScoutingPFJet_HFEMMultiplicity",
    "ScoutingPFJet_HFHadronMultiplicity",
]

# Standard CMS event-level branches.
EVENT_BRANCHES = [
    "run",
    "luminosityBlock",
    "event",
    "bunchCrossing",
    "orbitNumber",
]

# Optional event-level Scouting branches passed through if present.
OPTIONAL_EVENT_BRANCHES = [
    "ScoutingRho_fixedGridRhoFastjetAll",
    "ScoutingMET_pt",
    "ScoutingMET_phi",
]


_REQUIRED_METADATA_KEYS = {
    "version": str,
}

_REQUIRED_CUT_KEYS = {
    "ht_cut":      (int, float),
    "jet_pt_cut":  (int, float),
    "jet_eta_cut": (int, float),
    "min_jets":    int,
}


def load_config(config_path: str) -> dict:
    """Load and validate a cut config JSON file."""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Config file not found: {config_path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON in {config_path}: {exc}")

    for section, required in (("metadata", _REQUIRED_METADATA_KEYS), ("cuts", _REQUIRED_CUT_KEYS)):
        if section not in cfg:
            sys.exit(f"Config missing top-level section: '{section}'")
        missing = [k for k in required if k not in cfg[section]]
        if missing:
            sys.exit(f"Config section '{section}' missing required keys: {missing}")
        for key, expected in required.items():
            if not isinstance(cfg[section][key], expected):
                sys.exit(
                    f"Config '{section}.{key}' has wrong type: "
                    f"expected {expected}, got {type(cfg[section][key]).__name__}"
                )

    return cfg


def _available(tree_keys: set, branches: list) -> list:
    return [b for b in branches if b in tree_keys]


def _compute_cartesian(jets: ak.Array) -> ak.Array:
    """Compute px, py, pz, e from pt/eta/phi/m and attach them as new fields.

    These are not stored in ScoutingNanoAOD; coffea derives them via the vector
    mixin. We compute and store them so downstream code (e.g. ML inference) can
    read them directly from the slimmed file without a coffea dependency.
    """
    pt = jets["ScoutingPFJet_pt"]
    eta = jets["ScoutingPFJet_eta"]
    phi = jets["ScoutingPFJet_phi"]

    px = pt * np.cos(phi)
    py = pt * np.sin(phi)
    pz = pt * np.sinh(eta)

    if "ScoutingPFJet_m" in ak.fields(jets):
        mass = jets["ScoutingPFJet_m"]
        e = np.sqrt(px**2 + py**2 + pz**2 + mass**2)
    else:
        e = np.sqrt(px**2 + py**2 + pz**2)

    jets = ak.with_field(jets, px, "ScoutingPFJet_px")
    jets = ak.with_field(jets, py, "ScoutingPFJet_py")
    jets = ak.with_field(jets, pz, "ScoutingPFJet_pz")
    jets = ak.with_field(jets, e,  "ScoutingPFJet_e")
    return jets


def slim(
    input_path: str,
    output_path: str,
    config: dict,
    config_path: str,
    in_tree_name: str,
    chunk_size: int,
) -> None:
    ht_cut      = float(config["cuts"]["ht_cut"])
    jet_pt_cut  = float(config["cuts"]["jet_pt_cut"])
    jet_eta_cut = float(config["cuts"]["jet_eta_cut"])
    min_jets    = int(config["cuts"]["min_jets"])
    config_version = config["metadata"]["version"]

    with uproot.open(input_path) as in_file:
        if in_tree_name not in in_file:
            sys.exit(
                f"Tree '{in_tree_name}' not found in {input_path}. "
                f"Available keys: {list(in_file.keys())}"
            )

        tree = in_file[in_tree_name]
        tree_keys = set(tree.keys())

        jet_branches = _available(tree_keys, SCOUTING_PF_JET_BRANCHES)
        event_branches = _available(tree_keys, EVENT_BRANCHES)
        optional_branches = _available(tree_keys, OPTIONAL_EVENT_BRANCHES)

        if not jet_branches:
            sys.exit(f"No ScoutingPFJet branches found in tree '{in_tree_name}'.")

        # px/py/pz/e are never stored; compute them if the kinematics are present.
        need_cartesian = (
            "ScoutingPFJet_px" not in tree_keys
            and all(f"ScoutingPFJet_{c}" in tree_keys for c in ("pt", "eta", "phi"))
        )

        read_branches = event_branches + jet_branches + optional_branches

        print(f"Input:   {input_path}  (tree: {in_tree_name})")
        print(f"Output:  {output_path}  (tree: events)")
        print(f"Slimmer version: {VERSION}  |  Config version: {config_version}  ({config_path})")
        print(
            f"Cuts:    jet pT > {jet_pt_cut} GeV | |eta| < {jet_eta_cut} | "
            f"N_jets >= {min_jets} | HT > {ht_cut} GeV"
        )
        print(
            f"Branches: {len(jet_branches)} jet, {len(event_branches)} event"
            + (f", {len(optional_branches)} optional" if optional_branches else "")
            + ("  [will compute px/py/pz/e]" if need_cartesian else "")
        )

        # Cutflow: track events surviving each selection stage.
        # Index 0 = all events, index 3 = final output (matches events TTree).
        cutflow_labels = [
            "All events",
            f"1+ jet (pT>{jet_pt_cut:.0f} GeV, |eta|<{jet_eta_cut:.1f})",
            f"N_jets >= {min_jets}",
            f"HT > {ht_cut:.0f} GeV",
        ]
        cutflow_counts = [0, 0, 0, 0]

        total_in = 0
        total_out = 0

        with uproot.recreate(output_path) as out_file:
            out_tree = None

            for chunk in tree.iterate(read_branches, library="ak", step_size=chunk_size):
                n_chunk = len(chunk)
                total_in += n_chunk
                cutflow_counts[0] += n_chunk

                # Build a zipped record so one boolean mask filters every branch.
                jet_fields = {f: chunk[f] for f in jet_branches}
                jets = ak.zip(jet_fields)

                # Jet-level selection: pT and |eta|
                jet_mask = (
                    (jets["ScoutingPFJet_pt"] > jet_pt_cut) &
                    (abs(jets["ScoutingPFJet_eta"]) < jet_eta_cut)
                )
                jets = jets[jet_mask]

                # Attach computed cartesian 4-vector components.
                if need_cartesian:
                    jets = _compute_cartesian(jets)

                # Derived per-event quantities (after jet selection).
                n_jets = ak.num(jets["ScoutingPFJet_pt"])
                ht = ak.sum(jets["ScoutingPFJet_pt"], axis=1)

                # Cutflow accumulation
                cutflow_counts[1] += int(ak.sum(n_jets >= 1))
                cutflow_counts[2] += int(ak.sum(n_jets >= min_jets))

                # Event-level selection: minimum jet multiplicity and HT floor.
                event_mask = (n_jets >= min_jets) & (ht > ht_cut)
                cutflow_counts[3] += int(ak.sum(event_mask))

                jets = jets[event_mask]
                ht = ht[event_mask]

                out_record = {}

                for f in event_branches + optional_branches:
                    out_record[f] = chunk[f][event_mask]

                out_record["nScoutingPFJet"] = ak.num(jets["ScoutingPFJet_pt"])
                out_record["HT"] = ht

                for f in ak.fields(jets):
                    out_record[f] = jets[f]

                n_kept = int(ak.sum(event_mask))
                total_out += n_kept

                if out_tree is None:
                    out_file["events"] = out_record
                    out_tree = out_file["events"]
                else:
                    out_tree.extend(out_record)

                print(
                    f"  {total_in:>10,} events read  |  {total_out:>10,} kept"
                    f"  ({100 * total_out / total_in:.1f}%)",
                    end="\r",
                )

            # --- Cutflow histogram ---
            cutflow_hist = bh.Histogram(
                bh.axis.StrCategory(cutflow_labels),
                storage=bh.storage.Double(),
            )
            for i, count in enumerate(cutflow_counts):
                cutflow_hist.view()[i] = float(count)
            out_file["cutflow"] = cutflow_hist

            # --- Version histograms ---
            # StrCategory histograms are the reliable way to store string metadata
            # in uproot; byte-string TTree branches cause RNTuple routing errors.
            for hist_name, value in (
                ("slimmer_version", VERSION),
                ("config_version",  config_version),
            ):
                h = bh.Histogram(bh.axis.StrCategory([value]), storage=bh.storage.Double())
                h.view()[0] = 1.0
                out_file[hist_name] = h

            # --- Metadata TTree (one entry, numeric values only) ---
            out_file["meta"] = {
                "ht_cut":      np.array([ht_cut],      dtype=np.float32),
                "jet_pt_cut":  np.array([jet_pt_cut],  dtype=np.float32),
                "jet_eta_cut": np.array([jet_eta_cut], dtype=np.float32),
                "min_jets":    np.array([min_jets],    dtype=np.int32),
            }

    print(
        f"\nDone.   {total_in:,} events in  ->  {total_out:,} events out"
        f"  ({100 * total_out / max(total_in, 1):.1f}%)"
    )
    print("Cutflow:")
    for label, count in zip(cutflow_labels, cutflow_counts):
        print(f"  {label:<45s}  {count:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slim a ScoutingNanoAOD ROOT file for the 6-jet multijet analysis.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Input ScoutingNanoAOD ROOT file")
    parser.add_argument("config", help="JSON file containing cut configuration")
    parser.add_argument(
        "--tree", default="Events", metavar="NAME",
        help="Input tree name",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=100_000, metavar="N",
        help="Events per processing chunk",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    output_path = "slimmed_" + os.path.basename(args.input)

    slim(
        input_path=args.input,
        output_path=output_path,
        config=cfg,
        config_path=args.config,
        in_tree_name=args.tree,
        chunk_size=args.chunk_size,
    )


if __name__ == "__main__":
    main()
