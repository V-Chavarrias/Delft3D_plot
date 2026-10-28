# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for user-facing skill resources included with the package."""

from importlib.resources import files


def test_user_skills_are_available_as_package_resources() -> None:
    skills = files("d3dplot").joinpath("skills")
    expected_names = (
        "d3dplot-create-input",
        "d3dplot-replot",
        "d3dplot-troubleshoot",
    )

    for name in expected_names:
        skill_file = skills.joinpath(name, "SKILL.md")
        content = skill_file.read_text(encoding="utf-8")
        assert f"name: {name}" in content
        assert "description:" in content
