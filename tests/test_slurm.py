# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the SLURM array-job template."""

from __future__ import annotations

import pytest

from d3dplot.config.project import SlurmConfig
from d3dplot.execution.slurm import render_slurm_script


@pytest.mark.unit
def test_slurm_script_uses_safe_single_node_array_defaults() -> None:
    script = render_slurm_script(SlurmConfig())

    assert "#SBATCH --job-name=run" in script
    assert "#SBATCH --nodes=1" in script
    assert "#SBATCH --ntasks-per-node=1" in script
    assert "#SBATCH --cpus-per-task=4" in script
    assert "#SBATCH --partition=4vcpu" in script
    assert "#SBATCH --time=12:00:00" in script
    assert '--shard "$SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT"' in script
    assert "sbatch " not in script
    assert script.endswith("\n")


@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [
        SlurmConfig(nodes=2),
        SlurmConfig(job_name="run\n#SBATCH --account=other"),
        SlurmConfig(partition="compute\n#SBATCH --account=other"),
        SlurmConfig(time_limit="12:00:00\n#SBATCH --account=other"),
        SlurmConfig(time_limit="twelve hours"),
    ],
)
def test_slurm_script_rejects_unsupported_or_unsafe_settings(
    config: SlurmConfig,
) -> None:
    with pytest.raises(ValueError):
        render_slurm_script(config)
