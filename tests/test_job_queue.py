"""Tests for FIFO job queue."""

from __future__ import annotations

import threading
import time
import unittest
import uuid

from src.worker import job_queue


class TestJobQueue(unittest.TestCase):
    def setUp(self) -> None:
        job_queue.clear_queue()

    def tearDown(self) -> None:
        job_queue.clear_queue()

    def test_enqueue_returns_position(self) -> None:
        first = job_queue.enqueue_job(str(uuid.uuid4()))
        second = job_queue.enqueue_job(str(uuid.uuid4()))
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)
        self.assertEqual(job_queue.pending_count(), 2)

    def test_take_returns_fifo_order(self) -> None:
        a = str(uuid.uuid4())
        b = str(uuid.uuid4())
        job_queue.enqueue_job(a)
        job_queue.enqueue_job(b)
        self.assertEqual(job_queue.take_job(timeout=0.1), a)
        self.assertEqual(job_queue.take_job(timeout=0.1), b)

    def test_worker_waits_for_job(self) -> None:
        run_id = str(uuid.uuid4())
        taken: list[str] = []

        def worker() -> None:
            item = job_queue.take_job(timeout=2.0)
            if item:
                taken.append(item)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.05)
        job_queue.enqueue_job(run_id)
        thread.join(timeout=2.0)
        self.assertEqual(taken, [run_id])


if __name__ == "__main__":
    unittest.main()
