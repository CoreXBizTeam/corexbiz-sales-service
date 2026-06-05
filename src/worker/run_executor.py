"""Run pipeline execution wrapper for worker threads."""

from __future__ import annotations

import logging
from typing import Any

from src.log import get_logger, log_action

logger = get_logger(__name__)


def safe_execute_run(run: dict[str, Any]) -> None:
    from src.worker.run_job import execute_run

    try:
        execute_run(run)
    except Exception as exc:
        log_action(
            logger,
            logging.ERROR,
            "WORKER",
            f"run/{run.get('id')}",
            None,
            traces=[("error", str(exc))],
            exc_info=True,
        )
