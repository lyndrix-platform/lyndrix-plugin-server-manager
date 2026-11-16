"""Test bootstrap.

Importing ``core.api`` cold trips a circular import inside lyndrix-core
(core.api → plugins → ui.layout → notifications → core.api). The running
platform never hits it because main.py loads core.services first, so mirror
that order here before any plugin module pulls in core.api.
"""
# TODO(agent): fix the core.api ↔ ui.layout cycle in lyndrix-core and drop this.
import core.services  # noqa: F401

import importlib
import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PKG = "server_manager_plugin"


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        return None


class _AppAliasFinder(importlib.abc.MetaPathFinder):
    """Maps ``server_manager_plugin.app.*`` onto the already imported ``app.*``.

    The core loads the plugin as a package, so ``entrypoint.py`` uses relative
    imports (``from .app.api import …``); the tests import ``app.*`` top-level.
    Aliasing keeps ONE module object per file — a second import would define
    the SQLAlchemy models twice and split the service singleton.
    """

    def find_spec(self, fullname, path=None, target=None):
        prefix = f"{_PKG}.app"
        if fullname != prefix and not fullname.startswith(prefix + "."):
            return None
        real = importlib.import_module(fullname[len(_PKG) + 1:])
        return importlib.util.spec_from_loader(fullname, _AliasLoader(real))


@pytest.fixture(scope="session")
def plugin_entrypoint():
    """The plugin's ``entrypoint`` module, imported the way the core does it."""
    name = f"{_PKG}.entrypoint"
    if name in sys.modules:
        return sys.modules[name]
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = package
    sys.meta_path.insert(0, _AppAliasFinder())
    return importlib.import_module(name)
