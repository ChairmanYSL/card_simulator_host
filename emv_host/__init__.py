"""Zero-install shim: lets ``python -m emv_host ...`` run from the repository
root without pip-installing the backend package.

Python imports this root-level package first (cwd is on sys.path); we put
``backend`` at the FRONT of sys.path, remove this module from sys.modules and
import the real ``backend/emv_host``, which then backs everything - including
the ``__main__`` module that the ``-m`` machinery resolves afterwards.  After
the swap, ``import emv_host`` anywhere in the repo yields the real package
too.

Backend must be forced to the front of sys.path even when it is already
present (e.g. via PYTHONPATH), otherwise the re-import would find this shim
again and recurse.
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if not sys.path or sys.path[0] != str(_BACKEND):
    sys.path.insert(0, str(_BACKEND))

sys.modules.pop(__name__, None)
_real = __import__("emv_host")
sys.modules[__name__] = _real
