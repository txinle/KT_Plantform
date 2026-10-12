"""Production settings with cookies adapted to the loopback HTTP UAT tunnel."""

from .settings import *  # noqa: F403
from .settings import DATABASES, DEBUG

if DEBUG or DATABASES["default"]["NAME"] != "kt_e2e":
    raise RuntimeError("Capture UAT settings require DEBUG=False and the isolated kt_e2e database.")

# WebKit correctly rejects Secure cookies over the SSH tunnel's HTTP URL.
# The production HTTPS entry continues to use config.settings unchanged.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
