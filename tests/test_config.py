# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for project configuration models and YAML IO."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.config import (
    ProjectConfig,
    StatusConfig,
    Times,
    apply_overrides,
    config_json_schema,
    dump_config,
    dump_json_config,
    load_config,
    load_config_data,
    write_resolved_config,
)
from d3dplot.config.measurements import MapMeasurementsConfig
from d3dplot.config.project import PathsConfig
from d3dplot.config.selections import IndicesSelection, TimeRange, TimesConfig


def _minimal_data() -> dict[str, Any]:
    return {
        "simulations": [{"path": "model", "label": "reference"}],
        "analyses": [{"analysis": "map2d", "variables": ["bed_level"]}],
    }


@pytest.mark.unit
def test_yaml_round_trip_uses_nested_time_and_map_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        """
simulations:
  - path: model
    label: reference
analyses:
  - analysis: map2d
    variables: [bed_level]
    times:
      at: [2021-02-08, 2021-08-08]
      axis: morphological
      tolerance: 10 days
    map:
      render: raster
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    analysis = config.analyses[0]
    assert isinstance(analysis, Map2DConfig)
    assert analysis.times.at == [
        datetime(2021, 2, 8),
        datetime(2021, 8, 8),
    ]
    assert analysis.times.axis == "morphological"
    assert analysis.times.tolerance == timedelta(days=10)
    assert analysis.map.render == "raster"

    reloaded = ProjectConfig.model_validate(yaml.safe_load(dump_config(config)))
    assert reloaded == config


@pytest.mark.unit
def test_json_round_trip(tmp_path: Path) -> None:
    config = ProjectConfig.model_validate(_minimal_data())
    config_path = tmp_path / "project.json"
    config_path.write_text(dump_json_config(config), encoding="utf-8")

    assert load_config(config_path) == config


@pytest.mark.unit
def test_load_config_data_preserves_non_mapping_root(tmp_path: Path) -> None:
    config_path = tmp_path / "project.yaml"
    config_path.write_text("- not-a-mapping\n", encoding="utf-8")

    assert load_config_data(config_path) == ["not-a-mapping"]

    with pytest.raises(ValueError, match="root must be a mapping"):
        load_config(config_path)


@pytest.mark.unit
def test_defaults_and_global_figure_inheritance() -> None:
    default_config = ProjectConfig.model_validate(_minimal_data())
    default_analysis = default_config.analyses[0]
    assert isinstance(default_analysis, Map2DConfig)
    assert default_analysis.times.at == "all"
    assert default_analysis.times.axis == "hydrodynamic"
    assert default_analysis.times.tolerance is None
    assert default_analysis.active_sediment_thickness_threshold_m == 0.001
    assert default_config.status.stale_after == timedelta(hours=1)
    assert default_config.figure.font_size == 10.0
    assert default_config.figure.font_family == "DejaVu Sans"
    assert default_config.figure.size_cm == (16.0, 10.0)
    assert default_config.figure.dpi == 150
    assert default_analysis.map.colormap == "viridis"
    assert default_analysis.map.render == "auto"
    assert default_analysis.map.aspect == "auto"

    statistics_config = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {"file": "river_km.csv"},
        }
    )
    assert statistics_config.statistics == ["mean_area_weighted"]
    assert statistics_config.views == ["profile"]

    data = _minimal_data()
    data["figure"] = {"dpi": 240, "font_size": 9}
    data["hooks"] = ["project_hooks:decorate"]
    data["reprocess"] = "always"
    data["analyses"][0]["figure"] = {"font_size": 12}
    data["analyses"][0]["hooks"] = ["analysis_hooks:decorate"]
    data["analyses"][0]["replot"] = "never"

    config = ProjectConfig.model_validate(data)
    analysis = config.analyses[0]
    assert config.output.dtype == "float32"
    assert config.execution.executor == "processes"
    assert analysis.times.at == "all"
    assert analysis.times.axis == "hydrodynamic"
    assert config.effective_figure(analysis).dpi == 240
    assert config.effective_figure(analysis).font_size == 12
    assert config.effective_hooks(analysis) == [
        "project_hooks:decorate",
        "analysis_hooks:decorate",
    ]
    assert config.effective_reprocess(analysis) == "always"
    assert config.effective_replot(analysis) == "never"

    analysis.hooks_replace = True
    assert config.effective_hooks(analysis) == ["analysis_hooks:decorate"]

    saved = ProjectConfig.model_validate(yaml.safe_load(dump_config(config)))
    saved_analysis = saved.analyses[0]
    assert saved.effective_figure(saved_analysis) == config.effective_figure(analysis)


@pytest.mark.unit
def test_status_stale_after_is_configurable_and_non_negative() -> None:
    status = StatusConfig.model_validate({"stale_after": "30 minutes"})
    assert status.stale_after == timedelta(minutes=30)

    data = _minimal_data()
    data["status"] = {"stale_after": "2 hours"}
    config = ProjectConfig.model_validate(data)
    round_trip = ProjectConfig.model_validate(yaml.safe_load(dump_config(config)))
    assert round_trip.status.stale_after == timedelta(hours=2)

    with pytest.raises(ValidationError, match="stale_after must be non-negative"):
        StatusConfig.model_validate({"stale_after": "-1 second"})


@pytest.mark.unit
def test_overrides_parse_yaml_values_and_do_not_mutate_source() -> None:
    config = ProjectConfig.model_validate(_minimal_data())

    updated = apply_overrides(
        config,
        [
            "analyses[0].figure.dpi=300",
            "analyses[0].times.axis=morphological",
            "analyses[0].map.extents=[[0, 100, 0, 200]]",
        ],
    )
    analysis = updated.analyses[0]
    assert isinstance(analysis, Map2DConfig)
    assert analysis.figure.dpi == 300
    assert analysis.times.axis == "morphological"
    assert analysis.map.extents == [(0.0, 100.0, 0.0, 200.0)]

    source_analysis = config.analyses[0]
    assert isinstance(source_analysis, Map2DConfig)
    assert source_analysis.figure.dpi == 150
    assert source_analysis.times.axis == "hydrodynamic"


@pytest.mark.unit
def test_overrides_support_dynamic_mapping_keys() -> None:
    config = ProjectConfig.model_validate(_minimal_data())
    updated = apply_overrides(
        config,
        ["analyses[0].map.color_limits.bed_level.value=[[null, 1]]"],
    )

    analysis = updated.analyses[0]
    assert isinstance(analysis, Map2DConfig)
    assert analysis.map.color_limits == {"bed_level": {"value": [(None, 1.0)]}}


@pytest.mark.unit
def test_overrides_reject_invalid_paths_and_unknown_fields() -> None:
    config = ProjectConfig.model_validate(_minimal_data())

    with pytest.raises(ValueError, match="list index is out of range"):
        apply_overrides(config, ["analyses[1].figure.dpi=300"])
    with pytest.raises(ValueError, match="override must have"):
        apply_overrides(config, ["analyses[0].figure.dpi"])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        apply_overrides(config, ["analyses[0].unknown_option=true"])


@pytest.mark.unit
def test_unknown_keys_are_rejected_at_all_model_levels() -> None:
    project_data = _minimal_data()
    project_data["unknown_option"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(project_data)

    analysis_data = _minimal_data()
    analysis_data["analyses"][0]["map"] = {"render": "raster", "unknown": True}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(analysis_data)

    legacy_time_data = _minimal_data()
    legacy_time_data["analyses"][0]["times"] = {
        "at": "all",
        "time_axis": "morphological",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(legacy_time_data)

    nested_duplicate_data = _minimal_data()
    nested_duplicate_data["analyses"][0]["times"] = {
        "duplicate_morphological_times": "first"
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(nested_duplicate_data)


@pytest.mark.unit
def test_fixed_utc_offset_validation() -> None:
    data = _minimal_data()
    data["time_zone"] = "+01:00"
    assert ProjectConfig.model_validate(data).time_zone == "+01:00"

    data["time_zone"] = "Europe/Amsterdam"
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(data)

    data["time_zone"] = "+14:01"
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(data)


@pytest.mark.unit
def test_map_measurement_time_zone_and_path_resolution(tmp_path: Path) -> None:
    measurement = MapMeasurementsConfig.model_validate(
        {"catalogue": "measurements.csv", "time_zone": "+01:30"}
    )
    assert measurement.time_zone == "+01:30"

    paths = PathsConfig()
    relative = paths.resolve_path("measurements.csv", base_dir=tmp_path)
    assert relative == (tmp_path / "measurements.csv").resolve()

    if os.name == "nt":
        mapped = paths.resolve_path("/p/measurements/file.tif")
        assert mapped == Path(r"P:\measurements\file.tif").resolve(strict=False)
    else:
        mapped = paths.resolve_path(r"P:\measurements\file.tif")
        assert mapped == Path("/p/measurements/file.tif")


@pytest.mark.unit
def test_schema_exports_nested_times_and_shared_map_render() -> None:
    schema = config_json_schema()
    definitions = schema["$defs"]

    times_properties = definitions["TimesConfig"]["properties"]
    assert {"at", "axis", "tolerance"} <= set(times_properties)
    assert "time_axis" not in times_properties
    assert "duplicate_morphological_times" not in times_properties

    map_properties = definitions["MapViewConfig"]["properties"]
    assert "render" in map_properties
    assert "render" not in definitions["FigureConfig"]["properties"]
    assert schema["additionalProperties"] is False


@pytest.mark.unit
def test_safe_yaml_loader_rejects_python_object_tags(tmp_path: Path) -> None:
    config_path = tmp_path / "unsafe.yaml"
    config_path.write_text(
        "!!python/object/apply:os.system ['echo unsafe']",
        encoding="utf-8",
    )

    with pytest.raises(yaml.YAMLError):
        load_config(config_path)


@pytest.mark.unit
def test_resolved_config_writer_uses_injected_writer() -> None:
    config = ProjectConfig.model_validate(_minimal_data())
    writes: list[tuple[Path, str]] = []

    def capture(path: Path, text: str) -> None:
        writes.append((path, text))

    write_resolved_config(config, "output/latest.yaml", capture, "0.1.0")

    assert len(writes) == 1
    path, text = writes[0]
    assert path == Path("output/latest.yaml")
    assert text.startswith("# d3dplot_version: 0.1.0\n")
    resolved = ProjectConfig.model_validate(yaml.safe_load(text))
    resolved_analysis = resolved.analyses[0]
    assert resolved.effective_figure(resolved_analysis) == config.effective_figure(
        config.analyses[0]
    )
    assert resolved.effective_reprocess(
        resolved_analysis
    ) == config.effective_reprocess(config.analyses[0])


@pytest.mark.unit
def test_times_alias_is_the_nested_times_model() -> None:
    times = Times.model_validate(
        {
            "at": {"start": "2021-01-01", "end": "2021-01-03", "step": "1 day"},
            "axis": "hydrodynamic",
            "tolerance": "30 minutes",
        }
    )

    assert times.tolerance == timedelta(minutes=30)
    assert isinstance(times.at, TimeRange)
    assert times.at.step == timedelta(days=1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [float("nan"), "NaN", ".nan", [1, float("nan")]],
)
def test_matlab_nan_selects_all_times(value: object) -> None:
    assert TimesConfig(at=value).at == "all"


@pytest.mark.unit
@pytest.mark.parametrize("value", [float("inf"), "Inf", ".inf"])
def test_matlab_inf_selects_last_time(value: object) -> None:
    assert TimesConfig(at=value).at == "last"


@pytest.mark.unit
def test_matlab_inf_can_be_combined_with_one_based_indices() -> None:
    selection = TimesConfig(at=[1, float("inf")]).at

    assert selection == IndicesSelection(indices=[1], include_last=True)
    assert TimesConfig(at=[1.0, "Inf"]).at == IndicesSelection(
        indices=[1],
        include_last=True,
    )


@pytest.mark.unit
def test_integer_time_selectors_are_one_based_indices() -> None:
    assert TimesConfig(at=1).at == IndicesSelection(indices=[1])
    assert TimesConfig(at=[1, 3]).at == IndicesSelection(indices=[1, 3])


@pytest.mark.unit
def test_time_index_selection_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        TimesConfig(at=0)
    with pytest.raises(ValidationError):
        TimesConfig(at={"indices": [0]})
    with pytest.raises(ValidationError):
        TimesConfig(at=[1, float("-inf")])


@pytest.mark.unit
def test_active_sediment_thickness_threshold_is_configurable() -> None:
    config = ProjectConfig.model_validate(
        {
            **_minimal_data(),
            "analyses": [
                {
                    "analysis": "map2d",
                    "variables": ["ba_mor"],
                    "active_sediment_thickness_threshold_m": 0.025,
                }
            ],
        }
    )

    assert config.analyses[0].active_sediment_thickness_threshold_m == 0.025


@pytest.mark.unit
def test_active_sediment_thickness_threshold_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(
            {
                **_minimal_data(),
                "analyses": [
                    {
                        "analysis": "map2d",
                        "variables": ["ba_mor"],
                        "active_sediment_thickness_threshold_m": -0.001,
                    }
                ],
            }
        )
