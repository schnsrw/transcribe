"""
casual_sst.admin
================

Lightweight monitoring + configuration portal for Casual-SST.

Mounted at ``/admin`` when the ``ADMIN_TOKEN`` env var is set
(disabled by default — no token, no portal). Read-only by default;
the JSON endpoints under ``/admin/api/*`` cover:

  * service uptime + counters
  * active meetings and per-participant state
  * recent finalized transcriptions
  * in-process log tail (ring buffer)
  * the resolved config tree

Auth: a static token via the ``X-Admin-Token`` request header. Trivial
by design — the portal is meant for local ops and a small operator
team. Plug a real auth proxy in front if you expose it publicly.
"""

from .api import router as admin_router  # noqa: F401
from .metrics import METRICS  # noqa: F401
from .logs import install_log_handler  # noqa: F401
from .prom import router as prom_router  # noqa: F401
