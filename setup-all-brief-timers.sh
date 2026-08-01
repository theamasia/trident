#!/bin/bash
# TRIDENT All Brief Timers — 3x Daily (06:30, 12:30, 18:30 UTC)
# Installs/fixes markets, geo-intel, and weather brief timers.
# Aviation already has its own timer via setup-flights.sh — not touched here.
# Run on trident-01: sudo bash setup-all-brief-timers.sh
set -e

install_brief_timer() {
  local name="$1"          # e.g. markets
  local display="$2"       # e.g. Markets
  local api_path="$3"      # e.g. /api/markets/brief/generate

  echo "[ TRIDENT ] Installing ${display} Brief timer..."

  cat > /etc/systemd/system/trident-${name}-brief.service << SVCEOF
[Unit]
Description=TRIDENT ${display} Brief Generator

[Service]
Type=oneshot
User=root
ExecStart=/bin/bash -c '\
  TOKEN=\$(curl -sf -X POST http://localhost:5000/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\\"email\\":\\"admin@trident.local\\",\\"password\\":\\"TridentAdmin1!\\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)[\\"token\\"])") && \
  curl -sf -X POST http://localhost:5000${api_path} \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer \$TOKEN" > /dev/null && \
  echo "[ TRIDENT ] ${display} brief generated"'
StandardOutput=journal
StandardError=journal
SVCEOF

  cat > /etc/systemd/system/trident-${name}-brief.timer << TMREOF
[Unit]
Description=TRIDENT ${display} Brief — 3x Daily (06:30, 12:30, 18:30 UTC)

[Timer]
OnCalendar=*-*-* 06:30:00 UTC
OnCalendar=*-*-* 12:30:00 UTC
OnCalendar=*-*-* 18:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
TMREOF

  systemctl enable trident-${name}-brief.timer
  systemctl start trident-${name}-brief.timer
  echo "[ TRIDENT ] ${display} brief timer enabled (3x daily)"
}

install_brief_timer "markets" "Markets" "/api/markets/brief/generate"
install_brief_timer "geo" "Geo-Intel" "/api/geo/brief/generate"
install_brief_timer "weather" "Weather" "/api/weather/brief/generate"

systemctl daemon-reload

echo ""
echo "[ TRIDENT ] All brief timers installed. Current schedule:"
systemctl list-timers --all --no-pager | grep -i trident
