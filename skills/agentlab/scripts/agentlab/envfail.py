from __future__ import annotations

import re

# Environment cannot run. Not a model-quality failure. Kill the trial and skip the rest.
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
    (re.compile(r"unknown model", re.I), "model_not_found"),
    (re.compile(r"unknown option", re.I), "bad_cli"),
    (re.compile(r"unrecognized arguments", re.I), "bad_cli"),
    (re.compile(r"no such option", re.I), "bad_cli"),
]


def classify_env_error(text: str) -> str | None:
    if not text:
        return None
    for pattern, reason in _PATTERNS:
        if pattern.search(text):
            return reason
    return None
