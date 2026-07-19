#!/bin/bash
# auto_generate_galleries.sh — полный цикл создания и публикации галерей
# Запуск из WSL. Требует ID галерей из браузерного JS.

set -e

SK="1488e84963dfa1a66d5d5c1788e0a8e9e15c2f6977279e56ee90bce9f4f304b5"
IMG="https://i.postimg.cc/ZY12yJ5h/photo-5305713284646378373-x.jpg"
OUTPUT="${1:-generated_galleries.txt}"
GID_FILE="${2:-/tmp/browser_gallery_ids.txt}"

if [ ! -f "$GID_FILE" ]; then
  echo "Сгенерируй ID в консоли браузера (на https://postimg.cc/files):"
  echo ""
  echo "(async()=>{"
  echo " let ids=[];"
  echo " for(let i=0;i<10;i++){"
  echo "   let f=new FormData();f.set('action','add');f.set('name','auto_'+Date.now()%10000+'_'+i);"
  echo "   let r=await fetch('/json',{method:'POST',body:f,headers:{'X-Requested-With':'XMLHttpRequest'}});"
  echo "   let d=await r.json();let gid=(d.url_html||'').split('/').pop();if(gid)ids.push(gid);"
  echo "   console.log(i,':',gid);"
  echo "   await new Promise(r=>setTimeout(r,500));"
  echo " }"
  echo " console.log(ids.join('\\\\n'));"
  echo "})()"
  echo ""
  echo "Сохрани вывод в $GID_FILE и запусти скрипт снова."
  exit 1
fi

echo "Публикую галереи из $GID_FILE..."
TOTAL=0
while IFS= read -r GID; do
  GID=$(echo "$GID" | tr -d '[:space:]')
  [ -z "$GID" ] && continue

  SESSION="$(date +%s)000.$(shuf -i 10000000000000000-99999999999999999 -n 1)"

  RESULT=$(curl -s --max-time 10 \
    -H 'accept: application/json' \
    -H 'accept-language: ru-US,ru;q=0.9,en-US;q=0.8,en;q=0.7,ru-RU;q=0.6' \
    -H 'cache-control: no-cache' \
    -H 'content-type: multipart/form-data; boundary=----WebKitFormBoundarykcWzOqAPAEasmph8' \
    -b "SESSIONKEY=$SK; gallery=$GID" \
    -H 'origin: https://postimages.org' \
    -H 'priority: u=1, i' \
    -H 'referer: https://postimages.org/web' \
    -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
    -H 'sec-ch-ua-mobile: ?0' \
    -H 'sec-ch-ua-platform: "Windows"' \
    -H 'sec-fetch-dest: empty' -H 'sec-fetch-mode: cors' -H 'sec-fetch-site: same-origin' \
    -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0' \
    -H 'x-requested-with: XMLHttpRequest' \
    --data-raw $'------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="gallery"\r\n\r\n'"$GID"$'\r\n------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="optsize"\r\n\r\n0\r\n------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="expire"\r\n\r\n0\r\n------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="url"\r\n\r\n'"$IMG"$'\r\n------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="numfiles"\r\n\r\n1\r\n------WebKitFormBoundarykcWzOqAPAEasmph8\r\nContent-Disposition: form-data; name="upload_session"\r\n\r\n'"$SESSION"$'\r\n------WebKitFormBoundarykcWzOqAPAEasmph8--\r\n' \
    "https://postimages.org/json")

  IMG_URL=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url','FAIL'))" 2>/dev/null || echo "FAIL")
  TOTAL=$((TOTAL + 1))
  echo "[$TOTAL] $GID → $IMG_URL"
  echo "$GID" >> "$OUTPUT"

  sleep 0.3
done < "$GID_FILE"

echo ""
echo "Опубликовано: $TOTAL галерей → $OUTPUT"
echo "Проверка: curl -s -o /dev/null -w '%{http_code}' https://postimg.cc/gallery/$(head -1 $OUTPUT)"
