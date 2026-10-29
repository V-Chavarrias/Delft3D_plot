# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for shared raw reads and derived quantity calculations."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from d3dplot.simulation import discover_simulation, inspect_simulation
from d3dplot.simulation.map_reader import MapReader, MapReadResult
from d3dplot.simulation.time_axis import TimeAxis, TimeEntry
from d3dplot.store import OutputLayout
from d3dplot.variables.context import QuantityContext, QuantityRequest
from d3dplot.variables.engine import QuantityEngine, QuantityEngineError
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.registry import (
    SelectionTarget,
    UnknownVariableError,
    VariableKind,
    VariableLocation,
    VariableSpec,
)

_TIME = datetime(2021, 1, 1, tzinfo=UTC)


class _MemoryMapSession:
    def __init__(
        self,
        values: dict[str, np.ndarray],
        *,
        edge_orientation: np.ndarray | None = None,
        subrun_index: int = 0,
    ) -> None:
        self.subrun_index = subrun_index
        self._values = values
        self._edge_orientation = edge_orientation
        self.read_counts: dict[str, int] = {}

    def has_variable(self, spec: VariableSpec) -> bool:
        return any(candidate in self._values for candidate in spec.netcdf_candidates)

    def read(
        self,
        spec: VariableSpec,
        *,
        local_index: int,
        corrupt_partitions: tuple[int, ...] = (),
    ) -> MapReadResult:
        del local_index, corrupt_partitions
        self.read_counts[spec.name] = self.read_counts.get(spec.name, 0) + 1
        candidate = next(
            candidate
            for candidate in spec.netcdf_candidates
            if candidate in self._values
        )
        orientation = (
            self._edge_orientation if spec.location is VariableLocation.EDGE else None
        )
        return MapReadResult(self._values[candidate], orientation, ())


def _entry(
    *,
    time: datetime = _TIME,
    local_index: int = 0,
    subrun: int = 0,
) -> TimeEntry:
    return TimeEntry(
        time_index=local_index,
        subrun=subrun,
        local_index=local_index,
        time=time,
        morph_time=None,
        corrupt_partitions=(),
    )


@pytest.mark.unit
def test_engine_resolves_aliases_and_reads_shared_dependencies_once() -> None:
    session = _MemoryMapSession(
        {
            "mesh2d_ucmaga": np.array([3.0, 0.0]),
            "mesh2d_waterdepth": np.array([4.0, 0.0]),
            "mesh2d_s1": np.array([10.0, 5.0]),
        }
    )
    quantities = QuantityEngine().at_time(
        session,
        _entry(),
        QuantityContext(),
    )

    froude = quantities.get("Fr")
    discharge = quantities.get("qsp")
    energy = quantities.get("E")

    np.testing.assert_allclose(froude[0], 3.0 / np.sqrt(9.81 * 4.0))
    assert np.isnan(froude[1])
    np.testing.assert_allclose(discharge, [12.0, 0.0])
    np.testing.assert_allclose(energy, [10.0 + 9.0 / (2.0 * 9.81), 5.0])
    assert session.read_counts == {
        "velocity_magnitude": 1,
        "water_depth": 1,
        "water_level": 1,
    }


@pytest.mark.unit
def test_missing_dependency_reports_quantity_chain_and_subrun() -> None:
    session = _MemoryMapSession(
        {"mesh2d_waterdepth": np.array([1.0])},
        subrun_index=4,
    )
    quantities = QuantityEngine().at_time(session, _entry(subrun=4), QuantityContext())

    with pytest.raises(QuantityEngineError) as error:
        quantities.get("Fr")

    assert error.value.issue.code == "V-VAR-002"
    assert "sub-run 4" in error.value.issue.location
    assert "froude_number -> velocity_magnitude" in error.value.issue.message


@pytest.mark.unit
def test_unknown_map_quantity_uses_the_registered_suggestions() -> None:
    quantities = QuantityEngine().at_time(
        _MemoryMapSession({}),
        _entry(),
        QuantityContext(),
    )

    with pytest.raises(UnknownVariableError) as error:
        quantities.get("bed_lvel")

    assert error.value.code == "V-VAR-001"
    assert error.value.suggestions == ("bed_level",)


@pytest.mark.unit
def test_shields_uses_ordered_input_alternatives_and_per_fraction_density() -> None:
    context = QuantityContext(
        sediment_diameters_m=(0.001, 0.002),
        sediment_densities_kg_m3=(2650.0, 2700.0),
    )
    direct_session = _MemoryMapSession({"mesh2d_taus": np.array([5.0, 10.0])})
    direct = QuantityEngine().at_time(direct_session, _entry(), context)
    direct_values = direct.get("ShieldsD")
    relative_density = np.array([1.65, 1.7])
    np.testing.assert_allclose(
        direct_values,
        np.array([[5.0], [10.0]]) / (9.81 * 1000.0 * relative_density),
    )
    assert direct.choices == [
        "shields_number_times_diameter used inputs: bed_shear_stress"
    ]

    fallback_session = _MemoryMapSession(
        {
            "mesh2d_ucmaga": np.array([2.0]),
            "mesh2d_czs": np.array([40.0]),
        }
    )
    fallback = QuantityEngine().at_time(fallback_session, _entry(), context)
    fallback_values = fallback.get("shields_number_times_diameter")
    shear_stress = 1000.0 * 9.81 * 2.0**2 / 40.0**2
    np.testing.assert_allclose(
        fallback_values,
        [shear_stress / (9.81 * 1000.0 * relative_density)],
    )
    assert fallback.choices == [
        "shields_number_times_diameter used inputs: velocity_magnitude, chezy"
    ]


@pytest.mark.unit
def test_water_level_rate_uses_s0_or_previous_output_and_handles_first_time() -> None:
    previous_time = _TIME
    current_time = previous_time + timedelta(seconds=30)
    entry = _entry(time=current_time, local_index=1)
    with_s0 = QuantityEngine().at_time(
        _MemoryMapSession(
            {
                "mesh2d_s1": np.array([2.0, 3.0]),
                "mesh2d_s0": np.array([1.0, 2.0]),
            }
        ),
        entry,
        QuantityContext(current_time=current_time, previous_time=previous_time),
    )
    np.testing.assert_allclose(with_s0.get("dzetadt"), [1.0 / 30.0, 1.0 / 30.0])
    assert "mesh2d_s0" in with_s0.choices[0]

    previous = np.array([1.0, 2.0])
    fallback = QuantityEngine().at_time(
        _MemoryMapSession({"mesh2d_s1": np.array([2.0, 3.0])}),
        entry,
        QuantityContext(
            current_time=current_time,
            previous_time=previous_time,
            previous_water_level=previous,
        ),
    )
    np.testing.assert_allclose(
        fallback.get("water_level_rate"),
        [1.0 / 30.0, 1.0 / 30.0],
    )
    assert "preceding map output" in fallback.choices[0]

    first = QuantityEngine().at_time(
        _MemoryMapSession(
            {
                "mesh2d_s1": np.array([2.0]),
                "mesh2d_s0": np.array([1.0]),
            }
        ),
        _entry(),
        QuantityContext(current_time=_TIME),
    )
    assert np.isnan(first.get("dzetadt")).all()
    assert "first result is NaN" in first.choices[0]


@pytest.mark.unit
def test_active_area_threshold_equality_and_fraction_selection() -> None:
    session = _MemoryMapSession(
        {
            "mesh2d_flowelem_ba": np.array([2.0, 3.0, 4.0]),
            "mesh2d_thlyr": np.array(
                [[0.0004, 0.0005], [0.001, 0.0], [0.0007, 0.0005]]
            ),
            "mesh2d_sxtot": np.array([[3.0, 4.0, 12.0]]),
            "mesh2d_sytot": np.array([[4.0, 3.0, 5.0]]),
        }
    )
    quantities = QuantityEngine().at_time(session, _entry(), QuantityContext())

    active_area = quantities.get(
        QuantityRequest(
            requested_name="ba_mor",
            active_sediment_thickness_threshold_m=0.001,
        )
    )
    np.testing.assert_allclose(active_area, [0.0, 3.0, 4.0])

    selected_transport = quantities.get(
        QuantityRequest(
            requested_name="stot",
            fractions=(2,),
            sum_fractions=True,
        )
    )
    np.testing.assert_allclose(selected_transport, [5.0])

    with pytest.raises(QuantityEngineError) as error:
        quantities.get(QuantityRequest(requested_name="stot", fractions=(4,)))
    assert error.value.issue.code == "V-VAR-003"


@pytest.mark.unit
def test_geometric_grain_size_uses_matlab_one_based_bed_layer_selection() -> None:
    session = _MemoryMapSession(
        {
            "mesh2d_lyrfrac": np.array(
                [[[0.25, 0.75], [0.75, 0.25]]],
            )
        }
    )
    quantities = QuantityEngine().at_time(
        session,
        _entry(),
        QuantityContext(sediment_diameters_m=(0.001, 0.01)),
    )

    selected = quantities.get(QuantityRequest("dg", layers=(1, 2)))

    np.testing.assert_allclose(
        selected,
        [[0.001 * 10**0.75, 0.001 * 10**0.25]],
    )
    with pytest.raises(ValueError, match="one-based positive"):
        QuantityRequest("dg", layers=(0,))


def _two_face_geometry() -> GridGeometry:
    return GridGeometry(
        face_edges=np.array([[0, 1], [0, 2]]),
        face_edge_sign=np.array([[1, 1], [-1, 1]]),
        edge_length=np.ones(3),
        face_area=np.ones(2),
        face_x=np.array([0.0, 1.0]),
        face_y=np.zeros(2),
    )


@pytest.mark.unit
def test_edge_orientation_is_normalized_before_vorticity() -> None:
    geometry = GridGeometry(
        face_edges=np.array([[0, 1, 2, 3]]),
        face_edge_sign=np.ones((1, 4), dtype=np.int8),
        edge_length=np.array([1.0, 2.0, 1.0, 2.0]),
        face_area=np.array([2.0]),
        face_x=np.array([0.0]),
        face_y=np.array([0.0]),
    )
    session = _MemoryMapSession(
        {"mesh2d_u1": np.ones(4)},
        edge_orientation=np.array([-1.0, 1.0, 1.0, 1.0]),
    )
    quantities = QuantityEngine().at_time(
        session,
        _entry(),
        QuantityContext(grid_geometry=geometry),
    )

    np.testing.assert_allclose(quantities.get("vorticity"), [2.0])


@pytest.mark.unit
def test_streamfunctions_use_the_lazily_cached_face_laplacian() -> None:
    geometry = _two_face_geometry()
    session = _MemoryMapSession(
        {
            "mesh2d_u1": np.array([2.0, 0.0, 0.0]),
            "mesh2d_hu": np.array([3.0, 4.0, 5.0]),
        },
        edge_orientation=np.ones(3),
    )
    quantities = QuantityEngine().at_time(
        session,
        _entry(),
        QuantityContext(grid_geometry=geometry),
    )

    np.testing.assert_allclose(quantities.get("streamfunction"), [0.0, 2.0])
    np.testing.assert_allclose(
        quantities.get("transport_streamfunction"),
        [0.0, 6.0],
    )
    assert geometry._streamfunction_solver is not None
    assert session.read_counts["mesh2d_u1"] == 1


@pytest.mark.unit
def test_quantity_entry_points_register_external_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def double_depth(
        inputs: dict[str, np.ndarray],
        context: QuantityContext,
        request: QuantityRequest,
    ) -> np.ndarray:
        del context, request
        return inputs["water_depth"] * 2.0

    plugin_spec = VariableSpec(
        name="double_water_depth",
        kind=VariableKind.DERIVED,
        aliases=(),
        netcdf_candidates=(),
        location=VariableLocation.FACE,
        extra_dims=(),
        units="m",
        label_key="depth",
        inputs=("water_depth",),
        selection=SelectionTarget.INPUTS,
        version="1",
        compute=double_depth,
    )
    entry_point = SimpleNamespace(name="test", load=lambda: lambda: plugin_spec)
    monkeypatch.setattr(
        "d3dplot.variables.engine.entry_points",
        lambda *, group: (entry_point,),
    )
    quantities = QuantityEngine().at_time(
        _MemoryMapSession({"mesh2d_waterdepth": np.array([1.5, 2.0])}),
        _entry(),
        QuantityContext(),
    )

    np.testing.assert_allclose(quantities.get("double_water_depth"), [3.0, 4.0])


@pytest.mark.integration
def test_all_2d_quantities_match_synthetic_analytic_values(
    synthetic_case: Any,
    tmp_path: Any,
) -> None:
    simulation, report = discover_simulation(synthetic_case / "simulations" / "r001")
    assert simulation is not None
    assert report.is_valid
    inspection, inspection_report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        forbidden_roots=(),
    )
    assert inspection is not None, inspection_report.to_text()
    time_axis = TimeAxis.from_cache(inspection.times_path)
    expected = json.loads(
        (synthetic_case / "expected" / "quantities_r001.json").read_text(
            encoding="utf-8"
        )
    )
    entry = time_axis.entries[expected["time_index"]]
    context = replace(
        QuantityContext.from_model_input(
            simulation.subruns[entry.subrun].model_input,
            grid_geometry=GridGeometry.from_path(inspection.grid_paths[0]),
        ),
        current_time=entry.time,
        previous_time=time_axis.entries[expected["time_index"] - 1].time,
    )
    reader = MapReader(simulation, inspection.merge_plan_path)
    engine = QuantityEngine()
    with reader.session(entry.subrun) as map_session:
        quantities = engine.at_time(map_session, entry, context)
        face = expected["face_index"]
        np.testing.assert_allclose(
            quantities.get("Fr")[face],
            expected["froude_number"],
        )
        np.testing.assert_allclose(
            quantities.get("qsp")[face],
            expected["specific_discharge_m2_s"],
        )
        np.testing.assert_allclose(
            quantities.get("E")[face],
            expected["energy_head_m"],
        )
        np.testing.assert_allclose(
            quantities.get("ShieldsD")[face],
            expected["shields_number_times_diameter_m"],
        )
        np.testing.assert_allclose(
            quantities.get("udir")[face],
            expected["velocity_direction_degrees"],
        )
        np.testing.assert_allclose(
            quantities.get("uv")[face],
            expected["velocity_vector_m_s"],
        )
        np.testing.assert_allclose(
            quantities.get("dzetadt")[face],
            expected["water_level_rate_m_s"],
        )
        np.testing.assert_allclose(
            quantities.get("vorticity"),
            expected["vorticity_s_1"],
            rtol=1e-10,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            quantities.get("Ltot")[face],
            expected["total_sediment_thickness_m"],
        )
        np.testing.assert_allclose(
            quantities.get("ba_mor")[face],
            expected["morphological_active_area_m2"],
        )
        np.testing.assert_allclose(
            quantities.get("stot")[face],
            expected["sediment_transport_magnitude_m2_s"],
        )
        np.testing.assert_allclose(
            quantities.get("stot_sum")[face],
            expected["sediment_transport_magnitude_sum_m2_s"],
        )
        np.testing.assert_allclose(
            quantities.get("sb")[face],
            expected["bed_load_transport_magnitude_m2_s"],
        )
        np.testing.assert_allclose(
            quantities.get("dm")[face, 0],
            expected["mean_grain_size_arithmetic_m"],
        )
        np.testing.assert_allclose(
            quantities.get("dg")[face, 0],
            expected["mean_grain_size_geometric_m"],
        )
        for percentile in (10, 50, 90):
            np.testing.assert_allclose(
                quantities.get(f"d{percentile}")[face, 0],
                expected[f"d{percentile}"],
            )
        selected_percentile = quantities.get(
            QuantityRequest(requested_name="d50", fractions=(1,))
        )
        np.testing.assert_allclose(
            selected_percentile[face, 0],
            expected["d50"],
        )
