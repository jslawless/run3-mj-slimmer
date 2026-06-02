#!/bin/bash

for i in $(ls filelists); do
	python scripts/submit_slimmer.py -i filelists/$i -o . --config config/config.json --wheel run3_mj_slimmer-1.0.0-py3-none-any.whl --logdir ${i}_log
	condor_submit ${i}_log/submit.sub
	sleep 2
done
