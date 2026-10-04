"""
Access to the COTW tools of the repository (folder cotw/) from the GUI.

The tools are scripts in cotw/, not an installed package: their folder is added to sys.path the first time they
are needed. On Linux (tests), cotw/_projet.py installs the zugbruecke stand-in; on Windows it does nothing.
The GUI calls the same functions as the command-line tools, so both always produce the same files.
"""
import os
import sys

from deca.util import deca_root


def cotw_dir() -> str:
    return os.path.join(deca_root(), 'cotw')


def ensure_path():
    folder = cotw_dir()
    if not os.path.isfile(os.path.join(folder, 'construire_mod.py')):
        raise RuntimeError('COTW tools not found in {} (the GUI must run from the repository)'.format(folder))
    if folder not in sys.path:
        sys.path.insert(0, folder)


def modules():
    """(construire_mod, cloner_munition, catalogue, _projet, stringlookup, xlsbook)"""
    ensure_path()
    import _projet  # noqa: F401  (zugbruecke stand-in first)
    import construire_mod
    import cloner_munition
    import catalogue
    import stringlookup
    import xlsbook
    return construire_mod, cloner_munition, catalogue, _projet, stringlookup, xlsbook


def default_mods_dir() -> str:
    """<repository>/../mods: next to work/, like D:\\dev\\mods on the gaming PC."""
    return os.path.normpath(os.path.join(deca_root(), '..', 'mods'))
