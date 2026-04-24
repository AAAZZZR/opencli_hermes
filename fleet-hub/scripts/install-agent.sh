#!/usr/bin/env bash
# fleet-agent installer — substituted & served by fleet-hub.
#
# Placeholders (filled at request time):
#   __CENTRAL_URL__
#   __NODE_TOKEN__
#   __NODE_LABEL__
#   __OPENCLI_NPM_SPEC__
#   __FLEET_AGENT_INSTALL_SPEC__
#
# Usage (from your laptop):
#   curl -fsSL "https://fleet.yourdomain.com/api/v1/nodes/install/agent.sh?label=alice-mbp" | bash
set -euo pipefail

# Placeholders are replaced by the hub using `shlex.quote`, so each bare
# __PLACEHOLDER__ expands into a fully shell-safe literal — either a plain
# word or a single-quoted string. Do NOT add outer quotes around them.
CENTRAL_URL=__CENTRAL_URL__
NODE_TOKEN=__NODE_TOKEN__
NODE_LABEL=__NODE_LABEL__
OPENCLI_NPM_SPEC=__OPENCLI_NPM_SPEC__
FLEET_AGENT_INSTALL_SPEC=__FLEET_AGENT_INSTALL_SPEC__

FLEET_DIR="${HOME}/.fleet-agent"
VENV_DIR="${FLEET_DIR}/venv"
CONFIG_FILE="${FLEET_DIR}/config.env"
LOG_DIR="${FLEET_DIR}/logs"

log() { printf '[fleet-agent install] %s\n' "$*"; }
die() { printf '[fleet-agent install] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$OS" in
  darwin) PLATFORM="darwin" ;;
  linux)
    if grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then
      PLATFORM="wsl"
    else
      PLATFORM="linux"
    fi
    ;;
  *) die "unsupported platform: $OS (supported: darwin, linux, wsl)" ;;
esac
log "platform: $PLATFORM"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
need curl
need python3

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
[ "$PY_OK" = "1" ] || die "python >= 3.11 required (found $PY_VER)"
log "python: $PY_VER ok"

if ! command -v node >/dev/null 2>&1; then
  die "node.js >= 21 required — install via nvm: https://github.com/nvm-sh/nvm"
fi
NODE_MAJOR=$(node -v | sed -E 's/^v([0-9]+)\..*/\1/')
[ "$NODE_MAJOR" -ge 21 ] || die "node >= 21 required (found $(node -v))"
log "node: $(node -v) ok"

need npm

# ---------------------------------------------------------------------------
# Install @jackwener/opencli (latest)
# ---------------------------------------------------------------------------
log "installing $OPENCLI_NPM_SPEC globally…"
npm install -g "$OPENCLI_NPM_SPEC"

OPENCLI_VERSION=$(opencli --version 2>/dev/null || echo "unknown")
log "opencli: $OPENCLI_VERSION"

# ---------------------------------------------------------------------------
# fleet-agent venv + install
# ---------------------------------------------------------------------------
mkdir -p "$FLEET_DIR" "$LOG_DIR"
if [ ! -d "$VENV_DIR" ]; then
  log "creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip >/dev/null

log "installing fleet-agent from $FLEET_AGENT_INSTALL_SPEC"
python -m pip install --upgrade "$FLEET_AGENT_INSTALL_SPEC"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
umask 077
cat > "$CONFIG_FILE" <<EOF
# fleet-agent config — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
CENTRAL_URL=$CENTRAL_URL
NODE_TOKEN=$NODE_TOKEN
NODE_LABEL=$NODE_LABEL
OPENCLI_BIN=$(command -v opencli)
AGENT_MODE=bridge
LOG_LEVEL=INFO
EOF
log "config written to $CONFIG_FILE"

# ---------------------------------------------------------------------------
# Service install + start
# ---------------------------------------------------------------------------
SERVICE_NAME="fleet-agent"

case "$PLATFORM" in
  darwin)
    PLIST="${HOME}/Library/LaunchAgents/com.fleet.agent.plist"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.fleet.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>${VENV_DIR}/bin/python</string>
    <string>-m</string>
    <string>fleet_agent</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FLEET_AGENT_CONFIG</key><string>${CONFIG_FILE}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/agent.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/agent.err.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    log "launchd service installed at $PLIST"
    ;;

  linux|wsl)
    # Prefer systemd --user if available, otherwise fall back to nohup.
    if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
      UNIT_DIR="${HOME}/.config/systemd/user"
      mkdir -p "$UNIT_DIR"
      UNIT="${UNIT_DIR}/${SERVICE_NAME}.service"
      cat > "$UNIT" <<EOF
[Unit]
Description=Fleet Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${CONFIG_FILE}
Environment=FLEET_AGENT_CONFIG=${CONFIG_FILE}
ExecStart=${VENV_DIR}/bin/python -m fleet_agent
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/agent.out.log
StandardError=append:${LOG_DIR}/agent.err.log

[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable "${SERVICE_NAME}.service"
      systemctl --user restart "${SERVICE_NAME}.service"
      log "systemd --user service installed: ${SERVICE_NAME}"
      log "view logs: journalctl --user -u ${SERVICE_NAME} -f"
    else
      log "systemd --user not available — falling back to nohup"
      pkill -f "fleet_agent" 2>/dev/null || true
      nohup env FLEET_AGENT_CONFIG="$CONFIG_FILE" \
        "$VENV_DIR/bin/python" -m fleet_agent \
        > "$LOG_DIR/agent.out.log" 2> "$LOG_DIR/agent.err.log" &
      log "started via nohup, pid $!"
    fi
    ;;
esac

log "done. Node registered as '$NODE_LABEL', reporting to $CENTRAL_URL"
log "logs: $LOG_DIR"
