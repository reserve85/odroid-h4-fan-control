# ODROID H4 Fan Control Script

This repository contains a script to control the fan of an **ODROID H4**. The script adjusts fan speed based on temperature readings and is a Python port of an original shell script.

## Features

- Adjusts fan speed based on **CPU**, **Drive**, and **NVMe** temperatures.  
- Supports **linear**, **logarithmic**, and **exponential** fan curves.  
- Overheat protection with automatic shutdown.  
- Configurable sleep duration between temperature checks.  
- Verbose logging for troubleshooting.

## Requirements

- **ODROID H4** with the fan set to **software mode** in the BIOS.  
- **Python 3** (interpreter `python3`).  
- Optional: the `it87` kernel module if your hardware requires it.

> **Note:** Some ODROID H4 setups use the Hardkernel EC instead of `it87`. If your board uses the EC, you may need to adapt the script to read/write the EC sysfs paths instead of `it87`.

## Installation

1. **(Optional) Build and install the `it87` module** — only if your hardware requires it:
   ```bash
   git clone https://github.com/frankcrawford/it87.git
   cd it87
   make
   sudo make install
   sudo modprobe it87
   ```

2. **Clone this repository:**
   ```bash
   git clone https://github.com/reserve85/odroid-h4-fan-control.git
   cd odroid-h4-fan-control
   ```

3. **Make the Python script executable:**
   ```bash
   chmod +x fan_control.py
   ```

4. **Ensure Python 3 is installed:**
   ```bash
   python3 --version
   # If not installed:
   sudo apt update
   sudo apt install python3
   ```

5. **Create a systemd service to run the script at boot**

   Create `/etc/systemd/system/fan_control.service` with the following content (adjust the path and user if needed):

   ```ini
   [Unit]
   Description=Odroid H4 Fan Control (Python)
   After=multi-user.target

   [Service]
   Type=simple
   ExecStart=/usr/bin/python3 /home/reserve/odroid-h4-fan-control/fan_control.py -t 3 -o -c lin -v
   Restart=always
   RestartSec=2
   User=root

   [Install]
   WantedBy=multi-user.target
   ```

6. **Reload systemd and enable the service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable fan_control.service
   sudo systemctl start fan_control.service
   ```

## Usage

### Command line options

- `-v` : Enable verbose mode (detailed logging).  
- `-t <seconds>` : Set the sleep duration between checks (default: 5 seconds).  
- `-o` : Enable overheating protection (system will shut down if thresholds are exceeded).  
- `-c <lin|log|exp>` : Set the fan curve to linear (`lin`), logarithmic (`log`), or exponential (`exp`). Default is `lin`.

### Examples

Run the script manually with a 3 second interval, overheat protection enabled, linear curve, and verbose logging:

```bash
sudo python3 ./fan_control.py -t 3 -o -c lin -v
```

Start the systemd service (as configured above):

```bash
sudo systemctl start fan_control.service
```

View logs:

```bash
journalctl -fu fan_control.service
```

## Explanation of options

- **Verbose (`-v`)**: Enables detailed output for troubleshooting.  
- **Sleep duration (`-t`)**: Interval in seconds between temperature checks. Shorter intervals react faster but increase the frequency of reads.  
- **Overheat protection (`-o`)**: When enabled, the script will shut down the system if temperatures exceed configured thresholds by a safety margin.  
- **PWM method (`-c`)**: Determines how PWM is calculated from temperature:
  - `lin`: Linear mapping between temperature and PWM.  
  - `log`: Logarithmic mapping for finer control at lower temperatures.  
  - `exp`: Exponential mapping for more aggressive cooling as temperature rises.

## Notes

- The script reads temperatures from hwmon sysfs entries and writes PWM values to the appropriate `pwm` sysfs node. If your board exposes different sysfs paths (for example, Hardkernel EC paths), update the script accordingly.  
- The repository includes a systemd unit example that starts the script with `-t 3 -o -c lin -v`. Adjust the ExecStart line if you prefer different defaults.

## Credits

Based on the ODROID wiki article *Fan Speed Control with Temperature* and the original shell script. Ported to Python and adapted locally. YMMV.
