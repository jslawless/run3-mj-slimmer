# run3-mj-slimmer

Takes a ScoutingNanoAOD ROOT file and produces a smaller ROOT file containing only the branches needed for the 6-jet multijet (pair-produced RPV gluino → 3 jets) analysis.

## Output structure

### TTree `events`

| Branch | Type | Description |
|---|---|---|
| `run`, `luminosityBlock`, `event` | scalar | CMS event ID |
| `bunchCrossing`, `orbitNumber` | scalar | Bunch crossing / orbit |
| `nScoutingPFJet` | scalar `int32` | Jet multiplicity after selection |
| `HT` | scalar `float64` | Scalar pT sum of selected jets |
| `ScoutingPFJet_pt/eta/phi/m` | jagged | Standard 4-vector (m = mass) |
| `ScoutingPFJet_px/py/pz/e` | jagged | Cartesian 4-vector (computed, not stored in input) |
| `ScoutingPFJet_jetArea` | jagged | Jet area (for JEC) |
| `ScoutingPFJet_chargedHadronEnergy` | jagged | Charged hadron energy |
| `ScoutingPFJet_neutralHadronEnergy` | jagged | Neutral hadron energy |
| `ScoutingPFJet_photonEnergy` | jagged | Photon energy |
| `ScoutingPFJet_electronEnergy` | jagged | Electron energy |
| `ScoutingPFJet_muonEnergy` | jagged | Muon energy |
| `ScoutingPFJet_HFEMEnergy` | jagged | HF electromagnetic energy |
| `ScoutingPFJet_HFHadronEnergy` | jagged | HF hadronic energy |
| `ScoutingPFJet_HOEnergy` | jagged | HO energy |
| `ScoutingPFJet_chargedHadronMultiplicity` | jagged | Charged hadron count |
| `ScoutingPFJet_neutralHadronMultiplicity` | jagged | Neutral hadron count |
| `ScoutingPFJet_photonMultiplicity` | jagged | Photon count |
| `ScoutingPFJet_electronMultiplicity` | jagged | Electron count |
| `ScoutingPFJet_muonMultiplicity` | jagged | Muon count |
| `ScoutingPFJet_HFEMMultiplicity` | jagged | HF EM particle count |
| `ScoutingPFJet_HFHadronMultiplicity` | jagged | HF hadron count |
| `ScoutingRho_fixedGridRhoFastjetAll` | scalar | Pileup rho (if present) |
| `ScoutingMET_pt`, `ScoutingMET_phi` | scalar | MET (if present) |
| `nGenJet` | scalar `int32` | Generator-level ak4 jet multiplicity (MC-only) |
| `GenJet_pt/eta/phi/mass` | jagged | Generator-level ak4 jet 4-vector (MC-only) |

Branches not present in the input file are silently skipped.

When JEC/JER corrections are enabled (see below), `ScoutingPFJet_pt` and
`ScoutingPFJet_m` hold the **corrected** values and these extra per-jet branches
are added (raw values kept for provenance, systematic variations for downstream):

| Branch | Type | Description |
|---|---|---|
| `ScoutingPFJet_pt_raw`, `ScoutingPFJet_m_raw` | jagged | Uncorrected (raw) pT / mass |
| `ScoutingPFJet_pt_jesUp/Down`, `ScoutingPFJet_m_jesUp/Down` | jagged | Total JES up/down varied pT / mass |
| `ScoutingPFJet_pt_jerUp/Down`, `ScoutingPFJet_m_jerUp/Down` | jagged | JER up/down varied pT / mass (**MC only**) |

`HT`, `nScoutingPFJet` and the cutflow are computed from the **nominal corrected**
jets. Recompute per-variation HT downstream from the stored varied pT branches.

### TH1 `cutflow`

A one-dimensional histogram with four labeled bins tracking event counts through the selection:

| Bin | Label | Description |
|---|---|---|
| 0 | `All events` | Raw event count in the input file |
| 1 | `1+ jet (pT>X, \|eta\|<Y)` | Events with at least one jet passing the jet selection |
| 2 | `N_jets >= N` | Events with at least N jets after jet selection |
| 3 | `HT > X GeV` | Events passing all cuts (= entries in `events` TTree) |

### TH1 `version`

Single-bin `StrCategory` histogram whose bin label is the version string from `metadata.version` in the config file. Readable in ROOT as `h->GetXaxis()->GetBinLabel(1)`.

### TTree `meta`

Single-entry TTree storing the numeric cut parameters used:

| Branch | Description |
|---|---|
| `ht_cut` | HT threshold used |
| `jet_pt_cut` | Jet pT threshold used |
| `jet_eta_cut` | Jet \|eta\| threshold used |
| `min_jets` | Minimum jet multiplicity used |
| `corrections_enabled` | 1 if JEC/JER were applied, else 0 |
| `is_mc` | 1 if processed as MC (JEC+JER), 0 if data (JEC+residual) |

## Configuration

Cuts are defined in a JSON config file. The `version` field identifies which set of cuts was used and is stored in the output file's `meta` TTree alongside the slimmer software version.

```json
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
```

An example config is provided in `config.json`.

## JEC/JER corrections

The slimmer can apply CMS JME POG Jet Energy Corrections (JEC) and, for MC, Jet
Energy Resolution (JER) smearing to the (raw) `ScoutingPFJet` collection using
coffea's `jetmet_tools` driven by the JME `.txt` files. Corrections are applied
**before** the jet/HT selection, so cuts act on corrected jets.

1. Put the JME `.txt` files in `src/run3_mj_slimmer/data/jme/` (shipped in the
   wheel) or a local `data/jme/`. **Rename them** to the coffea extensions
   (`*.jec.txt`, `*.junc.txt`, `*.jr.txt`, `*.jersf.txt`) and mind the filename
   token rules — see `src/run3_mj_slimmer/data/jme/README.md`.
2. Add a `corrections` block to your config (see `config/config_corrections_mc.json`
   and `config/config_corrections_data.json`):

```json
"corrections": {
    "enabled": true,
    "is_mc": true,
    "algo": "AK4PFHLT",
    "era": "Summer22_22Sep2023_V2",
    "selection_mode": "nominal",
    "files_dir": "data/jme",
    "jec": {
        "L1FastJet":  "Summer22_22Sep2023_V2_MC_L1FastJet_AK4PFHLT.jec.txt",
        "L2Relative": "Summer22_22Sep2023_V2_MC_L2Relative_AK4PFHLT.jec.txt",
        "L3Absolute": "Summer22_22Sep2023_V2_MC_L3Absolute_AK4PFHLT.jec.txt"
    },
    "junc": "Summer22_22Sep2023_V2_MC_Uncertainty_AK4PFHLT.junc.txt",
    "jer": {
        "PtResolution": "Summer22_JRV1_MC_PtResolution_AK4PFHLT.jr.txt",
        "SF":           "Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt"
    }
}
```

- **MC vs data** is inferred from the presence of `GenJet` branches, or set
  explicitly with `is_mc`. MC applies L1+L2+L3 JEC and JER; data applies
  L1+L2+L3+L2L3Residual JEC and no JER. For data, `jec.L2L3Residual` may be a
  `{period: file}` map resolved per run via an `iovs` table.
- `selection_mode`: `"nominal"` (default) decides event membership from the
  central values; `"loose_or"` keeps an event/jet if it passes in nominal **or
  any** stored variation (avoids losing systematic migrations at the cut edge).
- Corrections run **on by default** when `enabled: true`. Override on the CLI
  with `--corrections` / `--no-corrections`.
- Type-1 MET propagation is **not** applied (ScoutingMET is passed through
  unchanged); the unclustered-energy inputs needed for it are not available.

## Usage

```bash
pip install -r requirements.txt
pip install .

# Output is written to ./slimmed_<input basename>
run3-mj-slimmer input.root config.json

# With JEC/JER corrections (on automatically if the config enables them)
run3-mj-slimmer input.root config/config_corrections_mc.json

# Operational overrides; force corrections off
run3-mj-slimmer input.root config.json --tree Events --chunk-size 100000 --no-corrections
```

## Options

```
positional arguments:
  input               Input ScoutingNanoAOD ROOT file
  config              JSON file containing cut (and optional corrections) config

optional arguments:
  --tree NAME         Input tree name (default: Events)
  --chunk-size N      Events per processing chunk (default: 100000)
  --output-tag TAG    Optional tag added to the output file name
  --corrections       Force-enable JEC/JER corrections
  --no-corrections    Disable JEC/JER corrections even if the config enables them
```

## Versioning

The version is set once in the config JSON under `metadata.version` and written to the `version` histogram in every output file. Bump it whenever you change the cut configuration so output files are self-describing.
