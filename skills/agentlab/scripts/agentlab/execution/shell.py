from __future__ import annotations

from contextlib import ExitStack
import subprocess
import time
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.execution.envfail import classify_env_error, env_stall_s
from agentlab.models import RunnerResult, Trial, Usage
from agentlab.recipes import bound_command
from agentlab.schema import Experiment
from agentlab.templates import expand_templates, resolve_argv
from agentlab.records.provenance import atomic_json
from agentlab.records.reader import read_usage


class ShellRunner:
    def __init__(self, exp: Experiment) -> None:
        self.exp = exp
        self._proc: subprocess.Popen | None = None

    def prepare(self, trial: Trial, ctx: dict[str, str]) -> Path:
        out = trial.outputs_dir()
        runner_dir = out / "runner"
        runner_dir.mkdir(parents=True, exist_ok=True)
        case_dir = trial.experiment_root / (trial.case.path or f"cases/{trial.case.id}")
        src = case_dir / trial.case.prompt_file
        text = src.read_text(encoding="utf-8") if src.is_file() else ""
        rendered = expand_templates(text, ctx) if text else ""
        prompt_path = out / "prompt.md"
        prompt_path.write_text(rendered, encoding="utf-8")
        (runner_dir / "prompt.md").write_text(rendered, encoding="utf-8")
        return prompt_path

    def run(
        self,
        trial: Trial,
        *,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        prompt_path: Path,
        deadline: float | None,
        prompt_mode: str = "stdin",
        prompt_flag: str = "--prompt-file",
        on_start=None,
        cancel_event=None,
    ) -> RunnerResult:
        out = trial.outputs_dir()
        out.mkdir(parents=True, exist_ok=True)
        stdout_path = out / "stdout.log"
        stderr_path = out / "stderr.log"
        prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
        final_argv = list(argv)
        stdin_data = None
        if prompt_mode == "stdin":
            stdin_data = prompt_text
        elif prompt_mode == "argv":
            final_argv.append(prompt_text)
        elif prompt_mode == "file":
            final_argv.extend([prompt_flag, str(prompt_path.resolve())])
        started = time.time()
        error_code = None
        killed = None
        invocation = {'command': final_argv, 'cwd': str(cwd), 'prompt_mode': prompt_mode,
                      'started_at': started, 'requested_model': trial.cell.model,
                      'verification': 'Command request only; external CLI context and actual model are not verified.'}
        atomic_json(out / 'runner' / 'execution.json', {**invocation, 'phase': 'starting'})
        try:
            with ExitStack() as stack, stdout_path.open("wb") as so, stderr_path.open("wb") as se:
                self._proc = subprocess.Popen(
                    final_argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=stack.enter_context(prompt_path.open("rb")) if stdin_data is not None else subprocess.DEVNULL,
                    stdout=so,
                    stderr=se,
                    **start_session_kwargs(),
                )
                if on_start and self._proc.pid:
                    on_start(self._proc.pid)
                stall_s = env_stall_s()
                suspect_at: float | None = None
                suspect_reason: str | None = None
                last_size = 0
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        _stop(self._proc)
                        killed = "cancelled"
                        error_code = "cancelled"
                        break
                    try:
                        self._proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        if deadline is not None and time.time() >= deadline:
                            _stop(self._proc)
                            killed = "timeout"
                            error_code = "command_timeout"
                            break
                        size = _log_size(stdout_path, stderr_path)
                        hit = _env_hit(stdout_path, stderr_path)
                        if hit:
                            if suspect_reason != hit or size > last_size:
                                suspect_at = time.time()
                                suspect_reason = hit
                                last_size = size
                            elif suspect_at is not None and time.time() - suspect_at >= stall_s:
                                _stop(self._proc)
                                killed = hit
                                error_code = "env_unusable"
                                break
                        else:
                            suspect_at = None
                            suspect_reason = None
                            last_size = size
        except FileNotFoundError:
            error_code = "bin_not_found"
            killed = None
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("bin not found\n", encoding="utf-8")
            return RunnerResult(
                exit_code=127,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                usage=Usage(),
                wall_clock_s=time.time() - started,
                killed_reason=killed,
                error_code=error_code,
            )
        except BaseException:
            if self._proc is not None:
                _stop(self._proc)
            raise
        finally:
            atomic_json(out / 'runner' / 'execution.json', {
                **invocation, 'phase': 'finished', 'wall_clock_s': time.time() - started,
                'exit_code': self._proc.returncode if self._proc is not None else None,
                'error_code': error_code, 'killed_reason': killed,
            })
        wall = time.time() - started
        code = self._proc.returncode if self._proc is not None else 127
        if code is None:
            code = 1
        if error_code is None and int(code) != 0:
            hit = _env_hit(stdout_path, stderr_path)
            if hit:
                error_code = "env_unusable"
                killed = hit
            else:
                error_code = "command_nonzero"
        usage = read_usage(out / "usage.json")
        return RunnerResult(
            exit_code=int(code),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            usage=usage,
            wall_clock_s=wall,
            killed_reason=killed,
            error_code=error_code,
        )

    def kill(self) -> None:
        if self._proc and self._proc.pid:
            kill_process_group(self._proc.pid)


def _stop(proc: subprocess.Popen) -> None:
    if proc.pid:
        kill_process_group(proc.pid, grace_s=1.0)
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    _wait_dead(proc, timeout=2.0)
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        _wait_dead(proc, timeout=3.0)


def _wait_dead(proc: subprocess.Popen, *, timeout: float = 15.0) -> None:
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return


def _log_size(stdout_path: Path, stderr_path: Path) -> int:
    total = 0
    for path in (stdout_path, stderr_path):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _env_hit(stdout_path: Path, stderr_path: Path) -> str | None:
    chunks: list[str] = []
    for path in (stderr_path, stdout_path):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return classify_env_error("\n".join(chunks))


def athlete_argv(exp: Experiment, trial: Trial, ctx: dict[str, str]) -> tuple[list[str], str, str]:
    raw, recipe = bound_command(exp, trial.cell, trial.case, trial.experiment_root)
    argv = resolve_argv(raw, trial.experiment_root, ctx)
    prompt = trial.cell.prompt or (recipe.prompt if recipe else None)
    mode = prompt.mode if prompt else "stdin"
    flag = (prompt.flag if prompt else None) or "--prompt-file"
    return argv, mode, flag
