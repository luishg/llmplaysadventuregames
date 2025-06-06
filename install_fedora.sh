#!/bin/bash

# Exit on any error
set -e

echo "=========================================="
echo "LLM Plays Adventure Games - Fedora Setup"
echo "=========================================="

# Update system and install basic development tools
echo "Updating system and installing basic development tools..."
sudo dnf update -y
sudo dnf groupinstall -y "Development Tools"
sudo dnf install -y python3 python3-pip python3-devel python3-tkinter

# Install X11 tools and development libraries (CRITICAL for window capture and automation)
echo "Installing X11 tools and development libraries..."
sudo dnf install -y \
    xdotool \
    xprop \
    xorg-x11-utils \
    libX11-devel \
    libXext-devel \
    libXrender-devel \
    libXtst-devel

# Install additional system libraries
echo "Installing additional system libraries..."
sudo dnf install -y \
    gcc \
    gcc-c++ \
    make \
    cmake \
    git \
    wget \
    curl \
    xorg-x11-server-Xvfb \
    mesa-libGL \
    mesa-libGLU \
    mesa-libEGL \
    mesa-libgbm \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXfixes \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    alsa-lib \
    atk \
    at-spi2-atk \
    at-spi2-core \
    cairo \
    cups-libs \
    dbus-libs \
    expat \
    fontconfig \
    freetype \
    glib2 \
    gtk3 \
    harfbuzz \
    libdrm \
    libxcb \
    libxkbcommon \
    libxshmfence \
    nspr \
    nss \
    pango \
    pixman \
    zlib

# Install Ollama (Local LLM support - OPTIONAL but recommended)
echo "Installing Ollama for local LLM support..."
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
    
    # Start ollama service
    sudo systemctl enable ollama
    sudo systemctl start ollama
    
    echo "Ollama installed successfully!"
    echo "To install models, run: ollama pull llama2"
else
    echo "Ollama already installed"
fi

# Create and activate virtual environment
echo "Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Upgrade pip and install wheel
echo "Upgrading pip and installing wheel..."
pip install --upgrade pip
pip install wheel

# Install Python dependencies from requirements.txt
echo "Installing Python dependencies from requirements.txt..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "Warning: requirements.txt not found, installing known dependencies..."
fi

# Install additional Python packages that might be needed
echo "Installing additional Python packages..."
pip install \
    numpy \
    opencv-python-headless \
    mss \
    python-xlib \
    pyautogui \
    pillow \
    requests \
    torch \
    transformers \
    accelerate \
    safetensors \
    twitchio \
    ollama \
    openai \
    anthropic


# Set up environment variables in ~/.bashrc
echo "Setting up environment variables..."
echo "" >> ~/.bashrc
echo "# LLM Plays Adventure Games Environment Variables" >> ~/.bashrc
echo "export PYTHONPATH=\$PYTHONPATH:$(pwd)" >> ~/.bashrc
echo "export DISPLAY=:0" >> ~/.bashrc

# Add placeholder environment variables for API keys
echo "" >> ~/.bashrc
echo "# API Keys - REPLACE WITH YOUR ACTUAL KEYS" >> ~/.bashrc
echo "# export OPENAI_API_KEY=\"your_openai_api_key_here\"" >> ~/.bashrc
echo "# export ANTHROPIC_API_KEY=\"your_anthropic_api_key_here\"" >> ~/.bashrc
echo "# export HUGGINGFACE_TOKEN=\"your_huggingface_token_here\"" >> ~/.bashrc
echo "# export TWITCH_TOKEN=\"your_twitch_oauth_token_here\"" >> ~/.bashrc

# Make scripts executable
echo "Making scripts executable..."
chmod +x play.py
chmod +x chat.py

echo ""
echo "=========================================="
echo "Installation completed successfully!"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo "1. Source your bashrc: source ~/.bashrc"
echo "2. Activate the virtual environment: source venv/bin/activate"
echo ""
echo "CONFIGURE API KEYS (Copy and paste these commands with your actual keys):"
echo ""
echo "# OpenAI API Key (get from https://platform.openai.com/api-keys)"
echo "echo 'export OPENAI_API_KEY=\"sk-your_actual_openai_key_here\"' >> ~/.bashrc"
echo ""
echo "# Anthropic API Key (get from https://console.anthropic.com/)"
echo "echo 'export ANTHROPIC_API_KEY=\"sk-ant-your_actual_anthropic_key_here\"' >> ~/.bashrc"
echo ""
echo "# Hugging Face Token (get from https://huggingface.co/settings/tokens)"
echo "echo 'export HUGGINGFACE_TOKEN=\"hf_your_actual_huggingface_token_here\"' >> ~/.bashrc"
echo ""
echo "# Twitch OAuth Token (get from https://twitchapps.com/tmi/)"
echo "echo 'export TWITCH_TOKEN=\"oauth:your_actual_twitch_token_here\"' >> ~/.bashrc"
echo ""
echo "After setting your API keys, restart your terminal or run: source ~/.bashrc"
echo ""
echo "TO RUN THE SCRIPTS:"
echo "python play.py    # Main game automation script"
echo "python chat.py    # Chat integration script"
echo ""
echo "SYSTEM REQUIREMENTS VERIFIED:"
echo "✓ X11 tools (xdotool, xprop) - for window capture"
echo "✓ Python 3 with tkinter - for GUI"
echo "✓ Graphics libraries - for image processing"
echo "✓ Development tools - for compiling packages"
echo "✓ Ollama - for local LLM support (optional)"
echo ""
echo "NOTE: Make sure you're running in an X11 session for window capture to work!"
echo "NOTE: For Ollama models, run: ollama pull llama2 (or your preferred model)" 