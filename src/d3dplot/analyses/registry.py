# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in analysis registry used by configuration and command workflows."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Literal, TypeAlias, get_args, get_origin

from d3dplot.analyses.base import AnalysisPlugin
from d3dplot.analyses.m1d.config import M1DConfig
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.profile.config import ProfileConfig
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.config.analysis import AnalysisConfigBase

AnalysisConfigType: TypeAlias = type[AnalysisConfigBase]

_BUILTIN_ANALYSES: Mapping[str, AnalysisConfigType] = MappingProxyType(
    {
        "m1d": M1DConfig,
        "map2d": Map2DConfig,
        "profile": ProfileConfig,
        "river_km_statistics": RiverKmStatisticsConfig,
    }
)


class AnalysisRegistryError(ValueError):
    """An installed analysis plugin does not meet the entry-point contract."""


@lru_cache(maxsize=1)
def _plugin_analysis_configs() -> Mapping[str, AnalysisConfigType]:
    plugins: dict[str, AnalysisConfigType] = {}
    for entry_point in sorted(
        entry_points(group="d3dplot.analyses"),
        key=lambda item: item.name,
    ):
        name = entry_point.name
        if not name or name != name.strip():
            raise AnalysisRegistryError(
                "Analysis plugin entry-point names must be non-empty and trimmed."
            )
        if name in _BUILTIN_ANALYSES:
            raise AnalysisRegistryError(
                f"Analysis plugin {name!r} conflicts with a built-in analysis."
            )
        if name in plugins:
            raise AnalysisRegistryError(
                f"More than one analysis plugin is registered as {name!r}."
            )

        plugin = entry_point.load()
        if not isinstance(plugin, AnalysisPlugin):
            raise AnalysisRegistryError(
                f"Analysis entry point {name!r} must load an AnalysisPlugin value."
            )
        config_model = plugin.config_model
        if not isinstance(config_model, type) or not issubclass(
            config_model,
            AnalysisConfigBase,
        ):
            raise AnalysisRegistryError(
                f"Analysis plugin {name!r} config_model must subclass "
                "AnalysisConfigBase."
            )
        analysis_field = config_model.model_fields.get("analysis")
        if (
            analysis_field is None
            or get_origin(analysis_field.annotation) is not Literal
        ):
            raise AnalysisRegistryError(
                f"Analysis plugin {name!r} must define analysis as Literal[{name!r}]."
            )
        if (
            get_args(analysis_field.annotation) != (name,)
            or analysis_field.default != name
        ):
            raise AnalysisRegistryError(
                f"Analysis plugin {name!r} must define analysis as Literal[{name!r}] "
                f"with default {name!r}."
            )
        plugins[name] = config_model
    return MappingProxyType(plugins)


def builtin_analysis_configs() -> Mapping[str, AnalysisConfigType]:
    """Return the immutable built-in analysis discriminator registry."""
    return _BUILTIN_ANALYSES


def registered_analysis_configs() -> Mapping[str, AnalysisConfigType]:
    """Return built-in and installed plugin config models."""
    return MappingProxyType(
        {
            **_BUILTIN_ANALYSES,
            **_plugin_analysis_configs(),
        }
    )


def builtin_analysis_names() -> tuple[str, ...]:
    """Return built-in analysis names in stable sorted order."""
    return tuple(sorted(_BUILTIN_ANALYSES))


def registered_analysis_names() -> tuple[str, ...]:
    """Return registered analysis names in stable sorted order."""
    return tuple(sorted(registered_analysis_configs()))


def analysis_config_model(name: str) -> AnalysisConfigType:
    """Return the registered config model for one analysis discriminator."""
    try:
        return registered_analysis_configs()[name]
    except KeyError:
        raise ValueError(f"Unknown analysis discriminator: {name!r}.") from None


def is_builtin_analysis(name: str) -> bool:
    """Return whether a discriminator names a built-in analysis."""
    return name in _BUILTIN_ANALYSES


def clear_plugin_analysis_cache() -> None:
    """Clear the entry-point cache, primarily for isolated registry tests."""
    _plugin_analysis_configs.cache_clear()
