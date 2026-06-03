"""Dispatch pipeline runs to background workers."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from src.log import get_logger, log_action
from src.worker.job_handoff import RunSpecError, encode_run_spec, validate_run_spec, write_run_spec_file

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]


def worker_mode() -> str:
    return (os.getenv("SALES_WORKER_MODE") or "inline").strip().lower()


def _safe_execute(run: Dict[str, Any]) -> None:
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


def _write_job_config(run: Dict[str, Any]) -> Path:
    runs_dir = ROOT / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="sales-run-", dir=str(runs_dir))
    os.close(fd)
    config_path = Path(path)
    write_run_spec_file(run, config_path)
    return config_path


def _dispatch_subprocess_worker(run: Dict[str, Any]) -> None:
    """Detached worker process (local substitute for Cloud Run Job)."""
    validated = validate_run_spec(run)
    config_path = _write_job_config(validated)
    env = os.environ.copy()
    try:
        env["SALES_RUN_SPEC"] = encode_run_spec(validated)
    except RunSpecError:
        # Large criteria: config file handoff only (run_job reads --config first).
        env.pop("SALES_RUN_SPEC", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.worker.run_job", "--config", str(config_path)],
        cwd=str(ROOT),
        env=env,
    )
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run.get('id')}",
        {"pid": proc.pid, "mode": "subprocess", "config": str(config_path)},
        traces=[("spawn", "detached worker started")],
    )


def _dispatch_cloud_run_worker(run: Dict[str, Any]) -> None:
    from src.worker.cloud_run_dispatch import dispatch_cloud_run_job

    try:
        validated = validate_run_spec(run)
        result = dispatch_cloud_run_job(validated)
        log_action(
            logger,
            logging.INFO,
            "WORKER",
            f"run/{validated.get('id')}",
            result,
            traces=[("dispatch", "Cloud Run Job submitted")],
        )
    except (RunSpecError, RuntimeError) as exc:
        log_action(
            logger,
            logging.ERROR,
            "WORKER",
            f"run/{run.get('id')}",
            None,
            traces=[("error", str(exc))],
            exc_info=True,
        )
        raise


def _dispatch_job_worker(run: Dict[str, Any]) -> None:
    validate_run_spec(run)
    from src.worker.cloud_run_dispatch import cloud_run_job_configured

    if cloud_run_job_configured():
        _dispatch_cloud_run_worker(run)
    else:
        _dispatch_subprocess_worker(run)


def enqueue_run(run: Dict[str, Any]) -> None:
    """
    Dispatch a run based on SALES_WORKER_MODE:

    - inline: background thread in API process (local dev)
    - job: Cloud Run Job when SALES_CLOUD_RUN_JOB is set, else local subprocess
    - sync: blocking execute in caller (tests)
    - disabled: no-op (API tests)
    """
    run_id = run.get("id")
    mode = worker_mode()
    log_action(
        logger,
        logging.INFO,
        "WORKER",
        f"run/{run_id}",
        {"mode": mode},
        traces=[("enqueue", "dispatching pipeline")],
    )

    if mode in ("disabled", "off", "none"):
        log_action(
            logger,
            logging.INFO,
            "WORKER",
            f"run/{run_id}",
            {"mode": mode},
            traces=[("skip", "worker dispatch disabled")],
        )
        return

    if mode == "sync":
        validate_run_spec(run)
        _safe_execute(run)
        return

    if mode == "job":
        _dispatch_job_worker(run)
        return

    if mode == "inline":
        validate_run_spec(run)
        import threading

        thread = threading.Thread(
            target=_safe_execute,
            args=(run,),
            name=f"sales-run-{run_id}",
            daemon=True,
        )
        thread.start()
        return

    log_action(
        logger,
        logging.WARNING,
        "WORKER",
        f"run/{run_id}",
        {"mode": mode},
        traces=[("fallback", "unknown SALES_WORKER_MODE; using inline")],
    )
    import threading

    threading.Thread(target=_safe_execute, args=(run,), daemon=True).start()
