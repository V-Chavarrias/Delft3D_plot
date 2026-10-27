# SPDX-License-Identifier: GPL-3.0-or-later
"""Write guarded MP4 movies from ordered map-frame images."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from d3dplot.store import OutputWriteGuard, atomic_path, atomic_write_text


class MovieWriterError(RuntimeError):
    """A map movie could not be written safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def write_mp4(
    frames: tuple[Path, ...],
    output_path: Path,
    duration_s: float,
    fingerprint: str,
    guard: OutputWriteGuard,
) -> bool:
    """Write an MP4 atomically, returning true when its fingerprint is reused."""
    if not frames:
        raise ValueError("A movie needs at least one frame.")
    if duration_s <= 0:
        raise ValueError("Movie frame duration must be positive.")
    if not fingerprint:
        raise ValueError("Movie fingerprint must be non-empty.")

    metadata_path = output_path.with_suffix(f"{output_path.suffix}.json")
    guard.validate_target(output_path)
    guard.validate_target(metadata_path)
    if _cache_matches(output_path, metadata_path, fingerprint):
        return True

    try:
        imageio: Any = import_module("imageio.v2")
        ffmpeg: Any = import_module("imageio_ffmpeg")
        ffmpeg.get_ffmpeg_exe()
    except (ImportError, OSError, RuntimeError) as error:
        raise MovieWriterError(
            "V-ENV-001",
            "MP4 export requires the optional movie dependencies; "
            "install delft3d-plot[movies].",
        ) from error

    with atomic_path(output_path, guard, temporary_suffix=".mp4") as temporary:
        try:
            writer = imageio.get_writer(
                str(temporary),
                format="FFMPEG",
                mode="I",
                fps=1.0 / duration_s,
                codec="libx264",
                macro_block_size=1,
            )
        except (ImportError, RuntimeError) as error:
            raise MovieWriterError(
                "V-ENV-001",
                "Could not initialize the MP4 encoder; install delft3d-plot[movies].",
            ) from error

        try:
            with writer:
                frame_shape: tuple[int, ...] | None = None
                for frame_path in frames:
                    try:
                        with Image.open(frame_path) as image:
                            pixels = np.asarray(image.convert("RGB"))
                    except (OSError, UnidentifiedImageError) as error:
                        raise MovieWriterError(
                            "V-PLOT-004",
                            f"Could not read movie frame {frame_path}: {error}",
                        ) from error
                    if frame_shape is None:
                        frame_shape = pixels.shape
                    elif pixels.shape != frame_shape:
                        raise MovieWriterError(
                            "V-PLOT-004",
                            "Movie frames must have identical image dimensions.",
                        )
                    pad_height = pixels.shape[0] % 2
                    pad_width = pixels.shape[1] % 2
                    if pad_height or pad_width:
                        pixels = np.pad(
                            pixels,
                            ((0, pad_height), (0, pad_width), (0, 0)),
                            mode="edge",
                        )
                    writer.append_data(pixels)
        except MovieWriterError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise MovieWriterError(
                "V-PLOT-004",
                f"Could not encode MP4 movie {output_path}: {error}",
            ) from error

    atomic_write_text(
        metadata_path,
        json.dumps({"fingerprint": fingerprint}, sort_keys=True) + "\n",
        guard,
    )
    return False


def _cache_matches(output_path: Path, metadata_path: Path, fingerprint: str) -> bool:
    try:
        if (
            not output_path.is_file()
            or output_path.stat().st_size == 0
            or not metadata_path.is_file()
        ):
            return False
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(metadata, dict) and metadata.get("fingerprint") == fingerprint
