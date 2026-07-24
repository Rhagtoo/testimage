#!/bin/bash
# AdGuard Pool Manager — start/stop/status for 8 parallel instances
set -e

POOL_DIR="/tmp/adg"
MAIN_AUTH="/home/rhagtoo/.local/share/adguardvpn-cli"

# Port→location mapping
declare -A LOCS=(
    [1081]="helsinki"
    [1082]="frankfurt"
    [1083]="amsterdam"
    [1084]="london"
    [1085]="paris"
    [1086]="warsaw"
    [1087]="vienna"
    [1088]="zurich"
)

cmd="${1:-status}"

case "$cmd" in
    start)
        echo "=== Starting 8-instance AdGuard pool ==="
        for port in $(seq 1081 1088); do
            dir="${POOL_DIR}$((port - 1080))"
            loc="${LOCS[$port]}"
            
            echo -n "  :$port ($loc)... "
            
            # Disconnect if running
            XDG_DATA_HOME="$dir" adguardvpn-cli disconnect 2>/dev/null || true
            
            # Fresh setup
            rm -rf "$dir"
            mkdir -p "$dir/adguardvpn-cli"
            cp -r "$MAIN_AUTH"/* "$dir/adguardvpn-cli/"
            
            # Config
            XDG_DATA_HOME="$dir" adguardvpn-cli config set-mode SOCKS >/dev/null 2>&1
            XDG_DATA_HOME="$dir" adguardvpn-cli config set-socks-port "$port" >/dev/null 2>&1
            
            # Connect
            XDG_DATA_HOME="$dir" adguardvpn-cli connect -l "$loc" >/dev/null 2>&1
            sleep 2
            
            # Verify
            status=$(XDG_DATA_HOME="$dir" adguardvpn-cli status 2>&1 | head -1)
            if echo "$status" | grep -q "Connected"; then
                echo "✓ $status"
            else
                echo "✗ $status"
            fi
        done
        echo "=== Done ==="
        ;;
    
    stop)
        echo "=== Stopping AdGuard pool ==="
        for port in $(seq 1081 1088); do
            dir="${POOL_DIR}$((port - 1080))"
            echo -n "  :$port... "
            XDG_DATA_HOME="$dir" adguardvpn-cli disconnect 2>/dev/null && echo "stopped" || echo "not running"
        done
        ;;
    
    status)
        echo "=== AdGuard Pool Status ==="
        alive=0
        for port in $(seq 1081 1088); do
            dir="${POOL_DIR}$((port - 1080))"
            status=$(XDG_DATA_HOME="$dir" adguardvpn-cli status 2>&1 | head -1)
            if echo "$status" | grep -q "Connected"; then
                loc=$(echo "$status" | grep -oP 'Connected to \K\S+')
                mode=$(echo "$status" | grep -oP 'in \K\S+ mode')
                echo "  :$port → $loc ($mode) ✓"
                ((alive++))
            else
                echo "  :$port → DISCONNECTED ✗"
            fi
        done
        echo "  Alive: $alive/8"
        ;;
    
    verify)
        echo "=== Ref check across pool ==="
        REF="y3tXqH0"
        for port in $(seq 1081 1088); do
            result=$(curl -x socks5h://127.0.0.1:$port -s -o /dev/null \
                -w "HTTP %{http_code} size %{size_download}" \
                --max-time 8 "https://testimage.cc/gallery/$REF" 2>/dev/null)
            echo "  :$port → $result"
        done
        ;;
    
    *)
        echo "Usage: $0 {start|stop|status|verify}"
        exit 1
        ;;
esac
