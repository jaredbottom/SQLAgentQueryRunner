#!/bin/bash

# SQL Agent Query Runner startup script

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "Error: .env file not found!"
    echo "Please copy .env.example to .env and configure your settings."
    exit 1
fi

# Start the Flask application
echo "Starting SQL Agent Query Runner..."
python app.py
