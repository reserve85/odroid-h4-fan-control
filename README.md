# ODROID H4 Fan Control Script

This repository contains a script to control the fan of an ODROID H4. The script adjusts the fan speed based on temperature readings, using the `it87` module.

## Features

- Adjusts fan speed based on CPU, Drive, and NVMe temperatures.
- Supports linear, logarithmic, and exponential fan curves.
- Overheat protection with automatic shutdown.
- Configurable sleep duration between temperature checks.
- Verbose logging for troubleshooting.

## Requirements

- ODROID H4 with fan set to software mode in the BIOS.
- `it87` module installed (https://github.com/frankcrawford/it87).

## Installation

1. **Download and build the `it87` module:**
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

3. **Make the script executable:**
   ```bash
   chmod +x fan_control.sh
   ```

4. **Create a systemd service to run the script at boot:**

   Create the service file:
   ```ini name=/etc/systemd/system/fan_control.service
   [Unit]
   Description=Fan Control Service
   After=network.target

   [Service]
   ExecStart=/usr/bin/ionice -c2 -n7 /usr/bin/nice -n19 /path/to/fan_control.sh -t 3 -o -c lin
   Restart=always
   User=USERNAME

   [Install]
   WantedBy=multi-user.target
   ```

   Replace `/path/to/fan_control.sh` with the actual path to the script.

5. **Reload systemd and enable the service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable fan_control.service
   sudo systemctl start fan_control.service
   ```

## Usage

### Command Line Options

- `-v` : Enable verbose mode.
- `-t sleep_duration` : Set the sleep duration between checks (default: 5 seconds).
- `-o` : Enable overheating protection.
- `-c pwm_method` : Set the fan curve to linear (`lin`), logarithmic (`log`), or exponential (`exp`). Default is linear (`lin`).

### Example

To run the script manually with a sleep duration of 3 seconds, overheating protection enabled, and a linear fan curve:
```bash
sudo ionice -c2 -n7 nice -n19 ./fan_control.sh -t 3 -o -c lin
```

## Explanation of Options

- **Verbose Mode (`-v`)**:
  Enables detailed logging for troubleshooting purposes. Useful for understanding the script’s behavior and diagnosing issues.

- **Sleep Duration (`-t`)**:
  Specifies the interval (in seconds) between temperature checks. A shorter duration makes the script more responsive to temperature changes but may increase CPU usage.

- **Overheating Protection (`-o`)**:
  When enabled, the script will shut down the system if temperatures exceed the defined thresholds by a specified margin. This feature helps prevent hardware damage due to overheating.

- **PWM Method (`-c`)**:
  Defines how the fan speed is calculated based on temperature:
  - `lin`: Linear relationship between temperature and fan speed.
  - `log`: Logarithmic relationship, providing finer control at lower temperatures.
  - `exp`: Exponential relationship, providing more aggressive cooling as temperature rises.

## Credits

This script is based on code from the ODROID wiki: [Fan Speed Control with Temperature](https://wiki.odroid.com/odroid-h4/application_note/fan_speed_control_with_temp). and made hastily on a rainy saturday afternoon using github copilot. YMMV
