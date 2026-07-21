#!/bin/bash
# Setup multiple SOCKS5 proxy ports for parallel scanning.
# Each port gets its own VPN location via cloned AdGuard data dirs.
# Adapt for your proxy provider.

BASE_PORT=1080
NUM_PORTS=30

# VPN locations — one per port
LOCATIONS=(
  "TOKYO" "SINGAPORE" "SYDNEY" "MUMBAI"
  "NEW YORK" "CHICAGO" "LOS ANGELES" "MIAMI" "TORONTO" "SAO PAULO"
  "HELSINKI" "STOCKHOLM" "OSLO" "COPENHAGEN"
  "LONDON" "AMSTERDAM" "FRANKFURT" "PARIS" "ZURICH"
  "MILAN" "MADRID" "VIENNA" "PRAGUE" "WARSAW"
  "BRUSSELS" "RIGA" "TALLINN" "VILNIUS"
  "HONG KONG" "SEOUL"
)

BASE_DATA="$HOME/.local/share/adguardvpn-cli"

echo "=== Setting up $NUM_PORTS proxy ports ==="

for i in $(seq 0 $((NUM_PORTS - 1))); do
  port=$((BASE_PORT + i))
  location="${LOCATIONS[$i]}"
  home_dir="/tmp/proxy_port_${port}"
  data_dir="${home_dir}/.local/share/adguardvpn-cli"

  echo "[$((i+1))/$NUM_PORTS] Port $port -> $location"

  # Clone data directory (preserves login session)
  rm -rf "$home_dir"
  mkdir -p "$data_dir"
  cp -r "$BASE_DATA"/* "$data_dir/" 2>/dev/null
  rm -f "$data_dir/vpn.pid" "$data_dir/vpn.socket"

  # Set SOCKS port
  HOME="$home_dir" adguardvpn-cli config set-socks-port "$port" >/dev/null 2>&1

  # Connect
  HOME="$home_dir" adguardvpn-cli connect -l "$location" -y >/dev/null 2>&1

  sleep 1
done

echo "Done. Active ports:"
ss -tlnp 2>/dev/null | grep adguardvpn-cli | grep -oP '127.0.0.1:\K[0-9]+' | sort -n | tr '\n' ' '
echo
