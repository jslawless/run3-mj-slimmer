"""End-to-end slimmer tests: write a toy ScoutingNanoAOD, run slim(), inspect."""

from pathlib import Path

import awkward as ak
import numpy as np
import uproot

from run3_mj_slimmer.slim import slim

FIX = Path(__file__).parent / "data" / "jme"

CUTS = {"ht_cut": 50.0, "jet_pt_cut": 20.0, "jet_eta_cut": 3.0, "min_jets": 1}

MC_CORR = {
    "enabled": True, "is_mc": True, "selection_mode": "nominal", "files_dir": str(FIX),
    "jec": {
        "L1FastJet":  "Summer22_22Sep2023_V2_MC_L1FastJet_AK4PFHLT.jec.txt",
        "L2Relative": "Summer22_22Sep2023_V2_MC_L2Relative_AK4PFHLT.jec.txt",
        "L3Absolute": "Summer22_22Sep2023_V2_MC_L3Absolute_AK4PFHLT.jec.txt",
    },
    "junc": "Summer22_22Sep2023_V2_MC_Uncertainty_AK4PFHLT.junc.txt",
    "jer": {
        "PtResolution": "Summer22_JRV1_MC_PtResolution_AK4PFHLT.jr.txt",
        "SF":           "Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt",
    },
}

DATA_CORR = {
    "enabled": True, "is_mc": False, "selection_mode": "nominal", "files_dir": str(FIX),
    "jec": {
        "L1FastJet":    "Summer22_RunCD_DATA_L1FastJet_AK4PFHLT.jec.txt",
        "L2Relative":   "Summer22_RunCD_DATA_L2Relative_AK4PFHLT.jec.txt",
        "L3Absolute":   "Summer22_RunCD_DATA_L3Absolute_AK4PFHLT.jec.txt",
        "L2L3Residual": "Summer22_RunCD_DATA_L2L3Residual_AK4PFHLT.jec.txt",
    },
    "junc": "Summer22_RunCD_DATA_Uncertainty_AK4PFHLT.junc.txt",
}


def _write_toy(path, with_gen=True):
    pt   = ak.Array([[120.0, 40.0], [300.0], [80.0, 70.0, 60.0]])
    eta  = ak.Array([[0.5, -1.0], [2.0], [0.2, 0.3, 0.4]])
    phi  = ak.Array([[0.1, 2.0], [-1.0], [0.0, 1.0, 2.0]])
    m    = ak.Array([[12.0, 8.0], [25.0], [9.0, 8.0, 7.0]])
    area = ak.Array([[0.5, 0.5], [0.5], [0.5, 0.5, 0.5]])
    che  = ak.Array([[10.0, 5.0], [30.0], [8.0, 7.0, 6.0]])
    chm  = ak.Array([[3, 2], [5], [2, 2, 1]])
    branches = {
        "run": np.array([1, 1, 1], dtype=np.uint32),
        "luminosityBlock": np.array([10, 11, 12], dtype=np.uint32),
        "event": np.array([100, 101, 102], dtype=np.uint64),
        "ScoutingRho_fixedGridRhoFastjetAll": np.array([20.0, 30.0, 25.0], dtype=np.float32),
        "ScoutingPFJet_pt": pt,
        "ScoutingPFJet_eta": eta,
        "ScoutingPFJet_phi": phi,
        "ScoutingPFJet_m": m,
        "ScoutingPFJet_jetArea": area,
        "ScoutingPFJet_chargedHadronEnergy": che,
        "ScoutingPFJet_chargedHadronMultiplicity": chm,
    }
    if with_gen:
        branches.update({
            "GenJet_pt":   ak.Array([[118.0, 39.0], [295.0], [79.0, 69.0, 59.0]]),
            "GenJet_eta":  eta,
            "GenJet_phi":  phi,
            "GenJet_mass": m,
        })
    with uproot.recreate(path) as f:
        f["Events"] = branches


def _cfg(corr):
    return {"metadata": {"version": "vtest"}, "cuts": CUTS, "corrections": corr}


def _events_keys(path):
    with uproot.open(path) as f:
        return set(f["events"].keys())


def _meta(path):
    with uproot.open(path) as f:
        return {k: f["meta"][k].array(library="np")[0] for k in f["meta"].keys()}


def test_mc_corrections_add_variation_branches(tmp_path):
    inp = tmp_path / "in.root"
    out = tmp_path / "out.root"
    _write_toy(inp, with_gen=True)
    slim(str(inp), str(out), _cfg(MC_CORR), "cfg", "Events", 1000, corrections_enabled=True)

    keys = _events_keys(out)
    assert any(k.endswith("_pt_raw") for k in keys)
    assert any(k.endswith("_pt_jesUp") for k in keys)
    assert any(k.endswith("_pt_jesDown") for k in keys)
    assert any(k.endswith("_pt_jerUp") for k in keys)
    assert any(k.endswith("_m_jerDown") for k in keys)

    with uproot.open(out) as f:
        arr = f["events"].arrays(["ScoutingPFJet_pt", "ScoutingPFJet_pt_raw"])
    pt = ak.flatten(arr["ScoutingPFJet_pt"])
    raw = ak.flatten(arr["ScoutingPFJet_pt_raw"])
    assert len(pt) > 0
    assert bool(ak.any(pt != raw))            # corrections actually applied

    meta = _meta(out)
    assert int(meta["corrections_enabled"]) == 1
    assert int(meta["is_mc"]) == 1


def test_disabled_is_backward_compatible(tmp_path):
    inp = tmp_path / "in.root"
    out = tmp_path / "out.root"
    _write_toy(inp, with_gen=True)
    slim(str(inp), str(out), _cfg({"enabled": False}), "cfg", "Events", 1000,
         corrections_enabled=False)

    keys = _events_keys(out)
    assert any(k.endswith("_pt") for k in keys)
    assert not any("_pt_raw" in k for k in keys)
    assert not any("_jesUp" in k for k in keys)
    assert int(_meta(out)["corrections_enabled"]) == 0


def test_data_path_has_jes_but_no_jer(tmp_path):
    inp = tmp_path / "in.root"
    out = tmp_path / "out.root"
    _write_toy(inp, with_gen=False)       # no GenJet -> data-like
    slim(str(inp), str(out), _cfg(DATA_CORR), "cfg", "Events", 1000,
         corrections_enabled=True)

    keys = _events_keys(out)
    assert any(k.endswith("_pt_jesUp") for k in keys)
    assert not any("_jerUp" in k for k in keys)
    assert int(_meta(out)["is_mc"]) == 0
