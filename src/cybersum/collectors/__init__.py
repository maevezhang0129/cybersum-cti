"""Ingestion collectors: live APIs into the Bronze layer.

Each module fetches from one provider and appends whatever it received to
``logs`` unaltered. They are the only components that reach outside the
network, and the only ones that need credentials beyond a model key.

Nothing in the demo or the evaluation harness runs them -- both work from
synthetic data already in the database -- so a clone with no Cloudflare token,
no UptimeRobot token and no Azure subscription is fully functional for
everything this repository demonstrates.

These files were previously named ``tests/test_fetch_api_*.py``. They contain no
assertions and make real network calls; the prefix meant ``pytest tests/`` would
have tried to run live API traffic. They are named for what they do now.
"""

from __future__ import annotations

__all__ = ["azure_webapp", "cloudflare_ddos", "cloudflare_firewall", "uptimerobot"]
