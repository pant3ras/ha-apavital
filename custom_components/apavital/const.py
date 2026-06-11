"""Constants for the Apavital integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "apavital"

# Config entry keys
CONF_TOKEN = "token"

# Base of the My Apavital JSON API (same origin the web dashboard calls).
API_BASE = "https://my.apavital.ro/api"

# The login is a JWT (Bearer) valid for ~365 days, so there is no session to keep
# warm — we just poll for data. Usage updates hourly at most, readings monthly.
DEFAULT_SCAN_INTERVAL = timedelta(hours=1)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Context fields the web app appends to login calls.
LOGIN_PAGE = "/login"
LOGIN_LABEL = "Contul Meu | APAVITAL"
