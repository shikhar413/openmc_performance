import logging
import os.path
import sys


def cmd_list(options, benchmarks):
    print("%r benchmarks:" % options.benchmarks)
    for bench in sorted(benchmarks):
        print("- %s" % bench.name)
    print()
    print("Total: %s benchmarks" % len(benchmarks))


def cmd_list_groups(manifest, *, showtags=True):
    all_benchmarks = set(manifest.benchmarks)

    groups = sorted(manifest.groups - {'all', 'default'})
    groups[0:0] = ['all', 'default']
    for group in groups:
        specs = list(manifest.resolve_group(group))
        known = set(specs) & all_benchmarks
        if not known:
            # skip empty groups
            continue

        print("%s (%s):" % (group, len(specs)))
        for spec in sorted(specs):
            print("- %s" % spec.name)
        print()

    if showtags:
        print("=============================")
        print()
        print("tags:")
        print()
        tags = sorted(manifest.tags or ())
        if not tags:
            print("(no tags)")
        else:
            for tag in tags:
                specs = list(manifest.resolve_group(tag))
                known = set(specs) & all_benchmarks
                if not known:
                    # skip empty groups
                    continue

                print("%s (%s):" % (tag, len(specs)))
                for spec in sorted(specs):
                    print("- %s" % spec.name)
                print()


def cmd_venv_create(options, root, openmc, benchmarks):
    from . import _venv
    from .venv import Requirements, VenvForBenchmarks

    if _venv.venv_exists(root):
        sys.exit(f'ERROR: the virtual environment already exists at {root}')

    # Requirements are determined based on presence of requirements.txt file in bm_* folder
    requirements = Requirements.from_benchmarks(benchmarks)
    venv = VenvForBenchmarks.ensure(
        root,
        openmc,
        inherit_environ=options.inherit_environ,
    )
    try:
        venv.install_pyperformance()
        venv.ensure_reqs(requirements)
    except _venv.RequirementsInstallationFailedError:
        sys.exit(1)
    print("The virtual environment %s has been created" % root)


def cmd_venv_recreate(options, root, openmc, benchmarks):
    from . import _venv, _utils
    from .venv import Requirements, VenvForBenchmarks

    requirements = Requirements.from_benchmarks(benchmarks)
    if _venv.venv_exists(root):
        print("The virtual environment %s already exists" % root)
        _utils.safe_rmtree(root)
        print("The old virtual environment %s has been removed" % root)
        print()
        venv = VenvForBenchmarks.ensure(
            root,
            openmc,
            inherit_environ=options.inherit_environ,
        )
        try:
            venv.install_pyperformance()
            venv.ensure_reqs(requirements)
        except _venv.RequirementsInstallationFailedError:
            sys.exit(1)
        print("The virtual environment %s has been recreated" % root)
    else:
        venv = VenvForBenchmarks.ensure(
            root,
            openmc,
            inherit_environ=options.inherit_environ,
        )
        try:
            venv.install_pyperformance()
            venv.ensure_reqs(requirements)
        except _venv.RequirementsInstallationFailedError:
            sys.exit(1)
        print("The virtual environment %s has been created" % root)


def cmd_venv_remove(options, root):
    from . import _utils

    if _utils.safe_rmtree(root):
        print("The virtual environment %s has been removed" % root)
    else:
        print("The virtual environment %s does not exist" % root)


def cmd_venv_show(options, root):
    from . import _venv

    exists = _venv.venv_exists(root)

    text = "Virtual environment path: %s" % root
    if exists:
        text += " (already created)"
    else:
        text += " (not created yet)"
    print(text)

    if not exists:
        print()
        print("Command to create it:")
        cmd = "python -m pyperformance venv create"
        if options.venv:
            cmd += " --venv=%s" % options.venv
        print(cmd)


def cmd_run(options, benchmarks):
    import pyperformance
    import json
    from .compare import display_benchmark_suite
    from .run import run_benchmarks

    logging.basicConfig(level=logging.INFO)

    print("Python benchmark suite %s" % pyperformance.__version__)
    print()

    if options.output and os.path.exists(options.output):
        print("ERROR: the output file %s already exists!" % options.output)
        sys.exit(1)

    if options.openmc:
        executable = options.openmc
        if not os.path.isabs(executable):
            print("ERROR: \"%s\" is not an absolute path" % executable)
            sys.exit(1)
    else:
        print("ERROR: openmc executable needs to be passed in through the -p flag")
        sys.exit(1)

    suite, errors = run_benchmarks(benchmarks, executable, options)

    if not suite:
        print("ERROR: No benchmark was run")
        sys.exit(1)

    if options.output:
        abs_path = os.path.abspath(options.output)
        if os.path.isdir(abs_path):
            print("ERROR: output filename cannot point to a directory")
        if not os.path.exists(os.path.dirname(abs_path)):
            os.makedirs(os.path.dirname(abs_path))
        suite_data = [s.data for s in suite]
        with open(abs_path, 'w') as f:
            f.write(json.dumps(suite_data, indent=4, default=str))
            f.write('\n')
    display_benchmark_suite(suite, title="Benchmark results summary")

    if errors:
        print("%s benchmarks failed:" % len(errors))
        for name, reason in errors:
            print("- %s (%s)" % (name, reason))
        print()
        sys.exit(1)


def cmd_compile(options):
    from .compile import parse_config, BenchmarkRevision

    conf = parse_config(options.config_file, "compile")
    if options is not None:
        if options.no_update:
            conf.update = False
        if options.no_tune:
            conf.system_tune = False
    bench = BenchmarkRevision(conf, options.revision, options.branch,
                              patch=options.patch, options=options)
    bench.main()


def cmd_compile_all(options):
    from .compile import BenchmarkAll

    bench = BenchmarkAll(options.config_file, options=options)
    bench.main()


def cmd_upload(options):
    import pyperf
    from .compile import parse_config, parse_date, BenchmarkRevision

    conf = parse_config(options.config_file, "upload")

    filename = options.json_file
    bench = pyperf.BenchmarkSuite.load(filename)
    metadata = bench.get_metadata()
    revision = metadata['commit_id']
    branch = metadata['commit_branch']
    commit_date = parse_date(metadata['commit_date'])

    bench = BenchmarkRevision(conf, revision, branch,
                              filename=filename, commit_date=commit_date,
                              setup_log=False, options=options)
    bench.upload()


def cmd_show(options):
    import pyperf
    from .compare import display_benchmark_suite

    suite = pyperf.BenchmarkSuite.load(options.filename)
    display_benchmark_suite(suite)


def cmd_compare(options):
    from .compare import compare_results, write_csv, VersionMismatchError

    try:
        results = compare_results(options)
    except VersionMismatchError as exc:
        print(f'ERROR: {exc}')
        sys.exit(1)

    if options.csv:
        write_csv(results, options.csv)
