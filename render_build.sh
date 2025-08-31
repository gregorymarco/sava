#!/bin/bash

# Render.com build script for Sava game application
# This script is specifically designed for Render's build environment

set -e  # Exit on any error

echo "🚀 Starting Render build for Sava game application..."

# Upgrade pip to the latest version
echo "📦 Upgrading pip..."
pip install --upgrade pip

# Install all dependencies from requirements.txt
echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo "✅ Build completed successfully!"
echo "🎮 Sava game application is ready for deployment!"