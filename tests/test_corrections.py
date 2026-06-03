"""Unit tests for run3_mj_slimmer.corrections (JEC/JER via coffea)."""

from pathlib import Path

import awkward as ak
import numpy as np
import pytest

from run3_mj_slimmer.corrections import (
    JetCorrectionFactory,
    _broadcast_rho,
    matched_gen_pt,
)

FIX = Path(__file__).parent / "data" / "jme"

MC_CFG = {
    "enabled": True,
    "is_mc": True,
    "files_dir": str(FIX),
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

DATA_CFG = {
    "enabled": True,
    "is_mc": False,
    "files_dir": str(FIX),
    "jec": {
        "L1FastJet":    "Summer22_RunCD_DATA_L1FastJet_AK4PFHLT.jec.txt",
        "L2Relative":   "Summer22_RunCD_DATA_L2Relative_AK4PFHLT.jec.txt",
        "L3Absolute":   "Summer22_RunCD_DATA_L3Absolute_AK4PFHLT.jec.txt",
        "L2L3Residual": "Summer22_RunCD_DATA_L2L3Residual_AK4PFHLT.jec.txt",
    },
    "junc": "Summer22_RunCD_DATA_Uncertainty_AK4PFHLT.junc.txt",
}


def _toy_jets():
    """A few events including a per-event-empty one."""
    return ak.zip({
        "pt":   ak.Array([[100.0, 50.0], [200.0], []]),
        "eta":  ak.Array([[0.5, -1.0], [2.0], []]),
        "phi":  ak.Array([[0.1, 2.0], [-1.0], []]),
        "mass": ak.Array([[10.0, 8.0], [20.0], []]),
        "area": ak.Array([[0.5, 0.5], [0.5], []]),
    })


def _toy_rho():
    return ak.Array([20.0, 30.0, 40.0])


def _toy_gen():
    # event 0: a gen jet matched to jet0 (dR~0); event 1,2: none nearby
    return ak.zip({
        "pt":   ak.Array([[98.0], [], []]),
        "eta":  ak.Array([[0.5], [], []]),
        "phi":  ak.Array([[0.1], [], []]),
        "mass": ak.Array([[9.0], [], []]),
    })


def test_mc_factory_builds_and_corrects():
    fac = JetCorrectionFactory(MC_CFG, is_mc=True)
    assert fac.has_jer is True
    assert set(fac.uncertainties) == {"JES_jes", "JER"}

    corr = fac.build_jets(_toy_jets(), rho=_toy_rho(), gen_jets=_toy_gen())

    # nominal corrected differs from raw, and shapes are preserved
    assert ak.to_list(ak.num(corr.pt, axis=1)) == [2, 1, 0]
    assert bool(ak.any(ak.flatten(corr.pt) != ak.flatten(corr.pt_raw)))

    # JES total brackets the nominal per-jet
    up = ak.flatten(corr["JES_jes"].up.pt)
    dn = ak.flatten(corr["JES_jes"].down.pt)
    nom = ak.flatten(corr.pt)
    assert bool(ak.all(up >= nom)) and bool(ak.all(nom >= dn))

    # variation records carry both pt and mass
    assert "mass" in corr["JES_jes"].up.fields
    assert "JER" in corr.fields
    assert "mass" in corr["JER"].up.fields


def test_data_factory_has_no_jer():
    fac = JetCorrectionFactory(DATA_CFG, is_mc=False)
    assert fac.has_jer is False
    assert fac.uncertainties == ["JES_jes"]

    corr = fac.build_jets(_toy_jets(), rho=_toy_rho(), gen_jets=None)
    assert "JER" not in corr.fields
    assert "JES_jes" in corr.fields
    assert bool(ak.any(ak.flatten(corr.pt) != ak.flatten(corr.pt_raw)))


def test_data_residual_run_period_selection():
    cfg = dict(DATA_CFG)
    cfg["jec"] = dict(DATA_CFG["jec"])
    cfg["jec"]["L2L3Residual"] = {
        "RunCD": "Summer22_RunCD_DATA_L2L3Residual_AK4PFHLT.jec.txt",
    }
    cfg["iovs"] = [{"period": "RunCD", "run_min": 1, "run_max": 1000}]
    fac = JetCorrectionFactory(cfg, is_mc=False, run=500)
    picked = [p for kind, p in fac.resolved_files if "L2L3Residual" in p]
    assert len(picked) == 1 and "RunCD" in picked[0]


def test_matched_gen_pt():
    reco = ak.zip({
        "pt":  ak.Array([[100.0, 60.0], []]),
        "eta": ak.Array([[0.5, 3.0], []]),
        "phi": ak.Array([[0.1, 0.1], []]),
    })
    gen = ak.zip({
        "pt":  ak.Array([[97.0], []]),     # near reco[0], far from reco[1]
        "eta": ak.Array([[0.5], []]),
        "phi": ak.Array([[0.1], []]),
    })
    out = matched_gen_pt(reco, gen)
    assert ak.to_list(out) == [[pytest.approx(97.0), 0.0], []]
    # no gen jets at all -> all zeros, shape preserved
    out0 = matched_gen_pt(reco, None)
    assert ak.to_list(out0) == [[0.0, 0.0], []]


def test_broadcast_rho():
    jet_pt = ak.Array([[1.0, 2.0], [3.0], []])
    rho = ak.Array([11.0, 22.0, 33.0])
    out = _broadcast_rho(rho, jet_pt)
    assert ak.to_list(out) == [[11.0, 11.0], [22.0], []]


def test_missing_file_raises():
    cfg = dict(MC_CFG)
    cfg = {**MC_CFG, "jec": {**MC_CFG["jec"], "L1FastJet": "does_not_exist.jec.txt"}}
    with pytest.raises(FileNotFoundError):
        JetCorrectionFactory(cfg, is_mc=True)


def test_sf_token_count_enforced(tmp_path):
    # A 6-token SF name must be rejected (coffea requires exactly 5).
    bad = tmp_path / "Summer22_22Sep2023_JRV1_MC_SF_AK4PFHLT.jersf.txt"
    bad.write_text((FIX / "Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt").read_text())
    cfg = {**MC_CFG, "files_dir": str(tmp_path),
           "jer": {**MC_CFG["jer"], "SF": bad.name}}
    # L1/L2/L3/junc/jr still resolve from the real fixture dir via packaged
    # fallback is not used here; copy them too so only the SF check fires.
    for name in [*MC_CFG["jec"].values(), MC_CFG["junc"],
                 MC_CFG["jer"]["PtResolution"]]:
        (tmp_path / name).write_text((FIX / name).read_text())
    with pytest.raises(ValueError, match="EXACTLY 5"):
        JetCorrectionFactory(cfg, is_mc=True)


def test_wrong_extension_raises(tmp_path):
    # A JEC level file without the .jec.txt extension must be rejected.
    wrong = tmp_path / "Summer22_22Sep2023_V2_MC_L1FastJet_AK4PFHLT.txt"
    wrong.write_text((FIX / MC_CFG["jec"]["L1FastJet"]).read_text())
    cfg = {**MC_CFG, "files_dir": str(tmp_path),
           "jec": {**MC_CFG["jec"], "L1FastJet": wrong.name}}
    with pytest.raises(ValueError, match="must end with"):
        JetCorrectionFactory(cfg, is_mc=True)
