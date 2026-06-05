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

    _validate_corrections(cfg.get("corrections"))

    return cfg


def _validate_corrections(corr: dict) -> None:
    """Validate the optional 'corrections' config block (absent => disabled)."""
    if corr is None:
        return
    if not isinstance(corr, dict):
        sys.exit("Config 'corrections' must be an object.")
    if not corr.get("enabled"):
        return

    jec = corr.get("jec")
    if not isinstance(jec, dict):
        sys.exit("Config 'corrections.jec' must be an object mapping level -> file.")
    for lvl in ("L1FastJet", "L2Relative", "L3Absolute"):
        if lvl not in jec:
            sys.exit(f"Config 'corrections.jec' missing required level: '{lvl}'")

    if not corr.get("junc"):
        sys.exit("Config 'corrections.junc' (JES Uncertainty file) is required.")

    mode = corr.get("selection_mode", "nominal")
    if mode not in ("nominal", "loose_or"):
        sys.exit("Config 'corrections.selection_mode' must be 'nominal' or 'loose_or'.")

    # When is_mc is given explicitly we can fully validate now; otherwise the
    # MC/data-specific files are checked at runtime once the input is inspected.
    is_mc = corr.get("is_mc")
    if is_mc is True:
        jer = corr.get("jer")
        if not isinstance(jer, dict) or "PtResolution" not in jer or "SF" not in jer:
            sys.exit(
                "Config 'corrections.jer' with 'PtResolution' and 'SF' is required for MC."
            )
    elif is_mc is False:
        if "L2L3Residual" not in jec:
            sys.exit("Config 'corrections.jec.L2L3Residual' is required for data.")


def _available(tree_keys: set, branches: list) -> list:
    return [b for b in branches if b in tree_keys]


def _peek_run(tree):
    """Read the first 'run' value as an IOV hint for data; None if unavailable."""
    try:
        arr = tree["run"].array(entry_stop=1, library="np")
        return int(arr[0]) if len(arr) else None
    except Exception:
        return None



def slim(
    input_path: str,
    output_path: str,
    config: dict,
    config_path: str,
    in_tree_name: str,
    chunk_size: int,
    corrections_enabled: bool = False,
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

        # --- JEC/JER corrections setup (factory built once, before the loop) ---
        factory = None
        corr_cfg = config.get("corrections", {}) or {}
        is_mc = bool(gen_jet_branches)
        selection_mode = "nominal"
        if corrections_enabled:
            from run3_mj_slimmer.corrections import JetCorrectionFactory, RHO_BRANCH

            if RHO_BRANCH not in optional_branches:
                sys.exit(
                    f"Corrections enabled but '{RHO_BRANCH}' is not in the input tree; "
                    "it is required for the L1FastJet correction."
                )
            is_mc = bool(corr_cfg.get("is_mc", bool(gen_jet_branches)))
            selection_mode = corr_cfg.get("selection_mode", "nominal")
            run_hint = None if is_mc else _peek_run(tree)
            factory = JetCorrectionFactory(corr_cfg, is_mc=is_mc, run=run_hint)

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
        if factory is not None:
            print(
                f"Corrections: ENABLED ({'MC' if is_mc else 'DATA'}) | "
                f"mode={selection_mode} | {len(factory.resolved_files)} JME files | "
                f"variations={factory.uncertainties}"
            )
        else:
            print("Corrections: disabled")

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

                if factory is None:
                    # ---- Legacy path: select on raw jet pT, pass jets through ----
                    jet_mask = (
                        (jets["ScoutingPFJet_pt"] > jet_pt_cut) &
                        (abs(jets["ScoutingPFJet_eta"]) < jet_eta_cut)
                    )
                    jets = jets[jet_mask]
                    n_jets = ak.num(jets["ScoutingPFJet_pt"])
                    ht = ak.sum(jets["ScoutingPFJet_pt"], axis=1)
                    cutflow_counts[1] += int(ak.sum(n_jets >= 1))
                    cutflow_counts[2] += int(ak.sum(n_jets >= min_jets))
                    keep = (n_jets >= min_jets) & (ht > ht_cut)
                    cutflow_counts[3] += int(ak.sum(keep))
                    jets = jets[keep]
                    ht_out = ht[keep]
                    jet_out = {
                        f[len("ScoutingPFJet_"):]: jets[f] for f in ak.fields(jets)
                    }
                else:
                    # ---- Corrected path: apply JEC/JER before the selection ----
                    short = ak.zip({
                        "pt":   jets["ScoutingPFJet_pt"],
                        "mass": jets["ScoutingPFJet_m"],
                        "eta":  jets["ScoutingPFJet_eta"],
                        "phi":  jets["ScoutingPFJet_phi"],
                        "area": jets["ScoutingPFJet_jetArea"],
                    })
                    gen = None
                    if is_mc and gen_jet_branches:
                        gen = ak.zip({
                            f[len("GenJet_"):]: chunk[f] for f in gen_jet_branches
                        })
                    corr = factory.build_jets(short, rho=chunk[RHO_BRANCH], gen_jets=gen)

                    # nominal + systematic per-jet pT variations
                    var_pt = {"nominal": corr.pt}
                    var_pt["jesUp"]   = corr["JES_jes"].up.pt
                    var_pt["jesDown"] = corr["JES_jes"].down.pt
                    if is_mc:
                        var_pt["jerUp"]   = corr["JER"].up.pt
                        var_pt["jerDown"] = corr["JER"].down.pt

                    eta_abs = abs(corr.eta)
                    jet_pass = {
                        k: (v > jet_pt_cut) & (eta_abs < jet_eta_cut)
                        for k, v in var_pt.items()
                    }
                    n_var = {k: ak.sum(m, axis=1) for k, m in jet_pass.items()}
                    ht_var = {
                        k: ak.sum(ak.where(jet_pass[k], var_pt[k], 0.0), axis=1)
                        for k in var_pt
                    }
                    evt_pass = {
                        k: (n_var[k] >= min_jets) & (ht_var[k] > ht_cut)
                        for k in var_pt
                    }

                    # selection_mode: "nominal" decides membership from the
                    # central values; "loose_or" keeps an event (and a jet) if it
                    # passes in nominal OR any stored variation, so systematic
                    # migrations at the cut boundary are not lost.
                    jet_keep = jet_pass["nominal"]
                    keep = evt_pass["nominal"]
                    if selection_mode == "loose_or":
                        for k in var_pt:
                            if k == "nominal":
                                continue
                            jet_keep = jet_keep | jet_pass[k]
                            keep = keep | evt_pass[k]

                    cutflow_counts[1] += int(ak.sum(n_var["nominal"] >= 1))
                    cutflow_counts[2] += int(ak.sum(n_var["nominal"] >= min_jets))
                    cutflow_counts[3] += int(ak.sum(keep))

                    def _sel(arr, _jk=jet_keep, _k=keep):
                        return arr[_jk][_k]

                    def _f32(arr):
                        return ak.values_astype(_sel(arr), np.float32)

                    # eta, phi, jetArea, energies and multiplicities are unchanged
                    # by JEC/JER; pt and m are replaced by the corrected values.
                    jet_out = {}
                    for f in ak.fields(jets):
                        short_name = f[len("ScoutingPFJet_"):]
                        if short_name in ("pt", "m"):
                            continue
                        jet_out[short_name] = _sel(jets[f])
                    jet_out["pt"]         = _f32(corr.pt)
                    jet_out["m"]          = _f32(corr.mass)
                    jet_out["pt_raw"]     = _f32(corr.pt_raw)
                    jet_out["m_raw"]      = _f32(corr.mass_raw)
                    jet_out["pt_jesUp"]   = _f32(corr["JES_jes"].up.pt)
                    jet_out["pt_jesDown"] = _f32(corr["JES_jes"].down.pt)
                    jet_out["m_jesUp"]    = _f32(corr["JES_jes"].up.mass)
                    jet_out["m_jesDown"]  = _f32(corr["JES_jes"].down.mass)
                    if is_mc:
                        jet_out["pt_jerUp"]   = _f32(corr["JER"].up.pt)
                        jet_out["pt_jerDown"] = _f32(corr["JER"].down.pt)
                        jet_out["m_jerUp"]    = _f32(corr["JER"].up.mass)
                        jet_out["m_jerDown"]  = _f32(corr["JER"].down.mass)

                    ht_out = ht_var["nominal"][keep]

                # ---- Shared output assembly (uses the chosen event mask) ----
                out_record = {}
                for f in event_branches + optional_branches:
                    out_record[f] = chunk[f][keep]

                out_record["HT"] = ht_out

                # Write all jet fields as a single nested branch so uproot creates
                # one nScoutingPFJet count instead of a separate count per field.
                # Fields are accessible as ScoutingPFJet.pt, ScoutingPFJet.eta, etc.
                out_record["ScoutingPFJet"] = ak.zip(jet_out)

                if gen_jet_branches:
                    out_record["GenJet"] = ak.zip({
                        f[len("GenJet_"):]: chunk[f][keep]
                        for f in gen_jet_branches
                    })

                if gen_part_branches:
                    out_record["GenPart"] = ak.zip({
                        f[len("GenPart_"):]: chunk[f][keep]
                        for f in gen_part_branches
                    })

                n_kept = int(ak.sum(keep))
                total_out += n_kept

                # uproot's TTree.extend raises "zero-size array to reduction
                # ... maximum" on a chunk with no kept events (empty jagged
                # branches), so only create/extend the tree from non-empty
                # chunks. If every chunk is empty (e.g. a low-HT QCD slice with
                # nothing passing the HT cut) the events tree is simply absent -
                # but the cutflow below is still written, which is what the
                # analyzer needs for the N_original weight denominator.
                if n_kept > 0:
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
                "corrections_enabled": np.array([1 if factory is not None else 0], dtype=np.int32),
                "is_mc":               np.array([1 if is_mc else 0], dtype=np.int32),
            }
            out_file.mktree("meta", {name: arr.dtype for name, arr in meta_record.items()})
            out_file["meta"].extend(meta_record)

    print(
        f"\nDone.   {total_in:,} events in  ->  {total_out:,} events out"
        f"  ({100 * total_out / total_in:.1f}%)"
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
    corr_group = parser.add_mutually_exclusive_group()
    corr_group.add_argument(
        "--corrections", dest="corrections", action="store_true", default=None,
        help="Force-enable JEC/JER corrections (needs a 'corrections' config block)",
    )
    corr_group.add_argument(
        "--no-corrections", dest="corrections", action="store_false",
        help="Disable JEC/JER corrections even if enabled in the config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    corr_cfg = cfg.get("corrections", {}) or {}
    if args.corrections is None:
        corrections_enabled = bool(corr_cfg.get("enabled", False))
    else:
        corrections_enabled = args.corrections

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
        corrections_enabled=corrections_enabled,
    )


if __name__ == "__main__":
    main()
