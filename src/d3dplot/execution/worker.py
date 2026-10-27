# SPDX-License-Identifier: GPL-3.0-or-later
"""Spawn-worker initialization and single-threaded library settings."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import QueueHandler
from multiprocessing.queues import Queue

_WORKER_ENVIRONMENT = {
    "MPLBACKEND": "Agg",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "HDF5_USE_FILE_LOCKING": "FALSE",
}


def initialize_worker(log_queue: Queue[logging.LogRecord]) -> None:
    """Route worker logs through the parent and disable nested threading."""
    for name, value in _WORKER_ENVIRONMENT.items():
        os.environ[name] = value
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(QueueHandler(log_queue))
    root_logger.setLevel(logging.INFO)


@contextmanager
def worker_environment() -> Iterator[None]:
    """Set worker environment variables before a spawn pool is created."""
    previous_values = {name: os.environ.get(name) for name in _WORKER_ENVIRONMENT}
    try:
        os.environ.update(_WORKER_ENVIRONMENT)
        yield
    finally:
        for name, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous_value
