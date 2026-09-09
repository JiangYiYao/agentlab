from __future__ import annotations

import os
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.models import Usage
from agentlab.provenance import atomic_json
from agentlab.runner.shell import _read_usage
from agentlab.schema import IDENTITY_ENV


def judge_command(spec, argv, cwd: Path, view: Path, prompt: str, timeout_s: float, tracker=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('AGENTLAB_') and k not in {'GIT_DIR', 'GIT_WORK_TREE'}}
    if not spec.inherit_host_identity:
        for key in IDENTITY_ENV:
            env.pop(key, None)
        home = view / '.home'
        home.mkdir(exist_ok=True)
        env['HOME'] = str(home)
    prompt_path = view / 'stdin.md'
    prompt_path.write_text(prompt, encoding='utf-8')
    command = list(argv)
    stdin = prompt.encode()
    if spec.prompt.mode == 'argv':
        command.append(prompt)
        stdin = None
    elif spec.prompt.mode == 'file':
        command.extend([spec.prompt.flag or '--prompt-file', str(prompt_path)])
        stdin = None
    usage = Usage()
    usage_path = cwd / 'usage.json'
    previous_usage = usage_path.stat().st_mtime_ns if usage_path.is_file() else None
    completed = None
    started = time.time()
    with tracker.watch('judge', lambda: usage) if tracker else nullcontext():
        deadline = tracker.trial_deadline('judge') if tracker else None
        if deadline is not None:
            timeout_s = min(timeout_s, max(0.01, deadline - time.time()))
        try:
            completed = run_process(command, cwd, env, timeout_s, stdin, getattr(tracker, "cancel_event", None))
            return completed
        finally:
            if usage_path.is_file() and usage_path.stat().st_mtime_ns != previous_usage:
                usage = _read_usage(usage_path)
            atomic_json(view / 'execution.json', {
                'command': command, 'wall_clock_s': time.time() - started,
                'usage': vars(usage), 'exit_code': completed.returncode if completed else None, 'model_verification': 'unverified; command records requested configuration',
            })


def run_process(argv, cwd, env, timeout_s, stdin=None, cancel_event=None):
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **start_session_kwargs())
    try:
        deadline = time.monotonic() + timeout_s
        first = True
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("evaluation cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout_s)
            try:
                stdout, stderr = proc.communicate(input=stdin if first else None, timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                first = False
    except BaseException:
        kill_process_group(proc.pid, grace_s=0.2)
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
