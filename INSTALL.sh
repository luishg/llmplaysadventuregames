#!/bin/bash

# INSTALL.sh
# This script installs system dependencies for the Graphic Adevnture Games AI Player project.
# It attempts to detect your package manager (apt or dnf) and install:
# - xdotool: For window management and interaction.
# - x11-utils/xorg-x11-utils: Provides xprop, used for window information.
# - python3-tk/python3-tkinter: For the Tkinter GUI library.
# - python3-pip: For installing Python packages.

echo "Maniac Mansion AI Player - System Dependency Installer"
echo "-----------------------------------------------------"

# Check for root/sudo privileges
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run this script with sudo or as root."
  echo "    Example: sudo ./INSTALL.sh"
  exit 1
fi

# Function to install packages using dnf
install_dnf() {
    echo "[+] Using dnf (Fedora/RHEL-based)..."
    dnf install -y xdotool xorg-x11-utils python3-tkinter python3-pip
    if [ $? -ne 0 ]; then
        echo "[!] Error installing packages with dnf. Please check the output above."
        exit 1
    fi
}

# Function to install packages using apt
install_apt() {
    echo "[+] Using apt (Debian/Ubuntu-based)..."
    apt-get update
    apt-get install -y xdotool x11-utils python3-tk python3-pip
    if [ $? -ne 0 ]; then
        echo "[!] Error installing packages with apt. Please check the output above."
        exit 1
    fi
}

# Detect package manager
if command -v dnf &> /dev/null; then
    install_dnf
elif command -v apt-get &> /dev/null; then
    install_apt
else
    echo "[!] Neither dnf nor apt-get found. Please install the following packages manually:"
    echo "    - xdotool"
    echo "    - x11-utils (or xorg-x11-utils for xprop)"
    echo "    - python3-tk (or python3-tkinter)"
    echo "    - python3-pip"
    exit 1
fi

echo ""
echo "[+] System dependencies installation script finished."
echo "-----------------------------------------------------"
echo "Next steps:"
echo "1. It is highly recommended to use a Python virtual environment:"
echo "   python3 -m venv .venv"
echo "   source .venv/bin/activate"
echo ""
echo "2. Install Python packages using pip:"
echo "   pip install -r requirements.txt"
echo ""
echo "3. If you plan to use local LLMs, ensure Ollama is installed and running."
echo "   Visit https://ollama.com/ for installation instructions."
echo "   Then pull a vision model, e.g.: ollama pull llava"
echo ""
echo "4. Configure API keys in play_v6.py or as environment variables if using remote LLMs."
echo ""
echo "Refer to README.md for full setup and usage instructions."

exit 0