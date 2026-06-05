# ODROID H4 Fan Control

A Python script to control the fan on an **ODROID H4**. The script adjusts fan speed based on temperature readings and provides robust detection, configurable curves, and optional overheat protection.

---

## Features

- Controls fan speed using **CPU**, **Drive**, and **NVMe** temperatures.  
- Supports **linear**, **logarithmic**, and **exponential** fan curves.  
- Optional **overheat protection** with automatic shutdown.  
- **Configurable sleep interval** between checks (default **2 seconds**).  
- **Verbose logging** for troubleshooting.  
- Automatic detection of the appropriate `hwmon`/`pwm` sysfs node (prefers `it8613`/`it87`).

---

## Requirements

- **ODROID H4** with the fan set to **software mode** in BIOS.  
- **Python 3** (`python3`) installed.  
- Optional: kernel module **it87** if your hardware requires it.  
- Optional: **nvme-cli** if you want NVMe temperature readings via NVMe SMART.

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

3. Make the script executable (script name **fan-control.py**):
   ```bash
   chmod +x fan-control.py
   ```

4. Ensure Python 3 is installed:
   ```bash
   python3 --version
   sudo apt update
   sudo apt install python3
   ```

5. Optional install `nvme-cli` for NVMe temperatures:
   ```bash
   sudo apt install nvme-cli
   ```

---

## Systemd Service Example

Create `/etc/systemd/system/fancontrol.service` with the following content. This example uses the recommended defaults:

```ini
[Unit]
Description=Odroid H4 Fan Control Python Service
After=multi-user.target

[Service]
Type=simple
WorkingDirectory=/home/reserve
ExecStart=/usr/bin/python3 /home/reserve/fan-control.py -v -o -c lin --tmin 40 --tmax 80 --pwm-min 30 --pwm-max 128 --overheat-threshold 10
Restart=always
RestartSec=2
User=root
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
- `--overheat-threshold <°C>` : Overheat shutdown threshold.  
- `-c`, `--curve <lin|log|exp>` : PWM curve type (default `lin`).  
- `--pwm-min <value>` : Minimum PWM value (example `30`).  
- `--pwm-max <value>` : Maximum PWM value (example `128`).  
- `--tmin <°C>` / `--tmax <°C>` : Temperature range for the curve (default `30`/`80`).

---

## Examples

Run manually with your requested settings:

```bash
sudo /usr/bin/python3 /home/reserve/fan-control.py -v -o -c lin --tmin 40 --tmax 80 --pwm-min 30 --pwm-max 128 --overheat-threshold 10
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

- The script prefers **coretemp**/hwmon CPU sensors (Package/Core) over ACPI thermal zones to avoid using chassis/ACPI temperatures.  
- If multiple sensors are available the script uses the highest relevant temperature to compute PWM by default. You can change this behavior in the script if you prefer CPU-only control.  
- The script writes PWM values to the detected `pwm` sysfs node. If your board exposes different sysfs paths (for example Hardkernel EC), update the script accordingly.  
- Default sleep interval is **2 seconds**. If you set a shorter interval, be mindful of increased sysfs reads and log volume.

---

## Customization

- To use separate temperature ranges per sensor (CPU, Drive, NVMe) or a different aggregation strategy, the script can be extended with per-sensor CLI flags and a custom `compute_pwm` strategy. If you want that, I can provide a patch.

---

## Troubleshooting

- If the script logs `No PWM sysfs path found`, run:
  ```bash
  for h in /sys/class/hwmon/hwmon*; do echo -n "$h: "; cat $h/name 2>/dev/null || echo "(no name)"; ls $h | grep -E '^pwm|^fan' 2>/dev/null || true; done
  ```
  and update the script to point to the correct `pwm` node if necessary.  
- If CPU temperature reads low (e.g., ACPI instead of coretemp), ensure `coretemp` is available and the script is preferring the `coretemp` hwmon entry.

---

## Credits

Based on the ODROID wiki article *Fan Speed Control with Temperature* and the original shell script. Ported to Python and adapted locally. YMMV.
