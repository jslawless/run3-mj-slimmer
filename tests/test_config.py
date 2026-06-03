"""Tests for the corrections config validation in load_config()."""

import json

import pytest

from run3_mj_slimmer.slim import load_config

BASE = {
    "metadata": {"version": "v1"},
    "cuts": {"ht_cut": 400.0, "jet_pt_cut": 20.0, "jet_eta_cut": 2.4, "min_jets": 6},
}


def _write(tmp_path, cfg):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg))
    return str(p)


def test_no_corrections_block_is_valid(tmp_path):
    cfg = load_config(_write(tmp_path, BASE))
    assert "corrections" not in cfg


def test_disabled_corrections_skips_deep_validation(tmp_path):
    cfg = dict(BASE, corrections={"enabled": False})
    out = load_config(_write(tmp_path, cfg))
    assert out["corrections"]["enabled"] is False


def test_enabled_requires_jec_levels(tmp_path):
    cfg = dict(BASE, corrections={"enabled": True, "jec": {"L1FastJet": "a.jec.txt"}})
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, cfg))


def test_enabled_requires_junc(tmp_path):
    cfg = dict(BASE, corrections={
        "enabled": True,
        "jec": {"L1FastJet": "a", "L2Relative": "b", "L3Absolute": "c"},
    })
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, cfg))


def test_mc_requires_jer(tmp_path):
    cfg = dict(BASE, corrections={
        "enabled": True, "is_mc": True, "junc": "u",
        "jec": {"L1FastJet": "a", "L2Relative": "b", "L3Absolute": "c"},
    })
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, cfg))


def test_data_requires_residual(tmp_path):
    cfg = dict(BASE, corrections={
        "enabled": True, "is_mc": False, "junc": "u",
        "jec": {"L1FastJet": "a", "L2Relative": "b", "L3Absolute": "c"},
    })
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, cfg))


def test_bad_selection_mode_rejected(tmp_path):
    cfg = dict(BASE, corrections={
        "enabled": True, "selection_mode": "bogus", "junc": "u",
        "jec": {"L1FastJet": "a", "L2Relative": "b", "L3Absolute": "c"},
    })
    with pytest.raises(SystemExit):
        load_config(_write(tmp_path, cfg))


def test_valid_mc_block_accepted(tmp_path):
    cfg = dict(BASE, corrections={
        "enabled": True, "is_mc": True, "selection_mode": "loose_or",
        "jec": {"L1FastJet": "a", "L2Relative": "b", "L3Absolute": "c"},
        "junc": "u", "jer": {"PtResolution": "r", "SF": "s"},
    })
    out = load_config(_write(tmp_path, cfg))
    assert out["corrections"]["selection_mode"] == "loose_or"
