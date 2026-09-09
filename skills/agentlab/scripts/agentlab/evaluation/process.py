from __future__ import annotations

import os
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.models import Usage
from agentlab.records.provenance import atomic_json
from agentlab.records.reader import read_usage
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
    error = None
    with tracker.watch('judge', lambda: usage) if tracker else nullcontext():
        deadline = tracker.trial_deadline('judge') if tracker else None
        if deadline is not None:
            timeout_s = min(timeout_s, max(0.01, deadline - time.time()))
        try:
            completed = run_process(command, cwd, env, timeout_s, stdin, getattr(tracker, "cancel_event", None), log_dir=view)
            return completed
        except BaseException as exc:
            error = str(exc)
            raise
        finally:
            if usage_path.is_file() and usage_path.stat().st_mtime_ns != previous_usage:
                usage = read_usage(usage_path)
            atomic_json(view / 'execution.json', {
                'command': command, 'wall_clock_s': time.time() - started,
                'cwd': str(cwd), 'prompt_mode': spec.prompt.mode, 'timeout_s': timeout_s,
                'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
                'inherit_host_identity': spec.inherit_host_identity, 'error': error,
                'input_scope': 'AgentLab command input; CLI-added system instructions and history are not captured',
                'usage': vars(usage), 'exit_code': completed.returncode if completed else None, 'model_verification': 'unverified; command records requested configuration',
            })


def run_process(argv, cwd, env, timeout_s, stdin=None, cancel_event=None, *, log_dir: Path | None = None):
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        for name in ('stdout.log', 'stderr.log'):
            (log_dir / name).write_bytes(b'')
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **start_session_kwargs())
    stdout = stderr = b''
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
            except subprocess.TimeoutExpired as exc:
                first = False
                # communicate retains cumulative output, including descendants' pipes.
                stdout, stderr = exc.output or stdout, exc.stderr or stderr
    except BaseException:
        kill_process_group(proc.pid, grace_s=0.2)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.output or stdout, exc.stderr or stderr
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        raise
    finally:
        if log_dir is not None:
            (log_dir / 'stdout.log').write_bytes(stdout or b'')
            (log_dir / 'stderr.log').write_bytes(stderr or b'')
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
