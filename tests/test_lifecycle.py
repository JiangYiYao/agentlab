from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agentlab.cli import main
from agentlab.records.runs import latest_run_id, load_manifest
from tests.helpers import make_min_exp


def experiment(tmp_path):
    root = make_min_exp(tmp_path / 'exp')
    raw = yaml.safe_load((root / 'experiment.yaml').read_text())
    raw['concerns'] = [{'id': 'size', 'intent': 'bounded output', 'role': 'gate', 'scope': 'case',
                        'measure': {'type': 'static_size'}, 'pass': {'op': '<', 'value': 1000}}]
    return root, raw


def save(root, raw):
    (root / 'experiment.yaml').write_text(yaml.safe_dump(raw, sort_keys=False))


def run(root, raw, *args):
    save(root, raw)
    return main(['run', '--exp', str(root), '--gate', *args])


def manifest(root):
    return load_manifest(root, latest_run_id(root))


def scores(root, variant='treatment'):
    return json.loads((root / 'trials' / f'{variant}__local-cli__smoke__r1' / 'scores.json').read_text())


def python_command(source):
    return [sys.executable, '-c', source]


def test_input_changes_invalidate_only_affected_execution(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    (root / 'variants/treatment/SKILL.md').write_text('x' * 2000)
    assert run(root, raw) == 1
    assert len(manifest(root)['ran']) == 1
    assert len(manifest(root)['reused']) == 1
    assert scores(root)[0]['value'] == 2000
    (root / 'cases/smoke/prompt.md').write_text('changed task')
    assert run(root, raw) == 1
    assert len(manifest(root)['ran']) == 2


def test_threshold_change_only_redecides(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    raw['concerns'][0]['pass']['value'] = 1
    assert run(root, raw) == 1
    assert manifest(root)['ran'] == []
    assert len(manifest(root)['reused']) == 2
    assert manifest(root)['rescored'] == []


def test_no_stale_outputs_after_rerun(tmp_path):
    root, raw = experiment(tmp_path)
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"score.json").write_text(\'{"score": true}\')')
    raw['concerns'][0].update(measure={'type': 'script', 'command': ['true'], 'output_json': 'outputs/score.json'}, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 0
    old = latest_run_id(root)
    raw['matrix']['cells'][0]['command'] = ['true']
    assert run(root, raw, '--no-reuse') == 1
    assert scores(root)[0]['unknown']
    assert (root/'runs'/old/'trials/treatment__local-cli__smoke__r1/outputs/score.json').is_file()


@pytest.mark.parametrize('baseline_only', [False, True])
def test_nonzero_execution_always_blocks_gate(tmp_path, baseline_only):
    root, raw = experiment(tmp_path)
    raw['matrix']['cells'][0]['command'] = ['false']
    raw['cases'][0]['require_exit_0'] = True
    raw['concerns'][0]['role'] = 'objective'
    raw['concerns'][0].pop('pass')
    if baseline_only:
        raw['variants'] = raw['variants'][:1]
    assert run(root, raw) == 1


def test_baseline_gate_is_enforced(tmp_path):
    root, raw = experiment(tmp_path)
    raw['variants'] = raw['variants'][:1]
    raw['concerns'][0]['pass']['value'] = 1
    assert run(root, raw) == 1


def test_script_exit_code_and_json_failure_are_serializable(tmp_path):
    root, raw = experiment(tmp_path)
    raw['concerns'][0].update(measure={'type': 'script', 'command': ['false']}, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) == 1
    assert scores(root)[0]['value'] is False
    raw['concerns'][0]['measure']['result'] = 'json'
    assert run(root, raw) == 1
    assert scores(root)[0]['unknown']
    assert isinstance(scores(root)[0]['evidence']['stderr'], str)


def test_missing_required_input_is_not_a_pass(tmp_path):
    root, raw = experiment(tmp_path)
    raw['concerns'][0].update(measure={'type': 'must_list', 'keep': 'missing.txt'}, **{'pass': {'op': '==', 'value': True}})
    assert run(root, raw) != 0


@pytest.mark.parametrize('parallel', [1, 4])
def test_money_reserved_before_dispatch(tmp_path, parallel):
    root, raw = experiment(tmp_path)
    raw['budget'].update(usd=1, max_parallel=parallel)
    raw['budget']['per_trial']['usd'] = 1
    raw['matrix']['cells'][0]['command'] = python_command('import os,time; from pathlib import Path; time.sleep(.05); (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"usage.json").write_text(\'{"usd":1}\')')
    assert run(root, raw) == 3
    assert len(manifest(root)['ran']) == 1
    assert manifest(root)['usage']['usd'] == 1


def test_default_prompt_and_case_timeout(tmp_path):
    root, raw = experiment(tmp_path)
    raw['matrix']['cells'][0].pop('prompt')
    assert run(root, raw) == 0
    raw['cases'][0]['timeout_s'] = 1
    raw['matrix']['cells'][0]['command'] = python_command('import time; time.sleep(10)')
    assert run(root, raw) == 1
    assert all('command_timeout' == json.loads(p.read_text())['error_code'] for p in (root/'trials').glob('*/meta.json'))


def test_historical_report_uses_snapshot_without_live_inputs(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    old = latest_run_id(root)
    original = (root/'runs'/old/'report.md').read_text()
    raw['concerns'][0]['intent'] = 'a different criterion'
    raw['concerns'][0]['pass']['value'] = 1
    save(root, raw)
    (root/'criteria.md').unlink()
    assert main(['report', '--exp', str(root), '--run', old]) == 0
    assert (root/'runs'/old/'report.md').read_text() == original


def add_judge(root, raw, mode='per_trial'):
    judge = root / 'judge.py'
    judge.write_text('import json,sys\nfrom pathlib import Path\ntext=sys.stdin.read()\nPath(sys.argv[1]).open("a").write("call\\n")\nprint(json.dumps({"value": float(sys.argv[2]), "scores":{"A":{"quality":float(sys.argv[2])},"B":{"quality":float(sys.argv[2])}}}))\n')
    raw['judge'] = {'command': [sys.executable, str(judge), str(root/'judge-calls'), '8'], 'mode': mode}
    raw['concerns'].append({'id':'quality', 'role':'objective', 'intent':'answer quality', 'measure':{'type':'llm_rubric'}, 'pass':{'op':'>=', 'value':5}})
    return judge


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
def test_changing_judge_reuses_execution_and_preserves_old_scores(tmp_path, mode):
    root, raw = experiment(tmp_path)
    add_judge(root, raw, mode)
    assert run(root, raw) == 0
    old = latest_run_id(root)
    previous = (root/'runs'/old/'trials/treatment__local-cli__smoke__r1/scores.json').read_text()
    raw['judge']['command'][-1] = '3'
    assert run(root, raw) == 1
    assert len(manifest(root)['reused']) == 2
    assert manifest(root)['ran'] == []
    assert next(s for s in scores(root) if s['concern_id']=='quality')['value'] == 3
    assert (root/'runs'/old/'trials/treatment__local-cli__smoke__r1/scores.json').read_text() == previous
    calls = (root/'judge-calls').read_text()
    assert run(root, raw) == 1
    assert (root/'judge-calls').read_text() == calls


def test_rescore_never_runs_changed_tested_program(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    raw['matrix']['cells'][0]['command'] = python_command(f'from pathlib import Path; Path({str(root/"must-not-run")!r}).touch()')
    save(root, raw)
    assert main(['rescore', '--exp', str(root)]) != 0
    assert not (root/'must-not-run').exists()


def test_stdout_and_declared_json_reach_judge(tmp_path):
    root, raw = experiment(tmp_path)
    raw['evidence'] = {'files':['answer.json']}
    raw['matrix']['cells'][0]['command'] = python_command('import os; from pathlib import Path; print("UNIQUE_ANSWER"); (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text("42")')
    judge = add_judge(root, raw)
    judge.write_text('import json,sys\nfrom pathlib import Path\nsys.stdin.read()\np=Path.cwd().parent/"evidence" if Path.cwd().name=="workspace" else Path.cwd()/"evidence"\nprint(json.dumps({"value": 8 if "UNIQUE_ANSWER" in (p/"stdout.log").read_text() and (p/"files/answer.json").read_text()=="42" else 0}))\n')
    assert run(root, raw) == 0


def test_judge_nonzero_rejects_valid_json(tmp_path):
    root, raw = experiment(tmp_path)
    judge=add_judge(root,raw)
    judge.write_text('print(\'{"value":10}\'); raise SystemExit(7)')
    assert run(root, raw) == 1
    assert next(s for s in scores(root) if s['concern_id']=='quality')['unknown']


def test_worktree_retry_uses_a_fresh_workspace(tmp_path):
    root, raw = experiment(tmp_path)
    repo=tmp_path/'repo'; repo.mkdir(); (repo/'a').write_text('original')
    for command in (['git','init','-q'],['git','add','.'],['git','-c','user.name=test','-c','user.email=test@local','commit','-qm','initial']):
        subprocess.run(command,cwd=repo,check=True)
    raw['isolation'].update(type='git-worktree',repo=str(repo))
    raw['matrix']['cells'][0]['command']=['false']
    assert run(root,raw)==1
    old=latest_run_id(root)
    assert run(root,raw,'--retry-failed')==1
    assert len(manifest(root)['ran'])==2
    assert (root/'runs'/old/'workspaces/treatment__local-cli__smoke__r1').is_dir()
    assert subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True)==''
    assert main(['cleanup','--exp',str(root)])==0


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
def test_rescore_from_selected_archive(tmp_path, mode):
    root,raw=experiment(tmp_path)
    add_judge(root,raw,mode)
    assert run(root,raw)==0
    old=latest_run_id(root)
    raw['judge']['command'][-1]='9'
    save(root,raw)
    assert main(['rescore','--exp',str(root),'--run',old])==0
    assert manifest(root)['ran']==[]
    assert len(manifest(root)['reused'])==2
    assert next(s for s in scores(root) if s['concern_id']=='quality')['value']==9


def test_judge_custom_criteria_file_prompt_and_identity(tmp_path):
    root,raw=experiment(tmp_path)
    judge=add_judge(root,raw)
    (root/'criteria.md').rename(root/'custom.md')
    raw['criteria']['path']='custom.md'
    raw['judge']['prompt']={'mode':'file','flag':'--task'}
    raw['judge']['inherit_host_identity']=False
    judge.write_text('import json,os,sys\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index("--task")+1])\nok="Fixture criteria" in p.read_text() and Path(os.environ["HOME"]).name==".home" and "CODEX_HOME" not in os.environ\nprint(json.dumps({"value":8 if ok else 0}))\n')
    assert run(root,raw)==0


def test_script_environment_is_applied(tmp_path):
    root,raw=experiment(tmp_path)
    raw['concerns'][0].update(measure={'type':'script','env':{'EXPECTED':'42'},'command':python_command('import os; assert os.environ["EXPECTED"]=="42"')}, **{'pass':{'op':'==','value':True}})
    assert run(root,raw)==0


def test_protected_source_change_is_reported_even_when_command_fails(tmp_path):
    root,raw=experiment(tmp_path)
    source=tmp_path/'source'; source.mkdir(); (source/'original').write_text('before')
    raw['artifact']['source_path']=str(source)
    raw['matrix']['cells'][0]['command']=python_command(f'from pathlib import Path; Path({str(source/"original")!r}).write_text("after"); raise SystemExit(1)')
    assert run(root,raw)==1
    first=json.loads((root/'trials/baseline__local-cli__smoke__r1/meta.json').read_text())
    assert first['error_code']=='isolation_leak'
    assert (source/'original').read_text()=='after'


def test_nested_repository_runs_are_frozen_and_isolated(tmp_path):
    root,raw=experiment(tmp_path)
    repos=[]
    for name in ['parent','nested']:
        repo=tmp_path/name; repo.mkdir(); (repo/'a.txt').write_text('before')
        for cmd in [['git','init','-q'],['git','add','.'],['git','-c','user.name=test','-c','user.email=test@local','commit','-qm','initial']]:
            subprocess.run(cmd,cwd=repo,check=True)
        repos.append(repo)
    raw['isolation'].update(type='git-worktree',repo=str(repos[0]),nested_repos=[{'path':'modules/app','source':str(repos[1]),'freeze':'HEAD'}])
    raw['matrix']['cells'][0]['command']=python_command('from pathlib import Path; p=Path("modules/app/a.txt"); assert p.read_text()=="before"; p.write_text("after")')
    assert run(root,raw)==0
    frozen=manifest(root)['experiment']['isolation']
    assert len(frozen['freeze'])==40
    assert len(frozen['nested_repos'][0]['freeze'])==40
    for repo in repos:
        assert (repo/'a.txt').read_text()=='before'
        assert subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True)==''


def test_large_stdin_does_not_bypass_timeout(tmp_path):
    root,raw=experiment(tmp_path)
    raw['variants']=raw['variants'][:1]
    (root/'cases/smoke/prompt.md').write_text('x'*1024*1024)
    raw['cases'][0]['timeout_s']=1
    raw['matrix']['cells'][0]['command']=python_command('import time; time.sleep(10)')
    import time
    started=time.monotonic()
    assert run(root,raw)==1
    assert time.monotonic()-started < 5


def test_unknown_measure_field_is_rejected_before_execution(tmp_path):
    root,raw=experiment(tmp_path)
    raw['concerns'][0]['measure']['outpt_json']='typo.json'
    save(root,raw)
    assert main(['brief','--exp',str(root)])==2
    assert not (root/'runs').exists()


def test_judge_reservations_share_experiment_budget(tmp_path):
    root,raw=experiment(tmp_path)
    add_judge(root,raw,'compare_case')
    raw['budget']['usd']=2
    raw['budget']['per_trial']['usd']=1
    raw['budget']['per_judge']={'usd':1}
    assert run(root,raw)==3
    assert not (root/'judge-calls').exists()
    assert manifest(root)['usage']['usd']==2


def test_raw_script_changes_only_rerun_measurements(tmp_path):
    root,raw=experiment(tmp_path)
    check=root/'check.py'; check.write_text('raise SystemExit(0)')
    raw['concerns'][0].update(measure={'type':'script','command':[sys.executable,str(check)]}, **{'pass':{'op':'==','value':True}})
    assert run(root,raw)==0
    check.write_text('raise SystemExit(1)')
    assert run(root,raw)==1
    assert manifest(root)['ran']==[]
    assert len(manifest(root)['rescored'])==2


def test_legacy_archive_remains_readable_without_reinterpreting_decision(tmp_path):
    root,raw=experiment(tmp_path)
    assert run(root,raw)==0
    ident=latest_run_id(root)
    path=root/'runs'/ident/'manifest.json'
    old=json.loads(path.read_text()); old.pop('experiment'); path.write_text(json.dumps(old))
    raw['concerns'][0]['pass']['value']=1
    save(root,raw)
    assert main(['report','--exp',str(root),'--run',ident])==0
    report=(root/'report.md').read_text()
    assert 'scored: 2' in report
    assert '旧运行没有契约快照' in report
    assert 'recommend_ship=True' in report
