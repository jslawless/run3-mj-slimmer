#!/bin/bash

for i in $(ls $1); do
	python scripts/submit_slimmer.py -i filelists/$i -o /store/group/lpcmultijets/johnny/slimmed_qcd_small  --config config/config.json --wheel $2 --logdir ${i}_log
	condor_submit ${i}_log/submit.sub
	sleep 2
done
