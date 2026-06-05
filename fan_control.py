#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fan-control.py - ODROID H4 Fan Control
Features:
- CLI params: -t/--sleep (default 2), -v, -o, -c (lin|log|exp), pwm/t thresholds
- Robust CPU/NVMe detection (thermal, hwmon, sensors, nvme-cli)
- Auto-detect PWM sysfs (prefers it8613/it87)
- Writes PWM only when changed
- Single status log per loop (rate-limited or on-change)
- sleep() is last call in main loop
"""

import argparse
import time
import logging
import os
import glob
import subprocess
import math
import re
import sys

# -------------------------
# CLI
# -------------------------
parser = argparse.ArgumentParser(description="ODROID H4 fan control")
parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
parser.add_argument("-t", "--sleep", type=int, default=2, help="Sleep duration between checks in seconds (default: 2)")
parser.add_argument("-o", "--overheat", action="store_true", help="Enable overheat protection")
parser.add_argument("-c", "--curve", type=str, default="lin", choices=["lin", "log", "exp"], help="PWM method (lin|log|exp)")
parser.add_argument("--pwm-min", type=int, default=20, help="Minimum PWM value")
parser.add_argument("--pwm-max", type=int, default=255, help="Maximum PWM value")
parser.add_argument("--tmin", type=int, default=30, help="Temperature (°C) at which PWM starts increasing")
parser.add_argument("--tmax", type=int, default=80, help="Temperature (°C) at which PWM reaches max")
args = parser.parse_args()

# -------------------------
# Logging
# -------------------------
level = logging.DEBUG if args.verbose else logging.INFO
logging.basicConfig(level=level, format="%(asctime)s %(message)s")
logger = logging.getLogger("fancontrol")

# -------------------------
# Constants
# -------------------------
SLEEP_DURATION = args.sleep
LOG_MIN_INTERVAL = 1.0  # seconds
OVERHEAT_THRESHOLD = 95
OVERHEAT_MARGIN = 5
PWM_MIN = args.pwm_min
PWM_MAX = args.pwm_max
TMIN = args.tmin
TMAX = args.tmax

# -------------------------
# Helpers: file read/write
# -------------------------
def _read_int(path):
    try:
        with open(path, "r") as f:
            s = f.read().strip()
            if not s:
                return None
            v = int(float(s))
            return v
    except Exception:
        return None

# -------------------------
# Temperature readers
# -------------------------
def read_cpu_temp():
    """
    Prefer coretemp / hwmon CPU sensors (Package/Core). Fallbacks: thermal_zone, sensors output.
    Returns int °C or None.
    """
    # 1) prefer hwmon entries with name 'coretemp' or temp labels containing 'package'/'core'
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = ""
        try:
            name = open(os.path.join(hw, "name")).read().strip().lower()
        except Exception:
            name = ""
        if "coretemp" in name or "core" in name or "package" in name:
            # try to read temp*_input and also check temp*_label if present
            for tfile in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
                # if there is a corresponding label file, prefer ones with 'package' or 'core'
                label = ""
                label_file = tfile.replace("_input", "_label")
                try:
                    label = open(label_file).read().strip().lower()
                except Exception:
                    label = ""
                if label and ("package" in label or "core" in label):
                    v = _read_int(tfile)
                    if v is None:
                        continue
                    return v // 1000 if v > 1000 else v
            # if no labeled temps, return first temp*_input found
            for tfile in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
                v = _read_int(tfile)
                if v is None:
                    continue
                return v // 1000 if v > 1000 else v

    # 2) fallback: try thermal_zone (acpitz etc.)
    for p in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        v = _read_int(p)
        if v is None:
            continue
        return v // 1000 if v > 1000 else v

    # 3) sensors command as last resort
    try:
        out = subprocess.check_output(["sensors"], stderr=subprocess.DEVNULL, text=True)
        # prefer "Package id" or "Core 0" lines
        for line in out.splitlines():
            if "package id" in line.lower() or re.search(r"core\s+\d+", line.lower()):
                m = re.search(r"(-?\d+(\.\d+)?)\s*°C", line)
                if m:
                    return int(round(float(m.group(1))))
        # otherwise take first °C found
        for line in out.splitlines():
            m = re.search(r"(-?\d+(\.\d+)?)\s*°C", line)
            if m:
                return int(round(float(m.group(1))))
    except Exception:
        pass

    logger.debug("read_cpu_temp: no CPU temperature source found")
    return None


def read_nvme_temp():
    temps = []

    # Try all NVMe namespaces via nvme-cli
    for dev in sorted(glob.glob("/dev/nvme*n1")):
        try:
            out = subprocess.check_output(
                ["nvme", "smart-log", dev],
                stderr=subprocess.DEVNULL,
                text=True
            )
            for line in out.splitlines():
                if "temperature" in line.lower():
                    m = re.search(r"(-?\d+)", line)
                    if m:
                        temps.append(int(m.group(1)))
                        break
        except Exception:
            pass

    if temps:
        return max(temps)

    # Fallback: hwmon
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            name = open(os.path.join(hw, "name")).read().strip().lower()
        except Exception:
            name = ""

        if "nvme" not in name:
            continue

        for f in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            v = _read_int(f)
            if v is not None:
                temps.append(v // 1000 if v > 1000 else v)

    return max(temps) if temps else None

def read_drive_temp():
    # placeholder: return None (no drive temp) to avoid false 0°C
    return None

# -------------------------
# PWM detection and write
# -------------------------
def find_pwm_path(preferred_names=("it8613", "it87", "w836", "nct", "ec", "odroid")):
    # prefer hwmon entries with matching name
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = ""
        try:
            name = open(os.path.join(hw, "name")).read().strip().lower()
        except Exception:
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

PWM_SYSFS = find_pwm_path()
if PWM_SYSFS:
    logger.debug("Using PWM sysfs path: %s", PWM_SYSFS)
else:
    logger.debug("No PWM sysfs path found; write_pwm will only log")

def write_pwm(value):
    v = int(max(min(value, PWM_MAX), PWM_MIN))
    if PWM_SYSFS:
        try:
            with open(PWM_SYSFS, "w") as f:
                f.write(str(v))
            logger.debug("Wrote PWM %s to %s", v, PWM_SYSFS)
        except Exception as e:
            logger.warning("Failed to write PWM to %s: %s", PWM_SYSFS, e)
    else:
        logger.debug("write_pwm(%s) called (no sysfs path)", v)

# -------------------------
# PWM curves
# -------------------------
def compute_pwm_linear(temp, tmin=TMIN, tmax=TMAX, pwm_min=PWM_MIN, pwm_max=PWM_MAX):
    if temp is None:
        return pwm_min
    if temp <= tmin:
        return pwm_min
    if temp >= tmax:
        return pwm_max
    ratio = (temp - tmin) / float(tmax - tmin)
    return int(pwm_min + ratio * (pwm_max - pwm_min))

def compute_pwm_log(temp, tmin=TMIN, tmax=TMAX, pwm_min=PWM_MIN, pwm_max=PWM_MAX):
    if temp is None:
        return pwm_min
    x = max(0.0, min(1.0, (temp - tmin) / float(tmax - tmin)))
    pwm = int(pwm_min + (math.log1p(x * 9) / math.log1p(9)) * (pwm_max - pwm_min))
    return max(pwm_min, min(pwm_max, pwm))

def compute_pwm_exp(temp, tmin=TMIN, tmax=TMAX, pwm_min=PWM_MIN, pwm_max=PWM_MAX):
    if temp is None:
        return pwm_min
    x = max(0.0, min(1.0, (temp - tmin) / float(tmax - tmin)))
    pwm = int(pwm_min + (x ** 2) * (pwm_max - pwm_min))
    return max(pwm_min, min(pwm_max, pwm))

def compute_pwm(cpu, drive, nvme, method="lin"):
    t = max(v for v in (cpu or 0, drive or 0, nvme or 0))
    if method == "lin":
        return compute_pwm_linear(t)
    if method == "log":
        return compute_pwm_log(t)
    if method == "exp":
        return compute_pwm_exp(t)
    return compute_pwm_linear(t)

# -------------------------
# Main loop
# -------------------------
def main():
    last_log_time = 0.0
    last_logged_state = None
    last_pwm = None

    logger.info("fan-control.py started (sleep=%ss, curve=%s, overheat=%s, verbose=%s)",
                SLEEP_DURATION, args.curve, args.overheat, args.verbose)

    while True:
        try:
            cpu = read_cpu_temp()
            nvme = read_nvme_temp()
            drive = read_drive_temp()

            pwm = compute_pwm(cpu, drive, nvme, args.curve)

            # write pwm only if changed
            if pwm != last_pwm:
                write_pwm(pwm)
                last_pwm = pwm

            cpu_str = f"{cpu}°C" if cpu is not None else "N/A"
            nvme_str = f"{nvme}°C" if nvme is not None else "N/A"
            drive_str = f"{drive}°C" if drive is not None else "N/A"
            status = f"CPU: {cpu_str}  Drive: {drive_str}  NVMe: {nvme_str}  PWM: {pwm}"

            now = time.time()
            if (now - last_log_time) >= LOG_MIN_INTERVAL or status != last_logged_state:
                logger.info(status)
                last_log_time = now
                last_logged_state = status

            # Overheat protection
            max_temp = max([t for t in (cpu or 0, drive or 0, nvme or 0)])
            if args.overheat and max_temp >= (OVERHEAT_THRESHOLD - OVERHEAT_MARGIN):
                logger.critical("Overheat threshold reached: shutting down (temp=%s)", max_temp)
                try:
                    os.sync()
                except Exception:
                    pass
                try:
                    subprocess.call(["shutdown", "-h", "now"])
                except Exception:
                    try:
                        os.system("shutdown -h now")
                    except Exception:
                        logger.error("Failed to initiate shutdown")
                break

            # sleep must be last call
            time.sleep(SLEEP_DURATION)

        except KeyboardInterrupt:
            logger.info("fan-control.py interrupted by user")
            break
        except Exception as e:
            logger.exception("Unhandled exception in main loop: %s", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
