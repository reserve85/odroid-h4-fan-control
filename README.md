# ODROID H4 Fan Control

A Python script to control the fan on an **ODROID H4**. The script adjusts fan speed based on temperature readings and provides robust detection, configurable curves, and optional overheat protection.

---

## Features

- Controls fan speed using **CPU**, **Drive** (best-effort, requires `smartctl`), and **NVMe** temperatures.  
- Supports **linear**, **logarithmic**, and **exponential** fan curves.  
- Optional **overheat protection** with automatic shutdown at a configurable threshold (default **95 °C**).  
- **Fail-safe:** if *all* temperature sensors are unavailable, the fan runs at **maximum** speed instead of turning off.  
- **Hysteresis:** the PWM value is only rewritten when it changes by more than `--pwm-hysteresis` (default **2**), reducing sysfs churn.  
- **Configurable sleep interval** between checks (default **2 seconds**).  
- **Verbose logging** for troubleshooting, with rate-limited status lines.  
- Automatic detection of the appropriate `hwmon`/`pwm` sysfs node (prefers `it8613`/`it87`).  
- **Import-safe & testable:** no side effects at import time; full `pytest` suite included.

---

## Requirements

- **ODROID H4** with the fan set to **software mode** in BIOS.  
- **Python 3** (`python3`) installed (3.8+).  
- Optional: kernel module **it87** if your hardware requires it.  
- Optional: **nvme-cli** if you want NVMe temperature readings via NVMe SMART.  
- Optional: **smartmontools** (`smartctl`) for SATA drive temperature readings.

> **Note:** Some ODROID H4 boards use the Hardkernel EC instead of `it87`. If your board uses the EC, you may need to adapt sysfs paths in the script.

---

## Installation

1. Optional build and install `it87` if required:
   ```bash
   git clone https://github.com/frankcrawford/it87.git
   cd it87
   make
   sudo make install
   sudo modprobe it87
   ```

2. Clone this repository:
   ```bash
   git clone https://github.com/reserve85/odroid-h4-fan-control.git
   cd odroid-h4-fan-control
   ```

3. Make the script executable (script name **fan_control.py**):
   ```bash
   chmod +x fan_control.py
   ```

4. Ensure Python 3 is installed:
   ```bash
   python3 --version
   sudo apt update
   sudo apt install python3
   ```

5. Optional install `nvme-cli` for NVMe temperatures and `smartmontools` for drive temperatures:
   ```bash
   sudo apt install nvme-cli smartmontools
   ```

---

## Systemd Service Example

Create `/etc/systemd/system/fancontrol.service` with the following content. Adjust the install path (`/opt/odroid-fan/`) to where you cloned the repo:

```ini
[Unit]
Description=ODROID H4 Fan Control Python Service
# Start after the filesystems and basic system init are available
After=sysinit.target local-fs.target

[Service]
Type=simple
WorkingDirectory=/opt/odroid-fan
ExecStart=/usr/bin/python3 /opt/odroid-fan/fan_control.py -v -o -c lin --tmin 40 --tmax 80 --pwm-min 30 --pwm-max 128 --overheat-threshold 95 --pwm-hysteresis 2
Restart=always
RestartSec=2
# Fan control must write to /sys/class/hwmon, which requires root.
# Additional hardening below restricts everything else.
User=root
ProtectSystem=strict
ReadWritePaths=/sys/class/hwmon
PrivateTmp=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fancontrol

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable fancontrol.service
sudo systemctl start fancontrol.service
sudo journalctl -u fancontrol.service -f -o short-iso-precise
```

---

## Command Line Options

- `-v`, `--verbose` : Enable verbose logging.  
- `-t`, `--sleep <seconds>` : Sleep interval between checks (default **2**).  
- `-o`, `--overheat` : Enable overheat protection (shutdown when threshold reached).  
- `--overheat-threshold <°C>` : Overheat shutdown threshold (default **95**). The shutdown triggers **exactly** at this temperature.  
- `--pwm-hysteresis <value>` : Rewrite the PWM value only when it changes by more than this (default **2**).  
- `-c`, `--curve <lin|log|exp>` : PWM curve type (default `lin`).  
- `--pwm-min <value>` : Minimum PWM value (example `30`).  
- `--pwm-max <value>` : Maximum PWM value (example `128`).  
- `--tmin <°C>` / `--tmax <°C>` : Temperature range for the curve (default `30`/`80`).

Invalid values (e.g. `--tmin >= --tmax`, `--sleep <= 0`, PWM outside `0..255`) are rejected with a clean error and exit code `2`.

---

## Examples

Run manually with your requested settings:

```bash
sudo /usr/bin/python3 /opt/odroid-fan/fan_control.py -v -o -c lin --tmin 40 --tmax 80 --pwm-min 30 --pwm-max 128 --overheat-threshold 95
```

Start the systemd service:

```bash
sudo systemctl start fancontrol.service
```

View logs:

```bash
sudo journalctl -u fancontrol.service -f -o short-iso-precise
```

---

## Behavior Notes

- The script prefers **coretemp**/hwmon CPU sensors (Package/Core) over ACPI thermal zones to avoid using chassis/ACPI temperatures, and uses the **hottest** matching core.  
- When multiple sensors are available the script uses the highest relevant temperature to compute PWM. If *all* sensors are unavailable it fails safe to **maximum** PWM.  
- The script writes PWM values to the detected `pwm` sysfs node. If your board exposes different sysfs paths (for example Hardkernel EC), update the script accordingly.  
- Default sleep interval is **2 seconds**. Status lines are rate-limited (min. 1 s) and forced on state changes (PWM change or overheat), so log volume stays bounded even with a short interval.  
- Drive temperature via `smartctl` is **best-effort**: if `smartctl` or a drive is missing, that sensor is simply reported as `N/A`.

---

## Development / Testing

The project ships with a full `pytest` suite under `tests/`. **All hardware I/O (sysfs, `subprocess`, sysfs writes) is mocked** — no test touches real hardware.

```bash
# install dev deps (pytest)
pip install pytest

# run the suite (terminates quickly; no live sensor loop runs during collection)
python -m pytest tests/
```

A dedicated regression test (`tests/test_import_safety.py`) asserts that importing `fan_control` performs **no** argparse/sysfs work — the historical cause of endless pytest runs.

Optional lint:

```bash
ruff check fan_control.py tests/
```

---

## Customization

- To use separate temperature ranges per sensor (CPU, Drive, NVMe) or a different aggregation strategy, the script can be extended with per-sensor CLI flags and a custom `compute_pwm` strategy.

---

## Troubleshooting

- If the script logs `No PWM sysfs path found`, run:
  ```bash
  for h in /sys/class/hwmon/hwmon*; do echo -n "$h: "; cat $h/name 2>/dev/null || echo "(no name)"; ls $h | grep -E '^pwm|^fan' 2>/dev/null || true; done
  ```
  and update the script to point to the correct `pwm` node if necessary.  
- If CPU temperature reads low (e.g., ACPI instead of coretemp), ensure `coretemp` is available and the script is preferring the `coretemp` hwmon entry.
- If drive temperatures always show `N/A`, install `smartmontools` (`sudo apt install smartmontools`) and ensure `smartctl` is on `PATH`.

---

## Credits

Based on the ODROID wiki article *Fan Speed Control with Temperature* and the original shell script. Ported to Python and adapted locally. YMMV.
