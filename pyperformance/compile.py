import configparser
import datetime
import errno
import json
import logging
import math
import os
import os.path
import re
import shlex
import statistics
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from base64 import b64encode

import pyperformance
from pyperformance import _utils, _pip


DEFAULT_BRANCH = 'develop'
DEFAULT_PROJECT = 'OpenMC'
LOG_FORMAT = '%(asctime)-15s: %(message)s'

EXIT_ALREADY_EXIST = 10
EXIT_COMPILE_ERROR = 11
EXIT_VENV_ERROR = 11
EXIT_BENCH_ERROR = 12


def parse_date(text):
    def replace_timezone(regs):
        text = regs.group(0)
        return text[:2] + text[3:]

    # replace '+01:00' with '+0100'
    text2 = re.sub(r'[0-9]{2}:[0-9]{2}$', replace_timezone, text)

    # ISO 8601 with timezone: '2017-03-30T19:12:18+00:00'
    return datetime.datetime.strptime(text2, "%Y-%m-%dT%H:%M:%S%z")


class Task(object):
    def __init__(self, app, cwd):
        self.app = app
        self.cwd = cwd

    def get_output_nocheck(self, *cmd):
        return self.app.get_output_nocheck(*cmd, cwd=self.cwd)

    def get_output(self, *cmd):
        return self.app.get_output(*cmd, cwd=self.cwd)

    def run_nocheck(self, *cmd, **kw):
        return self.app.run_nocheck(*cmd, cwd=self.cwd, **kw)

    def run(self, *cmd, **kw):
        self.app.run(*cmd, cwd=self.cwd, **kw)


class Repository(Task):
    def __init__(self, app, path):
        super().__init__(app, path)
        self.logger = app.logger
        self.app = app
        self.conf = app.conf

    def fetch(self):
        self.run('git', 'fetch')

    def parse_revision(self, revision):
        branch_rev = '%s/%s' % (self.conf.git_remote, revision)

        exitcode, stdout = self.get_output_nocheck('git', 'rev-parse',
                                                   '--verify', branch_rev)
        if not exitcode:
            return (True, branch_rev, stdout)

        exitcode, stdout = self.get_output_nocheck('git', 'rev-parse',
                                                   '--verify', revision)
        if not exitcode and stdout.startswith(revision):
            revision = stdout
            return (False, revision, revision)

        self.logger.error("ERROR: unable to parse revision %r" % (revision,))
        sys.exit(1)

    def checkout(self, revision):
        # remove all untracked files
        self.run('git', 'clean', '-fdx')

        # checkout to requested revision
        self.run('git', 'reset', '--hard', 'HEAD')
        self.run('git', 'checkout', revision)

        # remove all untracked files
        self.run('git', 'clean', '-fdx')

        # update submodules if necessary
        self.run('git', 'submodule', 'update', '--init')

    def get_revision_info(self, revision):
        cmd = ['git', 'show', '-s', '--pretty=format:%H|%ci', '%s^!' % revision]
        stdout = self.get_output(*cmd)
        node, date = stdout.split('|')
        date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S %z')
        # convert local date to UTC
        date = (date - date.utcoffset()).replace(tzinfo=datetime.timezone.utc)
        return (node, date)


class Application(object):
    def __init__(self, conf, options):
        self.conf = conf
        self.options = options
        logging.basicConfig(format=LOG_FORMAT)
        self.logger = logging.getLogger()
        self.log_filename = None

    def setup_log(self, prefix):
        prefix = re.sub('[^A-Za-z0-9_-]+', '_', prefix)
        prefix = re.sub('_+', '_', prefix)

        date = datetime.datetime.now()
        date = date.strftime('%Y-%m-%d_%H-%M-%S.log')
        filename = '%s-%s' % (prefix, date)
        self.log_filename = os.path.join(self.conf.log_dir, filename)

        log = self.log_filename
        if os.path.exists(log):
            self.logger.error("ERROR: Log file %s already exists" % log)
            sys.exit(1)
        self.safe_makedirs(os.path.dirname(log))

        handler = logging.FileHandler(log)
        formatter = logging.Formatter(LOG_FORMAT)
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def create_subprocess(self, cmd, **kwargs):
        self.logger.error("+ %s" % ' '.join(map(shlex.quote, cmd)))
        return subprocess.Popen(cmd, **kwargs)

    def run_nocheck(self, *cmd, stdin_filename=None, **kwargs):
        if stdin_filename:
            stdin_file = open(stdin_filename, "rb", 0)
            kwargs['stdin'] = stdin_file.fileno()
        else:
            stdin_file = None
        log_stdout = kwargs.pop('log_stdout', True)

        if log_stdout:
            kwargs['stdout'] = subprocess.PIPE
            kwargs['stderr'] = subprocess.STDOUT
            kwargs['universal_newlines'] = True

        try:
            proc = self.create_subprocess(cmd, **kwargs)

            # FIXME: support Python 2?
            with proc:
                if log_stdout:
                    for line in proc.stdout:
                        line = line.rstrip()
                        self.logger.error(line)
                exitcode = proc.wait()
        finally:
            if stdin_file is not None:
                stdin_file.close()

        if exitcode:
            cmd_str = ' '.join(map(shlex.quote, cmd))
            self.logger.error("Command %s failed with exit code %s"
                              % (cmd_str, exitcode))

        return exitcode

    def run(self, *cmd, **kw):
        exitcode = self.run_nocheck(*cmd, **kw)
        if exitcode:
            sys.exit(exitcode)

    def get_output_nocheck(self, *cmd, **kwargs):
        proc = self.create_subprocess(cmd,
                                      stdout=subprocess.PIPE,
                                      universal_newlines=True,
                                      **kwargs)
        # FIXME: support Python 2?
        with proc:
            stdout = proc.communicate()[0]

        stdout = stdout.rstrip()

        exitcode = proc.wait()
        if exitcode:
            cmd_str = ' '.join(map(shlex.quote, cmd))
            self.logger.error("Command %s failed with exit code %s"
                              % (cmd_str, exitcode))

        return (exitcode, stdout)

    def get_output(self, *cmd, **kwargs):
        exitcode, stdout = self.get_output_nocheck(*cmd, **kwargs)
        if exitcode:
            for line in stdout.splitlines():
                self.logger.error(line)
            sys.exit(exitcode)

        return stdout

    def safe_makedirs(self, directory):
        try:
            os.makedirs(directory)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise


def resolve_openmc(prefix, builddir):
    program_ext = ''

    if prefix:
        program = os.path.join(prefix, "bin", "openmc" + program_ext)
        exists = os.path.exists(program)
    else:
        assert builddir
        program = os.path.join(builddir, "openmc" + program_ext)
        exists = os.path.exists(program)
    return program, exists


class OpenMC(Task):
    def __init__(self, app, conf):
        super().__init__(app, conf.build_dir)
        self.app = app
        self.branch = app.branch
        self.conf = conf
        self.logger = app.logger
        self.program = None
        self.version = None
        self.commit_hash = None

    def patch(self, filename):
        if not filename:
            return

        self.logger.error('Apply patch %s in %s (revision %s)'
                          % (filename, self.conf.repo_dir, self.app.revision))
        self.app.run('patch', '-p1',
                     cwd=self.conf.repo_dir,
                     stdin_filename=filename)

    def compile(self):
        if self.conf.compile:
            build_dir = self.conf.build_dir

            _utils.safe_rmtree(build_dir)
            self.app.safe_makedirs(build_dir)

            config_args = ['-B', build_dir]
            # TODO-SK add cmake options for other build types, and update config options
            configure = 'cmake'
            # Assumes CMakeLists.txt file is one level above the build directory
            config_args.append(os.path.join(build_dir, '..'))
            self.run(configure, *config_args)

            argv = ['make', '-C', build_dir]
            if self.conf.jobs:
                argv.append('-j%d' % self.conf.jobs)
            self.run(*argv)

    def install_openmc(self):
        if self.conf.install:
            program, _ = resolve_openmc(self.conf.prefix, self.conf.build_dir)
            _utils.safe_rmtree(self.conf.prefix)
            self.app.safe_makedirs(self.conf.prefix)
            self.run('make', 'install')
        else:
            program, _ = resolve_openmc(self.conf.build_dir, self.conf.build_dir)
        # else don't install: run openmc from the compilation directory
        self.program = program

    def get_version(self):
        # Dump the OpenMC version
        self.logger.error("Installed OpenMC version:")
        self.run(self.program, '--version')

        # Get the OpenMC version
        stdout = self.get_output(self.program, '--version')
        stdout_split = stdout.split('\n')
        self.version = stdout_split[0].split()[2]
        self.commit_hash = stdout_split[1].split()[2]
        self.logger.error("OpenMC version: %s, commit hash: %s" % (self.version, self.commit_hash))

    def get_package_only_flags(self):
        arguments = []
        extra_paths = []

        if extra_paths:
            # Flags are one CLI arg each and do not need quotes.
            ps = ['-I%s/include' % p for p in extra_paths]
            arguments.append('CFLAGS=%s' % ' '.join(ps))
            ps = ['-L%s/lib' % p for p in extra_paths]
            arguments.append('LDFLAGS=%s' % ' '.join(ps))
        return arguments

    def get_package_prefix(self, name):
        if sys.platform == 'darwin':
            cmd = ['brew', '--prefix', name]
        else:
            self.logger.error("ERROR: package-only libraries"
                              " are not supported on %s" % sys.platform)
            sys.exit(1)

        stdout = self.get_output(*cmd)
        self.logger.error("Package prefix for %s is '%s'" % (name, stdout))
        return stdout

    def download(self, url, filename):
        self.logger.error("Download %s into %s" % (url, filename))
        _utils.download(url, filename)

    def compile_install(self):
        self.compile()
        self.install_openmc()
        self.get_version()


class BenchmarkRevision(Application):

    _dryrun = False

    def __init__(self, conf, revision, branch=None, patch=None,
                 setup_log=True, filename=None, commit_date=None,
                 options=None):
        super().__init__(conf, options)
        self.patch = patch
        self.exitcode = 0
        self.uploaded = False

        if setup_log:
            if branch:
                prefix = 'compile-%s-%s' % (branch, revision)
            else:
                prefix = 'compile-%s' % revision
            self.setup_log(prefix)

        if filename is None:
            self.repository = Repository(self, conf.repo_dir)
            self.init_revision(revision, branch)
        else:
            # path used by cmd_upload()
            self.repository = None
            self.filename = filename
            self.revision = revision
            self.branch = branch
            self.commit_date = commit_date
            self.logger.error("Commit: branch=%s, revision=%s"
                              % (self.branch, self.revision))

        self.upload_filename = os.path.join(self.conf.uploaded_json_dir,
                                            os.path.basename(self.filename))

    def init_revision(self, revision, branch=None):
        if self.conf.update:
            self.repository.fetch()

        if branch:
            is_branch, rev_name, full_revision = self.repository.parse_revision(branch)
            if not is_branch:
                self.logger.error("ERROR: %r is not a Git branch" % self.branch)
                sys.exit(1)
            self.branch = branch
        else:
            self.branch = None

        is_branch, rev_name, full_revision = self.repository.parse_revision(revision)
        if is_branch:
            if self.branch and revision != self.branch:
                raise ValueError("inconsistenct branches: "
                                 "revision=%r, branch=%r"
                                 % (revision, branch))
            self.branch = revision
        elif not self.branch:
            self.branch = DEFAULT_BRANCH

        self.revision, date = self.repository.get_revision_info(rev_name)
        self.logger.error("Commit: branch=%s, revision=%s, date=%s"
                          % (self.branch, self.revision, date))
        self.commit_date = date

        date = date.strftime('%Y-%m-%d_%H-%M')

        filename = '%s-%s-%s' % (date, self.branch, self.revision[:12])
        if self.patch:
            patch = os.path.basename(self.patch)
            patch = os.path.splitext(patch)[0]
            filename = "%s-patch-%s" % (filename, patch)
        filename = filename + ".json"
        if self.patch:
            self.filename = os.path.join(self.conf.json_patch_dir, filename)
        else:
            self.filename = os.path.join(self.conf.json_dir, filename)

    def compile_install(self):
        if self.conf.compile:
            self.repository.checkout(self.revision)

            # First: remove everything
            _utils.safe_rmtree(self.conf.build_dir)

            self.openmc.patch(self.patch)
        self.openmc.compile_install()

    def create_venv(self):
        from .run import get_run_id
        from . import _openmcinfo
        # Create venv
        openmc = self.openmc.program
        if self._dryrun:
            program, exists = resolve_openmc(
                self.conf.build_dir,
                self.conf.build_dir,
            )
            if not openmc or not exists:
                sys.exit(EXIT_VENV_ERROR)
        python = sys.executable
        runid = get_run_id(_openmcinfo.get_version_info(openmc)[0])
        venv_path = os.path.join(self.conf.venv_dir, runid.name)
        cmd = [python, '-u', '-m', 'pyperformance', 'venv', 'recreate',
               '--venv', venv_path,
               #'--benchmarks', '<NONE>',
               '-p', openmc
               ]
        if self.options.inherit_environ:
            cmd.append('--inherit-environ=%s' % ','.join(self.options.inherit_environ))
        exitcode = self.run_nocheck(*cmd)
        if exitcode:
            sys.exit(EXIT_VENV_ERROR)
        binname = 'bin'
        base = os.path.basename(python)
        return os.path.join(venv_path, binname, base)

    def run_benchmark(self, python=None):
        self.safe_makedirs(os.path.dirname(self.filename))
        if os.path.exists(self.filename):
            _utils.safe_rmfile(self.filename)
        venv_path = os.path.abspath(os.path.join(os.path.dirname(self.python), '..'))

        cmd = [self.python, '-u',
               '-m', 'pyperformance',
               'run',
               '-p', self.openmc.program,
               '--output', self.filename,
               '--venv', venv_path,
               '--branch', self.branch]
        if self.conf.verbose:
            cmd.append('--verbose')
        if self.options.inherit_environ:
            cmd.append('--inherit-environ=%s' % ','.join(self.options.inherit_environ))
        if self.conf.manifest:
            cmd.extend(('--manifest', self.conf.manifest))
        if self.conf.benchmarks:
            cmd.append('--benchmarks=%s' % self.conf.benchmarks)
        if self.conf.timeout:
            cmd.append(('--timeout', str(self.conf.timeout)))
        if self.conf.n_trials:
            cmd.extend(('--n-trials', self.conf.n_trials))
        if self.conf.project:
            cmd.extend(('--project', self.conf.project))
        exitcode = self.run_nocheck(*cmd)

        return bool(exitcode)

    def clean_venv(self):
        venv_path = os.path.abspath(os.path.join(os.path.dirname(self.python), '..'))
        cmd = [sys.executable, '-m', 'pyperformance', 'venv', 'remove',
               '-p', self.openmc.program, '--venv', venv_path]
        exitcode = self.run_nocheck(*cmd)
        return bool(exitcode)

    def upload(self, json_data):
        if self.uploaded:
            raise Exception("already uploaded")

        if self.filename == self.upload_filename:
            self.logger.error("ERROR: %s was already uploaded!"
                              % self.filename)
            sys.exit(1)

        if os.path.exists(self.upload_filename):
            self.logger.error("ERROR: cannot upload, %s file ready exists!"
                              % self.upload_filename)
            sys.exit(1)

        self.safe_makedirs(self.conf.uploaded_json_dir)

        data = dict(json=json.dumps(json_data))

        url = self.conf.url
        if not url.endswith('/'):
            url += '/'
        url += 'result/add/json/'
        self.logger.error("Upload %s benchmarks to %s" % (len(json_data), url))

        params = urlencode(data).encode('utf-8')
        try:
            p = self.conf.authentication.encode('ascii')
            request = Request(url)
            request.add_header('Authorization', 'Basic %s' % b64encode(p).decode('ascii'))
            response = urlopen(request, params)
            body = response.read()
            response.close()
        except HTTPError as err:
            self.logger.error("HTTP Error: %s" % err)
            errmsg = err.read().decode('utf8')
            self.logger.error(errmsg)
            err.close()
            return

        self.logger.error('Response: "%s"' % body.decode('utf-8'))

        self.logger.error("Move %s to %s"
                          % (self.filename, self.upload_filename))
        os.rename(self.filename, self.upload_filename)

        self.uploaded = True

    def prepare(self):
        self.logger.error("Compile and benchmarks Python rev %s (branch %s)"
                          % (self.revision, self.branch))
        self.logger.error('')

        if os.path.exists(self.filename):
            filename = self.filename
        elif self.conf.upload and os.path.exists(self.upload_filename):
            filename = self.upload_filename
        else:
            filename = False
        if filename:
            # Benchmark already uploaded
            self.logger.error("JSON file %s already exists: do nothing"
                              % filename)

            # Remove the log file
            if self.log_filename:
                self.logger.error("Remove log file %s" % self.log_filename)
                del self.logger.handlers[:]
                os.unlink(self.log_filename)

            sys.exit(EXIT_ALREADY_EXIST)

        if self.log_filename:
            self.logger.error("Write logs into %s" % self.log_filename)

        if self.patch and self.conf.upload:
            self.logger.error("Disable upload on patched Python")
            self.conf.upload = False

    def compile_bench(self):
        self.openmc = OpenMC(self, self.conf)

        if not self._dryrun:
            try:
                self.compile_install()
            except SystemExit:
                sys.exit(EXIT_COMPILE_ERROR)

        if self.conf.venv_dir:
            venv_path = self.create_venv()
        else:
            venv_path = None
        self.python = venv_path

        return False

    def main(self):
        self.start_compile = time.monotonic()

        self.prepare()
        failed = self.compile_bench()

        self.end_compile = time.monotonic()
        dt_compile = self.end_compile - self.start_compile
        dt_compile = datetime.timedelta(seconds=dt_compile)
        self.logger.error("Compilation completed in %s" % dt_compile)

        if not failed:
            self.start_benchmark = time.monotonic()
            failed = self.run_benchmark()
            self.end_benchmark = time.monotonic()
            dt_benchmark = self.end_benchmark - self.start_benchmark
            dt_benchmark = datetime.timedelta(seconds=dt_benchmark)
            self.logger.error("Benchmarks completed in %s" % dt_benchmark)

        if not failed and self.conf.upload:
            with open(self.filename) as f:
                json_data = json.load(f)
            self.upload(json_data)

        if self.uploaded:
            self.logger.error("Benchmark results uploaded and written into %s"
                              % self.upload_filename)
        elif failed:
            self.logger.error("Benchmark failed but results written into %s"
                              % self.filename)
        else:
            self.logger.error("Benchmark result written into %s"
                              % self.filename)

        if self.conf.clean_venv:
            failed = self.clean_venv()

        if failed:
            sys.exit(EXIT_BENCH_ERROR)


class Configuration:
    pass


def parse_config(filename, command):
    parse_compile = False
    parse_compile_all = False
    if command == 'compile_all':
        parse_compile = True
        parse_compile_all = True
    elif command == 'compile':
        parse_compile = True
    else:
        assert command == 'upload'

    conf = Configuration()
    cfgobj = configparser.ConfigParser()
    cfgobj.read(filename)

    def getstr(section, key, default=None):
        try:
            sectionobj = cfgobj[section]
            value = sectionobj[key]
        except KeyError:
            if default is None:
                raise
            return default

        # strip comments
        value = value.partition('#')[0]
        # strip spaces
        return value.strip()

    def getfile(section, key, default=None):
        value = getstr(section, key, default)
        if not value:
            return value
        value = os.path.expanduser(value)
        return value

    def getboolean(section, key, default):
        try:
            sectionobj = cfgobj[section]
            return sectionobj.getboolean(key, default)
        except KeyError:
            return default

    def getint(section, key, default=None):
        return int(getstr(section, key, default))

    def getfloat(section, key, default=None):
        return float(getstr(section, key, default))

    # [config]
    conf.json_dir = getfile('config', 'json_dir')
    conf.json_patch_dir = os.path.join(conf.json_dir, 'patch')
    conf.uploaded_json_dir = os.path.join(conf.json_dir, 'uploaded')

    if parse_compile:
        # [scm]
        conf.repo_dir = getfile('scm', 'repo_dir')
        conf.update = getboolean('scm', 'update', True)
        conf.git_remote = getstr('config', 'git_remote', default='remotes/origin')

        # [compile]
        conf.directory = getfile('compile', 'bench_dir')
        conf.install = getboolean('compile', 'install', False)
        conf.compile = getboolean('compile', 'compile', True)
        try:
            conf.jobs = getint('compile', 'jobs')
        except KeyError:
            conf.jobs = None

        # [run_benchmark]
        conf.manifest = getfile('run_benchmark', 'manifest', default='')
        conf.benchmarks = getstr('run_benchmark', 'benchmarks', default='')
        conf.project = getstr('run_benchmark', 'project', default='')
        conf.upload = getboolean('run_benchmark', 'upload', False)
        conf.verbose = getboolean('run_benchmark', 'verbose', False)
        conf.clean_venv = getboolean('run_benchmark', 'clean_venv', True)
        try:
            conf.n_trials = getint('run_benchmark', 'n_trials')
        except KeyError:
            conf.n_trials = None
        try:
            conf.timeout = getfloat('run_benchmark', 'timeout')
        except KeyError:
            conf.timeout = None

        # paths
        conf.build_dir = os.path.join(conf.repo_dir, 'build')
        conf.log_dir = os.path.join(conf.directory, 'logs')
        conf.bench_dir = os.path.join(conf.directory, 'bench_dir')
        conf.venv_dir = os.path.join(conf.directory, 'venv')

        check_upload = conf.upload
    else:
        check_upload = True

    # [upload]
    UPLOAD_OPTIONS = ('url', 'authentication')

    conf.url = getstr('upload', 'url', default='')
    conf.authentication = getstr('upload', 'authentication', default='')

    if check_upload and any(not getattr(conf, attr) for attr in UPLOAD_OPTIONS):
        print("ERROR: Upload requires to set the following "
              "configuration option in the the [upload] section "
              "of %s:"
              % filename)
        for attr in UPLOAD_OPTIONS:
            text = "- %s" % attr
            if not getattr(conf, attr):
                text += " (not set)"
            print(text)
        sys.exit(1)

    if parse_compile_all:
        # [compile_all]
        conf.branches = getstr('compile_all', 'branches', '').split()
        conf.revisions = []
        try:
            revisions = cfgobj.items('compile_all_revisions')
        except configparser.NoSectionError:
            pass
        else:
            for revision, name in revisions:
                # strip comments
                name = name.partition('#')[0]
                # strip spaces
                name = name.strip()
                conf.revisions.append((revision, name))

    return conf


class BenchmarkAll(Application):
    def __init__(self, config_filename, options):
        config_filename = os.path.abspath(config_filename)
        conf = parse_config(config_filename, "compile_all")
        super().__init__(conf, options)
        self.config_filename = config_filename
        self.safe_makedirs(self.conf.directory)
        self.setup_log('compile_all')
        self.outputs = []
        self.skipped = []
        self.failed = []
        self.timings = []
        self.logger = logging.getLogger()

    def benchmark(self, revision, branch):
        if branch:
            key = '%s-%s' % (branch, revision)
        else:
            key = revision

        cmd = [sys.executable, '-m', 'pyperformance', 'compile',
               self.config_filename, revision, branch]
        if not self.conf.update:
            cmd.append('--no-update')

        self.start = time.monotonic()
        exitcode = self.run_nocheck(*cmd, log_stdout=False)
        dt = time.monotonic() - self.start

        if exitcode:
            self.logger.error("Benchmark exit code: %s" % exitcode)

        if exitcode == EXIT_ALREADY_EXIST:
            # Benchmark already uploaded
            self.skipped.append(key)
            return

        # compile, venv and bench errors occur after repository update
        # and system tune
        if exitcode >= EXIT_COMPILE_ERROR:
            if self.conf.update:
                # Ony update the repository once
                self.conf.update = False

        if exitcode == 0 or exitcode == EXIT_BENCH_ERROR:
            self.outputs.append((key, exitcode == 0))
            self.timings.append(dt)
        else:
            self.failed.append(key)

    def report(self):
        for key in self.skipped:
            self.logger.error("Skipped: %s" % key)

        for key, success in self.outputs:
            if success:
                success_message = "All benchmarks succeeded"
            else:
                success_message = "Some benchmarks failed"
            self.logger.error("Tested: %s (%s)" % (key, success_message))

        for key in self.failed:
            text = "FAILED: %s" % key
            self.logger.error(text)

    def report_timings(self):
        def format_time(seconds):
            if seconds >= 100:
                return "%.0f min %.0f sec" % divmod(seconds, 60)
            else:
                return "%.0f sec" % math.ceil(seconds)

        self.logger.error("Timings:")
        self.logger.error("- min: %s" % format_time(min(self.timings)))
        text = "- avg: %s" % format_time(statistics.mean(self.timings))
        if len(self.timings) >= 2:
            stdev = statistics.stdev(self.timings)
            text = "%s -- std dev: %s" % (text, format_time(stdev))
        self.logger.error(text)
        self.logger.error("- max: %s" % format_time(max(self.timings)))

    def main(self):
        self.safe_makedirs(self.conf.directory)

        self.logger.error("Compile and benchmark all")
        if self.log_filename:
            self.logger.error("Write logs into %s" % self.log_filename)
        self.logger.error("Revisions: %r" % (self.conf.revisions,))
        self.logger.error("Branches: %r" % (self.conf.branches,))

        if not self.conf.revisions and not self.conf.branches:
            self.logger.error("ERROR: no branches nor revisions "
                              "configured for compile_all")
            sys.exit(1)

        try:
            for revision, branch in self.conf.revisions:
                self.benchmark(revision, branch)

            for branch in self.conf.branches:
                self.benchmark(branch, branch)
        finally:
            self.report()
            if self.timings:
                self.report_timings()

        if self.failed:
            sys.exit(1)
