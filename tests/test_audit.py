import hashlib
import json
import os
import re
import subprocess
import sys
from urllib.parse import unquote

import pytest

from agentlab.audit import Audit, PREVIEW_BYTES, score_anchor, write_audit
from agentlab.cli import main
from agentlab.report import write_report
from agentlab.runner.evaluation import run_process
from agentlab.runs import latest_run_id
from agentlab.schema import Experiment
from tests.test_lifecycle import add_judge, experiment, manifest, python_command, run

TID = 'treatment__local-cli__smoke__r1'


def event(root, run_id, cid):
    return json.loads((root/'runs'/run_id/'trials'/TID/'meta.json').read_text())['evaluation_events'][cid]


@pytest.mark.parametrize('mode', ['per_trial', 'compare_case'])
def test_audit_provenance_and_reassessment_diff(tmp_path, mode):
    root, raw = experiment(tmp_path)
    add_judge(root, raw, mode)
    (root/'cases/smoke/prompt.md').write_text('Explain <script>alert("TASK")</script>')
    assert run(root, raw) == 0
    first = latest_run_id(root)
    assert event(root, first, 'quality')['status'] == 'evaluated'
    original = (root/'runs'/first/'audit.html').read_text()
    assert '&lt;script&gt;alert(' in original
    assert '<script>alert(' not in original
    assert '完整裁判输入' in original
    assert '未被独立核实' in original
    assert (('匿名答卷与版本对应关系' in original) == (mode == 'compare_case'))

    raw['concerns'][0]['pass']['value'] = 500
    assert run(root, raw) == 0
    second = latest_run_id(root)
    assert event(root, second, 'quality') == {**event(root, first, 'quality'), 'status': 'reused'}
    assert event(root, second, 'size')['status'] == 'reused'
    raw['judge']['command'][-1] = '3'
    (root/'criteria.md').write_text('# Revised standard\nRequire concrete evidence')
    raw['criteria']['sha256'] = hashlib.sha256((root/'criteria.md').read_bytes()).hexdigest()
    assert run(root, raw) == 1
    third = latest_run_id(root)
    assert event(root, third, 'quality')['status'] == 'evaluated'
    assert event(root, third, 'quality')['source_run'] == third
    assert event(root, third, 'size')['status'] == 'reused'
    assert event(root, third, 'size')['source_run'] == first
    assert event(root, third, '__isolation_leak__')['status'] == 'reused'
    audit = (root/'runs'/third/'audit.html').read_text()
    assert f'--- {second}/评审标准' in audit
    assert f'+++ {third}/评审标准' in audit
    assert 'Require concrete evidence' in audit
    assert '复用旧分数' in audit
    assert '复用执行 · 本轮评分' in audit
    assert main(['cleanup', '--exp', str(root)]) == 0
    (root/'criteria.md').unlink()
    assert main(['report', '--exp', str(root), '--run', first]) == 0
    assert (root/'runs'/first/'audit.html').read_text() == original


def test_report_links_work_from_root_archive_and_custom_destination(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    custom = tmp_path/'custom report.md'
    write_report(Experiment.model_validate(manifest(root)['experiment']), root, custom, run_id=rid)
    for report in [root/'report.md', root/'runs'/rid/'report.md', custom]:
        links = re.findall(r'\[审计\]\(([^)]+)\)', report.read_text())
        assert len(links) == 6
        for url in links:
            path, anchor = url.split('#')
            target = report.parent / unquote(path)
            assert target.is_file()
            assert f'id="{anchor}"' in target.read_text()
    moved = root.with_name('moved experiment')
    root.rename(moved)
    assert main(['report', '--exp', str(moved), '--run', rid]) == 0
    assert (moved/'runs'/rid/'audit.html').is_file()


def test_legacy_evidence_missing_is_not_filled_from_current_files(tmp_path):
    root, raw = experiment(tmp_path)
    add_judge(root, raw)
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    trial = root/'runs'/rid/'trials'/TID
    meta = json.loads((trial/'meta.json').read_text())
    meta.pop('evaluation_events')
    (trial/'meta.json').write_text(json.dumps(meta))
    (trial/'outputs/judges/quality/stdin.md').unlink()
    (root/'cases/smoke/prompt.md').write_text('DO_NOT_USE_LIVE_TASK')
    page = write_audit(root, rid).read_text()
    assert '评分来源未知（旧记录或未记录）' in page
    assert '未归档，无法核实' in page
    assert 'DO_NOT_USE_LIVE_TASK' not in page


def test_script_complete_logs_and_json_result_are_archived(tmp_path):
    root, raw = experiment(tmp_path)
    command = python_command('import os,sys; from pathlib import Path; print("OUT"*1000); print("ERR"*1000,file=sys.stderr); (Path(os.environ["AGENTLAB_TRIAL_OUT"])/"answer.json").write_text(\'{"score":true,"why":"ok"}\')')
    raw['concerns'][0].update(measure={'type':'script', 'command':command, 'output_json':'outputs/answer.json'},
                              **{'pass':{'op':'==','value':True}})
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    view = root/'runs'/rid/'trials'/TID/'outputs/evaluators/size'
    assert (view/'stdout.log').read_text() == 'OUT'*1000+'\n'
    assert (view/'stderr.log').read_text() == 'ERR'*1000+'\n'
    assert json.loads((view/'execution.json').read_text())['exit_code'] == 0
    assert json.loads((view/'output.json').read_text())['why'] == 'ok'
    assert json.loads((view/'result.json').read_text())['value'] is True
    assert '脚本 JSON 输出快照' in (root/'runs'/rid/'audit.html').read_text()


@pytest.mark.parametrize('kind', ['script', 'judge'])
def test_timeout_preserves_partial_evaluator_output(tmp_path, kind):
    root, raw = experiment(tmp_path)
    raw['variants'] = raw['variants'][:1]
    cmd = python_command('import sys,time; print("partial stdout",flush=True); print("partial stderr",file=sys.stderr,flush=True); time.sleep(10)')
    if kind == 'judge':
        add_judge(root, raw)
        raw['judge'].update(command=cmd, timeout_s=1)
        folder = 'judges/quality'
    else:
        raw['concerns'][0].update(measure={'type':'script', 'command':cmd, 'timeout_s':1}, **{'pass':{'op':'==','value':True}})
        folder = 'evaluators/size'
    assert run(root, raw) == 1
    rid = latest_run_id(root)
    view = root/'runs'/rid/'trials/baseline__local-cli__smoke__r1/outputs'/folder
    assert (view/'stdout.log').read_text() == 'partial stdout\n'
    assert (view/'stderr.log').read_text() == 'partial stderr\n'
    record = json.loads((view/'execution.json').read_text())
    assert record['exit_code'] is None
    assert record['error']


def test_process_large_stdin_timeout_and_launch_failure_logs(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path, os.environ, .2,
                    b'x'*1024*1024, log_dir=tmp_path/'timeout')
    with pytest.raises(FileNotFoundError):
        run_process(['/nonexistent/agentlab-command'], tmp_path, os.environ, 1, log_dir=tmp_path/'missing')
    assert (tmp_path/'missing/stdout.log').read_bytes() == b''


def test_evaluator_waits_for_inherited_output_pipes(tmp_path):
    child = 'import time; time.sleep(.1); print("child output",flush=True)'
    parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}])'
    result = run_process([sys.executable, '-c', parent], tmp_path, os.environ, 3, log_dir=tmp_path)
    assert result.stdout == b'child output\n'
    assert (tmp_path/'stdout.log').read_bytes() == result.stdout


def test_audit_rejects_external_symlinks_and_bounds_previews(tmp_path):
    root, raw = experiment(tmp_path)
    assert run(root, raw) == 0
    rid = latest_run_id(root)
    outside = tmp_path/'outside.txt'; outside.write_text('external')
    link = root/'external.txt'; link.symlink_to(outside)
    audit = Audit(root, rid)
    assert audit.read(link) is None
    assert '<a' not in audit.link(link, 'external')
    assert '<a' not in audit.link(root/'../outside.txt', '<script>')
    large = root/'large.txt'; large.write_text('X'*(PREVIEW_BYTES+1))
    assert '预览已截断' in audit.read(large)
