#!/usr/bin/env bash

# Enable error handling
set -e

# Function to print colored status messages
print_status() {
    local color=$1
    local message=$2
    case $color in
        "green") echo -e "\033[0;92m$message\033[0m" >&2 ;;
        "red") echo -e "\033[0;91m$message\033[0m" >&2 ;;
        "blue") echo -e "\033[0;94m$message\033[0m" >&2 ;;
        *) echo "$message" >&2 ;;
    esac
}

# Store original user info when running with sudo
ORIGINAL_USER="${SUDO_USER:-$USER}"
ORIGINAL_HOME=$(eval echo ~$ORIGINAL_USER)

# Install required system dependency
print_status "blue" "Installing system dependency: libxcb-cursor0..."
if command -v apt-get &> /dev/null; then
    # Wait for dpkg/apt lock to be released
    while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1; do
        print_status "blue" "Waiting for package manager lock to be released..."
        sleep 3
    done
    sudo apt-get install -y libxcb-cursor0
    print_status "green" "libxcb-cursor0 installed."
else
    print_status "red" "apt-get not found. Please install libxcb-cursor0 manually."
fi

# Function to find conda installation directory
find_conda_installation() {
    local conda_dir=""
    
    # First check if conda is in PATH
    if command -v conda &> /dev/null; then
        print_status "blue" "Conda found in PATH, determining installation directory..."
        local conda_binary=$(command -v conda)
        local conda_bin_dir=$(dirname "$conda_binary")
        conda_dir=$(dirname "$conda_bin_dir")
        
        # Verify this points to a proper conda installation
        if [[ "$(basename "$conda_dir")" =~ ^(miniforge3|miniconda3|anaconda3)$ ]]; then
            echo "$conda_dir"
            return 0
        else
            # Search upwards to find the conda installation directory
            local current_dir="$conda_dir"
            while [ "$current_dir" != "/" ]; do
                if [[ "$(basename "$current_dir")" =~ ^(miniforge3|miniconda3|anaconda3)$ ]]; then
                    echo "$current_dir"
                    return 0
                fi
                current_dir=$(dirname "$current_dir")
            done
        fi
    fi
    
    # If not found in PATH or PATH doesn't lead to proper installation, search filesystem
    print_status "blue" "Conda not found in PATH or PATH doesn't lead to installation directory, searching filesystem..."
    
    # Define search locations (including original user's home when using sudo)
    local search_locations=("/opt" "/usr/local" "/root" "$HOME")
    if [ "$ORIGINAL_HOME" != "$HOME" ] && [ -d "$ORIGINAL_HOME" ]; then
        search_locations+=("$ORIGINAL_HOME")
    fi
    
    # Try common install locations first with limited depth
    for location in "${search_locations[@]}"; do
        if [ -d "$location" ]; then
            conda_dir=$(find "$location" -maxdepth 2 -type d \( -name "miniforge3" -o -name "miniconda3" -o -name "anaconda3" \) 2>/dev/null | head -n 1)
            if [ -n "$conda_dir" ] && [ -f "$conda_dir/bin/conda" ]; then
                echo "$conda_dir"
                return 0
            fi
        fi
    done
    
    # If not found, try deeper search
    print_status "blue" "Conda not found in common locations, trying deeper search..."
    for location in "${search_locations[@]}"; do
        if [ -d "$location" ]; then
            conda_dir=$(find "$location" -type d \( -name "miniforge3" -o -name "miniconda3" -o -name "anaconda3" \) 2>/dev/null | head -n 1)
            if [ -n "$conda_dir" ] && [ -f "$conda_dir/bin/conda" ]; then
                echo "$conda_dir"
                return 0
            fi
        fi
    done
    
    # Nothing found
    return 1
}

# Find conda installation
conda_directory=$(find_conda_installation)

if [ -z "$conda_directory" ]; then
    print_status "red" "Conda installation not found"
    print_status "red" "Please ensure conda is installed and accessible"
    set /p "conda_directory=Please enter the path to your root Conda installation(ex: C:\Users\YourUsername\miniforge3): "
fi

print_status "green" "Conda installation at: $conda_directory"

# Verify conda installation
if [ ! -f "$conda_directory/bin/conda" ]; then
    print_status "red" "Error: conda binary not found at $conda_directory/bin/conda"
    exit 1
fi

print_status "green" "Using conda installation: $conda_directory"

# Add conda to path
export PATH="$conda_directory/bin:$PATH"

# Check if PCA.tar.gz exists
if [ ! -f "./env/PCA.tar.gz" ]; then
    print_status "red" "PCA.tar.gz not found in ./env/ directory!"
    print_status "red" "Please ensure the file exists in the correct location"
    exit 1
fi

# Create conda envs directory inside the conda installation
conda_envs_dir="$conda_directory/envs"
print_status "blue" "Creating environments directory: $conda_envs_dir"
mkdir -p "$conda_envs_dir"

# Copy PCA.tar.gz to the envs directory
print_status "blue" "Copying PCA environment archive..."
cp "./env/PCA.tar.gz" "$conda_envs_dir/"

# Navigate to envs directory and create PCA directory
cd "$conda_envs_dir"
mkdir -p PCA

print_status "blue" "Extracting environment... (this may take a few minutes)"
print_status "blue" "Extracting to: $conda_envs_dir/PCA"

# Extract the environment
if ! tar -xzf PCA.tar.gz -C PCA; then
    print_status "red" "Failed to extract PCA environment"
    exit 1
fi

print_status "blue" "Setting permissions..."
# Make the PCA environment accessible to all users while preserving security
if ! chmod -R a+rX PCA; then
    print_status "red" "Failed to set permissions on PCA environment"
    exit 1
fi

# Validate environment structure
if [[ ! -d PCA/bin ]] || [[ ! -d PCA/lib ]]; then
    print_status "red" "Error: Extracted environment appears to be incomplete"
    print_status "red" "Missing bin or lib directories in: $conda_envs_dir/PCA"
    exit 1
fi

print_status "green" "Successfully installed PCA environment in: $conda_envs_dir/PCA"

# Clean up temporary files
print_status "blue" "Cleaning up temporary files..."
if ! rm -f "$conda_envs_dir/PCA.tar.gz"; then
    print_status "red" "Warning: Failed to clean up temporary files"
fi

print_status "green" "Installation completed successfully!"
print_status "blue" "Environment location: $conda_envs_dir/PCA"
print_status "blue" "You can now activate the environment with: conda activate PCA"