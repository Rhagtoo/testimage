#!/bin/bash
# Проверяет не ослеп ли воркер: смотрит последние 10 строк simple_scan.log на ref=blind
# Если все 404 и rps=0 больше 5 минут — воркер ослеп

LOG="./simple_scan.log"
PID_FILE="./scanner.pid"
WORKERS=(
  "https://pentest_site-ref1.user3.workers.dev"
  "https://pentest_site-ref2.user3.workers.dev"
  "https://pentest_site-ref.user2.workers.dev"
  "https://pentest_site-ref1.user2.workers.dev"
)

LAST_ALIVE=$(grep "\[alive\]" "$LOG" | tail -1)
RPS=$(echo "$LAST_ALIVE" | grep -oP 'rps=[\d.]+' | cut -d= -f2)
FOUND=$(echo "$LAST_ALIVE" | grep -oP 'found=\d+' | cut -d= -f2)

if [ "$RPS" = "0.0" ]; then
  CURRENT_WORKER=$(grep "cloudflare-worker-url" /proc/$(cat $PID_FILE)/cmdline 2>/dev/null | tr '\0' '\n' | grep "cloudflare-worker-url" -A1 | tail -1)
  echo "[$(date)] RPS=0! Worker may be blind: $CURRENT_WORKER" >> /tmp/worker_rotation.log
  
  # Попробовать следующий воркер
  for W in "${WORKERS[@]}"; do
    HEALTH=$(curl -s --max-time 5 "$W/health" 2>/dev/null)
    if [ "$HEALTH" = "ok" ]; then
      echo "[$(date)] Rotating to $W" >> /tmp/worker_rotation.log
      kill $(cat $PID_FILE) 2>/dev/null
      sleep 2
      cd . && python3 -u pentest_site_gallery_scanner.py \
        --fresh \
        --proxies-only \
        --cloudflare-worker-url "$W" \
        --cloudflare-worker-key YOUR_XKEY_SECRET \
        -n 0 -w 30 --persistent --probe-timeout 4 \
        -o simple_scan.txt \
        > simple_scan.log 2>&1 &
      break
    fi
  done
fi
