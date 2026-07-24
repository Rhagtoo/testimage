#!/bin/bash
# Setup 60 SOCKS5 proxy ports
BASE_PORT=1080
NUM_PORTS=60
BASE_DATA="$HOME/.local/share/adguardvpn-cli"

LOCATIONS=(
  "STOCKHOLM"
  "RIGA"
  "FRANKFURT"
  "COPENHAGEN"
  "HELSINKI"
  "PRAGUE"
  "OSLO"
  "WARSAW"
  "MILAN"
  "AMSTERDAM"
  "PARIS"
  "PALERMO"
  "BRUSSELS"
  "LONDON"
  "ZURICH"
  "MARSEILLE"
  "VIENNA"
  "VILNIUS"
  "TALLINN"
  "BRATISLAVA"
  "DUBLIN"
  "BERLIN"
  "ZAGREB"
  "KYIV"
  "ROME"
  "CHISINAU"
  "BUCHAREST"
  "MADRID"
  "LUXEMBOURG"
  "CAIRO"
  "BELGRADE"
  "MANCHESTER"
  "BARCELONA"
  "LISBON"
  "ISTANBUL"
  "ATHENS"
  "BUDAPEST"
  "SOFIA"
  "TEL AVIV"
  "NICOSIA"
  "NEW YORK"
  "MOSCOW"
  "TORONTO"
  "BOSTON"
  "MONTREAL"
  "ATLANTA"
  "CHICAGO"
  "DALLAS"
  "LAGOS"
  "MIAMI"
  "DENVER"
  "SEATTLE"
  "VANCOUVER"
  "MEXICO CITY"
  "LAS VEGAS"
  "PHOENIX"
  "DUBAI"
  "HANOI"
  "SILICON VALLEY"
  "JOHANNESBURG"
)

echo "=== Setting up $NUM_PORTS proxy ports ==="

for i in $(seq 0 $((NUM_PORTS - 1))); do
  port=$((BASE_PORT + i))
  location="${LOCATIONS[$i]}"
  home_dir="/tmp/proxy_port_${port}"
  data_dir="${home_dir}/.local/share/adguardvpn-cli"

  echo "[$((i+1))/$NUM_PORTS] Port $port -> $location"

  rm -rf "$home_dir"
  mkdir -p "$data_dir"
  cp -r "$BASE_DATA"/* "$data_dir/" 2>/dev/null
  rm -f "$data_dir/vpn.pid" "$data_dir/vpn.socket"

  HOME="$home_dir" adguardvpn-cli config set-socks-port "$port" >/dev/null 2>&1
  HOME="$home_dir" adguardvpn-cli connect -l "$location" -y >/dev/null 2>&1

  sleep 0.5
done

echo ""
echo "Done. Active ports:"
ss -tlnp 2>/dev/null | grep adguardvpn-cli | grep -oP '127.0.0.1:\K[0-9]+' | sort -n | tr '
' ' '
echo ""
echo "Total: $(ss -tlnp 2>/dev/null | grep adguardvpn-cli | grep -c '127.0.0.1')"
