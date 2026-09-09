"""Shared archival objects and disposable, independently writable working copies.

Archive views use relative links to experiment-local objects. Never execute commands
inside an archive: materialize() always creates independent regular files first.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from agentlab.errors import ContractError
from agentlab.provenance import atomic_json


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def link_view(target: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(target, dest.parent)
    if dest.is_symlink() and os.readlink(dest) == relative:
        return
    remove_path(dest)
    dest.symlink_to(relative, target_is_directory=target.is_dir())


def init_storage(root: Path, run_id: str) -> None:
    cache = root / 'cache' / 'trials'
    legacy = root / 'trials'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(exist_ok=True)
    if legacy.is_symlink() and legacy.resolve() != cache.resolve():
        raise ContractError('storage_conflict', 'trials/ points outside the managed cache')
    # Existing trials may contain registered legacy worktrees. Moving their
    # parent directory would invalidate Git registrations, so leave them in place.
    if not legacy.is_dir() or legacy.is_symlink():
        link_view(cache, legacy)
    workspace = root / 'workspaces' / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    link_view(workspace, root / 'runs' / run_id / 'workspaces')


def _files(source: Path, *, exclude: set[str] | None = None):
    # File links in archive views are read as content. Directory links must be
    # explicitly materialized to avoid cycles or accidental recursive traversal.
    for directory, dirs, files in os.walk(source, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            path = parent / name
            if path.relative_to(source).as_posix() in (exclude or set()):
                dirs.remove(name)
                continue
            if path.is_symlink():
                dirs.remove(name)
                raise ContractError('unsupported_output_link', f'output directory link must be materialized before archiving: {path}')
        for name in files:
            path = parent / name
            if path.is_file():
                yield path
            elif path.is_symlink():
                raise ContractError('missing_output_link', f'broken output link: {path}')


def _blob(root: Path, src: Path) -> tuple[Path, dict]:
    mode = src.stat().st_mode & 0o777
    with src.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    key = f'{digest}-{mode:o}'
    dest = root / 'artifacts' / 'sha256' / key[:2] / key
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix='.writing-')
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, 'wb') as out, src.open('rb') as source:
                shutil.copyfileobj(source, out)
            tmp.chmod(mode)
            # Each publisher has its own temporary file; equivalent concurrent
            # publications may atomically replace the same immutable content.
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    return dest, {'blob': dest.relative_to(root).as_posix(), 'size': src.stat().st_size, 'mode': mode}


def _copy_directories(source: Path, dest: Path, exclude: set[str] | None = None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for directory, dirs, _ in os.walk(source):
        parent = Path(directory)
        dirs[:] = [name for name in dirs if (parent / name).relative_to(source).as_posix() not in (exclude or set())]
        (dest / parent.relative_to(source)).mkdir(parents=True, exist_ok=True)


def snapshot(root: Path, source: Path, dest: Path, *, exclude: set[str] | None = None) -> dict[str, dict]:
    entries = {}
    if not source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return entries
    _copy_directories(source, dest, exclude)
    for src in _files(source, exclude=exclude):
        rel = src.relative_to(source).as_posix()
        blob, entry = _blob(root, src)
        entries[rel] = entry
        link_view(blob, dest / rel)
    return entries


def execution_path(root: Path, execution_id: str) -> Path:
    key = hashlib.sha256(execution_id.encode()).hexdigest()
    return root / 'executions' / key


def save_execution(trial) -> None:
    if not trial.execution_id:
        return
    root = trial.experiment_root
    dest = execution_path(root, trial.execution_id)
    if (dest / 'manifest.json').is_file():
        return
    entries = snapshot(root, trial.outputs_dir(), dest / 'outputs', exclude={'home', 'evaluation-home'})
    atomic_json(dest / 'manifest.json', {'execution_id': trial.execution_id,
                'execution_basis': trial.execution_basis, 'files': entries})
    meta = json.loads((trial.trial_dir() / 'meta.json').read_text()) if (trial.trial_dir() / 'meta.json').is_file() else {}
    if trial.result:
        meta.update(exit_code=trial.result.exit_code, usage=vars(trial.result.usage),
                    wall_clock_s=trial.result.wall_clock_s, error_code=trial.error_code,
                    killed_reason=trial.killed_reason, phase='executed')
    atomic_json(dest / 'meta.json', meta)


def archive_outputs(root: Path, src: Path, dest: Path, evaluation: Path, execution_id: str | None,
                    previous: Path | None = None) -> None:
    execution = execution_path(root, execution_id) if execution_id else None
    manifest = execution / 'manifest.json' if execution else None
    original = json.loads(manifest.read_text()).get('files', {}) if manifest and manifest.is_file() else {}
    entries = {}
    remove_path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    # This evaluation is still being assembled; completed prior evaluations use
    # different run ids and are never overwritten here.
    remove_path(evaluation / 'outputs')
    transient = {'home', 'evaluation-home'} | {f'judges/{p.name}/.home' for p in (src / 'judges').glob('*')}
    _copy_directories(src, dest, transient)
    for path in _files(src, exclude=transient) if src.is_dir() else []:
        rel = path.relative_to(src).as_posix()
        blob, entry = _blob(root, path)
        if original.get(rel) == entry:
            target = execution / 'outputs' / rel
        else:
            target = evaluation / 'outputs' / rel
            link_view(blob, target)
        link_view(target, dest / rel)
        entries[rel] = {'source': target.relative_to(root).as_posix(), **entry}
    # Completed cache entries omit bulky judge inputs. Carry their archive
    # references forward only when that judge has not assembled a new view.
    if previous and previous.is_dir():
        for path in _files(previous):
            rel = path.relative_to(previous).as_posix()
            parts = Path(rel).parts
            if len(parts) < 4 or parts[0] != 'judges' or parts[2] not in {'workspace', 'evidence'}:
                continue
            if rel in entries or (src / 'judges' / parts[1] / 'evidence').exists():
                continue
            blob, entry = _blob(root, path)
            target = evaluation / 'outputs' / rel
            link_view(blob, target)
            link_view(target, dest / rel)
            entries[rel] = {'source': target.relative_to(root).as_posix(), **entry}
    atomic_json(evaluation / 'manifest.json', {'execution': execution.relative_to(root).as_posix() if original else None,
                                              'files': entries})


def freeze_compare(root: Path, run_id: str, view: Path) -> None:
    if view.is_symlink():
        return
    dest = root / 'evaluations' / run_id / 'compare' / view.name
    entries = snapshot(root, view, dest, exclude={'.home'})
    atomic_json(dest / 'storage.json', {'files': entries})
    remove_path(view)
    link_view(dest, view)


def materialize(source: Path, dest: Path) -> None:
    """Never preserve archive links or hard-link data into a writable workspace."""
    remove_path(dest)
    shutil.copytree(source, dest, symlinks=False)


def latest_trial(root: Path, trial_id: str) -> Path | None:
    for run in sorted((root / 'runs').glob('*'), reverse=True):
        path = run / 'trials' / trial_id
        if (path / 'meta.json').is_file():
            return path
    return None


def _inventory(path: Path) -> dict:
    result = {p.relative_to(path).as_posix() + '/': None for p in path.rglob('*') if p.is_dir() and not p.is_symlink()}
    for src in _files(path):
        with src.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result[src.relative_to(path).as_posix()] = digest
    return result


def finish_cache(root: Path, trial_id: str, run_id: str) -> None:
    trial = root / 'trials' / trial_id
    # Judge input copies are already in the shared archive. Retain their small
    # logs in the cache, but discard duplicated workspaces and evidence views.
    for judge in (trial / 'outputs' / 'judges').glob('*'):
        for name in ('workspace', 'evidence', '.home'):
            remove_path(judge / name)
    for name in ('home', 'evaluation-home'):
        remove_path(trial / 'outputs' / name)
    marker = trial / 'cache.json'
    marker.unlink(missing_ok=True)
    atomic_json(marker, {'run_id': run_id, 'files': _inventory(trial)})


def usage(root: Path) -> dict:
    categories = {}
    seen = set()
    for path in sorted(root.iterdir()):
        name = path.name
        if not path.exists() or path.is_symlink():
            continue
        if path.is_file():
            item = categories.setdefault('root_files', {'bytes': 0, 'files': 0, 'references': 0})
            item['bytes'] += path.stat().st_size
            item['files'] += 1
            continue
        count = size = links = 0
        for directory, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for file in files:
                p = Path(directory) / file
                if p.is_symlink():
                    links += 1
                    continue
                stat = p.stat()
                key = (stat.st_dev, stat.st_ino)
                if key not in seen:
                    seen.add(key)
                    size += stat.st_size
                    count += 1
        categories[name] = {'bytes': size, 'files': count, 'references': links}
    return {'bytes': sum(c['bytes'] for c in categories.values()), 'categories': categories,
            'note': 'Regular-file bytes, counted once per inode; filesystem metadata and external CLI caches are excluded.'}


def cleanup_plan(root: Path) -> tuple[list[Path], list[str]]:
    remove, skipped = [], []
    trials = list((root / 'cache' / 'trials').glob('*'))
    if not (root / 'trials').is_symlink():
        trials.extend((root / 'trials').glob('*'))
    for trial in trials:
        if trial.is_symlink():
            skipped.append(str(trial))
            continue
        marker = trial / 'cache.json'
        try:
            saved = json.loads(marker.read_text())
            archive = root / 'runs' / saved['run_id'] / 'trials' / trial.name
            current = _inventory(trial)
            current.pop('cache.json', None)
            if not (archive / 'meta.json').is_file() or current != saved['files']:
                skipped.append(str(trial))
                continue
        except (OSError, ValueError, KeyError, ContractError):
            skipped.append(str(trial))
            continue
        remove.append(trial)
    for parent in [root / 'workspaces', *[p for p in (root / 'runs').glob('*/workspaces') if not p.is_symlink()]]:
        if parent.is_dir() and not parent.is_symlink():
            remove.extend(p for p in parent.iterdir() if not p.is_symlink())
    return remove, skipped


def cleanup(root: Path, *, dry_run: bool = False) -> dict:
    from agentlab.adapters.isolation.worktree import (
        WorktreeIsolation, experiment_worktrees, remove_worktree, resolve_repo,
    )
    from agentlab.flock import FileLock
    import yaml

    lock = FileLock(root / 'run.lock')
    if not lock.acquire(blocking=False):
        raise ContractError('run_in_progress', 'cannot clean storage while an experiment is running')
    try:
        configs = []
        try:
            configs.append(yaml.safe_load((root / 'experiment.yaml').read_text()) or {})
        except (OSError, ValueError, yaml.YAMLError):
            pass
        for path in (root / 'runs').glob('*/manifest.json'):
            try:
                configs.append(json.loads(path.read_text()).get('experiment') or {})
            except (OSError, ValueError):
                continue
        repos = set()
        for config in configs:
            if not isinstance(config, dict):
                continue
            iso = config.get('isolation') or {}
            for spec in [iso.get('repo'), *[n.get('source') for n in iso.get('nested_repos') or []]]:
                if spec:
                    repo = resolve_repo(spec, root)
                    if repo.is_dir():
                        repos.add(repo)
        registered = {}
        for repo in repos:
            for path in experiment_worktrees(repo, root):
                registered[path] = repo
        targets, skipped = cleanup_plan(root)
        planned = sorted({str(p.relative_to(root.resolve())) for p in registered} |
                         {str(p.relative_to(root)) for p in targets})
        removed = []
        if not dry_run:
            # Nested worktrees must be unregistered before their parents.
            for path, repo in sorted(registered.items(), key=lambda item: len(item[0].parts), reverse=True):
                with WorktreeIsolation(repo=repo).worktree_lock():
                    remove_worktree(repo, path)
                    removed.append(str(path.relative_to(root.resolve())))
            for target in targets:
                if not target.exists():
                    continue
                # Preserve any worktree whose repository could not be discovered
                # or unregistered instead of deleting its registration by hand.
                if any(target.rglob('.git')):
                    skipped.append(str(target))
                    continue
                remove_path(target)
                removed.append(str(target.relative_to(root)))
        return {'dry_run': dry_run, 'paths': planned, 'removed': removed, 'skipped': skipped,
                'history_preserved': True}
    finally:
        lock.release()
