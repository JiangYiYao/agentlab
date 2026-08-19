from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from agentlab.adapters.isolation.process import kill_process_group, start_session_kwargs
from agentlab.envfail import classify_env_error
from agentlab.errors import ContractError
from agentlab.models import RunnerResult, Trial, Usage
from agentlab.recipes import bound_command
from agentlab.schema import Experiment
from agentlab.templates import build_context, expand_templates, resolve_argv


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
        try:
            with stdout_path.open("wb") as so, stderr_path.open("wb") as se:
                self._proc = subprocess.Popen(
                    final_argv,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=so,
                    stderr=se,
                    **start_session_kwargs(),
                )
                if stdin_data is not None and self._proc.stdin is not None:
                    self._proc.stdin.write(stdin_data.encode())
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
                while True:
                    try:
                        self._proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        if deadline is not None and time.time() >= deadline:
                            _stop(self._proc)
                            killed = "timeout"
                            error_code = "command_timeout"
                            break
                        hit = _env_hit(stdout_path, stderr_path)
                        if hit:
                            _stop(self._proc)
                            killed = hit
                            error_code = "env_unusable"
                            break
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
        wall = time.time() - started
        code = self._proc.returncode if self._proc is not None else 127
        if code is None:
            code = 1
        if code != 0 and error_code is None:
            error_code = "command_nonzero"
        usage = _read_usage(out / "usage.json")
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
    flag = prompt.flag or "--prompt-file"
    return argv, mode, flag


def _read_usage(path: Path) -> Usage:
    if not path.is_file():
        return Usage()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Usage()
    tokens_in = data.get("tokens_in")
    tokens_out = data.get("tokens_out")
    usd = data.get("usd")
    return Usage(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usd=usd,
        tokens_unknown=tokens_in is None and tokens_out is None,
        usd_unknown=usd is None,
    )
