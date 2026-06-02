#!/bin/bash
for i in "$1"/*; do
    filename=$(basename "$i")
    IFS='.' read -ra arrIN <<< "$filename"

    python scripts/submit_slimmer.py -i filelists/$i -o $3/${arrIN[0]} --config config/config.json --wheel $2 --logdir ${i}_log
    condor_submit ${i}_log/submit.sub
    sleep 2
done
