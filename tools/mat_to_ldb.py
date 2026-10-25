# SPDX-License-Identifier: GPL-3.0-or-later
"""Recreate a land-boundary .ldb (Tekal, same format as .pli/.pol) from a Matlab land-boundary cache (.mat).

The .mat holds one N x 2 array (default variable `ldb`) with polylines separated by NaN rows, as written by Matlab
`D3D_read_ldb`. Original polyline names are not stored in the .mat, so blocks are named L000001, L000002, ...

Usage:
    python tools/mat_to_ldb.py <input.mat> <output.ldb> [--variable ldb] [--decimals 4]

The output must not exist (never overwritten) and may not be on the P-drive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import scipy.io


def split_polylines(xy: np.ndarray) -> list[np.ndarray]:
    xy = np.asarray(xy, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"Expected an N x 2 array, got {xy.shape}")
    sep = np.flatnonzero(np.isnan(xy).any(axis=1))
    return [part[~np.isnan(part).any(axis=1)] for part in np.split(xy, sep)]


def write_ldb(path: Path, lines: list[np.ndarray], decimals: int = 4):
    fmt = f"{{:.{decimals}f}}\t{{:.{decimals}f}}"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for k, ln in enumerate(lines, start=1):
            fh.write(f"L{k:06d}\n{len(ln)}\t2\n")
            fh.write("\n".join(fmt.format(x, y) for x, y in ln))
            fh.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mat", type=Path)
    ap.add_argument("ldb", type=Path)
    ap.add_argument("--variable", default="ldb")
    ap.add_argument("--decimals", type=int, default=4)
    args = ap.parse_args(argv)

    out = args.ldb.resolve()
    if out.drive.lower().startswith("p:") or str(out).replace("\\", "/").lower().startswith("/p/"):
        raise SystemExit(f"Refusing to write to the P-drive: {out}")
    if out.exists():
        raise SystemExit(f"Output exists, not overwriting: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)

    xy = scipy.io.loadmat(args.mat, variable_names=[args.variable])[args.variable]
    lines = [ln for ln in split_polylines(xy) if len(ln) > 0]
    write_ldb(out, lines, args.decimals)
    print(f"{len(lines)} polylines, {sum(len(ln) for ln in lines)} points -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
