#!/bin/bash
# Usage: bash scripts/run_all.sh <filelists-dir> <wheel> <eos-outdir> [config]
# The config defaults to config/config.json (NO corrections). For JEC/JER on MC
# pass config/config_corrections_mc.json (data: config/config_corrections_data.json).
CONFIG="${4:-config/config.json}"
for i in "$1"/*; do
    filename=$(basename "$i")
    IFS='.' read -ra arrIN <<< "$filename"

    tag=${arrIN[0]}
    python scripts/submit_slimmer.py -i $i -o $3/$tag --config "$CONFIG" --wheel $2 --logdir ${tag}_log
    condor_submit ${tag}_log/submit.sub
    sleep 2
done
