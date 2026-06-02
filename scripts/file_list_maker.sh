#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: $0 <eos_directory>"
    echo "Example: $0 /store/user/jodervan/ScoutNano_QCD4Jets_Run3Winter24/QCD-4Jets_HT-70to100_TuneCP5_13p6TeV_madgraphMLM-pythia8/ScoutNano_QCD4Jets_HT70to100_withGen/260527_001120/0000"
    exit 1
fi

DIR="$1"

# Remove trailing slash if present
DIR="${DIR%/}"

# Build output filename from the top-level dataset directory (first component after /store/user/<user>/<project>/)
# e.g. QCD-4Jets_HT-70to100_TuneCP5_13p6TeV_madgraphMLM-pythia8 -> QCD-4Jets_HT-70to100.txt
OUTNAME=$(echo "$DIR" | awk -F'/' '{for(i=1;i<=NF;i++) if($i ~ /^QCD|^TTto|^Wto|^Zto|^DYto/) {print $i; exit}}')

# Fallback: use the basename of the directory if no known pattern matched
if [ -z "$OUTNAME" ]; then
    OUTNAME=$(basename "$DIR")
fi

OUTFILE="${OUTNAME}.txt"

echo "Listing: $DIR"
echo "Output:  $OUTFILE"

eosls "$DIR" | grep '\.root$' | sed "s|^|${DIR}/|" > "$OUTFILE"

echo "Done. $(wc -l < "$OUTFILE") .root files written to $OUTFILE"

