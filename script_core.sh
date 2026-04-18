#!/bin/bash
# UNIX Shell Script
# Script for testing scripts using docker
# images for veus-transfer project.
# Veus Technology, all rights reserved.
# Created: November 2019

# -------------------------------- Auxiliary Functions --------------------------------

# Function that calls a menu to select the platform
#
# Returns:
# IMAGE_LIST: Global string array variable. This variable contains the name
# of all the images to be used by docker.
function select_platform() {
    echo "Select the target Raspberry Pi platform:"
    echo "1 - All Raspberry Pi platforms"
    echo "2 - Raspberry Pi 32-bit  (armv7/bullseye)  — Pi 2 / 3 / 4 / Zero 2W"
    echo "3 - Raspberry Pi 64-bit  (aarch64/bookworm) — Pi 4 / 5 / Zero 2W"
    read PLATFORM_OPTION
    echo

    # Assertive

    # Checks if an option was selected
    if [ -z $PLATFORM_OPTION ]; then
        echo "Invalid option."
        echo "Script execution aborted."
        exit 1
    fi

    # Check selected options
    IFS=', ' read -r -a selectedOptions <<< "$PLATFORM_OPTION"

    # Creates image list
    for option in ${selectedOptions[@]}; do
        if (($option == 1)) || (($option == 2)); then
            IMAGE_LIST[0]="armv7/linux/bullseye"
        fi
        if (($option == 1)) || (($option == 3)); then
            IMAGE_LIST[1]="aarch64/linux/bookworm"
        fi
    done
}

# Function that calls a menu to select the binary to compile
#
# Returns:
# BINARY_LIST: Global string array variable. This variable contains the name
# of all the binaries to be compiled. Binary names must be same as .spec files.
function select_binary() {
    echo "Select a binary to compile:"
    echo "1 - omblepy"
    read SUB_OPTION
    echo

    # Assertive

    # Checks if an option was selected
    if [ -z $SUB_OPTION ]; then
        echo "Invalid option."
        echo "Script execution aborted."
        exit 1
    fi

    # Check selected options
    IFS=', ' read -r -a selectedOptions <<<"$SUB_OPTION"

    # Creates image list
    for option in ${selectedOptions[@]}; do
        if (($option == 1)); then
            BINARY_LIST[0]="omblepy"
        fi
    done
}

# Checks if a given command exists
command_exists() {
    command -v "$@" >/dev/null 2>&1
}

# Exports functions to other modules
export -f select_platform
export -f select_binary
export -f command_exists
