from collections import namedtuple
import hashlib
import json
import sys
import time
import traceback
from subprocess import TimeoutExpired

import pyperformance
from . import _utils, _openmc, _openmcinfo
from .venv import Requirements, VenvForBenchmarks, REQUIREMENTS_FILE
from . import _venv


class BenchmarkException(Exception):
    pass


class RunID(namedtuple('RunID', 'python compat bench timestamp')):

    def __new__(cls, python, compat, bench, timestamp):
        self = super().__new__(
            cls,
            python,
            compat,
            bench or None,
            int(timestamp) if timestamp else None,
        )
        return self

    def __str__(self):
        if not self.timestamp:
            return self.name
        return f'{self.name}-{self.timestamp}'

    @property
    def name(self):
        try:
            return self._name
        except AttributeError:
            name = f'{self.python}-compat-{self.compat}'
            if self.bench:
                name = f'{name}-bm-{self.bench.name}'
            self._name = name
            return self._name


def get_run_id(openmc, bench=None):
    openmc_id = _openmc.get_id(openmc)
    compat_id = get_compatibility_id(bench)
    ts = time.time()
    return RunID(openmc_id, compat_id, bench, ts)


def run_benchmarks(should_run, openmc, options):
    to_run = sorted(should_run)

    info, _ = _openmcinfo.get_version_info(openmc)
    runid = get_run_id(info)

    root_dir = options.venv if options.venv else _venv.get_venv_root(openmc=info)
    requirements = Requirements.from_benchmarks(should_run)
    common = VenvForBenchmarks.ensure(
        root_dir,
        openmc,
        upgrade='oncreate',
        inherit_environ=options.inherit_environ,
    )
    try:
        common.ensure_pyperformance()
        common.ensure_reqs(requirements)
    except _venv.RequirementsInstallationFailedError:
        sys.exit(1)

    benchmarks = {}
    venvs = set()
    for i, bench in enumerate(to_run):
        bench_runid = runid._replace(bench=bench)
        assert bench_runid.name, (bench, bench_runid)
        name = bench_runid.name
        print()
        print('='*50)
        print(f'(checking common venv has dependencies for benchmark {bench.name})')
        # Check that common venv has dependencies for bench.
        try:
            common.ensure_reqs(bench)
        except _venv.RequirementsInstallationFailedError:
            print(f'common venv is missing requirements for benchmark {bench.name}')
            benchmarks[bench] = (None, bench_runid)
        else:
            benchmarks[bench] = (common, bench_runid)
    print()

    suite = []
    run_count = str(len(to_run))
    errors = []

    pyperf_opts = get_pyperf_opts(options)

    for index, bench in enumerate(to_run):
        name = bench.name
        print("[%s/%s] %s..." %
              (str(index + 1).rjust(len(run_count)), run_count, name))
        sys.stdout.flush()

        bench_venv, bench_runid = benchmarks.get(bench)
        if bench_venv is None:
            print("ERROR: Benchmark %s failed: could not install requirements" % name)
            errors.append((name, "Install requirements error"))
            continue
        try:
            result = bench.run(
                bench_venv.python,
                openmc,
                bench_runid,
                pyperf_opts,
                venv=bench_venv,
                verbose=options.verbose,
                timeout=options.timeout,
                n_trials=options.n_trials,
                branch=options.branch,
                project=options.project
            )
        except TimeoutExpired as exc:
            print("ERROR: Benchmark %s timed out" % name)
            traceback.print_exc()
            errors.append((name, exc))
        except RuntimeError as exc:
            print("ERROR: Benchmark %s failed: %s" % (name, exc))
            traceback.print_exc()
            errors.append((name, exc))
        except Exception as exc:
            print("ERROR: Benchmark %s failed: %s" % (name, exc))
            traceback.print_exc()
            errors.append((name, exc))
        else:
            suite.append(result)

    print()

    return (suite, errors)


# Utility functions

def get_compatibility_id(bench=None):
    # XXX Do not include the pyperformance reqs if a benchmark was provided?
    reqs = sorted(_utils.iter_clean_lines(REQUIREMENTS_FILE))
    if bench:
        lockfile = bench.requirements_lockfile
        if lockfile and os.path.exists(lockfile):
            reqs += sorted(_utils.iter_clean_lines(lockfile))

    data = [
        # XXX Favor pyperf.__version__ instead?
        pyperformance.__version__,
        '\n'.join(reqs),
    ]

    h = hashlib.sha256()
    for value in data:
        h.update(value.encode('utf-8'))
    compat_id = h.hexdigest()
    # XXX Return the whole string?
    compat_id = compat_id[:12]

    return compat_id


def get_pyperf_opts(options):
    opts = []

    if options.verbose:
        opts.append('--verbose')

    if options.affinity:
        opts.append('--affinity=%s' % options.affinity)
    if options.track_memory:
        opts.append('--track-memory')
    if options.inherit_environ:
        opts.append('--inherit-environ=%s' % ','.join(options.inherit_environ))

    return opts
