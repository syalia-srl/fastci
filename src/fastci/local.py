"""Keeping a laptop usable while fastci runs: workers, a memory cap, low priority."""

from __future__ import annotations

import shutil
import subprocess

GIB = 1 << 30


def mem_available(meminfo: str = "/proc/meminfo") -> int | None:
    """MemAvailable in bytes, or None when it cannot be read."""
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def workers(cpus: int, available: int, share: float, per_worker: int) -> int:
    return max(1, min(cpus - 1, int(available * share // per_worker)))


def wrap(argv: list[str], available: int | None, share: float, systemd: bool) -> list[str]:
    nice = ["nice", "-n", "10", *argv]
    if not (systemd and available):
        return nice
    cap = f"MemoryMax={int(available * share)}"
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        cap,
        "-p",
        "MemorySwapMax=0",
        *nice,
    ]


def _ok(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def has_systemd_user() -> bool:
    if not shutil.which("systemd-run"):
        return False
    out = _ok(["systemctl", "--user", "is-system-running"])
    return out is not None and (out.returncode == 0 or out.stdout.strip() == "degraded")


def has_gpu() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    out = _ok(["nvidia-smi", "-L"])
    return out is not None and out.returncode == 0 and bool(out.stdout.strip())


def killed_by_cap(code: int) -> bool:
    return code in (137, -9)
