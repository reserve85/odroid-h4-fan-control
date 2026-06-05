#!/usr/bin/env python3
import os
import time
import argparse
import subprocess
import math
import sys
import logging

# -----------------------------
# Default configuration
# -----------------------------
CPU_LOW_TEMP = 40
CPU_HIGH_TEMP = 80
DRIVE_LOW_TEMP = 25
DRIVE_HIGH_TEMP = 45
NVME_LOW_TEMP = 35
NVME_HIGH_TEMP = 60
MIN_PWM = 30
MAX_PWM = 128
OVERHEAT_THRESHOLD = 10

# -----------------------------
# Helper functions
# -----------------------------
def log(msg, verbose):
    if verbose:
        print(msg)

def log_error(msg):
    logging.error(msg)
    print(msg, file=sys.stderr)

def shutdown_system():
    log_error("Overheating detected. Shutting down system immediately.")
    subprocess.run(["shutdown", "-h", "now"])

def read_sysfs(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except:
        return None

def write_sysfs(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
    except Exception as e:
        log_error(f"Failed to write {value} to {path}: {e}")

def get_hwmon_dir(match):
    for root, dirs, files in os.walk("/sys/class/hwmon"):
        for d in dirs:
            name_path = os.path.join(root, d, "name")
            if os.path.isfile(name_path):
                try:
                    name = read_sysfs(name_path)
                    if name and match.lower() in name.lower():
                        return os.path.join(root, d)
                except:
                    pass
    return None

def get_hwmon_dirs(match):
    results = []
    for root, dirs, files in os.walk("/sys/class/hwmon"):
        for d in dirs:
            name_path = os.path.join(root, d, "name")
            if os.path.isfile(name_path):
                name = read_sysfs(name_path)
                if name == match:
                    results.append(os.path.join(root, d))
    return results

# -----------------------------
# PWM calculation
# -----------------------------
def calculate_pwm(temp, low, high, method):
    if temp < low:
        return 0
    if temp >= high:
        return MAX_PWM

    if method == "lin":
        return MIN_PWM + int((temp - low) * (MAX_PWM - MIN_PWM) / (high - low))

    elif method == "log":
        rng = high - low
        offset = temp - low + 1
        val = MIN_PWM + (MAX_PWM - MIN_PWM) * math.log(offset) / math.log(rng + 1)
        return int(max(MIN_PWM, min(MAX_PWM, val)))

    elif method == "exp":
        rng = high - low
        offset = temp - low
        val = MIN_PWM + (MAX_PWM - MIN_PWM) * ((math.exp(offset / rng) - 1) / (math.e - 1))
        return int(max(MIN_PWM, min(MAX_PWM, val)))

    else:
        raise ValueError(f"Unknown PWM method: {method}")

# -----------------------------
# Main
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", action="store_true", help="Verbose mode")
    parser.add_argument("-t", type=int, default=5, help="Sleep duration")
    parser.add_argument("-o", action="store_true", help="Enable overheat protection")
    parser.add_argument("-c", type=str, default="lin", help="PWM method: lin, log, exp")
    args = parser.parse_args()

    verbose = args.v
    sleep_duration = args.t
    overheat = args.o
    method = args.c

    log(f"Sleep duration: {sleep_duration}", verbose)
    log(f"Overheat protection: {overheat}", verbose)
    log(f"PWM method: {method}", verbose)

    # Root check
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo"] + sys.argv)

    # Find hwmon dirs
    it87 = get_hwmon_dir("it87") or get_hwmon_dir("it86") or get_hwmon_dir("it8613")
    coretemp = get_hwmon_dir("coretemp")

    if not it87 or not coretemp:
        log_error("Error: it87 or coretemp hwmon directory not found.")
        sys.exit(1)

    fan_pwm = os.path.join(it87, "pwm2")
    fan_rpm = os.path.join(it87, "fan2_input")
    fan_enable = os.path.join(it87, "pwm2_enable")
    cpu_temp = os.path.join(coretemp, "temp1_input")

    drive_dirs = get_hwmon_dirs("drivetemp")
    nvme_dirs = get_hwmon_dirs("nvme")

    # Validate sysfs
    for p in [fan_pwm, fan_rpm, fan_enable, cpu_temp]:
        if not os.path.exists(p):
            log_error(f"Missing sysfs path: {p}")
            sys.exit(1)

    # Save original mode
    original_mode = read_sysfs(fan_enable)

    def cleanup():
        log(f"Restoring original fan mode: {original_mode}", verbose)
        write_sysfs(fan_enable, original_mode)

    import atexit
    atexit.register(cleanup)

    # Enable manual mode
    write_sysfs(fan_enable, 1)
    log("PWM set to manual mode", verbose)

    # Main loop
    while True:
        cpu = int(read_sysfs(cpu_temp)) // 1000

        # Overheat CPU
        if overheat and cpu >= CPU_HIGH_TEMP + OVERHEAT_THRESHOLD:
            log_error(f"CPU overheating: {cpu}°C")
            shutdown_system()

        # Drives
        max_drive = 0
        for d in drive_dirs:
            t = int(read_sysfs(os.path.join(d, "temp1_input"))) // 1000
            max_drive = max(max_drive, t)
            if overheat and t >= DRIVE_HIGH_TEMP + OVERHEAT_THRESHOLD:
                log_error(f"Drive overheating: {t}°C")
                shutdown_system()

        # NVMe
        max_nvme = 0
        for d in nvme_dirs:
            t = int(read_sysfs(os.path.join(d, "temp1_input"))) // 1000
            max_nvme = max(max_nvme, t)
            if overheat and t >= NVME_HIGH_TEMP + OVERHEAT_THRESHOLD:
                log_error(f"NVMe overheating: {t}°C")
                shutdown_system()

        pwm_cpu = calculate_pwm(cpu, CPU_LOW_TEMP, CPU_HIGH_TEMP, method)
        pwm_drive = calculate_pwm(max_drive, DRIVE_LOW_TEMP, DRIVE_HIGH_TEMP, method)
        pwm_nvme = calculate_pwm(max_nvme, NVME_LOW_TEMP, NVME_HIGH_TEMP, method)

        pwm = max(pwm_cpu, pwm_drive, pwm_nvme)

        write_sysfs(fan_pwm, pwm)

        log(f"CPU: {cpu}°C  Drive: {max_drive}°C  NVMe: {max_nvme}°C  PWM: {pwm}", verbose)

        time.sleep(sleep_duration)

# -----------------------------
if __name__ == "__main__":
    main()
