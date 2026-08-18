from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from agentlab.errors import AdapterError
from agentlab.schema import Variant


class DirArtifact:
    type = "dir"

    def validate(self, variant: Variant, root: Path) -> None:
        path = (root / variant.path).resolve()
        if not path.is_dir() or not any(path.iterdir()):
            raise AdapterError("artifact_missing_dir", f"{variant.path} is not a non-empty directory")

    def fingerprint(self, variant: Variant, root: Path) -> str:
        path = (root / variant.path).resolve()
        digest = hashlib.sha256()
        for file in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(file.relative_to(path).as_posix().encode())
            digest.update(file.read_bytes())
        return digest.hexdigest()

    def materialize(self, variant: Variant, dest: Path, root: Path) -> Path:
        src = (root / variant.path).resolve()
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, symlinks=False)
        return dest
