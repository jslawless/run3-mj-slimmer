#!/usr/bin/env python3
"""submit_slimmer.py - Submit run3-mj-slimmer jobs to HTCondor.

Reads a coffea-style fileset JSON, splits files into per-job groups,
and writes condor submission files. Each job installs run3-mj-slimmer
from a pre-built wheel and runs it on its assigned files.

Build the wheel before submitting:
    pip wheel /path/to/run3-mj-slimmer -w .

Submit:
    python submit_slimmer.py \\
        -i fileset.json \\
        -o root://cmseos.fnal.gov//store/user/you/slimmed \\
        --config config.json \\
        --wheel run3_mj_slimmer-1.0.0-py3-none-any.whl

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
import socket
import argparse
import json


def configure_batch(logdir, names, transfer, outdir, cpu, queue, ram):
    return f"""\
universe                = vanilla
executable              = {logdir}/$(name).sh
arguments               = $(ClusterId)$(ProcId)
output                  = {logdir}/log_$(ClusterId)_$(name).out
error                   = {logdir}/log_$(ClusterId)_$(name).err
log                     = {logdir}/log_$(ClusterId)_$(name).log
Should_Transfer_Files   = YES
transfer_input_files    = {transfer}
output_destination      = {outdir}
transfer_output_files   = output_files/
when_to_transfer_output = ON_SUCCESS
RequestCPUs             = {cpu}
+JobFlavour             = {queue}
request_memory          = {ram}

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

## Set up Python virtual environment and install run3-mj-slimmer
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet {WHEEL}

## Run
echo
mkdir -p output_files
{RUN_COMMANDS}

echo
echo "Output destination: {OUTDIR}"

echo
echo "Ending job on " `date`
"""


class Fileset:
    def __init__(self, args):
        self.infile = args.inFile
        self.nf_per_job = args.nfPerJob
        self.outdir = args.outdir
        self.logdir = args.logdir
        self.fileset = {}
        self.jobs = []

        self._read()
        self._split()
        self._ensure_outdir()
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

    def _ensure_outdir(self):
        outdir = self.outdir
        if "root://cmseos.fnal.gov/" in outdir:
            if "cmslpc" in socket.gethostname():
                eos = outdir.split(".fnal.gov/")[1]
                path = "/eos/uscms" + eos
                if not os.path.exists(path):
                    print(f"Creating EOS directory: {eos}")
                    os.system(f"eosmkdir -p {eos}")
            else:
                print(f"\033[1;31mWarning: cannot verify output directory exists: {outdir}\033[0m")
        elif outdir and outdir != ".":
            os.makedirs(outdir, exist_ok=True)


class Batch:
    def __init__(self, jobs, args):
        self.jobs = jobs
        self.outdir = args.outdir
        self.logdir = args.logdir
        self.cpu = args.cpu
        self.queue = args.queue
        self.ram = args.memory
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
                    run_cmds.append(
                        f"run3-mj-slimmer {filepath} {config_basename}"
                        f" --tree {tree_name}"
                        f" && mv slimmed_{basename} output_files/"
                    )
                exe = EXECUTABLE_TEMPLATE.format(
                    WHEEL=wheel_basename,
                    RUN_COMMANDS="\n".join(run_cmds),
                    OUTDIR=self.outdir,
                )
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
            outdir=self.outdir,
            cpu=self.cpu,
            queue=self.queue,
            ram=self.ram,
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
    parser.add_argument("-o", "--outdir",   required=True,  help="Output directory (local or EOS xrootd URL)")
    parser.add_argument("--config",         required=True,  help="run3-mj-slimmer config JSON")
    parser.add_argument("--wheel",          required=True,  help="Pre-built run3-mj-slimmer .whl file")
    parser.add_argument("-n", "--nfPerJob", type=int, default=1, help="Files per job")
    parser.add_argument("--tree",   default="Events",   help="Fallback input tree name (overridden by fileset JSON)")
    parser.add_argument("--logdir", default="batch",    help="Directory for condor log/sh files")
    parser.add_argument("--cpu",    type=int, default=1, help="CPUs per job")
    parser.add_argument("--queue",  default="tomorrow", help="HTCondor JobFlavour")
    parser.add_argument("--memory", default="4GB",      help="Memory per job")
    parser.add_argument("--exec",   action="store_true", help="Submit jobs immediately after writing")

    args = parser.parse_args()

    fileset = Fileset(args)
    batch = Batch(fileset.jobs, args)
    batch.submit(args.exec)
