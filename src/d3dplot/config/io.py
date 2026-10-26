# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe YAML, JSON Schema, resolved-config, and override helpers."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeAlias

import yaml

from d3dplot.config.project import ProjectConfig

TextWriter: TypeAlias = Callable[[Path, str], None]


def load_config(path: str | Path) -> ProjectConfig:
    """Load and validate a YAML or JSON project configuration."""
    data = load_config_data(path)
    config_path = Path(path)
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be a mapping: {config_path}")
    return ProjectConfig.model_validate(data)


def load_config_data(path: str | Path) -> object:
    """Parse YAML or JSON configuration data without validating its schema."""
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def dump_config(config: ProjectConfig) -> str:
    """Serialize explicitly configured values as readable YAML."""
    data = config.model_dump(mode="json", exclude_unset=True)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def dump_json_config(config: ProjectConfig) -> str:
    """Serialize explicitly configured values as readable JSON."""
    data = config.model_dump(mode="json", exclude_unset=True)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def save_config(
    config: ProjectConfig,
    path: str | Path,
    writer: TextWriter,
) -> None:
    """Save YAML through a caller-supplied writer, such as the guarded store writer."""
    writer(Path(path), dump_config(config))


def save_json_config(
    config: ProjectConfig,
    path: str | Path,
    writer: TextWriter,
) -> None:
    """Save JSON through a caller-supplied writer."""
    writer(Path(path), dump_json_config(config))


def apply_overrides(
    config: ProjectConfig,
    overrides: Sequence[str],
) -> ProjectConfig:
    """Apply dotted-path overrides whose values are parsed with yaml.safe_load."""
    data = config.model_dump(mode="python", exclude_unset=True)
    for override in overrides:
        path, separator, raw_value = override.partition("=")
        if not separator:
            raise ValueError(f"override must have the form path=value: {override!r}")
        segments = _parse_override_path(path)
        value = yaml.safe_load(raw_value)
        _assign_path(data, segments, value, path)
    return ProjectConfig.model_validate(data)


def config_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for the complete project configuration."""
    return ProjectConfig.model_json_schema()


def dump_json_schema() -> str:
    """Serialize the configuration JSON Schema for editor tooling."""
    return json.dumps(config_json_schema(), ensure_ascii=False, indent=2) + "\n"


def write_json_schema(path: str | Path, writer: TextWriter) -> None:
    """Write JSON Schema through a caller-supplied writer."""
    writer(Path(path), dump_json_schema())


def dump_resolved_config(config: ProjectConfig, tool_version: str) -> str:
    """Serialize all defaults and analysis-level effective figure settings."""
    if not tool_version or "\n" in tool_version or "\r" in tool_version:
        raise ValueError("tool_version must be a non-empty single-line value")

    data: dict[str, Any] = config.model_dump(mode="json")
    analyses_data: list[dict[str, Any]] = data["analyses"]
    for index, analysis in enumerate(config.analyses):
        analyses_data[index]["figure"] = config.effective_figure(analysis).model_dump(
            mode="json"
        )
        analyses_data[index]["reprocess"] = config.effective_reprocess(analysis)
        analyses_data[index]["replot"] = config.effective_replot(analysis)

    yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return f"# d3dplot_version: {tool_version}\n{yaml_text}"


def write_resolved_config(
    config: ProjectConfig,
    path: str | Path,
    writer: TextWriter,
    tool_version: str,
) -> None:
    """Write the resolved YAML through the caller's guarded writer."""
    writer(Path(path), dump_resolved_config(config, tool_version))


def _parse_override_path(path: str) -> list[str | int]:
    if not path:
        raise ValueError("override path cannot be empty")

    segments: list[str | int] = []
    position = 0
    while position < len(path):
        if path[position] == "[":
            closing = path.find("]", position + 1)
            if closing < 0:
                raise ValueError(f"unclosed list index in override path: {path!r}")
            index_text = path[position + 1 : closing]
            if not index_text.isdecimal():
                raise ValueError(
                    f"list indices must be non-negative integers: {path!r}"
                )
            segments.append(int(index_text))
            position = closing + 1
        else:
            end = position
            while end < len(path) and path[end] not in ".[]":
                end += 1
            if end == position:
                raise ValueError(f"invalid override path: {path!r}")
            segments.append(path[position:end])
            position = end

        if position == len(path):
            break
        if path[position] == ".":
            position += 1
            if position == len(path) or path[position] in ".[]":
                raise ValueError(f"invalid override path: {path!r}")
        elif path[position] != "[":
            raise ValueError(f"invalid override path: {path!r}")

    return segments


def _assign_path(
    data: dict[str, Any],
    segments: list[str | int],
    value: Any,
    path: str,
) -> None:
    current: Any = data
    for position, segment in enumerate(segments[:-1]):
        next_segment = segments[position + 1]
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                raise ValueError(f"list index is out of range in override: {path!r}")
            current = current[segment]
            continue

        if not isinstance(current, dict):
            raise ValueError(f"override path does not point to a mapping: {path!r}")
        if segment not in current:
            current[segment] = [] if isinstance(next_segment, int) else {}
        current = current[segment]

    final_segment = segments[-1]
    if isinstance(final_segment, int):
        if not isinstance(current, list) or final_segment >= len(current):
            raise ValueError(f"list index is out of range in override: {path!r}")
        current[final_segment] = value
        return

    if not isinstance(current, dict):
        raise ValueError(f"override path does not point to a mapping: {path!r}")
    current[final_segment] = value
