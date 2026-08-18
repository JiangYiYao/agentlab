from __future__ import annotations

from pathlib import Path

from agentlab.errors import ContractError
from agentlab.models import Trial
from agentlab.schema import Experiment, fingerprint_contract, trial_count


def trial_id(variant_id: str, cell_id: str, case_id: str, repeat: int) -> str:
    return f"{variant_id}__{cell_id}__{case_id}__r{repeat}"


def expand(exp: Experiment, experiment_root: Path) -> list[Trial]:
    if not exp.cases:
        raise ContractError("no_cases", "cases is empty")
    if trial_count(exp) > exp.budget.max_trials:
        raise ContractError(
            "too_many_trials",
            f"expanded trials={trial_count(exp)} exceeds budget.max_trials={exp.budget.max_trials}",
        )
    digest = fingerprint_contract(exp)
    trials: list[Trial] = []
    variants = sorted(exp.variants, key=lambda v: (0 if v.role == "baseline" else 1, v.id))
    for case in exp.cases:
        for cell in exp.matrix.cells:
            for variant in variants:
                for r in range(1, exp.repetitions + 1):
                    trials.append(
                        Trial(
                            id=trial_id(variant.id, cell.id, case.id, r),
                            variant=variant,
                            cell=cell,
                            case=case,
                            repeat=r,
                            contract_hash=digest,
                            experiment_root=experiment_root,
                        )
                    )
    trials.sort(key=lambda t: (t.case.id, t.cell.id, 0 if t.variant.role == "baseline" else 1, t.repeat))
    return trials


def filter_trials(
    trials: list[Trial],
    *,
    only_variant: str | None,
    only_cell: str | None,
    only_case: str | None,
) -> list[Trial]:
    out = trials
    if only_variant:
        out = [t for t in out if t.variant.id == only_variant]
    else:
        out = [t for t in out if not t.variant.opt_in]
    if only_cell:
        out = [t for t in out if t.cell.id == only_cell]
    if only_case:
        out = [t for t in out if t.case.id == only_case]
    return out
