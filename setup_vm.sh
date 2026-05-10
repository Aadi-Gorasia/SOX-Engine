#!/usr/bin/env bash
# setup_vm.sh — Run once on a fresh Ubuntu 22.04 / Debian 12 VM.
# Installs deps, compiles the engine, installs the server as a systemd service.
#
# Usage:
#   scp uttt_engine.h uttt_bitboard.h uttt_eval.h uttt_search.h \
#       uttt_uci.cpp uttt_server.py setup_vm.sh  user@YOUR_VM_IP:~/uttt/
#   ssh user@YOUR_VM_IP "cd ~/uttt && bash setup_vm.sh"

set -euo pipefail
cd "$(dirname "$0")"

echo "=== Installing build tools ==="
sudo apt-get update -qq
sudo apt-get install -y g++ libomp-dev python3 ufw

echo "=== Compiling engine ==="
g++ -std=c++17 -O3 -march=native -funroll-loops \
    -flto -fopenmp -DNDEBUG \
    -o uttt_engine uttt_uci.cpp -lm -lpthread
echo "    Build OK — $(./uttt_engine --version 2>/dev/null || echo 'binary ready')"

echo "=== Firewall: allow only your IP on port 9999 ==="
# Replace with your actual home/office IP, or remove the ufw lines
# if you're behind a VPN / private network.
# sudo ufw allow from YOUR.HOME.IP.HERE to any port 9999
# sudo ufw enable
echo "    (Skipped — edit this script to add your IP whitelist)"

echo "=== Installing systemd service ==="
SERVICE_FILE=/etc/systemd/system/uttt-engine.service
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=UTTT Engine TCP Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(which python3) $(pwd)/uttt_server.py --port 9999 --engine $(pwd)/uttt_engine
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable uttt-engine
sudo systemctl restart uttt-engine
echo "=== Done! Service status: ==="
sudo systemctl status uttt-engine --no-pager