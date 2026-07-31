#!/bin/bash
# TRIDENT Service Resilience Setup
#
# Adds auto-restart-on-failure and staggered startup to ALL TRIDENT systemd services.
#
# This SUPERSEDES the earlier one-off staggering fix given directly in chat, which only
# covered 5 services and only added an ExecStartPre sleep (no auto-restart-on-failure).
# This script is the complete, permanent replacement: it covers every TRIDENT service,
# adds a proper drop-in `.service.d/resilience.conf` for each unit, and layers in
# `Restart=on-failure` with backoff limits so services also come back up automatically
# after crashes — not just after a clean reboot.
#
# Safe to re-run any time (idempotent) — each run simply rewrites the same drop-in files.
#
# Run once on trident-01:
#   sudo bash /opt/trident/scripts/setup-resilience.sh

set -e

echo "[ TRIDENT ] Configuring service resilience (auto-restart + staggered boot)..."

# All TRIDENT services, in the order we want them to start (core first, then dependents).
# Stagger delay increases per service so they don't all hammer the CPU/disk at once during boot.
declare -A SERVICES_STAGGER=(
  ["trident"]=0
  ["trident-markets"]=10
  ["trident-geo"]=15
  ["trident-flights"]=20
  ["trident-weather"]=25
  ["trident-briefs"]=30
  ["trident-grid"]=35
  ["trident-network"]=40
  ["trident-satellites"]=45
  ["trident-radio-ops"]=50
  ["trident-reolink"]=55
  ["trident-voice"]=60
  ["trident-imagegen"]=70
)

for svc in "${!SERVICES_STAGGER[@]}"; do
  delay="${SERVICES_STAGGER[$svc]}"

  if ! systemctl list-unit-files | grep -q "^${svc}.service"; then
    echo "[ SKIP ] ${svc}.service not installed on this system"
    continue
  fi

  echo "[ CONFIG ] ${svc}.service — stagger ${delay}s, auto-restart enabled"

  mkdir -p "/etc/systemd/system/${svc}.service.d"
  tee "/etc/systemd/system/${svc}.service.d/resilience.conf" > /dev/null << CONF
[Unit]
# Ensure network is up before this service tries to start (prevents early connection failures)
After=network-online.target
Wants=network-online.target
# Restart-storm backoff limits belong in [Unit], not [Service] -- systemd rejects
# StartLimitIntervalSec/StartLimitBurst under [Service] as unknown keys.
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
# Stagger startup so services don't all compete for CPU/disk at once after a reboot
ExecStartPre=/bin/sleep ${delay}

# Auto-restart on crash or failure
Restart=on-failure
RestartSec=10
CONF

  systemctl enable "${svc}.service" 2>/dev/null || true
done

systemctl daemon-reload

echo ""
echo "[ TRIDENT ] Resilience configuration complete."
echo "[ TRIDENT ] All services will now:"
echo "  - Auto-restart on failure (up to 5 times per 5 minutes, then stop trying to avoid restart storms)"
echo "  - Start in a staggered order after boot to avoid resource contention"
echo "  - Be enabled to start automatically on boot"
echo ""
echo "[ TRIDENT ] To apply immediately without rebooting, run:"
echo "  sudo systemctl daemon-reload"
echo ""
echo "[ TRIDENT ] Changes take effect on next service restart or system boot."
