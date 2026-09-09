import hashlib
import json
import shutil
from pathlib import Path

import pytest

from agentlab.cli import main
from agentlab.records.flock import FileLock
from agentlab.records.runs import latest_run_id
from agentlab.records.storage import usage
from tests.test_lifecycle import add_judge, experiment, manifest, python_command, run, save


def payload_experiment(tmp_path, mode='per_trial'):
    root, raw = experiment(tmp_path)
    raw['evidence'] = {'files': ['payload.bin']}
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"payload.bin").write_bytes(b"p"*262144)')
    add_judge(root, raw, mode)
    return root, raw


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
def test_large_payload_stored_once_across_repeated_evaluations(tmp_path, mode):
    root, raw = payload_experiment(tmp_path, mode)
    assert run(root, raw) == 0
    first = latest_run_id(root)
    execution_manifests = list(root.glob('executions/*/manifest.json'))
    assert len(execution_manifests) == 2
    assert main(['rescore', '--exp', str(root)]) == 0
    assert manifest(root)['ran'] == []
    assert len(list(root.glob('executions/*/manifest.json'))) == 2
    digest = hashlib.sha256(b'p'*262144).hexdigest()
    blobs = list(root.glob(f'artifacts/sha256/{digest[:2]}/{digest}-*'))
    assert len(blobs) == 1
    assert blobs[0].stat().st_size == 262144
    for run_path in (root/'runs').iterdir():
        if not run_path.is_dir():
            continue
        for out in run_path.glob('trials/*/outputs/payload.bin'):
            assert out.is_symlink()
            assert out.resolve() == blobs[0]
    # Writable caches must not be links/hardlinks into archived data.
    cached = root/'trials/baseline__local-cli__smoke__r1/outputs/payload.bin'
    assert not cached.is_symlink()
    assert cached.stat().st_ino != blobs[0].stat().st_ino
    cached.write_bytes(b'changed')
    assert (root/'runs'/first/'trials/baseline__local-cli__smoke__r1/outputs/payload.bin').read_bytes() == b'p'*262144


def test_scoring_output_is_separate_from_original_execution(tmp_path):
    root, raw = experiment(tmp_path)
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"score":true}\')')
    raw['concerns'][0].update(measure={'type': 'script', 'result': 'json', 'output_json': 'outputs/answer.json',
        'command': python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"score":false}\')')},
        **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 1
    assert all(json.loads(p.read_text())['score'] is True for p in root.glob('executions/*/outputs/answer.json'))
    assert all(json.loads(p.read_text())['score'] is False for p in root.glob('evaluations/*/trials/*/outputs/answer.json'))


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
def test_cleanup_preserves_history_and_rescore_without_reexecution(tmp_path, mode):
    root, raw = payload_experiment(tmp_path, mode)
    raw['isolation']['keep_sandbox'] = True
    assert run(root, raw) == 0
    first = latest_run_id(root)
    report = (root/'runs'/first/'report.md').read_text()
    before = usage(root)['bytes']
    assert main(['cleanup', '--exp', str(root), '--dry-run']) == 0
    assert any((root/'cache/trials').iterdir())
    assert any((root/'workspaces').iterdir())
    # Cleanup must not depend on live criteria or a currently valid contract.
    criteria = (root/'criteria.md').read_text()
    (root/'criteria.md').unlink()
    assert main(['cleanup', '--exp', str(root)]) == 0
    assert not list((root/'cache/trials').iterdir())
    assert not list((root/'workspaces').iterdir())
    assert usage(root)['bytes'] < before
    assert main(['report', '--exp', str(root), '--run', first]) == 0
    assert (root/'runs'/first/'report.md').read_text() == report
    (root/'criteria.md').write_text(criteria)
    assert main(['rescore', '--exp', str(root), '--run', first]) == 0
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['reused']) == 2


def test_cleanup_skips_modified_cache_and_refuses_running_experiment(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    trial = root/'trials/baseline__local-cli__smoke__r1'
    (trial/'outputs/manual.txt').write_text('keep my investigation notes')
    lock = FileLock(root/'run.lock')
    assert lock.acquire(blocking=False)
    try:
        assert main(['cleanup', '--exp', str(root)]) == 2
        assert (trial/'outputs/manual.txt').is_file()
    finally:
        lock.release()
    assert main(['cleanup', '--exp', str(root)]) == 0
    assert (trial/'outputs/manual.txt').is_file()
    assert not (root/'trials/treatment__local-cli__smoke__r1').exists()


def test_moved_experiment_keeps_relative_archive_views(tmp_path):
    root, raw = payload_experiment(tmp_path)
    assert run(root, raw) == 0
    first = latest_run_id(root)
    moved = root.with_name('moved')
    root.rename(moved)
    assert main(['report', '--exp', str(moved), '--run', first]) == 0
    payload = moved/'runs'/first/'trials/baseline__local-cli__smoke__r1/outputs/payload.bin'
    assert payload.read_bytes() == b'p'*262144
    assert payload.resolve().is_relative_to(moved)
    assert main(['storage', '--exp', str(moved), '--json']) == 0


def test_reuse_after_cache_cleanup_and_legacy_archive_reading(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    first = latest_run_id(root)
    # A previous full-copy run remains a valid reader source.
    trial = root/'runs'/first/'trials/baseline__local-cli__smoke__r1'
    independent = root/'legacy-copy'
    shutil.copytree(trial/'outputs', independent, symlinks=False)
    shutil.rmtree(trial/'outputs')
    independent.rename(trial/'outputs')
    assert main(['cleanup', '--exp', str(root)]) == 0
    assert run(root, raw) == 0
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['reused']) == 2


def test_storage_counts_objects_once_and_includes_custom_inputs(tmp_path, capsys):
    root, raw = payload_experiment(tmp_path)
    assert run(root, raw) == 0
    (root/'other-inputs').mkdir()
    (root/'other-inputs/input.txt').write_text('custom')
    capsys.readouterr()
    assert main(['storage', '--exp', str(root), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['categories']['other-inputs']['bytes'] == 6
    assert result['categories']['artifacts']['bytes'] >= 262144
    assert result['categories']['runs']['references'] > 0
    assert result['categories']['runs']['bytes'] < 262144


def test_empty_workspace_survives_archival_and_cache_cleanup(tmp_path):
    root, raw = experiment(tmp_path)
    raw['evidence'] = {'workspace': True}
    raw['concerns'][0].update(measure={'type': 'script', 'cwd': 'sandbox',
                                      'command': python_command('import os; assert not os.listdir()')},
                              **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    assert main(['cleanup', '--exp', str(root)]) == 0
    raw['concerns'][0]['measure']['env'] = {'CHECK_MODE': 'again'}
    save(root, raw)
    assert main(['rescore', '--exp', str(root)]) == 0
    assert manifest(root)['ran'] == []
