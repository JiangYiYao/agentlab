"""Regression coverage for stale scoring, dependency templates, run scope and judge inputs."""
import json
import sys

import pytest

from agentlab.cli import main
from agentlab.runs import latest_run_id
from tests.test_lifecycle import add_judge, experiment, manifest, python_command, run, save, scores


@pytest.mark.parametrize('operation', ['run', 'rescore'])
@pytest.mark.parametrize('output_json', ['outputs/score.json', '${trial_out}/score.json', 'score.json'])
def test_rescoring_rejects_previous_evaluator_json(tmp_path, output_json, operation):
    root, raw = experiment(tmp_path)
    writer = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"score.json").write_text(\'{"score":true}\')')
    raw['concerns'][0].update(measure={'type': 'script', 'command': writer, 'result': 'json',
                                     'output_json': output_json}, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    previous = latest_run_id(root)
    archived_scores = root/'runs'/previous/'trials/treatment__local-cli__smoke__r1/scores.json'
    original = archived_scores.read_bytes()
    raw['concerns'][0]['measure']['command'] = ['true']
    save(root, raw)
    assert main([operation, '--exp', str(root), *(['--gate'] if operation == 'run' else [])]) == 1
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['rescored']) == 2
    assert scores(root)[0]['unknown']
    assert 'stale' in scores(root)[0]['evidence']['error']
    assert archived_scores.read_bytes() == original
    # Rewriting identical content is still a new result from this invocation.
    raw['concerns'][0]['measure']['command'] = writer
    assert run(root, raw) == 0
    assert manifest(root)['ran'] == []


def test_rescoring_can_read_unchanged_athlete_json(tmp_path):
    root, raw = experiment(tmp_path)
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"score":true}\')')
    raw['concerns'][0].update(measure={'type': 'script', 'command': ['true'], 'result': 'json',
                                     'output_json': 'outputs/answer.json'}, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    source = latest_run_id(root)
    raw['concerns'][0]['measure']['env'] = {'CHECK_MODE': 'changed'}
    save(root, raw)
    assert main(['rescore', '--exp', str(root), '--run', source]) == 0
    assert manifest(root)['ran'] == []
    assert scores(root)[0]['value'] is True
    # An evaluator replacing an athlete output cannot turn it into trusted athlete evidence.
    raw['concerns'][0]['measure']['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"score":false}\')')
    assert run(root, raw) == 1
    raw['concerns'][0]['measure']['command'] = ['true']
    assert run(root, raw) == 1
    assert scores(root)[0]['unknown']


@pytest.mark.parametrize('field,initial,changed', [('keep', 'SKILL.md', 'missing.txt'), ('gone', 'missing.txt', 'SKILL.md')])
def test_case_template_checklist_invalidates_measurement(tmp_path, field, initial, changed):
    root, raw = experiment(tmp_path)
    raw['artifact']['layout'] = 'inplace'
    raw['evidence'] = {'workspace': True}
    checklist = root/'cases/smoke/check.txt'
    checklist.write_text(initial + '\n')
    raw['concerns'][0].update(measure={'type': 'must_list', field: '${case.path}/check.txt'},
                              **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    checklist.write_text(changed + '\n')
    assert run(root, raw) == 1
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['rescored']) == 2
    assert scores(root)[0]['value'] is False


@pytest.mark.parametrize('kind', ['declared_input', 'command'])
def test_case_template_execution_dependencies(tmp_path, kind):
    root, raw = experiment(tmp_path)
    raw['cases'].append({'id': 'second', 'path': 'cases/second', 'prompt_file': 'prompt.md'})
    (root/'cases/second').mkdir()
    (root/'cases/second/prompt.md').write_text('same task')
    for case in ['smoke', 'second']:
        (root/f'cases/{case}/input.py').write_text('print("original")')
    if kind == 'declared_input':
        for case in raw['cases']:
            case['inputs'] = ['${case.path}/input.py']
    else:
        raw['matrix']['cells'][0]['command'] = [sys.executable, '${case.path}/input.py']
    assert run(root, raw) == 0
    (root/'cases/smoke/input.py').write_text('print("changed")')
    assert run(root, raw) == 0
    assert len(manifest(root)['ran']) == 2
    assert all('__smoke__' in trial for trial in manifest(root)['ran'])
    assert len(manifest(root)['reused']) == 2


@pytest.mark.parametrize('kind', ['declared_input', 'command'])
def test_case_template_script_measurement_dependencies(tmp_path, kind):
    root, raw = experiment(tmp_path)
    script = root/'cases/smoke/check.py'
    script.write_text('raise SystemExit(0)')
    measure = {'type': 'script', 'command': [sys.executable, '${case.path}/check.py']}
    if kind == 'declared_input':
        measure['command'] = python_command('import os; from pathlib import Path; exec((Path(os.environ["AGENTLAB_CASE_DIR"])/"check.py").read_text())')
        measure['inputs'] = ['${case.path}/check.py']
    raw['concerns'][0].update(measure=measure, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    script.write_text('raise SystemExit(1)')
    assert run(root, raw) == 1
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['rescored']) == 2


@pytest.mark.parametrize('configured,actual', [(3, 1), (1, 3)])
def test_promote_and_rescore_use_run_repetitions(tmp_path, configured, actual):
    root, raw = experiment(tmp_path)
    raw['repetitions'] = configured
    assert run(root, raw, '--repetitions', str(actual)) == 0
    assert main(['promote', '--exp', str(root), '--only-variant', 'treatment']) == 0
    assert main(['rescore', '--exp', str(root)]) == 0
    assert len(manifest(root)['planned']) == actual * 2
    assert manifest(root)['ran'] == []
    assert main(['promote', '--exp', str(root), '--only-variant', 'treatment']) == 0
    # Current thresholds still apply; only the sample count comes from the run.
    raw['concerns'][0]['pass']['value'] = 1
    save(root, raw)
    assert main(['promote', '--exp', str(root), '--only-variant', 'treatment']) == 1


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
@pytest.mark.parametrize('kind', ['text', 'files', 'code'])
def test_judge_materials_follow_actual_artifacts(tmp_path, mode, kind):
    root, raw = experiment(tmp_path)
    commands = {
        'text': 'print("UNIQUE_ANSWER")',
        'files': 'import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"answer":42}\')',
        'code': 'from pathlib import Path; Path("answer.txt").write_text("code change")',
    }
    raw['matrix']['cells'][0]['command'] = python_command(commands[kind])
    if kind == 'files':
        raw['evidence'] = {'files': ['answer.json']}
    add_judge(root, raw, mode)
    assert run(root, raw) == 0
    current = root/'runs'/latest_run_id(root)
    prompts = list((current/'compare').glob('*/stdin.md')) if mode == 'compare_case' else list((current/'trials').glob('*/outputs/judges/quality/stdin.md'))
    assert prompts
    for path in prompts:
        text = path.read_text()
        assert '先读补丁和改后文件' not in text
        assert '待评代码根' not in text
        assert 'unknown' in text
        expected = {'text': '/stdout.log', 'files': '/files/', 'code': '文件改动：'}[kind]
        assert expected in text
        if kind != 'code':
            assert '文件改动：' not in text
    if mode == 'compare_case':
        for path in (current/'compare').glob('*/evidence/*'):
            if kind == 'text':
                assert 'UNIQUE_ANSWER' in (path/'stdout.log').read_text()
            if kind == 'files':
                assert json.loads((path/'files/answer.json').read_text())['answer'] == 42


def test_rescore_retains_source_case_selection(tmp_path):
    root, raw = experiment(tmp_path)
    (root/'cases/second').mkdir()
    (root/'cases/second/prompt.md').write_text('another task')
    raw['cases'].append({'id': 'second', 'path': 'cases/second'})
    raw['repetitions'] = 3
    raw['budget']['max_trials'] = 16
    assert run(root, raw, '--repetitions', '1', '--only-case', 'smoke') == 0
    source = latest_run_id(root)
    assert main(['rescore', '--exp', str(root), '--run', source]) == 0
    assert manifest(root)['only_case'] == 'smoke'
    assert len(manifest(root)['planned']) == 2
    assert manifest(root)['ran'] == []
    assert main(['promote', '--exp', str(root), '--only-variant', 'treatment']) == 0
