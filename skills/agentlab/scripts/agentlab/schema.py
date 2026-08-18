from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentlab.errors import ContractError

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
FORBIDDEN_KEYS = {"skill_install", "harness", "rubric", "swap_order", "slices"}
IDENTITY_ENV = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}
RECIPE_ENV_ALLOW = {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}
RESERVED_CELL_ENV = IDENTITY_ENV | {
    "SCUTIO_HOME",
    "SCUTIO_PYTHON",
    "SCUTIO_TOOLKIT_SCRIPTS",
    "SCUTIO_SKILLS_DIR",
    "GIT_DIR",
    "GIT_WORK_TREE",
}


def _check_forbidden(data: dict[str, Any], path: str) -> None:
    for key in data:
        if key in FORBIDDEN_KEYS:
            raise ContractError("unknown_field", f"forbidden field {key!r}", path=path)
        if key.startswith("AGENTLAB_") and path.endswith(".env"):
            pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hypothesis(StrictModel):
    change: str
    bet: str
    hurt: str
    falsify: str


class Artifact(StrictModel):
    type: Literal["dir"] = "dir"
    name: str
    layout: Literal["sidecar", "inplace"] = "sidecar"
    source_path: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def only_dir(cls, value: Any) -> Any:
        if value is not None and value != "dir":
            raise ContractError("unsupported_artifact_type", f"artifact.type={value!r}")
        return value


class CriteriaRef(StrictModel):
    path: str = "criteria.md"
    sha256: str | None = None


class PromptSpec(StrictModel):
    mode: Literal["stdin", "argv", "file"] = "stdin"
    flag: str | None = None


class JudgeSpec(StrictModel):
    command: list[str]
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    timeout_s: int | None = 180
    inherit_host_identity: bool = True


class PassRule(StrictModel):
    op: Literal[">", "<", ">=", "<=", "==", "!="]
    vs: Literal["baseline", "value"] = "value"
    value: Any = None
    margin: float = 0.0
    min_n: int = 1


class Measure(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    command: list[str] | None = None
    cwd: Literal["experiment", "sandbox", "trial"] | None = None
    timeout_s: int | None = None
    output_json: str | None = None
    value_path: str | None = None
    env: dict[str, str] | None = None
    gold_dir: str | None = None
    compare_root: Literal["project_root"] | None = None
    ignore: list[str] | None = None
    keep: str | None = None
    gone: str | None = None
    mode: str | None = None
    allow_write: list[str] | None = None
    forbid_write: list[str] | None = None
    source: str | None = None
    labels: list[str] | None = None
    extractor: str | None = None
    pattern: dict[str, str] | str | None = None
    report_from: dict[str, Any] | None = None
    expected: Any = None
    must_include: list[str] | None = None
    needles: list[str] | None = None
    file: str | None = None
    json_path: str | None = None
    prefix_env: str | None = None
    must_suffix: str | None = None
    quantity: str | None = None
    path: str | None = None
    variant_path: str | None = None
    higher_is_better: bool | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    judge: JudgeSpec | None = None
    model: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_forbidden(data, "measure")
        return data


class Concern(StrictModel):
    id: str
    intent: str
    role: Literal["objective", "metric", "gate"]
    scope: Literal["case", "cell"] | None = None
    soft: bool = False
    measure: Measure
    pass_: PassRule | None = Field(default=None, alias="pass")
    aggregate: Literal["mean", "median", "min", "max", "all_pass"] | None = None
    judge: JudgeSpec | None = None

    @field_validator("id")
    @classmethod
    def slug_id(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ContractError("duplicate_id", f"invalid id {value!r}", path="concerns")
        return value


class Variant(StrictModel):
    id: str
    role: Literal["baseline", "treatment"]
    path: str
    hypothesis: Hypothesis | str | None = None
    parent: str | None = None
    created_by: Literal["import", "manual", "skill-hypothesis"] | None = None
    # Negative / extra treatments. Omitted from default expand and --gate
    # unless `--only-variant` names them.
    opt_in: bool = False

    @field_validator("id")
    @classmethod
    def slug_id(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ContractError("duplicate_id", f"invalid id {value!r}", path="variants")
        return value


class Cell(StrictModel):
    id: str
    model: str | None = None
    command: list[str] | None = None
    recipe: str | None = None
    prompt: PromptSpec | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    inherit_host_identity: bool | None = None

    @field_validator("id")
    @classmethod
    def slug_id(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ContractError("duplicate_id", f"invalid id {value!r}", path="matrix.cells")
        return value

    @field_validator("env")
    @classmethod
    def no_reserved(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return value
        for key in value:
            if key in RESERVED_CELL_ENV or key.startswith("AGENTLAB_"):
                raise ContractError("reserved_env_key", f"cell.env cannot set {key}", path="matrix.cells.env")
        return value


class Matrix(StrictModel):
    cells: list[Cell]
    cell_rule: dict[str, Any] | None = None
    all_cells_must_pass: bool | None = None


class ReplayLock(StrictModel):
    code: str
    name: str
    as_of: str
    horizon: str
    market: str | None = None


class Replay(StrictModel):
    mode: Literal["snapshot_replay"]
    lock: ReplayLock
    clock: str | None = None
    skip_live_network: bool = False
    news: Literal["cassette", "omit", "live"] | None = None
    cassette_path: str | None = None


class Fixtures(StrictModel):
    repo: str | None = None
    freeze: str | None = None
    subdir: str | None = None
    gold: str | None = None
    must_gone: str | None = None
    must_keep: str | None = None
    snapshot: str | None = None


class Isolation(StrictModel):
    type: Literal["git-worktree", "homedir", "tempdir"]
    subdir: str = "."
    inherit_host_identity: bool = True
    repo: str | None = None
    freeze: str | None = None
    worktree_add_serial: bool = True
    keep_sandbox: bool = False
    keep_on_fail: bool = True
    env_inject: dict[str, str] | None = None

    @field_validator("worktree_add_serial")
    @classmethod
    def must_serialize(cls, value: bool) -> bool:
        if value is False:
            raise ContractError("worktree_add_must_serialize", "worktree_add_serial must be true")
        return value

    @field_validator("env_inject")
    @classmethod
    def env_inject_keys(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return value
        for key in value:
            if key in IDENTITY_ENV:
                raise ContractError(
                    "reserved_env_key",
                    f"isolation.env_inject cannot set {key}",
                    path="isolation.env_inject",
                )
        return value


class CaseIsolation(StrictModel):
    type: Literal["git-worktree", "homedir", "tempdir"] | None = None
    inherit_host_identity: bool | None = None


class Case(StrictModel):
    id: str
    path: str | None = None
    prompt_file: str = "prompt.md"
    command: list[str] | None = None
    require_exit_0: bool = False
    tags: list[str] | None = None
    timeout_s: int | None = None
    isolation: CaseIsolation | None = None
    fixtures: Fixtures | None = None
    replay: Replay | None = None
    expected_labels: dict[str, Any] | None = None
    env: dict[str, str] | None = None

    @field_validator("id")
    @classmethod
    def slug_id(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ContractError("duplicate_id", f"invalid id {value!r}", path="cases")
        return value

    @field_validator("env")
    @classmethod
    def no_reserved(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return value
        for key in value:
            if key in RESERVED_CELL_ENV or key.startswith("AGENTLAB_"):
                raise ContractError("reserved_env_key", f"case.env cannot set {key}", path="cases.env")
        return value


class PerTrialBudget(StrictModel):
    wall_clock_s: int | None = None
    tokens: int | None = None
    usd: float | None = None


class Budget(StrictModel):
    max_trials: int = 40
    max_parallel: int = 4
    wall_clock_s: int | None = None
    tokens: int | None = None
    usd: float | None = None
    per_trial: PerTrialBudget = Field(default_factory=PerTrialBudget)
    on_exceed: Literal["stop", "skip_remaining"] = "stop"


class Promotion(StrictModel):
    all_cells_must_pass: bool = True
    accept_soft_gates: list[str] = Field(default_factory=list)


class Recipe(StrictModel):
    id: str | None = None
    command: list[str] | None = None
    inherit_host_identity: bool = True
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    usage: dict[str, Any] | None = None
    unstable_kill: bool = False
    env: dict[str, str] | None = None
    write_files: list[Any] | None = None
    pin_notes: str | None = None

    @field_validator("env")
    @classmethod
    def recipe_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if not value:
            return value
        extra = set(value) - RECIPE_ENV_ALLOW
        if extra:
            raise ContractError(
                "recipe_env_not_allowed",
                f"recipe.env only allows {sorted(RECIPE_ENV_ALLOW)}, got {sorted(extra)}",
                path="recipes.env",
            )
        return value


class Experiment(StrictModel):
    schema_version: Literal[1]
    id: str
    name: str
    description: str | None = None
    created_at: str | None = None
    artifact: Artifact
    variants: list[Variant]
    concerns: list[Concern]
    matrix: Matrix
    cases: list[Case]
    isolation: Isolation
    budget: Budget
    criteria: CriteriaRef
    judge: JudgeSpec | None = None
    repetitions: int = 1
    promotion: Promotion = Field(default_factory=Promotion)
    recipes: dict[str, Recipe] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def slug_id(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ContractError("duplicate_id", f"invalid experiment id {value!r}", path="id")
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_top_forbidden(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_forbidden(data, "experiment")
        return data


SCHEMA_VERSION = 1


def fingerprint_contract(exp: Experiment) -> str:
    obj = exp.model_dump(mode="json", exclude={"notes"})
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trial_count(exp: Experiment) -> int:
    return len(exp.variants) * len(exp.matrix.cells) * len(exp.cases) * exp.repetitions
