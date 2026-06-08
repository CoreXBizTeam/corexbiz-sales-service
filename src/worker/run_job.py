"""Execute a lead pipeline run."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import UUID

from src.log import configure_logging, get_logger, log_action, log_run_progress
from src.worker.job_handoff import RunSpecError, load_run_spec

ROOT = Path(__file__).resolve().parents[2]
logger = get_logger(__name__)

Trace = Tuple[Union[str, int], str]


def _dispatch_webhook(run: Dict[str, Any], *, event: str, qualified_count: int) -> None:
    from src.api.serialize import serialize_row
    from src.db.pool import get_pool
    from src.db import repository as repo
    from src.worker.webhook import dispatch_run_webhook

    run_id = UUID(str(run["id"]))
    try:
        if dispatch_run_webhook(
            serialize_row(run), event=event, qualified_count=qualified_count
        ):
            with get_pool().connection() as conn:
                with conn.transaction():
                    repo.mark_webhook_sent(conn, run_id)
    except Exception as exc:
        log_action(
            logger,
            logging.ERROR,
            "WEBHOOK",
            f"run/{run.get('id')}",
            {"event": event},
            traces=[("error", str(exc))],
            exc_info=True,
        )


def _build_pipeline_config(run: Dict[str, Any]) -> Dict[str, Any]:
    criteria = dict(run.get("criteria") or {})
    if isinstance(criteria, str):
        criteria = json.loads(criteria)
    run_id = str(run["id"])
    source_type = str(run.get("source_type") or "")
    return {
        "run_id": run_id,
        "list_name": run.get("list_name"),
        "source_type": source_type,
        "criteria": criteria,
        "site_id": run.get("site_id"),
        "output_stub": str(ROOT / "runs" / f"{run_id}_{source_type}"),
    }


def _subprocess_traces(proc: subprocess.CompletedProcess[str]) -> List[Trace]:
    traces: List[Trace] = [(proc.returncode, "pipeline subprocess exited")]
    combined = _sanitize_pipeline_output(
        "\n".join(part for part in (proc.stderr, proc.stdout) if part).strip()
    )
    if not combined:
        return traces
    for line in combined.splitlines()[-12:]:
        stripped = line.strip()
        if stripped:
            traces.append(("out", stripped))
    return traces


def _sanitize_pipeline_output(text: str) -> str:
    """Drop noisy stderr (e.g. urllib3 LibreSSL warnings) from stored run errors."""
    kept: List[str] = []
    for line in text.splitlines():
        if "NotOpenSSLWarning" in line:
            continue
        if line.strip().startswith("warnings.warn("):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _pipeline_error_message(proc: subprocess.CompletedProcess[str]) -> str:
    combined = _sanitize_pipeline_output(
        "\n".join(part for part in (proc.stderr, proc.stdout) if part).strip()
    )
    if not combined:
        return "pipeline failed"
    for line in reversed(combined.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if "ApiError:" in stripped or "RuntimeError:" in stripped or "Error:" in stripped:
            return stripped[:2000]
        if stripped.startswith("Command "):
            continue
    return combined[:2000]


def _pipeline_output_logging_enabled() -> bool:
    return os.getenv("SALES_LOG_PIPELINE_OUTPUT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _run_pipeline_command(cmd: List[str], *, run_id: str) -> subprocess.CompletedProcess[str]:
    from src.config.env import subprocess_environ

    env = subprocess_environ()
    if not _pipeline_output_logging_enabled():
        return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)

    log_run_progress(
        run_id,
        "running",
        stage="pipeline",
        traces=[("exec", " ".join(Path(p).name for p in cmd[1:3]))],
    )
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    lines: List[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        stripped = line.strip()
        if stripped:
            log_action(
                logger,
                logging.INFO,
                "RUN",
                f"run/{run_id}",
                {"stage": "pipeline"},
                traces=[("out", stripped[:500])],
            )
    proc.wait()
    output = "".join(lines)
    code = proc.returncode if proc.returncode is not None else 1
    return subprocess.CompletedProcess(cmd, code, output, "")


def run_pipeline_subprocess(config: Dict[str, Any], sqlite_path: Path) -> None:
    """Run finder/qualifier pipeline into a temporary SQLite database."""
    run_id = str(config.get("run_id") or "")
    source_type = str(config.get("source_type") or "")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(config, tmp)
        config_path = tmp.name

    cmd = [
        sys.executable,
        str(ROOT / "run_lead_pipeline.py"),
        "--config",
        config_path,
        "--db",
        str(sqlite_path),
    ]
    log_run_progress(
        run_id,
        "running",
        source_type=source_type,
        stage="pipeline",
        data={"sqlite": str(sqlite_path)},
        traces=[("start", "run_lead_pipeline.py")],
    )
    try:
        proc = _run_pipeline_command(cmd, run_id=run_id)
    finally:
        Path(config_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        log_run_progress(
            run_id,
            "failed",
            source_type=source_type,
            stage="pipeline",
            traces=_subprocess_traces(proc),
            level=logging.ERROR,
        )
        err = _pipeline_error_message(proc)
        raise RuntimeError(err[:2000])

    log_run_progress(
        run_id,
        "running",
        source_type=source_type,
        stage="pipeline",
        traces=[("done", "run_lead_pipeline.py")],
    )


def _started_at_value(run: Dict[str, Any]) -> datetime | None:
    value = run.get("started_at")
    if isinstance(value, datetime):
        return value
    return None


def _persist_failed_run(run: Dict[str, Any], err: str) -> Dict[str, Any]:
    from src.db.pool import get_pool
    from src.db import repository as repo

    run_id = UUID(str(run["id"]))
    site_id = str(run["site_id"])
    failed = {
        **run,
        "status": "failed",
        "error": err[:2000],
        "message": "Lead run failed.",
        "finished_at": datetime.now(timezone.utc),
    }
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            repo.persist_run_result(
                conn,
                run_id=run_id,
                site_id=site_id,
                site_url=run.get("site_url"),
                list_name=run.get("list_name"),
                source_type=str(run.get("source_type") or ""),
                criteria=dict(run.get("criteria") or {}),
                notes=str(run.get("notes") or ""),
                webhook_url=run.get("webhook_url"),
                status="failed",
                error=failed["error"],
                message=failed["message"],
                started_at=_started_at_value(run),
            )
    return failed


def execute_run(run: Dict[str, Any], *, sqlite_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Execute pipeline for a run spec and persist results once at the end.

    Returns summary dict with status and sync counts.
    """
    from src.config.env import load_project_env

    load_project_env()
    from src.db.pool import get_pool
    from src.db import repository as repo
    from src.worker.sync_sqlite import init_empty_sqlite, sync_sqlite_to_postgres

    run_id = UUID(str(run["id"]))
    site_id = str(run["site_id"])
    source_type = str(run.get("source_type") or "")

    pool = get_pool()
    with pool.connection() as conn:
        existing = repo.get_run(conn, run_id)
        if existing is not None:
            existing_status = str(existing.get("status") or "")
            if existing_status in ("completed", "failed"):
                raise RuntimeError(f"run {run_id} already finished ({existing_status})")
            if existing_status == "queued":
                with conn.transaction():
                    repo.mark_run_running(
                        conn,
                        run_id,
                        message=f"Running pipeline ({source_type})…",
                    )
                existing = repo.get_run(conn, run_id) or existing
            run = {**run, **repo.run_row_to_spec(existing)}

    log_run_progress(
        run_id,
        "running",
        source_type=source_type,
        site_id=site_id,
        stage="worker",
        traces=[("start", "execute_run")],
    )

    db_path = sqlite_path
    if db_path is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sales-run-"))
        db_path = temp_dir / "pipeline.db"
        init_empty_sqlite(db_path)

    config = _build_pipeline_config(run)
    summary: Dict[str, Any] = {"run_id": str(run_id), "sqlite_path": str(db_path)}

    try:
        run_pipeline_subprocess(config, db_path)
        log_run_progress(
            run_id,
            "running",
            source_type=source_type,
            stage="sync",
            traces=[("start", "sqlite → Postgres")],
        )
        with pool.connection() as conn:
            with conn.transaction():
                repo.persist_run_result(
                    conn,
                    run_id=run_id,
                    site_id=site_id,
                    site_url=run.get("site_url"),
                    list_name=run.get("list_name"),
                    source_type=source_type,
                    criteria=dict(run.get("criteria") or {}),
                    notes=str(run.get("notes") or ""),
                    webhook_url=run.get("webhook_url"),
                    status="completed",
                    message=(
                        f"Lead list “{run.get('list_name') or run_id}” finished successfully."
                    ),
                    started_at=_started_at_value(run),
                )
                counts = sync_sqlite_to_postgres(
                    conn, db_path, run_id=run_id, site_id=site_id
                )
        completed = {
            **run,
            "status": "completed",
            "message": (
                f"Lead list “{run.get('list_name') or run_id}” finished successfully."
            ),
            "finished_at": datetime.now(timezone.utc),
        }
        summary.update({"status": "completed", **counts})
        log_run_progress(
            run_id,
            "completed",
            source_type=source_type,
            stage="sync",
            data=counts,
            traces=[("done", "synced to Postgres")],
        )
        _dispatch_webhook(
            completed,
            event="run.completed",
            qualified_count=counts.get("qualified_leads", 0),
        )
        return summary
    except Exception as exc:
        err = str(exc)
        log_run_progress(
            run_id,
            "failed",
            source_type=source_type,
            stage="worker",
            error=err,
            level=logging.ERROR,
            traces=[("error", err[:500])],
        )
        failed = _persist_failed_run(run, err)
        summary.update({"status": "failed", "error": err})
        _dispatch_webhook(failed, event="run.failed", qualified_count=0)
        raise
    finally:
        if sqlite_path is None and db_path is not None:
            parent = db_path.parent
            try:
                if db_path.exists():
                    db_path.unlink()
                if parent.name.startswith("sales-run-") and parent.exists():
                    parent.rmdir()
            except OSError:
                pass


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    from src.config.env import load_project_env

    load_project_env()
    configure_logging()

    parser = argparse.ArgumentParser(description="Execute one sales pipeline run")
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON run spec written by the API enqueue step",
    )
    args = parser.parse_args(argv)

    config_path = args.config
    try:
        run = load_run_spec(config_path=config_path)
        summary = execute_run(run)
        print(json.dumps(summary))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
