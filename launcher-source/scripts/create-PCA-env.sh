#!/usr/bin/env bash

# Usage: bash create-PCA-env.sh /path/to/conda

CONDA_PATH="$1"

echo "Using Conda Path: $CONDA_PATH . Creating Linux PCA Environment..."
source "$CONDA_PATH/bin/activate"
echo "Removing PCA Environment if it exists..."
conda env remove -n PCA -y
echo "Creating PCA Environment..."
conda init
conda activate base
python -m pip cache purge
#TODO check if requirements.yml path works for Linux
conda env create -v -f "config/requirements.yml"
echo "Packing Environment..."
rm -rf env
mkdir env
conda pack -n PCA -o env/PCA.tar.gz
chmod +x env/PCA.tar.gz
echo "Done Creating Environment!"