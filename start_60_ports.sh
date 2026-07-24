#!/bin/bash
# Launch 60 AdGuard SOCKS5 instances on ports 1080-1139
# Each with a different VPN location
# set -e disabled — continue on individual failures

BASE_DATA="$HOME/.local/share/adguardvpn-cli"
BASE_PORT=1080
NUM_PORTS=60

LOCATIONS=(
  "Stockholm"
  "Helsinki"
  "Frankfurt"
  "Copenhagen"
  "Amsterdam"
  "Oslo"
  "Milan"
  "Zurich"
  "Vienna"
  "Paris"
  "Riga"
  "Prague"
  "Warsaw"
  "London"
  "Dublin"
  "Brussels"
  "Bratislava"
  "Berlin"
  "Zagreb"
  "Bucharest"
  "Madrid"
  "Rome"
  "Manchester"
  "Barcelona"
  "Vilnius"
  "Belgrade"
  "Luxembourg"
  "Chișinău"
  "Tallinn"
  "Cairo"
  "Kyiv"
  "Lisbon"
  "Istanbul"
  "Athens"
  "Marseille"
  "Palermo"
  "Sofia"
  "Nicosia"
  "Tel Aviv"
  "Budapest"
  "Montreal"
  "Toronto"
  "New York"
  "Moscow (Virtual)"
  "Atlanta"
  "Miami"
  "Dallas"
  "Chicago"
  "Boston"
  "Denver"
  "Seattle"
  "Lagos"
  "Phoenix"
  "Dubai"
  "Silicon Valley"
  "Las Vegas"
  "Mexico City"
  "Hanoi"
  "Taipei"
  "Johannesburg"
)

echo "=== Starting $NUM_PORTS AdGuard SOCKS5 instances ==="
echo "Started: $(date)"

success=0
failed=0

for i in $(seq 0 $((NUM_PORTS - 1))); do
  port=$((BASE_PORT + i))
  location="${LOCATIONS[$i]}"
  home_dir="/tmp/ag_port_${port}"
  data_dir="${home_dir}/.local/share/adguardvpn-cli"

  echo -n "[$((i+1))/$NUM_PORTS] :$port → $location ... "

  rm -rf "$home_dir"
  mkdir -p "$data_dir"

  # Copy only config/auth files (NOT logs — they're huge)
  cp "$BASE_DATA/adguardvpn-cli.conf" "$data_dir/" 2>/dev/null || true
  cp "$BASE_DATA/cache_selector.dat" "$data_dir/" 2>/dev/null || true
  cp "$BASE_DATA/cache_selector_config.dat" "$data_dir/" 2>/dev/null || true

  # Config
  HOME="$home_dir" adguardvpn-cli config set-mode SOCKS >/dev/null 2>&1 || true
  HOME="$home_dir" adguardvpn-cli config set-socks-port "$port" >/dev/null 2>&1 || true

  # Connect
  if HOME="$home_dir" adguardvpn-cli connect -l "$location" >/dev/null 2>&1; then
    sleep 0.2
    status=$(HOME="$home_dir" adguardvpn-cli status 2>&1)
    if echo "$status" | grep -q "Connected"; then
      echo "✓"
      ((success++))
    else
      echo "✗ post-connect: $(echo $status | head -1)"
      ((failed++))
    fi
  else
    echo "✗ connect failed"
    ((failed++))
  fi
done

echo ""
echo "=== Done: $(date) ==="
echo "✓ Success: $success  ✗ Failed: $failed"
echo ""
active=$(ss -tlnp 2>/dev/null | grep -c 'adguardvpn')
echo "Active listeners: $active"
ss -tlnp 2>/dev/null | grep 'adguardvpn' | grep -oP '127.0.0.1:\K[0-9]+' | sort -n | tr '\n' ' '
echo ""
