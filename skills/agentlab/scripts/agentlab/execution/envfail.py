from __future__ import annotations

import os
import re

# Terminal environment failure. Warnings that the process then recovers from are ignored
# unless the process also stalls (no more output) after the match.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"has not been trusted", re.I), "workspace_untrusted"),
    (re.compile(r"hastrustdialogaccepted", re.I), "workspace_untrusted"),
    (re.compile(r"trust dialog", re.I), "workspace_untrusted"),
    (re.compile(r"workspace trust", re.I), "workspace_untrusted"),
    (re.compile(r"login required", re.I), "login_required"),
    (re.compile(r"not logged in", re.I), "login_required"),
    (re.compile(r"please (log|sign)[- ]?in", re.I), "login_required"),
    (re.compile(r"authentication required", re.I), "login_required"),
    (re.compile(r"model not found", re.I), "model_not_found"),
    (re.compile(r"unknown option", re.I), "bad_cli"),
    (re.compile(r"unrecognized arguments", re.I), "bad_cli"),
    (re.compile(r"no such option", re.I), "bad_cli"),
    (re.compile(r"\b429\b", re.I), "rate_limited"),
    (re.compile(r"rate limit", re.I), "rate_limited"),
    (re.compile(r"quota (exceeded|exhausted)", re.I), "quota"),
    (re.compile(r"\b503\b", re.I), "service_unavailable"),
    (re.compile(r"no available channel", re.I), "service_unavailable"),
]


def classify_env_error(text: str) -> str | None:
    if not text:
        return None
    for pattern, reason in _PATTERNS:
        if pattern.search(text):
            return reason
    return None


def env_stall_s() -> float:
    raw = os.environ.get("AGENTLAB_ENV_STALL_S")
    if raw:
        try:
            return max(0.5, float(raw))
        except ValueError:
            pass
    return 20.0
