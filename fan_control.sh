#!/bin/bash

# User-defined temperature and PWM variables
CPU_LOW_TEMP=40        # Low temperature threshold for CPU
CPU_HIGH_TEMP=80       # High temperature threshold for CPU
DRIVE_LOW_TEMP=25      # Low temperature threshold for Drives
DRIVE_HIGH_TEMP=45     # High temperature threshold for Drives
NVME_LOW_TEMP=35       # Low temperature threshold for NVMe
NVME_HIGH_TEMP=60      # High temperature threshold for NVMe
MIN_PWM=30             # Minimum PWM value
MAX_PWM=128            # Maximum PWM value (255 = full speed)
OVERHEAT_THRESHOLD=10  # Temperature threshold above high temp for overheating protection

# Command line controlled variables
SLEEP_DURATION=5       # Default sleep duration between checks
OVERHEAT_PROTECTION=false  # Enable or disable overheating protection
VERBOSE=false          # Verbose flag
PWM_METHOD="lin"       # Default PWM calculation method

# Function to find a single hwmon directory by driver name
get_hwmon_dir() {
    local type=$1
    for dir in /sys/class/hwmon/*/; do
        if [ -f "${dir}name" ]; then
            name=$(cat "${dir}name")
            echo "$name" | grep -Eqi "$type|it86|it87|it8613" && {
                echo "${dir%/}"
                return
            }
        fi
    done
}
# get_hwmon_dir() {
#     local type=$1
#     for dir in /sys/class/hwmon/*/; do
#         if [ -f "${dir}name" ] && grep -q "^${type}$" "${dir}name" 2>/dev/null; then
#             echo "${dir%/}"
#             return
#         fi
#     done
# }

# Function to dynamically find all hwmon directories matching a driver name
get_hwmon_dirs() {
    local type=$1
    local dirs=()
    for dir in /sys/class/hwmon/*/; do
        if [ -f "${dir}name" ] && grep -q "^${type}$" "${dir}name" 2>/dev/null; then
            dirs+=("${dir%/}")
        fi
    done
    echo "${dirs[@]}"
}

# Function to print messages based on verbose flag
log() {
    if [ "$VERBOSE" = true ]; then
        echo "$1"
    fi
}

# Function to log errors
log_error() {
    logger "$1"
    echo "$1" >&2
}

# Function to shut down the system
shutdown_system() {
    log_error "Overheating detected. Shutting down the system immediately."
    shutdown -h now
}

# Function to calculate interpolated PWM value
calculate_pwm() {
    local temp=$1
    local low_temp=$2
    local high_temp=$3

    if [ "$temp" -lt "$low_temp" ]; then
        echo 0  # Turn off the fan if the temperature is below the low threshold
        return
    elif [ "$temp" -ge "$high_temp" ]; then
        echo $MAX_PWM
        return
    fi

    case $PWM_METHOD in
        lin)
            local range_temp=$((high_temp - low_temp))
            local range_pwm=$((MAX_PWM - MIN_PWM))
            local temp_offset=$((temp - low_temp))
            local pwm_value=$((MIN_PWM + (temp_offset * range_pwm / range_temp)))
            echo $pwm_value
            ;;
        log)
            local range_temp=$((high_temp - low_temp))
            local temp_offset=$((temp - low_temp + 1))
            local pwm_value=$(awk -v min=$MIN_PWM -v max=$MAX_PWM -v temp=$temp_offset -v range=$range_temp 'BEGIN { val = min + (max - min) * log(temp) / log(range + 1); if (val < min) val = min; if (val > max) val = max; printf "%.0f\n", val }')
            echo "$pwm_value"
            ;;
        exp)
            local range_temp=$((high_temp - low_temp))
            local temp_offset=$((temp - low_temp))
            local pwm_value=$(awk -v min=$MIN_PWM -v max=$MAX_PWM -v temp=$temp_offset -v range=$range_temp 'BEGIN { val = min + (max - min) * ((exp(temp / range) - 1) / (exp(1) - 1)); if (val < min) val = min; if (val > max) val = max; printf "%.0f\n", val }')
            echo "$pwm_value"
            ;;
        *)
            log_error "Unknown PWM calculation method: $PWM_METHOD"
            exit 1
            ;;
    esac
}

check_and_load_module() {
    if ! lsmod | awk '{print $1}' | grep -qx it87; then
        log "it87 module is not loaded. Attempting to load it..."

        if sudo modprobe it87; then
            log "it87 module loaded successfully."
        else
            log_error "Failed to load it87 module. Exiting."
            exit 1
        fi
    else
        log "it87 module is already loaded."
    fi
}

# Parse command line options
while getopts "vt:oc:" opt; do
    case ${opt} in
        v )
            VERBOSE=true
            ;;
        t )
            SLEEP_DURATION=$OPTARG
            ;;
        o )
            OVERHEAT_PROTECTION=true
            ;;
        c )
            PWM_METHOD=$OPTARG
            ;;
        \? )
            echo "Usage: $0 [-v] [-t sleep_duration] [-o] [-c pwm_method]"
            echo "Options:"
            echo "  -v                  Enable verbose mode."
            echo "  -t sleep_duration   Set the sleep duration between checks (default: 5 seconds)."
            echo "  -o                  Enable overheating protection."
            echo "  -c pwm_method       Set the fan curve to linear (lin), logarithmic (log), or exponential (exp). Default is linear (lin)."
            exit 1
            ;;
    esac
done

# Print the current user, sleep duration, overheating protection status, and PWM method
log "Current user: $(whoami)"
log "Sleep duration: ${SLEEP_DURATION} seconds"
log "Overheating protection enabled: $OVERHEAT_PROTECTION"
log "PWM calculation method: $PWM_METHOD"

# Check if the script is run as root, otherwise re-run with sudo
[ "$EUID" -eq 0 ] || exec sudo "$0" "$@"

# Check and load the it86 module if necessary
check_and_load_module

# Get hwmon base directories
it87Dir=$(get_hwmon_dir "it86|it87|it8613")
coretempDir=$(get_hwmon_dir "coretemp")

if [ -z "$it87Dir" ] || [ -z "$coretempDir" ]; then
    log_error "Error: it87 or coretemp hwmon directory not found."
    exit 1
fi

# Derive individual sysfs paths
fanSpeedDir="$it87Dir/pwm2"
fanRpmDir="$it87Dir/fan2_input"
fanEnableDir="$it87Dir/pwm2_enable"
cpuTempDir="$coretempDir/temp1_input"

# Get drivetemp and nvme directories
driveTempDirs=($(get_hwmon_dirs "drivetemp"))
nvmeTempDirs=($(get_hwmon_dirs "nvme"))

# Check if fan speed, fan RPM, and CPU temperature directories exist
if [ ! -e "$fanSpeedDir" ] || [ ! -e "$fanRpmDir" ] || [ ! -e "$cpuTempDir" ] || [ ! -e "$fanEnableDir" ]; then
    log_error "Error: fan speed, fan RPM, CPU temperature, or PWM enable directory not found."
    exit 1
fi

# Save original PWM mode and restore it on exit
originalPwmEnable=$(<"$fanEnableDir")
cleanup() {
    log "Restoring original fan control mode ($originalPwmEnable)."
    echo "$originalPwmEnable" > "$fanEnableDir" 2>/dev/null
}
trap cleanup EXIT INT TERM

# Enable manual PWM control
echo 1 > "$fanEnableDir"
log "PWM set to manual mode."

# Infinite loop to monitor and control fan speed based on the highest temperature
while true; do
    # Read the current fan speed, fan RPM, and CPU temperature
    fanSpeed=$(<"$fanSpeedDir")
    fanRpm=$(<"$fanRpmDir")
    cpuTemp=$(<"$cpuTempDir")
    cpuTemp=$((cpuTemp / 1000))  # Convert from millidegrees to degrees Celsius

    # Check for broken fan
    if [ "$fanSpeed" -ne 0 ] && [ "$fanRpm" -eq 0 ]; then
        log_error "Error: Fan is broken. PWM is $fanSpeed but RPM is 0."
        exit 1
    fi

    # Overheating protection check for CPU
    if [ "$OVERHEAT_PROTECTION" = true ] && [ "$cpuTemp" -ge $((CPU_HIGH_TEMP + OVERHEAT_THRESHOLD)) ]; then
        log_error "Error: CPU temperature ($cpuTemp°C) exceeded the overheating threshold."
        shutdown_system
    fi

    # Initialize maxTemp with the CPU temperature
    maxCPU=$cpuTemp

    # Check drive temperatures
    maxDrive=0
    for dir in "${driveTempDirs[@]}"; do
        driveTemp=$(<"$dir/temp1_input")
        driveTemp=$((driveTemp / 1000))  # Convert from millidegrees to degrees Celsius
        if [ "$driveTemp" -gt "$maxDrive" ]; then
            maxDrive=$driveTemp
        fi

        # Overheating protection check for Drives
        if [ "$OVERHEAT_PROTECTION" = true ] && [ "$driveTemp" -ge $((DRIVE_HIGH_TEMP + OVERHEAT_THRESHOLD)) ]; then
            log_error "Error: Drive temperature ($driveTemp°C) exceeded the overheating threshold."
            shutdown_system
        fi
    done

    # Check NVMe temperatures
    maxNVMe=0
    for dir in "${nvmeTempDirs[@]}"; do
        nvmeTemp=$(<"$dir/temp1_input")
        nvmeTemp=$((nvmeTemp / 1000))  # Convert from millidegrees to degrees Celsius
        if [ "$nvmeTemp" -gt "$maxNVMe" ]; then
            maxNVMe=$nvmeTemp
        fi

        # Overheating protection check for NVMe
        if [ "$OVERHEAT_PROTECTION" = true ] && [ "$nvmeTemp" -ge $((NVME_HIGH_TEMP + OVERHEAT_THRESHOLD)) ]; then
            log_error "Error: NVMe temperature ($nvmeTemp°C) exceeded the overheating threshold."
            shutdown_system
        fi
    done

    # Print the highest temperatures and current fan speed/RPM
    log "Max CPU Temp(Celsius): $maxCPU"
    log "Max Drive Temp(Celsius): $maxDrive"
    log "Max NVMe Temp(Celsius): $maxNVMe"
    log "fanSpeed PWM value: $fanSpeed"
    log "fan RPM: $fanRpm"

    # Calculate PWM values for CPU, Drive, and NVMe temperatures
    pwmCPU=$(calculate_pwm "$maxCPU" "$CPU_LOW_TEMP" "$CPU_HIGH_TEMP")
    pwmDrive=$(calculate_pwm "$maxDrive" "$DRIVE_LOW_TEMP" "$DRIVE_HIGH_TEMP")
    pwmNVMe=$(calculate_pwm "$maxNVMe" "$NVME_LOW_TEMP" "$NVME_HIGH_TEMP")

    # Determine the highest PWM value
    if [ "$pwmCPU" -ge "$pwmDrive" ] && [ "$pwmCPU" -ge "$pwmNVMe" ]; then
        pwmValue=$pwmCPU
        log "Setting PWM based on CPU temperature"
    elif [ "$pwmDrive" -ge "$pwmCPU" ] && [ "$pwmDrive" -ge "$pwmNVMe" ]; then
        pwmValue=$pwmDrive
        log "Setting PWM based on Drive temperature"
    else
        pwmValue=$pwmNVMe
        log "Setting PWM based on NVMe temperature"
    fi

    # Set the fan speed to the highest PWM value
    echo "$pwmValue" > "$fanSpeedDir"

    # Wait for the defined sleep duration before checking again
    sleep "$SLEEP_DURATION"
    log "============================================="
done
