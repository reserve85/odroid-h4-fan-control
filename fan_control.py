#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fan_control.py - ODROID H4 Fan Control
Features:
- CLI params: -t/--sleep (default 2), -v, -o, -c (lin|log|exp), pwm/t thresholds,
  --overheat-threshold, --pwm-hysteresis
- Robust CPU/NVMe/Drive detection (thermal, hwmon, sensors, nvme-cli, smartctl)
- Auto-detect PWM sysfs (prefers it8613/it87); resolved lazily, not at import
- Writes PWM only when changed (with hysteresis gate)
- Fail-safe: all sensors unavailable -> fan at pwm_max, not off
- Single status log per loop (rate-limited or on state transition)
- sleep() is last call in main loop
- Import-safe: no argparse/sysfs side effects at module import time
"""

from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Literal, Optional, Sequence, Tuple

# -------------------------
# Types
# -------------------------
TempReading = Optional[int]
CurveMethod = Literal["lin", "log", "exp"]

LOG_MIN_INTERVAL = 1.0  # seconds
SUBPROCESS_TIMEOUT = 5  # seconds


@dataclass(frozen=True)
class FanConfig:
    sleep: int
    overheat: bool
    curve: CurveMethod
    pwm_min: int
    pwm_max: int
    tmin: int
    tmax: int
    overheat_threshold: int = 95
    pwm_hysteresis: int = 2
    verbose: bool = False

    def validate(self) -> "FanConfig":
        if self.tmin >= self.tmax:
            raise ValueError(f"--tmin ({self.tmin}) must be < --tmax ({self.tmax})")
        if self.pwm_min > self.pwm_max:
            raise ValueError(f"--pwm-min ({self.pwm_min}) must be <= --pwm-max ({self.pwm_max})")
        if self.pwm_min < 0 or self.pwm_max > 255:
            raise ValueError(
                f"PWM values must be within 0..255 "
                f"(got pwm_min={self.pwm_min}, pwm_max={self.pwm_max})"
            )
        if self.sleep <= 0:
            raise ValueError(f"--sleep must be > 0 (got {self.sleep})")
        return self


@dataclass
class SensorSnapshot:
    cpu: TempReading = None
    drive: TempReading = None
    nvme: TempReading = None

    @classmethod
    def from_readings(
        cls, cpu: TempReading, drive: TempReading, nvme: TempReading
    ) -> "SensorSnapshot":
        return cls(cpu=cpu, drive=drive, nvme=nvme)

    @property
    def max_temp(self) -> TempReading:
        """Hottest reading in °C, or None iff *all* sensors are unavailable."""
        readings = [r for r in (self.cpu, self.drive, self.nvme) if r is not None]
        return max(readings) if readings else None


@dataclass
class LoopDecision:
    pwm: int
    should_write: bool
    should_shutdown: bool
    status: str
    fail_safe: bool = False


# -------------------------
# Logging (no side effects until configured)
# -------------------------
logger = logging.getLogger("fancontrol")


# -------------------------
# CLI
# -------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ODROID H4 fan control")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "-t",
        "--sleep",
        type=int,
        default=2,
        help="Sleep duration between checks in seconds (default: 2)",
    )
    parser.add_argument("-o", "--overheat", action="store_true", help="Enable overheat protection")
    parser.add_argument(
        "-c",
        "--curve",
        type=str,
        default="lin",
        choices=["lin", "log", "exp"],
        help="PWM method (lin|log|exp)",
    )
    parser.add_argument("--pwm-min", type=int, default=20, help="Minimum PWM value")
    parser.add_argument("--pwm-max", type=int, default=255, help="Maximum PWM value")
    parser.add_argument(
        "--tmin", type=int, default=30, help="Temperature (°C) at which PWM starts increasing"
    )
    parser.add_argument(
        "--tmax", type=int, default=80, help="Temperature (°C) at which PWM reaches max"
    )
    parser.add_argument(
        "--overheat-threshold",
        type=int,
        default=95,
        help="Temperature (°C) at which overheat shutdown triggers (default: 95)",
    )
    parser.add_argument(
        "--pwm-hysteresis",
        type=int,
        default=2,
        help="Rewrite PWM only when the new value differs by more than this (default: 2)",
    )
    return parser


def parse_cli(argv: Optional[Sequence[str]] = None) -> FanConfig:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = FanConfig(
        sleep=args.sleep,
        overheat=args.overheat,
        curve=args.curve,
        pwm_min=args.pwm_min,
        pwm_max=args.pwm_max,
        tmin=args.tmin,
        tmax=args.tmax,
        overheat_threshold=args.overheat_threshold,
        pwm_hysteresis=args.pwm_hysteresis,
        verbose=args.verbose,
    )
    try:
        return config.validate()
    except ValueError as exc:
        parser.error(str(exc))  # exits with code 2 and a clean message
        raise  # unreachable; keeps type-checkers happy


# -------------------------
# Helpers: file read/write
# -------------------------
def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r") as f:
            s = f.read().strip()
            if not s:
                return None
            return int(float(s))
    except (OSError, ValueError):
        return None


# -------------------------
# Temperature readers
# -------------------------
def scale_temp(raw: int, *, millidegrees: bool) -> int:
    """Convert a raw sensor value to whole °C by source contract (#12).

    hwmon/thermal_zone inputs are always millidegrees → always divide by 1000.
    Sources that already report whole degrees (e.g. parsed `sensors` output)
    pass ``millidegrees=False``.
    """
    if millidegrees:
        return raw // 1000
    return raw


# Memoizes "path/source absent" probes so NVMe/sensors aren't respawned every
# loop iteration (#9).
_missing_path_cache: Dict[str, bool] = {}


def _probe_missing(key: str) -> bool:
    """Return True (and cache) if ``key`` is known-absent."""
    return _missing_path_cache.get(key, False)


def _mark_missing(key: str) -> None:
    _missing_path_cache[key] = True


def read_nvme_temp() -> TempReading:
    temps = []

    # Try all NVMe namespaces via nvme-cli; cache absence of devices (#9).
    if not _probe_missing("nvme_devices"):
        devs = sorted(glob.glob("/dev/nvme*n1"))
        if not devs:
            _mark_missing("nvme_devices")
        for dev in devs:
            try:
                out = subprocess.check_output(
                    ["nvme", "smart-log", dev],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=SUBPROCESS_TIMEOUT,
                )
                for line in out.splitlines():
                    if "temperature" in line.lower():
                        m = re.search(r"(-?\d+)", line)
                        if m:
                            temps.append(int(m.group(1)))
                            break
            except (OSError, subprocess.SubprocessError):
                pass

    if temps:
        return max(temps)

    # Fallback: hwmon (always millidegrees per source contract)
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = ""
        try:
            with open(os.path.join(hw, "name")) as f:
                name = f.read().strip().lower()
        except OSError:
            name = ""

        if "nvme" not in name:
            continue

        for f in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            v = _read_int(f)
            if v is not None:
                temps.append(scale_temp(v, millidegrees=True))

    return max(temps) if temps else None


def read_drive_temp() -> TempReading:
    """Best-effort drive temperature via ``smartctl -A`` on /dev/sd* (#17).

    Returns None when smartctl is unavailable, no drive is present, or no
    temperature line is found. Absence of devices/missing binary is cached so
    the probe does not respawn every loop iteration (#9).
    """
    if _probe_missing("smartctl") or _probe_missing("sd_devices"):
        return None

    devs = sorted(glob.glob("/dev/sd[a-z]"))
    if not devs:
        _mark_missing("sd_devices")
        return None

    temps = []
    for dev in devs:
        try:
            out = subprocess.check_output(
                ["smartctl", "-A", dev],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            _mark_missing("smartctl")
            return None
        except (OSError, subprocess.SubprocessError):
            continue

        for line in out.splitlines():
            # ATA attributes: temperature often in the last column of a row
            # that mentions "Temperature_Celsius" or similar.
            if "temperature" in line.lower():
                m = re.findall(r"(-?\d+)", line)
                if m:
                    # smartctl reports whole degrees Celsius for ATA; take the
                    # last numeric column (raw is last)
                    temps.append(int(m[-1]))
                    break

    return max(temps) if temps else None

# -------------------------
# PWM detection and write
# -------------------------
_pwm_path_cache: Optional[str] = None
_pwm_path_resolved = False


def get_pwm_path() -> Optional[str]:
    """Lazy accessor: resolve PWM sysfs path once, on first call (#5)."""
    global _pwm_path_cache, _pwm_path_resolved
    if not _pwm_path_resolved:
        _pwm_path_cache = find_pwm_path()
        _pwm_path_resolved = True
    return _pwm_path_cache


def find_pwm_path(
    preferred_names: Tuple[str, ...] = ("it8613", "it87", "w836", "nct", "ec", "odroid")
) -> Optional[str]:
    # prefer hwmon entries with matching name
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = ""
        try:
            with open(os.path.join(hw, "name")) as f:
                name = f.read().strip().lower()
        except OSError:
            name = ""
        if any(p in name for p in preferred_names):
            for pwm in sorted(glob.glob(os.path.join(hw, "pwm[0-9]"))):
                if os.path.isfile(pwm):
                    return pwm
    # fallback: first pwm* anywhere
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        for pwm in sorted(glob.glob(os.path.join(hw, "pwm[0-9]"))):
            if os.path.isfile(pwm):
                return pwm
    return None


def write_pwm(value: int, config: FanConfig, path: Optional[str]) -> bool:
    """Write a clamped PWM value to ``path``. Returns success (#7)."""
    v = int(max(min(value, config.pwm_max), config.pwm_min))
    if path:
        try:
            with open(path, "w") as f:
                f.write(str(v))
            logger.debug("Wrote PWM %s to %s", v, path)
            return True
        except PermissionError as exc:
            logger.warning("Permission denied writing PWM to %s: %s", path, exc)
            return False
        except FileNotFoundError as exc:
            logger.warning("PWM path vanished: %s: %s", path, exc)
            return False
        except OSError as exc:
            logger.warning("Failed to write PWM to %s: %s", path, exc)
            return False
    else:
        logger.debug("write_pwm(%s) called (no sysfs path)", v)
        return True


def _shutdown() -> None:
    """Single shutdown invocation (#2-tail)."""
    try:
        os.sync()
    except OSError:
        pass
    try:
        subprocess.run(["shutdown", "-h", "now"], check=False)
    except (OSError, subprocess.SubprocessError):
        logger.error("Failed to initiate shutdown")

# -------------------------
# PWM curves
# -------------------------
def compute_pwm_linear(temp: Optional[int], config: FanConfig) -> int:
    if temp is None:
        return config.pwm_min
    if config.tmin >= config.tmax:  # defensive (#4)
        return config.pwm_min
    if temp <= config.tmin:
        return config.pwm_min
    if temp >= config.tmax:
        return config.pwm_max
    ratio = (temp - config.tmin) / float(config.tmax - config.tmin)
    return int(config.pwm_min + ratio * (config.pwm_max - config.pwm_min))


def compute_pwm_log(temp: Optional[int], config: FanConfig) -> int:
    if temp is None:
        return config.pwm_min
    if config.tmin >= config.tmax:  # defensive (#4)
        return config.pwm_min
    x = max(0.0, min(1.0, (temp - config.tmin) / float(config.tmax - config.tmin)))
    pwm = int(
        config.pwm_min + (math.log1p(x * 9) / math.log1p(9)) * (config.pwm_max - config.pwm_min)
    )
    return max(config.pwm_min, min(config.pwm_max, pwm))


def compute_pwm_exp(temp: Optional[int], config: FanConfig) -> int:
    if temp is None:
        return config.pwm_min
    if config.tmin >= config.tmax:  # defensive (#4)
        return config.pwm_min
    x = max(0.0, min(1.0, (temp - config.tmin) / float(config.tmax - config.tmin)))
    pwm = int(config.pwm_min + (x**2) * (config.pwm_max - config.pwm_min))
    return max(config.pwm_min, min(config.pwm_max, pwm))


def compute_pwm(
    cpu: TempReading, drive: TempReading, nvme: TempReading, method: CurveMethod, config: FanConfig
) -> int:
    readings = [r for r in (cpu, drive, nvme) if r is not None]
    t = max(readings) if readings else None
    if method == "log":
        return compute_pwm_log(t, config)
    if method == "exp":
        return compute_pwm_exp(t, config)
    return compute_pwm_linear(t, config)

# -------------------------
# Loop seam (testable)
# -------------------------
@dataclass
class LoopDeps:
    read_cpu: Callable[[], TempReading]
    read_nvme: Callable[[], TempReading]
    read_drive: Callable[[], TempReading]
    write_pwm_fn: Callable[[int, FanConfig, Optional[str]], bool]
    shutdown_fn: Callable[[], None]
    sleep_fn: Callable[[float], None]
    clock: Callable[[], float]
    pwm_path: Optional[str]

    @classmethod
    def real(cls) -> "LoopDeps":
        return cls(
            read_cpu=read_cpu_temp,
            read_nvme=read_nvme_temp,
            read_drive=read_drive_temp,
            write_pwm_fn=write_pwm,
            shutdown_fn=_shutdown,
            sleep_fn=time.sleep,
            clock=time.time,
            pwm_path=get_pwm_path(),
        )


@dataclass
class LoopState:
    last_pwm: Optional[int] = None
    last_log_time: float = 0.0
    last_logged_status: Optional[str] = None
    shutdown_sent: bool = False


def decide(snapshot: SensorSnapshot, config: FanConfig, *, last_pwm: Optional[int]) -> LoopDecision:
    """Pure decision function: fail-safe, curve, hysteresis, overheat (#2/#3/#14)."""
    max_temp = snapshot.max_temp
    cpu_str = f"{snapshot.cpu}°C" if snapshot.cpu is not None else "N/A"
    drive_str = f"{snapshot.drive}°C" if snapshot.drive is not None else "N/A"
    nvme_str = f"{snapshot.nvme}°C" if snapshot.nvme is not None else "N/A"

    if max_temp is None:
        # Fail-safe: all sensors unavailable -> full speed, not off (#3).
        status = (
            f"CPU: {cpu_str}  Drive: {drive_str}  NVMe: {nvme_str}  "
            f"PWM: {config.pwm_max} (fail-safe: no readings)"
        )
        return LoopDecision(
            pwm=config.pwm_max,
            should_write=last_pwm != config.pwm_max,
            should_shutdown=False,
            status=status,
            fail_safe=True,
        )

    pwm = compute_pwm(snapshot.cpu, snapshot.drive, snapshot.nvme, config.curve, config)

    # Overheat at the exact threshold — no hidden margin (#2).
    should_shutdown = config.overheat and max_temp >= config.overheat_threshold

    # Hysteresis: rewrite only when delta > threshold (#14).
    if last_pwm is None:
        should_write = True
    else:
        should_write = abs(pwm - last_pwm) > config.pwm_hysteresis

    status = f"CPU: {cpu_str}  Drive: {drive_str}  NVMe: {nvme_str}  PWM: {pwm}"
    return LoopDecision(
        pwm=pwm,
        should_write=should_write,
        should_shutdown=should_shutdown,
        status=status,
        fail_safe=False,
    )


def run_loop_iteration(deps: LoopDeps, state: LoopState, config: FanConfig) -> LoopState:
    """One loop iteration: read -> decide -> act -> log (#6)."""
    try:
        snapshot = SensorSnapshot.from_readings(
            deps.read_cpu(), deps.read_drive(), deps.read_nvme()
        )
        decision = decide(snapshot, config, last_pwm=state.last_pwm)

        if decision.fail_safe:
            logger.error("No temperature readings available; fail-safe: PWM=%s", decision.pwm)

        if decision.should_write:
            if deps.write_pwm_fn(decision.pwm, config, deps.pwm_path):
                state.last_pwm = decision.pwm  # only cache successful writes (#7)
            # on failure: do NOT advance cache; retry next iteration
        else:
            state.last_pwm = decision.pwm

        now = deps.clock()
        # Rate-limit: require elapsed >= interval AND changed status, with a
        # forced log on state transitions (PWM change or overheat) (#10).
        status_changed = decision.status != state.last_logged_status
        interval_elapsed = (now - state.last_log_time) >= LOG_MIN_INTERVAL
        if status_changed or interval_elapsed:
            logger.info(decision.status)
            state.last_log_time = now
            state.last_logged_status = decision.status

        if decision.should_shutdown and not state.shutdown_sent:
            logger.critical(
                "Overheat threshold reached: shutting down (temp=%s)", snapshot.max_temp
            )
            deps.shutdown_fn()
            state.shutdown_sent = True

        deps.sleep_fn(config.sleep)  # sleep must be last call
        return state
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.exception("Unhandled exception in main loop: %s", exc)
        deps.sleep_fn(max(config.sleep, 5))  # configured backoff
        return state

def main(
    config: Optional[FanConfig] = None,
    *,
    max_iterations: Optional[int] = None,
    stop_event: Optional[object] = None,
    deps: Optional[LoopDeps] = None,
) -> None:
    if config is None:
        config = parse_cli()

    # Configure logging only here (never at import time) (#5).
    level = logging.DEBUG if config.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(message)s")

    if deps is None:
        deps = LoopDeps.real()

    # Startup diagnostics at INFO (#11).
    if deps.pwm_path:
        logger.info("Using PWM sysfs path: %s", deps.pwm_path)
    else:
        logger.error("No PWM sysfs path found; write_pwm will only log")

    logger.info(
        "fan_control.py started (sleep=%ss, curve=%s, overheat=%s, "
        "overheat_threshold=%s, pwm_hysteresis=%s, verbose=%s, "
        "pwm_min=%s, pwm_max=%s, tmin=%s, tmax=%s)",
        config.sleep,
        config.curve,
        config.overheat,
        config.overheat_threshold,
        config.pwm_hysteresis,
        config.verbose,
        config.pwm_min,
        config.pwm_max,
        config.tmin,
        config.tmax,
    )

    state = LoopState()
    iterations = 0
    while True:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break
        try:
            state = run_loop_iteration(deps, state, config)
            iterations += 1
        except KeyboardInterrupt:
            logger.info("fan_control.py interrupted by user")
            break
        if state.shutdown_sent:
            break


if __name__ == "__main__":
    main()
