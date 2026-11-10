#!/bin/bash
# Network lockdown: the only outbound traffic allowed is HTTPS (+ ping) to the model APIs.
# This also blocks the LAN, mDNS/Bonjour and AirDrop/AWDL at the IP level. Bluetooth is
# not IP, so turn it off by hand.
#   sudo ./setup/lockdown.sh          # apply (re-run daily; API IPs can rotate)
#   sudo ./setup/lockdown.sh --off    # restore normal networking
# Blocked packets are logged to pflog0. A pcap of them is written to /Users/Shared/pflog-*.pcap,
# so you can see the network attempts agents make (ping/arp-scan/ssh/nc...).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

API_HOSTS=(api.anthropic.com api.openai.com)
RULES=/etc/pf.anchors/runner.conf
HOSTS_TAG="# runner-pinned"

if [[ "${1:-}" == "--off" ]]; then
  pkill -f "tcpdump -i pflog0" || true
  pfctl -f /etc/pf.conf 2>/dev/null; pfctl -d 2>/dev/null || true
  sed -i '' "/$HOSTS_TAG/d" /etc/hosts
  dscacheutil -flushcache; killall -HUP mDNSResponder || true
  echo "Lockdown removed."; exit 0
fi

# Resolve API IPs while DNS still works, then pin them in /etc/hosts (DNS gets blocked).
sed -i '' "/$HOSTS_TAG/d" /etc/hosts
IPS=()
for h in "${API_HOSTS[@]}"; do
  for ip in $(dig +short A "$h" | grep -E '^[0-9.]+$'); do
    IPS+=("$ip"); echo "$ip $h $HOSTS_TAG" >> /etc/hosts
  done
done
[[ ${#IPS[@]} -gt 0 ]] || { echo "Could not resolve API hosts (already locked down? run --off first)"; exit 1; }
dscacheutil -flushcache; killall -HUP mDNSResponder || true

cat > "$RULES" <<EOF
table <api> const { ${IPS[*]} }
set skip on lo0
block drop log all
pass out quick inet proto udp from any port 68 to any port 67 keep state   # DHCP lease renewal
pass in  quick inet proto udp from any port 67 to any port 68
pass out quick inet proto tcp to <api> port 443 keep state
pass out quick inet proto icmp to <api> keep state
EOF

ifconfig pflog0 create 2>/dev/null || true
pfctl -q -f "$RULES"
pfctl -q -e 2>/dev/null || true

pkill -f "tcpdump -i pflog0" || true
PCAP="/Users/Shared/pflog-$(date +%Y%m%d-%H%M%S).pcap"
nohup tcpdump -i pflog0 -n -w "$PCAP" >/dev/null 2>&1 &
chmod 600 "$PCAP" 2>/dev/null || true

echo "Locked down. Allowed: ${IPS[*]}"
echo "Blocked-packet log: $PCAP   (read with: sudo tcpdump -n -e -r $PCAP)"
echo "Sanity check:"
curl -s -m 5 -o /dev/null -w "  api.anthropic.com -> HTTP %{http_code}\n" https://api.anthropic.com/v1/messages || true
curl -s -m 5 -o /dev/null -w "  example.com       -> %{http_code} (should fail)\n" https://example.com || echo "  example.com       -> blocked (good)"
