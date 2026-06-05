#!/bin/bash
set -e

cd "$(dirname "$0")"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install / update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Run
echo "Starting PredictArena AI..."
python main.py
