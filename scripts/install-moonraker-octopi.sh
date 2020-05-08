#!/bin/bash
# This script installs Moonraker on a Raspberry Pi machine running the
# OctoPi distribution.

PYTHONDIR="${HOME}/klippy-env"

# Step 1:  Verify Klipper has been installed
check_klipper()
{
    if [ "$(systemctl list-units --full -all -t service --no-legend | grep -F "klipper.service")" ]; then
        echo "Klipper service found!"
    else
        echo "Klipper service not found, please install Klipper first"
        exit -1
    fi

    if [ -d ${PYTHONDIR} ]; then
        echo "Klippy virtualenv found!  Installing tornado..."
        ${PYTHONDIR}/bin/pip install tornado
    else
        echo "Klipper Virtual ENV not installed, check your Klipper installation"
        exit -1
    fi
}

# Step 2: Install startup script
install_script()
{
    report_status "Installing system start script..."
    sudo cp "${SRCDIR}/scripts/moonraker-start.sh" /etc/init.d/moonraker
    sudo update-rc.d moonraker defaults
}

# Step 3: Install startup script config
install_config()
{
    DEFAULTS_FILE=/etc/default/moonraker
    [ -f $DEFAULTS_FILE ] && return

    report_status "Installing system start configuration..."
    sudo /bin/sh -c "cat > $DEFAULTS_FILE" <<EOF
# Configuration for /etc/init.d/moonraker

MOONRAKER_USER=$USER

MOONRAKER_EXEC=${PYTHONDIR}/bin/python

MOONRAKER_ARGS="${SRCDIR}/moonraker/moonraker.py"

EOF
}

# Step 4: Start server
start_software()
{
    report_status "Launching Moonraker API Server..."
    sudo /etc/init.d/klipper stop
    sudo /etc/init.d/moonraker restart
    sudo /etc/init.d/klipper start
}

# Helper functions
report_status()
{
    echo -e "\n\n###### $1"
}

verify_ready()
{
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit -1
    fi
}

# Force script to exit if an error occurs
set -e

# Find SRCDIR from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd )"

# Run installation steps defined above
verify_ready
check_klipper
install_script
install_config
start_software
