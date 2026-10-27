# Variable catalogue

<!-- Generated from `d3dplot.variables.registry`; do not edit by hand. -->

Names, Matlab aliases, and registered NetCDF candidates are resolved
case-insensitively. Candidate spellings below are preserved for file reads.
Derived dependencies include required, optional, and alternative inputs.
Registered computation callbacks run through the shared quantity engine.

| Canonical | Kind | Aliases | NetCDF candidates | Location | Extra dimensions | Units | Label key | Inputs | Optional inputs | Alternatives | Requirements | Selection | Version |
|-----------|------|---------|-------------------|----------|------------------|-------|-----------|--------|----------------|-------------|--------------|-----------|---------|
| `bed_level` | raw | `bl`, `DPS`, `etab` | `mesh2d_mor_bl`, `mesh2d_flowelem_bl` | face | - | `m` | `bed_elevation` | - | - | - | - | - | - |
| `bed_load_transport_magnitude` | derived | `sb` | - | face | `nSedTot` | `m^2/s` | `bedload_sediment_transport` | `mesh2d_sbcx`, `mesh2d_sbcy` | - | - | - | output | 1 |
| `bed_shear_stress` | raw | `taub` | `mesh2d_taus` | face | - | `Pa` | `bed_shear_stress` | - | - | - | - | - | - |
| `cell_area` | raw | `ba` | `mesh2d_flowelem_ba` | face | - | `m^2` | `cell_area` | - | - | - | - | - | - |
| `chezy` | raw | `czs` | `mesh2d_czs` | face | - | `m^0.5/s` | `chezy_friction_coefficient` | - | - | - | - | - | - |
| `chloride_mass` | derived | `clm2` | - | face | - | `kg/m^2` | `chloride_mass` | `mesh2d_sa1`, `mesh2d_flowelem_zw` | - | - | `sal2cl` | inputs | 1 |
| `energy_head` | derived | `E` | - | face | - | `m` | `energy_head` | `water_level`, `velocity_magnitude` | - | - | `gravity` | inputs | 1 |
| `froude_number` | derived | `Fr` | - | face | - | `1` | `fr` | `velocity_magnitude`, `water_depth` | - | - | `gravity`, `dry_depth_m` | inputs | 1 |
| `grain_size_percentile` | derived | `d10`, `d50`, `d90` | - | face | `nBedLayers` | `m` | `grain_size_percentile` | `layer_fraction` | - | - | `sediment_diameters` | output | 1 |
| `layer_fraction` | raw | `Fak`, `lyrfrac`, `Fs` | `mesh2d_lyrfrac` | face | `nBedLayers`, `nSedTot` | `1` | `volume_fraction_content_size_frac` | - | - | - | - | - | - |
| `layer_thickness` | raw | `thlyr`, `La` | `mesh2d_thlyr` | face | `nBedLayers` | `m` | `sediment_thickness` | - | - | - | - | - | - |
| `mean_grain_size_arithmetic` | derived | `dm` | - | face | `nBedLayers` | `m` | `dm` | `layer_fraction` | - | - | `sediment_diameters` | output | 1 |
| `mean_grain_size_geometric` | derived | `dg` | - | face | `nBedLayers` | `m` | `dg` | `layer_fraction` | - | - | `sediment_diameters` | output | 1 |
| `mean_grain_size_geometric_output` | raw | - | `mesh2d_dg` | face | - | `m` | `dg` | - | - | - | - | - | - |
| `mesh2d_hu` | raw | - | `mesh2d_hu` | edge | - | `m` | `depth` | - | - | - | - | - | - |
| `mesh2d_s0` | raw | - | `mesh2d_s0` | face | - | `m` | `water_level` | - | - | - | - | - | - |
| `mesh2d_sbcx` | raw | - | `mesh2d_sbcx` | face | `nSedTot` | `m^2/s` | `bedload_sediment_transport` | - | - | - | - | - | - |
| `mesh2d_sbcy` | raw | - | `mesh2d_sbcy` | face | `nSedTot` | `m^2/s` | `bedload_sediment_transport` | - | - | - | - | - | - |
| `mesh2d_sxtot` | raw | - | `mesh2d_sxtot` | face | `nSedTot` | `m^2/s` | `sediment_transport_size_frac_L2T` | - | - | - | - | - | - |
| `mesh2d_sytot` | raw | - | `mesh2d_sytot` | face | `nSedTot` | `m^2/s` | `sediment_transport_size_frac_L2T` | - | - | - | - | - | - |
| `mesh2d_u1` | raw | - | `mesh2d_u1` | edge | - | `m/s` | `velocity_magnitude` | - | - | - | - | - | - |
| `mesh2d_ucx` | raw | - | `mesh2d_ucx` | face | - | `m/s` | `velocity_vector` | - | - | - | - | - | - |
| `mesh2d_ucy` | raw | - | `mesh2d_ucy` | face | - | `m/s` | `velocity_vector` | - | - | - | - | - | - |
| `morphological_active_area` | derived | `ba_mor` | - | face | - | `m^2` | `morphodynamic_cell_area` | `cell_area`, `total_sediment_thickness` | - | - | - | inputs | 1 |
| `residence_time_depth_average` | derived | `T_da` | - | face | - | `s` | `depth_average_residence_time` | `mesh2d_cTR<i>`, `mesh2d_dTR<i>`, `mesh2d_flowelem_zw` | - | - | `substance_file`, `decay_rate` | inputs | 1 |
| `residence_time_max` | derived | `T_max` | - | face | - | `s` | `maximum_residence_time` | `mesh2d_cTR<i>`, `mesh2d_dTR<i>`, `mesh2d_flowelem_zw` | - | - | `substance_file`, `decay_rate` | inputs | 1 |
| `residence_time_surface` | derived | `T_surf` | - | face | - | `s` | `surface_residence_time` | `mesh2d_cTR<i>`, `mesh2d_dTR<i>`, `mesh2d_flowelem_zw` | - | - | `substance_file`, `decay_rate` | inputs | 1 |
| `sediment_transport_magnitude` | derived | `stot` | - | face | `nSedTot` | `m^2/s` | `sediment_transport_size_frac_L2T` | `mesh2d_sxtot`, `mesh2d_sytot` | - | - | - | output | 1 |
| `sediment_transport_magnitude_sum` | derived | `stot_sum` | - | face | - | `m^2/s` | `total_sediment_transport_L2T` | `sediment_transport_magnitude` | - | - | - | inputs | 1 |
| `shields_number_times_diameter` | derived | `ShieldsD` | - | face | `nSedTot` | `m` | `shieldsd` | - | - | `bed_shear_stress`; `velocity_magnitude` + `chezy` | `gravity`, `water_density`, `sediment_density`, `sediment_diameters` | output | 1 |
| `specific_discharge` | derived | `qsp` | - | face | - | `m^2/s` | `specific_discharge` | `velocity_magnitude`, `water_depth` | - | - | - | inputs | 1 |
| `streamfunction` | derived | - | - | face | - | `m^2/s` | `streamfunction` | `mesh2d_u1` | - | - | `grid_geometry`, `sparse_laplacian_factorization` | inputs | 1 |
| `total_sediment_thickness` | derived | `Ltot` | - | face | - | `m` | `sediment_thickness` | `layer_thickness` | - | - | - | inputs | 1 |
| `transport_streamfunction` | derived | - | - | face | - | `m^3/s` | `transport_streamfunction` | `mesh2d_u1`, `mesh2d_hu` | - | - | `grid_geometry`, `sparse_laplacian_factorization` | inputs | 1 |
| `velocity_direction` | derived | `udir` | - | face | - | `degree` | `direction` | `mesh2d_ucx`, `mesh2d_ucy` | - | - | - | inputs | 1 |
| `velocity_magnitude` | raw | `umag`, `umag_layer` | `mesh2d_ucmaga`, `mesh2d_ucmag` | face | - | `m/s` | `velocity_magnitude` | - | - | - | - | - | - |
| `velocity_magnitude_layer_mean` | derived | - | - | face | - | `m/s` | `velocity_magnitude` | `velocity_magnitude` | - | - | - | inputs | 1 |
| `velocity_vector` | derived | `uv` | - | face | `component` | `m/s` | `velocity_vector` | `mesh2d_ucx`, `mesh2d_ucy` | - | - | - | inputs | 1 |
| `vorticity` | derived | - | - | face | - | `1/s` | `vorticity` | `mesh2d_u1` | - | - | `grid_geometry` | inputs | 1 |
| `water_depth` | raw | `h`, `wd`, `waterdepth` | `mesh2d_waterdepth` | face | - | `m` | `depth` | - | - | - | - | - | - |
| `water_level` | raw | `wl` | `mesh2d_s1` | face | - | `m` | `water_level` | - | - | - | - | - | - |
| `water_level_rate` | derived | `dzetadt` | - | face | - | `m/s` | `dzetadt` | `water_level` | `mesh2d_s0` | - | `map_times` | inputs | 1 |
