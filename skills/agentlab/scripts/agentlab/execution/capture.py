"""Capture declared execution inputs before launch, independently of writable outputs."""
from __future__ import annotations

import os
from pathlib import Path

from agentlab.records.provenance import atomic_json, digest, dependency_context, command_files
from agentlab.records.storage import archive_file, execution_path, link_view
from agentlab.templates import expand_templates


def capture_inputs(trial, exp, program: Path, prompt: Path) -> None:
    root = trial.experiment_root
    dest = execution_path(root, trial.execution_id) / 'inputs'
    dest.mkdir(parents=True, exist_ok=True)
    files, missing = {}, []

    def file(src: Path, rel: str):
        if not src.is_file():
            missing.append({'path': rel, 'source': str(src), 'reason': 'missing or not a regular file'})
            return
        blob, entry = archive_file(root, src)
        link_view(blob, dest / rel)
        files[rel] = {**entry, 'source': str(src)}

    def tree(src: Path, rel: str):
        if not src.is_dir():
            file(src, rel)
            return
        if dest.resolve().is_relative_to(src.resolve()):
            missing.append({'path': rel, 'source': str(src), 'reason': 'input contains the experiment archive; declare narrower inputs'})
            return
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for directory, dirs, names in os.walk(src, followlinks=False):
            parent = Path(directory)
            (dest / rel / parent.relative_to(src)).mkdir(parents=True, exist_ok=True)
            for name in list(dirs):
                child = parent / name
                if name in {'.git', '__pycache__'}:
                    dirs.remove(name)
                elif child.is_symlink():
                    missing.append({'path': f'{rel}/{child.relative_to(src)}', 'reason': 'directory symlink not traversed'})
                    dirs.remove(name)
            for name in names:
                child = parent / name
                if name == '.git':
                    continue
                target = f'{rel}/{child.relative_to(src).as_posix()}'
                if child.resolve().is_relative_to(src.resolve()):
                    file(child, target)
                else:
                    missing.append({'path': target, 'reason': 'external symlink not followed'})

    # Only the tested artifact, not the whole repository in inplace mode.
    tree(program if exp.artifact.layout == 'sidecar' else root / trial.variant.path, 'variant')
    case = root / (trial.case.path or f'cases/{trial.case.id}')
    file(case / trial.case.prompt_file, 'task.md')
    file(prompt, 'prompt.md')
    ctx = dependency_context(exp, trial)
    for index, spec in enumerate(trial.case.inputs):
        value = expand_templates(spec, ctx)
        if '${' in value:
            missing.append({'path': f'declared/{index}', 'source': spec, 'reason': 'runtime path is not a frozen input'})
            continue
        source = Path(value).expanduser()
        tree(source if source.is_absolute() else root / source, f'declared/{index}')
    # Preserve wrapper scripts, not interpreter binaries or an entire host environment.
    from agentlab.recipes import bound_command
    command, recipe = bound_command(exp, trial.cell, trial.case, root)
    dependencies = command_files(root, command, ctx)
    for index, source in enumerate(dependencies):
        if Path(source).suffix in {'.py', '.sh', '.js', '.mjs', '.rb', '.pl'}:
            file(Path(source), f'commands/{index}/{Path(source).name}')
    context = {
        'task': files.get('task.md', {}).get('blob'),
        'declared': {k: v['blob'] for k, v in files.items() if k.startswith('declared/')},
        'case': trial.case.model_dump(mode='json', exclude={'expected_labels', 'require_exit_0'}),
        'cell': trial.cell.model_dump(mode='json'),
        'recipe': recipe.model_dump(mode='json') if recipe else None,
        'command_files': dependencies,
        'isolation': exp.isolation.model_dump(mode='json', exclude={'keep_sandbox', 'keep_on_fail', 'protected_paths'}),
        'limits': exp.budget.per_trial.model_dump(mode='json'),
    }
    atomic_json(dest / 'manifest.json', {
        'execution_id': trial.execution_id, 'files': files, 'missing': missing,
        'program_root': str(program), 'context': context,
        'comparison_context': digest(context),
        'scope': 'Pre-launch artifact, task, declared case.inputs and recognized wrapper scripts. External services and undeclared inputs are not captured.',
    })


def capture_trace(trial, exp) -> None:
    """Index exported trace files before scoring; never scrape host session stores."""
    root, out = trial.experiment_root, trial.outputs_dir()
    dest = execution_path(root, trial.execution_id)
    entries = []
    for pattern in exp.trace.files:
        matches = [p for p in sorted(out.glob(pattern)) if p.is_file()]
        if not matches:
            entries.append({'pattern': pattern, 'missing': True})
        for src in matches:
            rel = src.relative_to(out).as_posix()
            if not src.resolve().is_relative_to(out.resolve()):
                entries.append({'path': rel, 'missing': True, 'reason': 'external symlink not followed'})
                continue
            blob, entry = archive_file(root, src)
            link_view(blob, dest / 'trace' / 'files' / rel)
            entries.append({'path': rel, **entry, 'missing': False})
    atomic_json(dest / 'trace' / 'manifest.json', {
        'configured': bool(exp.trace.files), 'entries': entries,
        'verification': 'Exported by the execution command; completeness and model identity are not independently verified.',
    })
