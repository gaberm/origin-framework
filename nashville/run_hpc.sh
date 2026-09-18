#!/bin/bash

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate origin
python -m nashville.run_hpc
