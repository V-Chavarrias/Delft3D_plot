# SPDX-License-Identifier: GPL-3.0-or-later
"""Postprocessing and plotting tools for D-Flow FM and SMT simulations."""

from importlib.metadata import version

from d3dplot.config import ProjectConfig, load_config

__version__ = version("delft3d-plot")

__all__ = ["ProjectConfig", "__version__", "load_config"]
