import os
import os.path
import sys

import pyperformance
from . import _utils, _pip, _venv


REQUIREMENTS_FILE = os.path.join(
    os.path.dirname(__file__), 'requirements', 'requirements.txt'
)
PYPERF_OPTIONAL = ['psutil']


class Requirements(object):

    @classmethod
    def from_file(cls, filename):
        self = cls()
        self._add_from_file(filename)
        return self

    @classmethod
    def from_benchmarks(cls, benchmarks):
        self = cls()
        for bench in benchmarks or ():
            filename = bench.requirements_lockfile
            self._add_from_file(filename)
        return self

    def __init__(self):
        # if pip or setuptools is updated:
        # .github/workflows/main.yml should be updated as well

        # requirements
        self.specs = []

    def __len__(self):
        return len(self.specs)

    def __iter__(self):
        for spec in self.specs:
            yield spec

    def _add_from_file(self, filename):
        if not os.path.exists(filename):
            return
        for line in _utils.iter_clean_lines(filename):
            fullpath = os.path.join(os.path.dirname(filename), line.strip())
            if os.path.isfile(fullpath):
                self._add(fullpath)
            else:
                self._add(line)

    def _add(self, line):
        self.specs.append(line)

    def get(self, name):
        for req in self.specs:
            if _pip.get_pkg_name(req) == name:
                return req
        return None


# This is used by the hg_startup benchmark.
def get_venv_program(program):
    bin_path = os.path.dirname(sys.executable)
    bin_path = os.path.realpath(bin_path)

    if not os.path.isabs(bin_path):
        print("ERROR: Python executable path is not absolute: %s"
              % sys.executable)
        sys.exit(1)

    if not os.path.exists(os.path.join(bin_path, 'activate')):
        print("ERROR: Unable to get the virtual environment of "
              "the Python executable %s" % sys.executable)
        sys.exit(1)

    if os.name == 'nt':
        path = os.path.join(bin_path, program)
    else:
        path = os.path.join(bin_path, program)

    if not os.path.exists(path):
        print("ERROR: Unable to get the program %r "
              "from the virtual environment %r"
              % (program, bin_path))
        sys.exit(1)

    return path


NECESSARY_ENV_VARS = {
    'nt': [
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMPUTERNAME",
        "ComSpec",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "CommonProgramW6432",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "Path",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "SystemDrive",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERDNSDOMAIN",
        "USERDOMAIN",
        "USERDOMAIN_ROAMINGPROFILE",
        "USERNAME",
        "USERPROFILE",
        "windir",
    ],
}
NECESSARY_ENV_VARS_DEFAULT = [
    "HOME",
    "PATH",
]

def _get_envvars(inherit=None, osname=None):
    # Restrict the env we use.
    try:
        necessary = NECESSARY_ENV_VARS[osname or os.name]
    except KeyError:
        necessary = NECESSARY_ENV_VARS_DEFAULT
    copy_env = list(necessary)
    if inherit:
        copy_env.extend(inherit)

    env = {}
    for name in copy_env:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


class VenvForBenchmarks(_venv.VirtualEnvironment):

    @classmethod
    def ensure(cls, root, openmc=None, *,
               inherit_environ=None,
               upgrade=False,
               **kwargs
               ):
        exists = _venv.venv_exists(root)
        if upgrade == 'oncreate':
            # Upgrade only when venv created from scratch
            upgrade = not exists
        elif upgrade == 'onexists':
            # Upgrade only when venv exists
            upgrade = exists
        elif isinstance(upgrade, str):
            raise NotImplementedError(upgrade)

        if exists:
            self = super().ensure(root, openmc)
            self.inherit_environ = inherit_environ
            if upgrade:
                self.upgrade_pip()
            else:
                self.ensure_pip(upgrade=False)
            return self
        else:
            return cls.create(
                root,
                openmc,
                inherit_environ=inherit_environ,
                upgrade=upgrade,
                **kwargs
            )

    def __init__(self, root, openmc=None, python=None, *, base=None, inherit_environ=None):
        super().__init__(root, openmc=openmc, python=python, base=base)
        self.inherit_environ = inherit_environ or None

    @property
    def _env(self):
        # Restrict the env we use.
        return _get_envvars(self.inherit_environ)

    def ensure_pyperformance(self, upgrade=False):
        if not upgrade and _pip.is_package_installed("pyperformance", python=self.python, env=self._env):
            print("Skipping pyperformance installation as it already exists in venv")
            return
        print("installing pyperformance in the venv at %s" % self.root)
        sys.exit()
        # Install pyperformance inside the virtual environment.
        if pyperformance.is_dev():
            basereqs = Requirements.from_file(REQUIREMENTS_FILE)
            self.ensure_reqs(basereqs)

            root_dir = os.path.dirname(pyperformance.PKG_ROOT)
            ec, _, _ = _pip.install_pyperformance(
                root_dir,
                python=self.python,
                env=self._env,
            )
            if ec != 0:
                raise _venv.RequirementsInstallationFailedError(root_dir)
        else:
            print("Only dev installation of pyperformance is supported at this time")
            raise _venv.RequirementsInstallationFailedError('pyperformance')

    def ensure_reqs(self, requirements=None):
        # parse requirements
        bench = None
        if requirements is None:
            requirements = Requirements()
        elif hasattr(requirements, 'requirements_lockfile'):
            bench = requirements
            requirements = Requirements.from_benchmarks([bench])

        if not requirements:
            print('(nothing to install)')
        else:
            # install requirements
            super().ensure_reqs(
                *requirements,
                upgrade=False,
            )

        # Dump the package list and their versions: pip freeze
        _pip.run_pip('freeze', python=self.python, env=self._env)

        return requirements
