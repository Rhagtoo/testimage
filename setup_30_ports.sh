#!/bin/bash
# Setup 30 AdGuard SOCKS instances on ports 1080-1109
# Each with a different VPN location

BASE_DATA="/home/rhagtoo/.local/share/adguardvpn-cli"
LOG_FILE="/home/rhagtoo/testimage/setup_30_ports.log"

LOCATIONS=(
  "TOKYO" "SINGAPORE" "SYDNEY" "MUMBAI"
  "NEW YORK" "CHICAGO" "LOS ANGELES" "MIAMI" "TORONTO" "SAO PAULO"
  "HELSINKI" "STOCKHOLM" "OSLO" "COPENHAGEN"
  "LONDON" "AMSTERDAM" "FRANKFURT" "PARIS" "ZURICH"
  "MILAN" "MADRID" "VIENNA" "PRAGUE" "WARSAW"
  "BRUSSELS" "RIGA" "TALLINN" "VILNIUS"
  "HONG KONG" "SEOUL"
)

echo "=== Setup 30 AdGuard ports ===" | tee "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"

for i in $(seq 0 29); do
  port=$((1080 + i))
  location="${LOCATIONS[$i]}"
  home_dir="/tmp/ag_port_${port}"
  data_dir="${home_dir}/.local/share/adguardvpn-cli"

  echo "[$((i+1))/30] Setting up port $port → $location" | tee -a "$LOG_FILE"

  # Clone data directory
  rm -rf "$home_dir"
  mkdir -p "$data_dir"
  cp -r "$BASE_DATA"/* "$data_dir/" 2>/dev/null
  rm -f "$data_dir/vpn.pid" "$data_dir/vpn.socket"

  # Set SOCKS port
  HOME="$home_dir" adguardvpn-cli config set-socks-port "$port" 2>&1 | tail -1 | tee -a "$LOG_FILE"

  # Connect
  HOME="$home_dir" adguardvpn-cli connect -l "$location" -y 2>&1 | tail -1 | tee -a "$LOG_FILE"

  sleep 1
done

echo "" | tee -a "$LOG_FILE"
echo "=== Done: $(date) ===" | tee -a "$LOG_FILE"

# Show status
echo "" | tee -a "$LOG_FILE"
echo "Active ports:" | tee -a "$LOG_FILE"
ss -tlnp 2>/dev/null | grep adguardvpn-cli | grep -o '127.0.0.1:[0-9]*' | sort -u | tee -a "$LOG_FILE"
