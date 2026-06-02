# How to run on LPC Condor Cluster

## 1. Activate your voms proxy
`voms-proxy-init --rfc --voms cms -valid 192:00`

## 2. Build project wheel
`pip wheel . -w .`

## 3. Run overall 
`source scripts/run_all.sh filelists-dir-you-want-to-run run3-mj-slimmer-wheel-you-made`

