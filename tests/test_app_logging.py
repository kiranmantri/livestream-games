"""The app's own INFO lines ("fact-checker on: ...", "stt recovered", "fact checks requeued ...") must reach the server log.
Nothing configured logging, so under uvicorn only WARNING+ came out (Python's last-resort handler): found in the FC7 smoke test."""
import logging
import subprocess
import sys


def test_importing_the_app_makes_app_info_lines_visible():
    import app.main  # noqa: F401

    log = logging.getLogger("app.checkworker")
    assert log.isEnabledFor(logging.INFO)
    assert logging.getLogger("app").handlers, "the 'app' logger needs its own handler (root has none under uvicorn)"


def test_an_info_line_of_a_worker_is_printed_once_in_a_fresh_process():
    code = ("import app.main, logging; logging.getLogger('app.checkworker').info('fact-checker on: X, 2 worker(s)');"
            "logging.getLogger('app.checkworker').debug('noise')")
    p = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, timeout=60)
    out = p.stdout + p.stderr
    assert out.count("fact-checker on: X, 2 worker(s)") == 1, out
    assert "noise" not in out
