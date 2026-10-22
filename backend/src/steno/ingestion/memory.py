"""Peak memory per stage, so a repository outgrowing its budget shows up before it fails (D127).

Linux keeps a process's peak resident memory (`VmHWM`); writing `5` to `/proc/self/clear_refs`
resets it, so each stage measures its own peak. Elsewhere, or when the reset isn't allowed,
nothing is recorded rather than a lifetime peak that would mislead.
"""

from pathlib import Path

STATUS = Path("/proc/self/status")
CLEAR_REFS = Path("/proc/self/clear_refs")
NEAR_LIMIT = 0.8  # share of a limit above which a stage is flagged


def reset_peak() -> bool:
    """Start measuring a new peak; False when this platform can't."""
    try:
        CLEAR_REFS.write_text("5")
    except OSError:
        return False
    return True


def peak_mb() -> int | None:
    """The peak resident memory since the last reset, in MB."""
    try:
        for line in STATUS.read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def near_limit(peak: float | None, limit: float | None) -> bool:
    return bool(peak and limit and peak > NEAR_LIMIT * limit)
