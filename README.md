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

Branches not present in the input file are silently skipped.

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

## Usage

```bash
pip install -r requirements.txt

python slim.py input.root output.root config.json

# Override the input tree name or chunk size (operational args only)
python slim.py input.root output.root config.json --tree Events --chunk-size 100000
```

## Options

```
positional arguments:
  input               Input ScoutingNanoAOD ROOT file
  output              Output slimmed ROOT file
  config              JSON file containing cut configuration

optional arguments:
  --tree NAME         Input tree name (default: Events)
  --chunk-size N      Events per processing chunk (default: 100000)
```

## Versioning

The version is set once in the config JSON under `metadata.version` and written to the `version` histogram in every output file. Bump it whenever you change the cut configuration so output files are self-describing.
