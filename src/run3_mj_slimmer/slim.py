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

# Generator-level ak4 jets (MC-only). Passed through without selection.
GEN_JET_BRANCHES = [
    "GenJet_pt",
    "GenJet_eta",
    "GenJet_phi",
    "GenJet_mass",
]

# Generator-level particles (MC-only). Passed through without selection.
GEN_PART_BRANCHES = [
    "GenPart_pt",
    "GenPart_eta",
    "GenPart_phi",
    "GenPart_mass",
    "GenPart_pdgId",
    "GenPart_status",
    "GenPart_charge",
    "GenPart_genPartIdxMother",
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
    version = config["metadata"]["version"]

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
        gen_jet_branches = _available(tree_keys, GEN_JET_BRANCHES)
        gen_part_branches = _available(tree_keys, GEN_PART_BRANCHES)

        if not jet_branches:
            sys.exit(f"No ScoutingPFJet branches found in tree '{in_tree_name}'.")

        read_branches = (
            event_branches + jet_branches + optional_branches
            + gen_jet_branches + gen_part_branches
        )

        print(f"Input:   {input_path}  (tree: {in_tree_name})")
        print(f"Output:  {output_path}  (tree: events)")
        print(f"Version: {version}  ({config_path})")
        print(
            f"Cuts:    jet pT > {jet_pt_cut} GeV | |eta| < {jet_eta_cut} | "
            f"N_jets >= {min_jets} | HT > {ht_cut} GeV"
        )
        print(
            f"Branches: {len(jet_branches)} jet, {len(event_branches)} event"
            + (f", {len(optional_branches)} optional" if optional_branches else "")
            + (f", {len(gen_jet_branches)} gen jet" if gen_jet_branches else "")
            + (f", {len(gen_part_branches)} gen part" if gen_part_branches else "")
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

                out_record["HT"] = ht

                # Write all jet fields as a single nested branch so uproot creates
                # one nScoutingPFJet count instead of a separate count per field.
                # Fields are accessible as ScoutingPFJet.pt, ScoutingPFJet.eta, etc.
                out_record["ScoutingPFJet"] = ak.zip({
                    f[len("ScoutingPFJet_"):]: jets[f] for f in ak.fields(jets)
                })

                if gen_jet_branches:
                    gen_jets = ak.zip({
                        f[len("GenJet_"):]: chunk[f][event_mask]
                        for f in gen_jet_branches
                    })
                    out_record["GenJet"] = gen_jets

                if gen_part_branches:
                    gen_parts = ak.zip({
                        f[len("GenPart_"):]: chunk[f][event_mask]
                        for f in gen_part_branches
                    })
                    out_record["GenPart"] = gen_parts

                n_kept = int(ak.sum(event_mask))
                total_out += n_kept

                if out_tree is None:
                    out_file.mktree(
                        "events",
                        {name: arr.type for name, arr in out_record.items()},
                    )
                    out_tree = out_file["events"]
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

            # --- Version histogram ---
            # StrCategory histogram is the reliable way to store a string in uproot;
            # byte-string TTree branches trigger the RNTuple routing path.
            version_hist = bh.Histogram(bh.axis.StrCategory([version]), storage=bh.storage.Double())
            version_hist.view()[0] = 1.0
            out_file["version"] = version_hist

            # --- Metadata TTree (one entry, numeric values only) ---
            meta_record = {
                "ht_cut":      np.array([ht_cut],      dtype=np.float32),
                "jet_pt_cut":  np.array([jet_pt_cut],  dtype=np.float32),
                "jet_eta_cut": np.array([jet_eta_cut], dtype=np.float32),
                "min_jets":    np.array([min_jets],    dtype=np.int32),
            }
            out_file.mktree("meta", {name: arr.dtype for name, arr in meta_record.items()})
            out_file["meta"].extend(meta_record)

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
    parser.add_argument(
        "--output-tag", type=str, default="",
        help="Optional tag added to the output file name",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.output_tag:
        output_path = "slimmed" + "_" + args.output_tag + "_" + os.path.basename(args.input)
    else:
        output_path = "slimmed" + "_" + os.path.basename(args.input)

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
