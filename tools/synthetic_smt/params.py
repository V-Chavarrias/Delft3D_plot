# SPDX-License-Identifier: GPL-3.0-or-later
"""Parameters of the synthetic SMT case. Changing a value changes the generated case."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

GENERATOR_VERSION = "0.3.0"

GRAVITY_M_S2 = 9.813
WATER_DENSITY_KG_M3 = 1000.0
SEDIMENT_DIAMETERS_M = (0.0005, 0.002, 0.008)
SEDIMENT_DENSITIES_KG_M3 = (2650.0, 2700.0, 2650.0)
ROTATION_RATE_S_1 = 1e-4

TZ = timezone(timedelta(hours=1))
REFERENCE_TIME = datetime(2021, 2, 8, tzinfo=TZ)
TIME_UNITS = "seconds since 2021-02-08 00:00:00 +01:00"


@dataclass(frozen=True)
class SimulationParams:
    label: str
    erosion_rate_m_per_day: float
    nan_faces: bool


@dataclass(frozen=True)
class Params:
    seed: int = 1

    # centreline: sine-generated curve, direction theta(s) = theta0 + omega*sin(2*pi*s/wavelength)
    x0: float = 180_000.0
    y0: float = 330_000.0
    theta0: float = math.pi / 2
    length: float = 10_000.0
    meander_wavelength: float = 2_000.0
    meander_amplitude_rad: float = 0.5
    km0: float = 10.0

    # grid: along-channel step and across-channel node positions (n > 0 = left bank)
    ds: float = 25.0
    channel_half_width: float = 50.0
    channel_dn: float = 25.0
    floodplain_width: float = 300.0
    floodplain_dn: float = 50.0
    # quads split into two triangles: along-channel indices, outermost right-floodplain column
    triangle_i: tuple[int, int] = (200, 206)

    # partitions: 0/1 = channel + right floodplain (upstream/downstream half), 2 = left floodplain strip
    n_partitions: int = 3

    # bed level (m+NAP): z0 - slope*s + bars in the channel + erosion in morphological time
    z0: float = 40.0
    slope: float = 2.0e-4
    bar_amplitude: float = 0.5
    bar_wavelength: float = 250.0
    floodplain_height: float = 5.0
    base_depth: float = 3.0

    # SMT time structure
    mdu_refdate: str = "20121231"  # deliberately different from the map time units (as in r209)
    morfac: float = 10.0
    spinup_s: float = 86_400.0
    hydro_days_after_spinup: tuple[float, ...] = (3.0, 6.0, 12.0, 9.0)
    stage_m: tuple[float, ...] = (0.0, 1.0, 2.5, 0.5)
    plain_fm_map_times: int = 40

    simulations: tuple[SimulationParams, ...] = field(
        default_factory=lambda: (
            SimulationParams("r001", erosion_rate_m_per_day=-0.002, nan_faces=True),
            SimulationParams("r002", erosion_rate_m_per_day=-0.004, nan_faces=False),
        )
    )

    # sediment
    n_fractions: int = 3
    n_bed_layers: int = 2

    # measurements (dates, local time +01:00); the last 2D one is outside the tolerance on purpose
    smb_measurement_dates: tuple[str, ...] = ("2021-02-08", "2021-09-01", "2021-05-12", "2021-12-01")
    raster_dates: tuple[str, ...] = ("2021-02-08", "2021-05-12", "2022-06-01")
    vakken_dates: tuple[str, ...] = ("2021-02-08", "2021-09-01")
    raster_resolution: float = 10.0
    raster_km_range: tuple[float, float] = (12.0, 16.0)
    vakken_km_range: tuple[float, float] = (12.0, 14.0)
    measurement_bias_m: float = 0.03

    def n_nodes_across(self) -> list[float]:
        n_fp = round(self.floodplain_width / self.floodplain_dn)
        n_ch = round(2 * self.channel_half_width / self.channel_dn)
        right = [-self.channel_half_width - self.floodplain_width + k * self.floodplain_dn for k in range(n_fp)]
        channel = [-self.channel_half_width + k * self.channel_dn for k in range(n_ch + 1)]
        left = [self.channel_half_width + (k + 1) * self.floodplain_dn for k in range(n_fp)]
        return right + channel + left

    def to_dict(self) -> dict:
        d = asdict(self)
        d["generator_version"] = GENERATOR_VERSION
        d["time_units"] = TIME_UNITS
        return d
