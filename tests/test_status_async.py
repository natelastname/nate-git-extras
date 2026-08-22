from pathlib import Path
import threading
import time

import nate_git_extras.status as status


def test_status_rescan_runs_off_ui_thread(tmp_path: Path, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    expected = (tmp_path, [])

    def slow_snapshot(*args, **kwargs):
        started.set()
        release.wait(timeout=1)
        return expected

    monkeypatch.setattr(status, "_snapshot", slow_snapshot)
    scan = status.ScanStatus()

    before = time.monotonic()
    assert status._start_scan(
        tmp_path,
        base="master",
        stale_days=14,
        include_remotes=False,
        scan=scan,
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert started.wait(timeout=0.5)
    assert status._poll_scan(scan) is None

    release.set()
    assert scan.thread is not None
    scan.thread.join(timeout=0.5)
    assert status._poll_scan(scan) == expected
