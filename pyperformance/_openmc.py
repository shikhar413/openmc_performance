# Generic helpers for working with a Python executable.

import hashlib


def get_id(openmc=None, short=True):
    """Return a string that uniquely identifies the given OpenMC executable."""
    # TODO-SK embed some more info about openmc executable and python version environment
    if short:
        openmc_id = openmc[:12]
    else:
        openmc_id = openmc

    return openmc_id
