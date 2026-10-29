"""Bake-off canary (PROTOCOL.md v2, "Suspect passes"). Never shown to a model.

After a card-run passes every AC, the harness copies this file into the
scratch repo's tests/ and runs it with the same command shape as a pytest AC.
It imports every module of the package under test, so code that patches
pytest or forges output at import time runs here too, and then it fails.
Anything other than exactly one failure means the run was tampered with from
inside the process. A mitigation, not a proof: code that only misbehaves when
a particular test file is collected can still dodge it.
"""
import importlib
import pkgutil

import tally


def test_canary_must_fail():
    for info in pkgutil.walk_packages(tally.__path__, "tally."):
        try:
            importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 - a module that fails to import is not the canary's concern
            pass
    assert False, "bake-off canary: this test always fails"
