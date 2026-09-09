import json
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentlab.cli import main
from agentlab.execution_report import ExecutionAudit, write_execution_audit
from agentlab.runs import latest_run_id
from agentlab.schema import TraceSpec
from agentlab.storage import execution_path
from tests.test_lifecycle import experiment, run, save, python_command, manifest

TID = 'treatment__local-cli__smoke__r1'


def original(root, rid, tid=TID):
    meta = json.loads((root/'runs'/rid/'trials'/tid/'meta.json').read_text())
    return execution_path(root, meta['execution_id'])


def diagnosis(root, rid, tid=TID):
    return next(x for x in json.loads((root/'runs'/rid/'diagnostics.json').read_text())['trials'] if x['trial_id'] == tid)


def test_prelaunch_snapshots_survive_mutation_cleanup_and_move(tmp_path):
    root, raw = experiment(tmp_path)
    raw['variants'] = raw['variants'][1:]
    raw['variants'][0]['role'] = 'baseline'
    raw['variants'][0].pop('hypothesis')
    raw['variants'][0].pop('parent')
    source = root/'input.txt'; source.write_text('original input')
    raw['cases'][0]['inputs'] = ['input.txt']
    script = root/'athlete.py'
    script.write_text('import os\nfrom pathlib import Path\np=Path(os.environ["AGENTLAB_PROGRAM_ROOT"])/"SKILL.md"\np.write_text("changed artifact")\nPath("'+str(source)+'").write_text("changed input")\nprint("executed")\n')
    raw['matrix']['cells'][0]['command'] = [sys.executable, str(script)]
    # Modifying a declared live input makes the current records stale; the
    # archived pre-launch input must still remain available for investigation.
    assert run(root, raw) == 1
    rid = latest_run_id(root)
    orig = original(root, rid)
    assert json.loads((orig/'outputs/runner/execution.json').read_text())['exit_code'] == 0
    assert (orig/'inputs/variant/SKILL.md').read_text() == '# treatment\n'
    assert (orig/'inputs/declared/0').read_text() == 'original input'
    assert (orig/'outputs/program/SKILL.md').read_text() == 'changed artifact'
    scripts = list((orig/'inputs/commands').glob('*/athlete.py'))
    assert len(scripts) == 1
    assert scripts[0].read_text() == script.read_text()
    before = (root/'runs'/rid/'execution.html').read_text()
    source.unlink(); script.unlink()
    assert main(['cleanup', '--exp', str(root)]) == 0
    assert main(['report', '--exp', str(root), '--run', rid]) == 0
    assert (root/'runs'/rid/'execution.html').read_text() == before
    moved = root.with_name('moved'); root.rename(moved)
    assert main(['report', '--exp', str(moved), '--run', rid]) == 0
    assert original(moved, rid).joinpath('inputs/variant/SKILL.md').read_text() == '# treatment\n'


def test_trace_is_frozen_before_evaluator_mutation_and_reused(tmp_path):
    root, raw = experiment(tmp_path)
    raw['trace'] = {'files': ['trace/*.jsonl', 'missing.jsonl']}
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; out=Path(os.environ["AGENTLAB_TRIAL_OUT"]); (out/"trace").mkdir(); (out/"trace/events.jsonl").write_text(\'{"type":"tool_call","tool":"search","result":"<script>bad()</script>"}\\n\'); print("done")')
    raw['concerns'][0].update(measure={'type':'script','command':python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"trace/events.jsonl").write_text("evaluator replaced")')}, **{'pass':{'op':'==','value':True}})
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    orig = original(root, rid)
    assert 'tool_call' in (orig/'trace/files/trace/events.jsonl').read_text()
    page = (root/'runs'/rid/'execution.html').read_text()
    assert '&lt;script&gt;bad()&lt;/script&gt;' in page and '<script>bad()' not in page
    assert '部分声明的轨迹文件缺失' in page
    assert 'evaluator replaced' not in page
    assert run(root, raw) == 0
    second = latest_run_id(root)
    assert original(root, second) == orig
    assert manifest(root)['ran'] == []
    assert 'tool_call' in (root/'runs'/second/'execution.html').read_text()


def test_pairing_directions_and_declared_context_mismatch(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    item = diagnosis(root, rid)
    assert item['baseline_trial'].startswith('baseline__')
    assert item['comparable_declared_context'] is True
    assert item['causes'] == []
    assert item['observations'][0]['kind'] == 'weaker_measurement'
    assert item['observations'][0]['higher_is_better'] is False
    # Reading an old record must use the archived rule, not live YAML.
    raw['concerns'][0]['measure']['higher_is_better'] = True
    save(root, raw)
    assert run(root, raw) == 0
    assert manifest(root)['ran'] == []
    assert manifest(root)['rescored'] == []
    assert not any(x['kind'] == 'weaker_measurement' for x in diagnosis(root, latest_run_id(root))['observations'])
    write_execution_audit(root, rid)
    assert diagnosis(root, rid)['observations'] == item['observations']
    orig = original(root, rid)
    path = orig/'inputs/manifest.json'
    data = json.loads(path.read_text()); data['comparison_context'] = 'different'; path.write_text(json.dumps(data))
    write_execution_audit(root, rid)
    changed = diagnosis(root, rid)
    assert changed['comparable_declared_context'] is False
    assert not any(x['kind'] == 'weaker_measurement' for x in changed['observations'])


def test_no_direction_no_regression_and_no_borrowed_baseline(tmp_path):
    root, raw = experiment(tmp_path)
    raw['concerns'][0]['role'] = 'metric'
    raw['concerns'][0].pop('pass')
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    assert diagnosis(root, rid)['observations'] == []
    assert run(root, raw, '--only-variant', 'treatment') == 0
    second = latest_run_id(root)
    assert diagnosis(root, second)['baseline_trial'] is None
    assert any('本轮没有' in gap for gap in diagnosis(root, second)['evidence_gaps'])


def test_failed_execution_retains_partial_trace_and_command(tmp_path):
    root, raw = experiment(tmp_path)
    raw['trace'] = {'files':['events.jsonl']}
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"events.jsonl").write_text("partial trace"); raise SystemExit(4)')
    raw['matrix']['cells'][0]['prompt'] = {'mode':'argv'}
    assert run(root, raw) == 1
    rid = latest_run_id(root)
    orig = original(root, rid)
    assert (orig/'trace/files/events.jsonl').read_text() == 'partial trace'
    execution = json.loads((orig/'outputs/runner/execution.json').read_text())
    assert execution['exit_code'] == 4
    assert execution['command'][-1] == (orig/'inputs/prompt.md').read_text()
    assert diagnosis(root, rid)['observations'][0]['kind'] == 'execution_problem'


def test_legacy_inputs_are_not_reconstructed_from_live_skill(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    orig = original(root, rid)
    shutil.rmtree(orig/'inputs')
    (root/'variants/treatment/SKILL.md').write_text('LIVE_SKILL_NOT_HISTORY')
    page = write_execution_audit(root, rid).read_text()
    assert 'LIVE_SKILL_NOT_HISTORY' not in page
    assert diagnosis(root, rid)['comparable_declared_context'] is None
    assert '运行前输入快照缺失或不完整' in page


def test_snapshot_does_not_recursively_copy_its_archive(tmp_path):
    from agentlab.execution_audit import capture_inputs
    from agentlab.models import Trial
    from agentlab.schema import Experiment
    root, raw = experiment(tmp_path)
    raw['cases'][0]['inputs'] = ['.']
    exp = Experiment.model_validate(raw)
    trial = Trial(id=TID, variant=exp.variants[1], cell=exp.matrix.cells[0], case=exp.cases[0],
                  repeat=1, contract_hash='test', experiment_root=root, execution_id='test:'+TID)
    capture_inputs(trial, exp, root/'variants/treatment', root/'cases/smoke/prompt.md')
    saved = json.loads((execution_path(root, trial.execution_id)/'inputs/manifest.json').read_text())
    assert any('archive' in item['reason'] for item in saved['missing'])


def test_script_false_is_a_single_value_miss_not_an_execution_failure(tmp_path):
    root, raw = experiment(tmp_path)
    raw['concerns'][0].update(measure={'type':'script','command':['false']}, **{'pass':{'op':'==','value':True}})
    assert run(root, raw) == 1
    kinds = [x['kind'] for x in diagnosis(root, latest_run_id(root))['observations']]
    assert 'single_value_rule_miss' in kinds
    assert 'execution_problem' not in kinds


@pytest.mark.parametrize('pattern', ['/tmp/*', '../trace', 'trace/../../session', '${HOME}/events'])
def test_trace_patterns_cannot_collect_host_sessions(pattern):
    with pytest.raises(ValidationError):
        TraceSpec(files=[pattern])
