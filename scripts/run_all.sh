#!/bin/bash
for i in "$1"/*; do
    filename=$(basename "$i")
    IFS='.' read -ra arrIN <<< "$filename"

    tag=${arrIN[0]}
    python scripts/submit_slimmer.py -i $i -o $3/$tag --config config/config.json --wheel $2 --logdir ${tag}_log
    condor_submit ${tag}_log/submit.sub
    sleep 2
done
