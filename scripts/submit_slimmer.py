#!/usr/bin/env python3
"""submit_slimmer.py - Submit run3-mj-slimmer jobs to HTCondor.

Reads a coffea-style fileset JSON, splits files into per-job groups,
and writes condor submission files. Each job installs run3-mj-slimmer
from a pre-built wheel and runs it on its assigned files.

Build the wheel before submitting:
    pip wheel /path/to/run3-mj-slimmer -w .

To run with JEC/JER corrections, drop the JME .txt files into
src/run3_mj_slimmer/data/jme/ BEFORE building the wheel: they are packaged into
the wheel and resolved automatically on the worker node (no extra transfers).
Each job installs the wheel with --no-deps into a venv that inherits the LCG
view's coffea/correctionlib/uproot, so no large PyPI downloads happen on the
worker (and the view's native correctionlib avoids the manylinux _core.so
`undefined symbol: __cxa_call_terminate` ABI crash).

Submit:
    python submit_slimmer.py \\
        -i fileset.json \\
        -o /store/user/you/slimmed \\
        --config config.json \\
        --wheel run3_mj_slimmer-1.0.0-py3-none-any.whl

-o / --eosoutdir is a BARE EOS path (e.g. /store/user/you/slimmed); the job
script adds the root://cmseos.fnal.gov/ redirector automatically. A full
root://host//store/... URL is also accepted - the leading redirector is
stripped so it is never doubled in the xrdcp destination.

Fileset JSON format (coffea-style):
    {
        "dataset_name": {
            "files": {
                "/path/to/file.root": "Events",
                ...
            }
        }
    }
"""

import os
import re
import socket
import argparse
import json


def configure_batch(logdir, names, transfer, eosoutdir, cpu, queue, ram, disk):
    return f"""\
universe                = vanilla
executable              = {logdir}/$(name).sh
arguments               = $(ClusterId)$(ProcId)
output                  = {logdir}/log_$(ClusterId)_$(name).out
error                   = {logdir}/log_$(ClusterId)_$(name).err
log                     = {logdir}/log_$(ClusterId)_$(name).log
Should_Transfer_Files   = YES
transfer_input_files    = {transfer}
RequestCPUs             = {cpu}
+JobFlavour             = {queue}
request_memory          = {ram}
request_disk            = {disk}

queue name from (
{names}
)
"""


EXECUTABLE_TEMPLATE = """\
#!/usr/bin/env bash
echo "Starting job on " `date`
echo "Running on: `uname -a`"
echo "System software: `cat /etc/redhat-release`"
workarea=$PWD
echo
echo "Work Area: $workarea"
ls
echo

## run3-mj-slimmer needs Python >=3.10 (the bare worker has 3.9) plus coffea,
## correctionlib, uproot, awkward - all already provided by the cvmfs LCG view,
## compiled against the view's own gcc/libstdc++. So source the view and build a
## venv that INHERITS its site-packages (--system-site-packages), then install
## ONLY our package on top with --no-deps (see below).
##
## Do NOT pip-install the deps: that pulls correctionlib's PyPI wheel, whose
## prebuilt _core.so fails to load against the LCG runtime with
## `undefined symbol: __cxa_call_terminate` - a wheel-vs-libstdc++ ABI mismatch
## that bumping the view's gcc does NOT fix. The view's own correctionlib loads
## fine (it matches its libstdc++), so we just use it. This also makes the job
## OS-agnostic: on el8 or el9 the selected view's native libs always match.
##
## Pick the view matching this node's OS and the newest gcc available for it.
LCG_BASE=/cvmfs/sft.cern.ch/lcg/views/LCG_106
osmaj=$(rpm -E %{{rhel}} 2>/dev/null || echo 9)
LCG_VIEW=$(ls "$LCG_BASE"/x86_64-el${{osmaj}}-gcc*-opt/setup.sh 2>/dev/null | sort -V | tail -1)
if [ -z "$LCG_VIEW" ] || [ ! -r "$LCG_VIEW" ]; then
  # Last resort: newest gcc for any arch this node can run.
  LCG_VIEW=$(ls "$LCG_BASE"/x86_64-el*-gcc*-opt/setup.sh 2>/dev/null | sort -V | tail -1)
fi
echo "Node OS major: $osmaj"
echo "Sourcing LCG view: $LCG_VIEW"
source "$LCG_VIEW"
echo "Base python: $(python3 --version)"

## Build a venv that inherits the view's packages; install ONLY our wheel, with
## no PyPI deps. Do NOT unset PYTHONPATH - that is how the view exposes its
## coffea/correctionlib to the venv.
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --quiet --no-deps {WHEEL}
echo "coffea: $(python3 -c 'import coffea; print(coffea.__version__)')"

## Run
echo
# mkdir -p output_files
{RUN_COMMANDS}
echo "what directory am I in?"
pwd
echo "List all root files = "
ls *.root
echo "List all files"
ls -alh
echo "*******************************************"
OUTDIR=root://cmseos.fnal.gov/{EOSOUTDIR}
echo "xrdcp output for condor to "
"""

EXECUTABLE_TEMPLATE2 ="""\
echo $OUTDIR
for FILE in *.root
do
  echo "xrdcp -f ${FILE} ${OUTDIR}/${FILE}"
  echo "${FILE}" 
  echo "${OUTDIR}"
 xrdcp -f ${FILE} ${OUTDIR}/${FILE} 2>&1
  XRDEXIT=$?
  if [[ $XRDEXIT -ne 0 ]]; then
    rm *.root ###note if you do this locally you remove possibly IMPORTANT ROOT FILES
    ### always be careful with "rm"
    echo "exit code $XRDEXIT, failure in xrdcp"
    exit $XRDEXIT
  fi
  rm ${FILE} ###note if you do this locally you remove possibly IMPORTANT ROOT FILES
    ### always be careful with "rm"
done

echo
echo "Ending job on " `date`
"""


class Fileset:
    def __init__(self, args):
        self.infile = args.inFile
        self.nf_per_job = args.nfPerJob
        self.eosoutdir = args.eosoutdir
        self.logdir = args.logdir
        self.fileset = {}
        self.jobs = []

        self._read()
        self._split()
        os.makedirs(self.logdir, exist_ok=True)

    def _read(self):
        try:
            with open(self.infile) as f:
                self.fileset = json.load(f)
        except FileNotFoundError:
            raise SystemExit(f"Fileset not found: {self.infile}")
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid JSON in {self.infile}: {e}")

    def _split(self):
        print(f"\nDatasets: {len(self.fileset)}")
        total = 0
        for k, (dataset, data) in enumerate(self.fileset.items()):
            files = list(data["files"].items())  # [(path, tree_name), ...]
            n = self.nf_per_job
            subjobs = [files[i:i + n] for i in range(0, len(files), n)]
            self.jobs.append((dataset, subjobs))
            print(f"  {k + 1}: {dataset}  →  {len(files)} files  →  {len(subjobs)} jobs")
            total += len(subjobs)
        print(f"\n  Total: {total} jobs\n")


class Batch:
    def __init__(self, jobs, args):
        self.jobs = jobs
        self.eosoutdir = args.eosoutdir
        self.logdir = args.logdir
        self.cpu = args.cpu
        self.queue = args.queue
        self.ram = args.memory
        self.disk = args.disk
        self.config = args.config
        self.wheel = args.wheel
        self.default_tree = args.tree
        self._write_jobs()
        self._write_submit()

    def _write_jobs(self):
        wheel_basename = os.path.basename(self.wheel)
        config_basename = os.path.basename(self.config)
        for dataset, subjobs in self.jobs:
            single = (len(subjobs) == 1)
            for i, files in enumerate(subjobs):
                name = dataset if single else f"{dataset}_{i}"
                run_cmds = []
                for filepath, tree in files:
                    tree_name = tree if tree else self.default_tree
                    basename = os.path.basename(filepath)
                    # Tag with the dataset name only (not the per-job index):
                    # the input basename already makes each output unique, so
                    # the output is slimmed_<dataset>_<input basename>.
                    run_cmds.append(
                        f"run3-mj-slimmer {filepath} {config_basename}"
                        f" --tree {tree_name}"
                        f" --output-tag {dataset}"
                    )
                exe = EXECUTABLE_TEMPLATE.format(
                    WHEEL=wheel_basename,
                    RUN_COMMANDS="\n".join(run_cmds),
                    EOSOUTDIR=self.eosoutdir,
                )
                exe = exe + EXECUTABLE_TEMPLATE2
                path = f"{self.logdir}/{name}.sh"
                with open(path, "w") as f:
                    f.write(exe)
                os.chmod(path, 0o755)

    def _write_submit(self):
        names = ""
        for dataset, subjobs in self.jobs:
            single = (len(subjobs) == 1)
            for i in range(len(subjobs)):
                name = dataset if single else f"{dataset}_{i}"
                names += f"\t{name}\n"

        transfer = f"{self.wheel},{self.config}"
        config = configure_batch(
            logdir=self.logdir,
            names=names.strip(),
            transfer=transfer,
            eosoutdir=self.eosoutdir,
            cpu=self.cpu,
            queue=self.queue,
            ram=self.ram,
            disk=self.disk,
        )
        with open(f"{self.logdir}/submit.sub", "w") as f:
            f.write(config)

    def submit(self, execute):
        if execute:
            os.system(f"condor_submit {self.logdir}/submit.sub")
            print()
            print("Your jobs are here:")
            os.system("condor_q")
            print()
        else:
            print()
            print(f"To submit:       condor_submit {self.logdir}/submit.sub")
            print("To check status: condor_q")
            print("To see jobs:     condor_q -nobatch")
            print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Submit run3-mj-slimmer jobs to HTCondor.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--inFile",   required=True,  help="Coffea-style fileset JSON")
    parser.add_argument("-o", "--eosoutdir",   required=True,  help="EOS output dir as a bare /store/... path (cmseos redirector added automatically)")
    parser.add_argument("--config",         required=True,  help="run3-mj-slimmer config JSON")
    parser.add_argument("--wheel",          required=True,  help="Pre-built run3-mj-slimmer .whl file")
    parser.add_argument("-n", "--nfPerJob", type=int, default=1, help="Files per job")
    parser.add_argument("--tree",   default="Events",   help="Fallback input tree name (overridden by fileset JSON)")
    parser.add_argument("--logdir", default="batch",    help="Directory for condor log/sh files")
    parser.add_argument("--cpu",    type=int, default=1, help="CPUs per job")
    parser.add_argument("--queue",  default="tomorrow", help="HTCondor JobFlavour")
    parser.add_argument("--memory", default="4GB",      help="Memory per job")
    parser.add_argument("--disk",   default="4GB",      help="Disk per job (venv inherits LCG packages; mostly root-file I/O headroom)")
    parser.add_argument("--exec",   action="store_true", help="Submit jobs immediately after writing")

    args = parser.parse_args()

    # The job template (EXECUTABLE_TEMPLATE) already prepends the cmseos.fnal.gov
    # redirector to the output dir, so accept a bare /store/... path. If a full
    # root://host//store/... URL is given, strip the leading redirector to avoid
    # a doubled prefix in the xrdcp destination.
    args.eosoutdir = re.sub(r"^root://[^/]+/+", "/", args.eosoutdir)

    fileset = Fileset(args)
    batch = Batch(fileset.jobs, args)
    batch.submit(args.exec)
