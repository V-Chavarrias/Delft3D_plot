# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate a non-submitting SLURM array-job template."""

from __future__ import annotations

import re

from d3dplot.config.project import SlurmConfig

_SAFE_DIRECTIVE_VALUE = re.compile(r"[A-Za-z0-9_.-]+")
_SAFE_TIME_LIMIT = re.compile(r"(?:[1-9]\d*-)?\d+:[0-5]\d:[0-5]\d")


def render_slurm_script(config: SlurmConfig) -> str:
    """Render a guarded-by-caller, single-node array-job template."""
    if config.nodes != 1:
        raise ValueError("The SLURM template supports one node per array job.")

    job_name = config.job_name or "run"
    for name, value in (
        ("job_name", job_name),
        ("partition", config.partition),
    ):
        if _SAFE_DIRECTIVE_VALUE.fullmatch(value) is None:
            raise ValueError(
                f"SLURM {name} must contain only letters, digits, dot, "
                "underscore, and hyphen."
            )
    if _SAFE_TIME_LIMIT.fullmatch(config.time_limit) is None:
        raise ValueError("SLURM time_limit must use HH:MM:SS or D-HH:MM:SS format.")

    lines = (
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks-per-node=1",
        f"#SBATCH --cpus-per-task={config.tasks_per_node}",
        f"#SBATCH --partition={config.partition}",
        f"#SBATCH --time={config.time_limit}",
        "",
        "set -euo pipefail",
        "",
        (
            ': "${D3DPLOT_CONFIG:?Set D3DPLOT_CONFIG to a cluster-visible '
            'project YAML path}"'
        ),
        ': "${SLURM_ARRAY_TASK_ID:?Submit with a zero-based SLURM job array}"',
        ': "${SLURM_ARRAY_TASK_COUNT:?Submit with a zero-based SLURM job array}"',
        ': "${SLURM_CPUS_PER_TASK:?SLURM did not allocate CPUs per task}"',
        "",
        "if ! command -v d3dplot >/dev/null 2>&1 || "
        "! d3dplot run --help >/dev/null 2>&1; then",
        '    echo "This template requires the d3dplot run command, which is not '
        'available in this version." >&2',
        "    exit 2",
        "fi",
        "",
        'srun --ntasks=1 d3dplot run "$D3DPLOT_CONFIG" '
        '--executor processes --workers "$SLURM_CPUS_PER_TASK" '
        '--shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT"',
        "",
    )
    return "\n".join(lines)
