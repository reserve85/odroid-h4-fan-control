#!/usr/bin/env python3
"""
fan-control.py - ODROID H4 fan control (Python)
Defaults: sleep = 2s, PWM method = lin, overheat disabled, verbose off
"""

import argparse
import time
import logging
import os
import sys

# -------------------------
# CLI
# -------------------------
parser = argparse.ArgumentParser(description="ODROID H4 fan control")
parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
parser.add_argument("-t", "--sleep", type=int, default=2, help="Sleep duration between checks in seconds (default: 2)")
parser.add_argument("-o", "--overheat", action="store_true", help="Enable overheat protection")
parser.add_argument("-c", "--curve", type=str, default="lin", choices=["lin", "log", "exp"], help="PWM method (lin|log|exp)")
args = parser.parse_args()

# -------------------------
# Logging
# -------------------------
level = logging.DEBUG if args.verbose else logging.INFO
logging.basicConfig(level=level, format="%(asctime)s %(message)s")
logger = logging.getLogger("fancontrol")

# -------------------------
# Configuration / constants
# -------------------------
SLEEP_DURATION = args.sleep
LOG_MIN_INTERVAL = 1.0  # seconds: at most one status line per second unless state changes
PWM_SYSFS_PATH = "/sys/class/hwmon"  # placeholder: adapt if needed
# Overheat thresholds (example values, adapt to your needs)
OVERHEAT_THRESHOLD = 95  # °C
OVERHEAT_MARGIN = 5      # °C

# -------------------------
# Helper functions (replace read/write with your hwmon logic)
# -------------------------
def read_cpu_temp():
    # Replace with actual hwmon read; placeholder returns a stable test value
    # Example: read from /sys/class/thermal/thermal_zone*/temp or hwmon
    try:
        # Example attempt: read first cpu temp available
        # with fallback to 0 if not found
        return int(open("/sys/class/thermal/thermal_zone0/temp").read().strip()) // 1000
    except Exception:
        return 34

def read_drive_temp():
    # Replace with actual drive temperature reads; return 0 if none
    return 0

def read_nvme_temp():
    # Replace with actual NVMe temp read; fallback example
    return 37

def write_pwm(value):
    # Replace with actual pwm write to sysfs; clamp 0-255 or 0-100 depending on hw
    # Example placeholder: log the write
    logger.debug(f"write_pwm({value}) called")
    # Example sysfs write (uncomment and adapt path if applicable):
    # with open("/sys/class/hwmon/hwmon0/pwm1", "w") as f:
    #     f.write(str(value))

def compute_pwm_linear(temp, tmin=30, tmax=80, pwm_min=20, pwm_max=255):
    if temp <= tmin:
        return pwm_min
    if temp >= tmax:
        return pwm_max
    # linear interpolation
    ratio = (temp - tmin) / (tmax - tmin)
    return int(pwm_min + ratio * (pwm_max - pwm_min))

def compute_pwm(cpu, drive, nvme, method="lin"):
    # choose the controlling temperature
    t = max(cpu, drive, nvme)
    if method == "lin":
        return compute_pwm_linear(t)
    elif method == "log":
        # simple log-like curve (example)
        import math
        pwm = int(20 + (math.log1p(max(0, t - 20)) / math.log1p(60)) * (255 - 20))
        return max(20, min(255, pwm))
    elif method == "exp":
        # simple exponential curve (example)
        pwm = int(20 + (( (t - 20) / 60 ) ** 2) * (255 - 20))
        return max(20, min(255, pwm))
    else:
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
            max_drive = read_drive_temp()
            max_nvme = read_nvme_temp()

            pwm = compute_pwm(cpu, max_drive, max_nvme, args.curve)

            # write pwm only if changed (reduces sysfs writes)
            if pwm != last_pwm:
                write_pwm(pwm)
                last_pwm = pwm

            status = f"CPU: {cpu}°C  Drive: {max_drive}°C  NVMe: {max_nvme}°C  PWM: {pwm}"
            now = time.time()

            # log at most once per LOG_MIN_INTERVAL seconds, or immediately if state changed
            if (now - last_log_time) >= LOG_MIN_INTERVAL or status != last_logged_state:
                logger.info(status)
                last_log_time = now
                last_logged_state = status

            # Overheat protection (if enabled)
            if args.overheat and max(cpu, max_drive, max_nvme) >= (OVERHEAT_THRESHOLD - OVERHEAT_MARGIN):
                logger.critical("Overheat threshold reached: shutting down (temp=%s)", max(cpu, max_drive, max_nvme))
                # sync and shutdown
                os.sync()
                # use shutdown command
                os.system("shutdown -h now")
                # if shutdown fails, break
                break

            # --- IMPORTANT: sleep must be the last call in the loop ---
            time.sleep(SLEEP_DURATION)

        except KeyboardInterrupt:
            logger.info("fan-control.py interrupted by user")
            break
        except Exception as e:
            logger.exception("Unhandled exception in main loop: %s", e)
            # on unexpected error, wait a bit before retrying to avoid tight crash loop
            time.sleep(5)

if __name__ == "__main__":
    main()
