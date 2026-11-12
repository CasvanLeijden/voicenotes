"""Loaded by Python at startup inside Voicenotes.app: make the project's packages importable."""
import os
import site
import sys

_sp = os.environ.get("VOICENOTES_SITE_PACKAGES")
if _sp and os.path.isdir(_sp):
    site.addsitedir(_sp)  # also processes .pth files (e.g. the editable voicenotes install)
_src = os.environ.get("VOICENOTES_SRC")
if _src and _src not in sys.path:
    sys.path.insert(0, _src)
