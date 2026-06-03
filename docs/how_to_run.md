# How to run on LPC Condor Cluster

## 1. Activate your voms proxy
`voms-proxy-init --rfc --voms cms -valid 192:00`

## 2. Build project wheel
`pip wheel . -w .`

To run with JEC/JER corrections, drop the (renamed) JME `.txt` files into
`src/run3_mj_slimmer/data/jme/` **before** building the wheel — they are packaged
into the wheel and found automatically on the worker node, so no extra condor
transfers are needed. See `src/run3_mj_slimmer/data/jme/README.md` for the
required file extensions and filename token rules, and use a config with a
`corrections` block (e.g. `config/config_corrections_mc.json`).

Note: each job `pip install`s the wheel, which pulls in coffea (large); the
`submit_slimmer.py` default `request_disk` is raised accordingly. Type-1 MET
propagation is not applied — `ScoutingMET` is passed through unchanged.

## 3. Run overall 
`source scripts/run_all.sh filelists-dir-you-want-to-run run3-mj-slimmer-wheel-you-made`

