# JME correction files (JEC / JER)

Drop the CMS JME POG plain-text correction files for your campaign here. They
are shipped inside the wheel (see `package-data` in `pyproject.toml`), so
HTCondor jobs need no extra file transfers. Reference them **by basename** in
the `corrections` block of your config JSON (`files_dir` may point here or be
left as the default).

Real correction files are **not** committed to git (see `.gitignore`); only
this README is tracked. Tiny synthetic fixtures used by the test suite live in
`tests/data/jme/`.

## Where to get the files

- JEC: <https://github.com/cms-jet/JECDatabase>
- JER: <https://github.com/cms-jet/JRDatabase>
- For trigger-level **scouting** jets, use the HLT AK4 PF tag your campaign
  provides (e.g. `AK4PFHLT`). The algorithm tag is taken from the filename, so
  nothing is hardcoded — just make sure it matches your ScoutingPFJet jets.

## Mandatory: file extensions

coffea picks its parser from the **second-to-last dot token**. Rename the raw
JME files so the extension is one of:

| Correction          | Required name suffix |
|---------------------|----------------------|
| All JEC levels      | `*.jec.txt`          |
| (incl. L2L3Residual)| `*.jec.txt`          |
| JES Uncertainty     | `*.junc.txt`         |
| JER PtResolution    | `*.jr.txt`           |
| JER ScaleFactor     | `*.jersf.txt`        |

A trailing `.gz` is allowed (e.g. `*.jec.txt.gz`).

## Mandatory: filename tokens

coffea classifies each correction from the filename, which must keep the JME
tokens `<campaign>_<dataera>_<datatype>_<level>_<jettype>`:

- JEC levels, Uncertainty, PtResolution: **5 or 6** underscore tokens.
- JER **ScaleFactor**: **exactly 5** underscore tokens (coffea limitation).
  Real Summer22+ SF files have 6 tokens, so you must rename them, e.g.
  `Summer22_22Sep2023_JRV1_MC_SF_AK4PFHLT.txt`
  → `Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt`.
- For **data**, all JEC level files (including L2L3Residual) must share the same
  campaign/dataera/datatype/jettype. Put the run period in `<dataera>` and keep
  them to 5 tokens, e.g. `Summer22_RunCD_DATA_L2L3Residual_AK4PFHLT.jec.txt`.

## Example layout (MC)

```
Summer22_22Sep2023_V2_MC_L1FastJet_AK4PFHLT.jec.txt
Summer22_22Sep2023_V2_MC_L2Relative_AK4PFHLT.jec.txt
Summer22_22Sep2023_V2_MC_L3Absolute_AK4PFHLT.jec.txt
Summer22_22Sep2023_V2_MC_Uncertainty_AK4PFHLT.junc.txt
Summer22_JRV1_MC_PtResolution_AK4PFHLT.jr.txt
Summer22_JRV1_MC_SF_AK4PFHLT.jersf.txt
```
