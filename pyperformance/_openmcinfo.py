# A utility library for getting information about a Python executable.
#
# This may be used as a script.

import os
import os.path
import sys
import subprocess
import datetime


def get_version_info(openmc):
    """Return an object with details about the given OpenMC executable.

    Most of the details are grouped by their source.
    """
    # Run _openmcinfo.py to get the raw info.
    argv = [openmc, '-v']
    try:
        text = subprocess.check_output(argv, encoding='utf-8')
    except subprocess.CalledProcessError:
        raise Exception(f'could not get info for {openmc}')
    text_split = text.split('\n')
    if len(text_split) < 2 or 'Commit hash' not in text_split[1] or 'OpenMC version' not in text_split[0]:
        raise Exception(f'could not get info for {openmc}')
    git_sha = text_split[1].split(': ', 1)[-1]
    version = text_split[0].split('version ')[-1]
    # TODO-SK pass other version info back if needed
    return git_sha, version


def get_build_info(openmc):
    # Assumes openmc repository root is 2 levels above path to exectuable
    git_dir = os.path.join(os.path.dirname(openmc), '..', '..', '.git')
    cmd = ['git', '--git-dir', git_dir, 'show', '-s', '--pretty=format:%H|%ci']
    try:
        stdout = subprocess.check_output(cmd, encoding='utf-8')
    except subprocess.CalledProcessError:
        raise Exception(f'could not get info for {openmc}')
    commitid, date = stdout.split('|')
    date = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S %z')
    # convert local date to UTC
    date = (date - date.utcoffset()).replace(tzinfo=datetime.timezone.utc)
    versioninfo = get_version_info(openmc)
    assert versioninfo[0] == commitid
    version = "Version {}".format(versioninfo[1])
    return commitid, date, version


def get_environment_info():
    cmd = ['lsb_release', '-a']
    try:
        stdout = subprocess.check_output(cmd, encoding='utf-8')
    except subprocess.CalledProcessError:
        raise Exception(f'could not get environment info')
    stdout_split = stdout.split('\n')
    os_name = None
    os_version = None
    for line in stdout_split:
        if 'Distributor ID:' in line:
            os_name = line.split(':')[-1].strip()
        elif 'Release:' in line:
            os_version = line.split(':')[-1].strip()
    if not os_name or not os_version:
        raise Exception(f'could not get environment info')
    return '{} {}'.format(os_name, os_version)


#######################################
# use as a script

if __name__ == '__main__':
    openmc = 'openmc'
    info = get_version_info(openmc)
    print()
