#!/usr/bin/env bash
# First installation of emon2influx-parser on an LXC (Ubuntu 24.04, Python 3.12), run once as
# root. Afterwards emon2influx-update keeps it up to date (doc/architecture.md §11.1).
#
#   curl -fsSLO https://raw.githubusercontent.com/AvansETI/emon2influx-parser/main/deploy/install.sh
#   less install.sh                     # read it first
#   sudo bash install.sh
#   sudo nano /etc/emon2influx-parser/env
#   sudo emon2influx-update             # installs the current release and starts the service
set -euo pipefail

REF="${E2I_REF:-main}"
RAW="https://raw.githubusercontent.com/AvansETI/emon2influx-parser/$REF"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME=emon2influx

step() { echo "==> $*"; }
warn() { echo "    warning: $*" >&2; }

[ "$(id -u)" = 0 ] || { echo "run as root (sudo bash install.sh)" >&2; exit 1; }

# A file from this checkout when present, else from GitHub.
fetch() {
  if [ -f "$SCRIPT_DIR/$1" ]; then cp "$SCRIPT_DIR/$1" "$2"; else curl -fsSL "$RAW/$1" -o "$2"; fi
}

step "packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv curl ca-certificates >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' ||
  warn "Python $(python3 --version) found; releases are built for 3.12 and will not install"

step "network"
# The manifest link answers 404 until the first release; any HTTP answer means reachable.
manifest="https://github.com/AvansETI/emon2influx-parser/releases/download/main-latest/manifest.json"
code="$(curl -sSL --max-time 15 -o /dev/null -w '%{http_code}' "$manifest" || true)"
case "$code" in
  200 | 404) echo "    ok   GitHub releases (HTTP $code)" ;;
  *) warn "cannot reach GitHub releases (HTTP ${code:-none})" ;;
esac

step "user and directories"
id "$USER_NAME" >/dev/null 2>&1 ||
  useradd --system --no-create-home --home-dir /var/lib/emon2influx-parser --shell /usr/sbin/nologin "$USER_NAME"
install -d -m 755 /opt/emon2influx-parser /opt/emon2influx-parser/releases
install -d -m 700 /etc/emon2influx-parser
install -d -m 755 /var/lib/emon2influx-parser

step "settings"
if [ -f /etc/emon2influx-parser/env ]; then
  echo "    keeping existing /etc/emon2influx-parser/env"
else
  # Read by systemd as root; the parser itself never reads this file. The paths are left out:
  # the service sets them (an env file would override its Environment= lines).
  tmp_env="$(mktemp)"
  fetch .env.example "$tmp_env"
  grep -vE '^(SPOOL_DIR|LOG_DIR|QUARANTINE_FILE|HEALTH_FILE)=' "$tmp_env" >/etc/emon2influx-parser/env
  rm -f "$tmp_env"
  chmod 600 /etc/emon2influx-parser/env
  echo "    created /etc/emon2influx-parser/env from .env.example: fill it in"
fi

step "updater and units"
tmp="$(mktemp)"
fetch deploy/emon2influx-update "$tmp"
install -m 755 "$tmp" /usr/local/sbin/emon2influx-update
rm -f "$tmp"
for unit in emon2influx-parser.service emon2influx-update.service emon2influx-update.timer; do
  fetch "deploy/$unit" "/etc/systemd/system/$unit"
  chmod 644 "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --quiet emon2influx-parser.service
systemctl enable --now --quiet emon2influx-update.timer

cat <<'DONE'

Done. Next:
  1. sudo nano /etc/emon2influx-parser/env
       - MQTT_*, INFLUX_* (INFLUX_URL: the local IP address of InfluxDB)
       - MODE=write and, on the production server only, ALLOW_PRODUCTION_BUCKET=true
  2. sudo emon2influx-update            # installs the current release and starts the service
  3. journalctl -u emon2influx-parser -f
The timer checks for a new release every minute; see journalctl -u emon2influx-update.
DONE
