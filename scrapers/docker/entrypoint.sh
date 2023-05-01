#!/bin/bash
set -e

# Start Xvfb
export DISPLAY=:99
Xvfb :99 -screen 0 1024x768x24 &

# Run your Python script
conda run --no-capture-output -n bids python rfp_scraper.py "$@"
