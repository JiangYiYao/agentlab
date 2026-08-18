from __future__ import annotations

import re
from pathlib import Path

from agentlab.errors import ContractError

_NAME_HINTS = (".env", "secrets.env", "id_rsa")
_CONTENT = re.compile(
    r"BEGIN (RSA|OPENSSH) PRIVATE KEY|\bsk-[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b"
)
_SKIP_DIRS = {"trials", ".git", ".venv", "__pycache__"}


def scan_experiment_secrets(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        name = path.name
        if name.endswith(".env") or name in {"secrets.env"} or "id_rsa" in name:
            raise ContractError(
                "secrets_in_experiment",
                f"secret-like filename {path.relative_to(root)}",
                path=str(path.relative_to(root)),
            )
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _CONTENT.search(text):
            raise ContractError(
                "secrets_in_experiment",
                f"secret-like content in {path.relative_to(root)}",
                path=str(path.relative_to(root)),
            )
