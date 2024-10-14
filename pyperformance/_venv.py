# Helpers for working with venvs.

import os
import os.path
import sys
import types

from . import _utils, _openmcinfo, _pip


class VenvCreationFailedError(Exception):
    def __init__(self, root, exitcode, already_existed):
        super().__init__(f'venv creation failed ({root})')
        self.root = root
        self.exitcode = exitcode
        self.already_existed = already_existed


class VenvPipInstallFailedError(Exception):
    def __init__(self, root, exitcode, msg=None):
        super().__init__(msg or f'failed to install pip in venv {root}')
        self.root = root
        self.exitcode = exitcode


class RequirementsInstallationFailedError(Exception):
    pass


def read_venv_config(root=None):
    """Return the config for the given venv, from its pyvenv.cfg file."""
    if not root:
        if sys.prefix == sys.base_prefix:
            raise Exception('current Python is not a venv')
        root = sys.prefix
    cfgfile = os.path.join(root, 'pyvenv.cfg')
    with open(cfgfile, encoding='utf-8') as infile:
        text = infile.read()
    return parse_venv_config(text, root)


def parse_venv_config(lines, root=None):
    if isinstance(lines, str):
        lines = lines.splitlines()
    else:
        lines = (l.rstrip(os.linesep) for l in lines)

    cfg = types.SimpleNamespace(
        home=None,
        version=None,
        system_site_packages=None,
        prompt=None,
        executable=None,
        command=None,
    )
    fields = set(vars(cfg))
    for line in lines:
        # We do not validate the lines.
        name, sep, value = line.partition('=')
        if not sep:
            continue
        # We do not check for duplicate names.
        name = name.strip().lower()
        if name == 'include-system-site-packages':
            name = 'system_site_packages'
        if name not in fields:
            # XXX Preserve this anyway?
            continue
        value = value.lstrip()
        if name == 'system_site_packages':
            value = (value == 'true')
        setattr(cfg, name, value)
    return cfg


def resolve_venv_openmc(root):
    python_package = 'openmc'
    # Assumes that venv python version will match python version of sys.executable
    python_version = 'python{}.{}'.format(sys.version_info.major, sys.version_info.minor)
    return os.path.join(root, 'lib', python_version, 'site-packages', python_package)


def get_venv_root(openmc, name=None, venvsdir='venv'):
    """Return the venv root to use for the given name (or given python)."""
    if not name:
        from .run import get_run_id
        runid = get_run_id(openmc)
        name = runid.name
    return os.path.abspath(
        os.path.join(venvsdir or '.', name),
    )


def venv_exists(root):
    venv_openmc = resolve_venv_openmc(root)
    return os.path.exists(venv_openmc)


def create_venv(root, *,
                env=None,
                downloaddir=None,
                withpip=True,
                cleanonfail=True
                ):
    """Create a new venv at the given root, optionally installing pip."""
    already_existed = os.path.exists(root)
    if withpip:
        args = ['-m', 'venv', root]
    else:
        args = ['-m', 'venv', '--without-pip', root]
    ec, _, _ = _utils.run_python(*args, python=sys.executable, env=env)
    if ec != 0:
        if cleanonfail and not already_existed:
            _utils.safe_rmtree(root)
        raise VenvCreationFailedError(root, ec, already_existed)
    return resolve_venv_openmc(root)


class VirtualEnvironment:

    _env = None

    @classmethod
    def create(cls, root=None, openmc=None, **kwargs):
        if not openmc:
            raise Exception(f'openmc executable not passed to VirtualEnvironment class')
        if isinstance(openmc, str):
            try:
                info, _ = _openmcinfo.get_version_info(openmc)
            except FileNotFoundError:
                print(openmc)
                raise Exception(f'openmc executable could not be found')
        else:
            info = openmc
        if not root:
            root = get_venv_root(openmc=info)

        print("Creating the virtual environment %s" % root)
        if venv_exists(root):
            raise Exception(f'virtual environment {root} already exists')

        try:
            venv_openmc = create_venv(
                root,
            )
        except BaseException:
            _utils.safe_rmtree(root)
            raise  # re-raise
        if not info:
            info, _ = _openmcinfo.get_version_info(openmc)
        venv_python = os.path.join(root, 'bin', 'python')
        self = cls(root, base=info, openmc=openmc, python=venv_python)
        self.ensure_pip()
        self.ensure_python_openmc()
        return self

    @classmethod
    def ensure(cls, root, openmc=None, **kwargs):
        if not openmc:
            raise Exception(f'openmc executable not passed to VirtualEnvironment class')
        info, _ = _openmcinfo.get_version_info(openmc)
        venv_python = os.path.join(root, 'bin', 'python')
        if venv_exists(root):
            return cls(root, base=info, openmc=openmc, python=venv_python)
        else:
            return cls.create(root, openmc, **kwargs)

    def __init__(self, root, openmc=None, python=None, *, base=None):
        self.root = root
        if base:
            self._base = base
        self._openmc = openmc
        self._python = python

    @property
    def openmc(self):
        return self._openmc

    @property
    def python(self):
        return self._python

    @property
    def info(self):
        try:
            return self._info
        except AttributeError:
            try:
                openmc = self._openmc
            except AttributeError:
                openmc = resolve_venv_openmc(self.root)
            self._info, _ = _openmcinfo.get_version_info(openmc)
            return self._info

    @property
    def base(self):
        try:
            return self._base
        except AttributeError:
            base_exe = self.info.sys._base_executable
            if not base_exe and base_exe != self.info.sys.executable:
                # XXX Use read_venv_config().
                raise NotImplementedError
                base_exe = ...
            self._base, _ = _openmcinfo.get_version_info(base_exe)
            return self._base

    def ensure_python_openmc(self):
        _pip.install_openmc_requirements(self._openmc, upgrade=True, python=self._python)
        assert os.path.exists(resolve_venv_openmc(self.root)), self.root

    def ensure_pip(self, downloaddir=None, *, installer=True, upgrade=True):
        if not upgrade and _pip.is_pip_installed(self.python, env=self._env):
            return
        ec, _, _ = _pip.install_pip(
            self.python,
            info=self.info,
            downloaddir=downloaddir or self.root,
            env=self._env,
            upgrade=upgrade,
        )
        if ec != 0:
            raise VenvPipInstallFailedError(self.root, ec)
        elif not _pip.is_pip_installed(self.python, env=self._env):
            raise VenvPipInstallFailedError(self.root, 0, "pip doesn't work")

        if installer:
            # Upgrade installer dependencies (setuptools, ...)
            ec, _, _ = _pip.ensure_installer(
                self.python,
                env=self._env,
                upgrade=True,
            )
            if ec != 0:
                raise RequirementsInstallationFailedError('wheel')

    def upgrade_pip(self, *, installer=True):
        ec, _, _ = _pip.upgrade_pip(
            self.python,
            info=self.info,
            env=self._env,
            installer=installer,
        )
        if ec != 0:
            raise RequirementsInstallationFailedError('pip')

    def ensure_reqs(self, *reqs, upgrade=True):
        print("Installing requirements into the virtual environment %s" % self.root)
        ec, _, _ = _pip.install_requirements(
            *reqs,
            python=self.python,
            env=self._env,
            upgrade=upgrade,
        )
        if ec:
            raise RequirementsInstallationFailedError(reqs)
