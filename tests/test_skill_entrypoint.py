"""Exercise the copied Skill through its two public files, outside the checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.helpers import make_min_exp


def test_copied_skill_runs_and_reports_from_an_unrelated_directory(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'skills/agentlab'
    skill = tmp_path / 'installed skills/agentlab'
    shutil.copytree(source, skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    cwd = tmp_path / 'unrelated project'
    cwd.mkdir()
    env = {**os.environ, 'AGENTLAB_PYTHON': sys.executable, 'AGENTLAB_HOME': str(tmp_path / 'home')}
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-I', str(skill / 'scripts/ensure_python.py')],
                            cwd=cwd, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    python = result.stdout.splitlines()[0]
    assert Path(python).is_absolute()
    root = make_min_exp(tmp_path / 'experiment')

    def cli(*args):
        result = subprocess.run([python, '-I', str(skill / 'scripts/cli.py'), *args],
                                cwd=cwd, env=env, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    assert 'rescore' in cli('--help')
    cli('brief', '--exp', str(root), '--confirm-criteria')
    cli('run', '--exp', str(root))
    first = (root / 'runs/LATEST').read_text().strip()
    cli('rescore', '--exp', str(root), '--run', first)
    current = root / 'runs' / (root / 'runs/LATEST').read_text().strip()
    manifest = json.loads((current / 'manifest.json').read_text())
    assert manifest['ran'] == []
    assert len(manifest['reused']) == 2
    cli('report', '--exp', str(root), '--run', first)
    for name in ('report.md', 'audit.html', 'execution.html', 'diagnostics.json'):
        assert (root / 'runs' / first / name).is_file()
    cli('status', '--exp', str(root))
    cli('storage', '--exp', str(root))
    cli('cleanup', '--exp', str(root), '--dry-run')


def test_reporting_does_not_load_execution_or_judge_drivers(tmp_path):
    scripts = Path(__file__).resolve().parents[1] / 'skills/agentlab/scripts'
    program = '''import sys
sys.path.insert(0, sys.argv[1])
from agentlab.reporting.report import write_report
assert 'agentlab.execution.scheduler' not in sys.modules
assert 'agentlab.execution.trial' not in sys.modules
assert 'agentlab.execution.shell' not in sys.modules
assert 'agentlab.evaluation.judge' not in sys.modules
assert 'agentlab.evaluation.process' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-I', '-c', program, str(scripts)],
                            cwd=tmp_path, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
