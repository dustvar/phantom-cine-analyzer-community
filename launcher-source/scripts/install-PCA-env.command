#!/usr/bin/env bash
set +x

# possible Anaconda/Miniconda install paths
conda_dirs=(
    "$HOME/miniforge3"
    "$HOME/.local/miniforge3"
    "/opt/miniforge3"
    "$HOME/miniconda3"
    "$HOME/.local/miniconda3"
    "/opt/miniconda3"
    "$HOME/anaconda3"
    "$HOME/.local/anaconda3"
    "/opt/anaconda3"
)

conda_directory=""
for d in "${conda_dirs[@]}"; do
    if [ -d "$d" ]; then
        conda_directory="$d"
        break
    fi
done

if [ -z "$conda_directory" ]; then
    echo "Error: Could not find a conda installation."
    set /p "conda_directory=Please enter the path to your root Conda installation(ex: C:\Users\YourUsername\miniforge3): "
fi

echo "Using directory: $conda_directory"
if [ ! -d "$conda_directory" ] && [ ! -f "$conda_directory/scripts/activate" ]; then
    echo "Error: The specified conda directory does not exist."
    set /p "conda_directory=Please enter the path to your root Conda installation(ex: C:\Users\YourUsername\miniforge3): "
fi

echo "Installing PCA Environment..."

# Get the directory where this script is located (inside the DMG)
SCRIPT_DIR="$(dirname "$0")"
echo "Script location: $SCRIPT_DIR"


# The PCA environment archive (PCA.tar.gz) should be provided by the user.
# Please place it in the `support/` directory next to the DMG file when
# distributing, then drag & drop the file into this prompt or type the
# full path and press Enter.

echo ""
echo "IMPORTANT: This installer requires the PCA environment archive (PCA.tar.gz)."
echo "It should be located in the 'support' directory alongside the DMG file you extracted."
echo "You can drag & drop the PCA.tar.gz file into this terminal to paste its path."
echo ""
read -e -p "Enter full path to PCA.tar.gz: " src

if [ -z "$src" ] || [ ! -f "$src" ]; then
    echo ""
    echo "Error: PCA.tar.gz not found at the provided path: $src"
    echo "Please ensure you extracted the distribution zip and that the file is in the 'support' folder." 
    read -p "Press Enter to exit..."
    exit 1
fi

cp "$src" "$conda_directory/envs/"

cd "$conda_directory/envs"
mkdir -p PCA

tar -xzvf PCA.tar.gz -C PCA

# Set permissions (skip on Windows)
if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
    chmod -R u+rwX,go+rX PCA
fi


echo "Done Installing Environment!"

echo "Cleaning Up..."
rm -f "$conda_directory/envs/PCA.tar.gz"
exit 0