#!/usr/bin/env bash
# =============================================================================
# Tiller Installer — Linux & macOS
# =============================================================================
set -euo pipefail

REPO_OWNER="joaotolovi"
REPO_NAME="Tiller"
REPO_REF="master"
ARCHIVE_URL="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REPO_REF}"
MODE="${TILLER_INSTALL_MODE:-install}"

DEFAULT_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DEFAULT_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DEFAULT_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"

INSTALL_DIR="${TILLER_INSTALL_DIR:-$DEFAULT_DATA_HOME/tiller}"
CONFIG_DIR="${TILLER_CONFIG_DIR:-$DEFAULT_CONFIG_HOME/tiller}"
CONFIG_PATH="${TILLER_CONFIG_PATH:-$CONFIG_DIR/tiller.yaml}"
LOG_DIR="${TILLER_LOG_DIR:-$DEFAULT_STATE_HOME/tiller/logs}"
SERVICE_NAME="${TILLER_SERVICE_NAME:-tiller}"
UV_BIN=""
GH_BIN=""
GH_VERSION="${TILLER_GH_VERSION:-2.92.0}"
TILLER_BIN_DIR="${TILLER_BIN_DIR:-$INSTALL_DIR/bin}"

info()    { printf '\033[0;34m[tiller-install]\033[0m %s\n' "$1"; }
success() { printf '\033[0;32m[tiller-install]\033[0m %s\n' "$1"; }
warn()    { printf '\033[0;33m[tiller-install]\033[0m WARNING: %s\n' "$1" >&2; }
die()     { printf '\033[0;31m[tiller-install]\033[0m ERROR: %s\n' "$1" >&2; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: '$1'. Please install it and retry."
}

validate_mode() {
  case "$MODE" in
    install|upgrade|reinstall|uninstall) ;;
    *) die "Invalid install mode: '$MODE'. Use install, upgrade, reinstall, or uninstall." ;;
  esac
}

select_mode_interactively() {
  [ -r /dev/tty ] || return 1

  while true; do
    {
      printf '\n[tiller-install] Tiller is already installed at %s\n' "$INSTALL_DIR"
      printf '[tiller-install] Choose what to do:\n'
      printf '  1) upgrade    - update existing installation in place\n'
      printf '  2) reinstall  - replace installation in place\n'
      printf '  3) uninstall  - remove installed app and service\n'
      printf '  4) cancel     - exit without changes\n'
      printf '> '
    } > /dev/tty

    local choice
    IFS= read -r choice < /dev/tty || return 1
    case "$choice" in
      1|upgrade)
        MODE="upgrade"
        info "Selected mode: $MODE"
        return 0
        ;;
      2|reinstall)
        MODE="reinstall"
        info "Selected mode: $MODE"
        return 0
        ;;
      3|uninstall)
        MODE="uninstall"
        info "Selected mode: $MODE"
        return 0
        ;;
      4|cancel|"")
        die "Installation cancelled. Re-run with TILLER_INSTALL_MODE=upgrade, reinstall, or uninstall if you want a non-interactive mode."
        ;;
      *)
        printf '[tiller-install] Invalid choice. Please enter 1, 2, 3, or 4.\n' > /dev/tty
        ;;
    esac
  done
}

is_installed() {
  [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/pyproject.toml" ]
}

os_name() {
  uname -s
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    info "Found uv at $UV_BIN"
    return
  fi

  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      UV_BIN="$candidate"
      export PATH="$(dirname "$UV_BIN"):$PATH"
      info "Found uv at $UV_BIN"
      return
    fi
  done

  info "uv not found — installing..."
  require_command curl
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      UV_BIN="$candidate"
      success "uv installed at $UV_BIN"
      return
    fi
  done

  die "uv installation finished but binary was not found. Check https://docs.astral.sh/uv/"
}

ensure_gh() {
  if command -v gh >/dev/null 2>&1; then
    GH_BIN="$(command -v gh)"
    info "Found gh at $GH_BIN"
    return
  fi

  local existing="$TILLER_BIN_DIR/gh"
  if [ -x "$existing" ]; then
    GH_BIN="$existing"
    export PATH="$TILLER_BIN_DIR:$PATH"
    info "Found gh at $GH_BIN"
    return
  fi

  require_command curl
  require_command tar
  mkdir -p "$TILLER_BIN_DIR"

  local os arch archive_ext platform archive_name download_url tmp_dir extracted_dir gh_candidate
  case "$(uname -s)" in
    Linux) platform="linux" archive_ext="tar.gz" ;;
    Darwin) platform="macOS" archive_ext="tar.gz" ;;
    *) warn "Unsupported platform for bundled gh install: $(uname -s)"; return ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) warn "Unsupported architecture for bundled gh install: $(uname -m)"; return ;;
  esac

  archive_name="gh_${GH_VERSION}_${platform}_${arch}.${archive_ext}"
  download_url="https://github.com/cli/cli/releases/download/v${GH_VERSION}/${archive_name}"
  tmp_dir="$(mktemp -d)"
  trap "rm -rf '$tmp_dir'" RETURN

  info "Installing GitHub CLI locally..."
  if ! curl -fsSL "$download_url" -o "$tmp_dir/$archive_name"; then
    warn "Could not download GitHub CLI from $download_url"
    return
  fi
  tar -xzf "$tmp_dir/$archive_name" -C "$tmp_dir"
  extracted_dir="$tmp_dir/gh_${GH_VERSION}_${platform}_${arch}"
  gh_candidate="$extracted_dir/bin/gh"
  if [ ! -x "$gh_candidate" ]; then
    warn "Downloaded GitHub CLI archive did not contain expected gh binary"
    return
  fi

  cp "$gh_candidate" "$existing"
  chmod +x "$existing"
  GH_BIN="$existing"
  export PATH="$TILLER_BIN_DIR:$PATH"
  success "Installed gh at $GH_BIN"
}

download_source() {
  require_command curl
  require_command tar

  mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap "rm -rf '$tmp_dir'" RETURN

  info "Downloading Tiller source from ${REPO_OWNER}/${REPO_NAME}@${REPO_REF}"
  curl -fsSL "$ARCHIVE_URL" -o "$tmp_dir/tiller.tar.gz"
  tar -xzf "$tmp_dir/tiller.tar.gz" -C "$tmp_dir"

  local extracted_dir="$tmp_dir/${REPO_NAME}-${REPO_REF}"
  [ -d "$extracted_dir" ] || die "Unable to locate extracted source directory"

  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  cp -R "$extracted_dir"/. "$INSTALL_DIR"
  success "Installed source into $INSTALL_DIR"
}

ensure_runtime() {
  info "Syncing project dependencies..."
  (cd "$INSTALL_DIR" && "$UV_BIN" sync -q --no-progress) || die "Failed to sync dependencies."
}

run_setup() {
  if [ -f "$CONFIG_PATH" ]; then
    info "Config already exists at $CONFIG_PATH — skipping interactive setup."
    return
  fi

  if [ -r /dev/tty ]; then
    info "Running interactive setup..."
    (
      exec < /dev/tty > /dev/tty 2>&1
      cd "$INSTALL_DIR"
      TILLER_GH_PATH="$GH_BIN" "$UV_BIN" run tiller setup --config "$CONFIG_PATH"
    ) || die "Setup failed."
    return
  fi

  warn "No interactive terminal detected — skipping setup."
  warn "Provide your config manually at: $CONFIG_PATH"
}

write_runner_script() {
  cat > "$INSTALL_DIR/.tiller-run.sh" <<RUNNER
#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$LOG_DIR"
cd "$INSTALL_DIR"
exec "$UV_BIN" run tiller run --config "$CONFIG_PATH" >> "$LOG_DIR/tiller.log" 2>&1
RUNNER

  chmod +x "$INSTALL_DIR/.tiller-run.sh"
  info "Runner script written to $INSTALL_DIR/.tiller-run.sh"
}

install_systemd_service() {
  local unit_path="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
  mkdir -p "$(dirname "$unit_path")"

  cat > "$unit_path" <<EOF
[Unit]
Description=Tiller background service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.tiller-run.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable "${SERVICE_NAME}.service"
  systemctl --user restart "${SERVICE_NAME}.service"
  success "Installed systemd user service: ${SERVICE_NAME}.service"
}

uninstall_systemd_service() {
  local unit_path="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  fi
  rm -f "$unit_path"
  info "Removed systemd user service: ${SERVICE_NAME}.service"
}

install_launchd_service() {
  local plist_path="$HOME/Library/LaunchAgents/com.tiller.${SERVICE_NAME}.plist"
  mkdir -p "$(dirname "$plist_path")"

  cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.tiller.${SERVICE_NAME}</string>
    <key>ProgramArguments</key>
    <array>
      <string>$INSTALL_DIR/.tiller-run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/tiller.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/tiller.stderr.log</string>
  </dict>
</plist>
EOF

  launchctl unload "$plist_path" >/dev/null 2>&1 || true
  launchctl load "$plist_path"
  launchctl kickstart -k "gui/$(id -u)/com.tiller.${SERVICE_NAME}" >/dev/null 2>&1 || true
  success "Installed launchd agent: com.tiller.${SERVICE_NAME}"
}

uninstall_launchd_service() {
  local plist_path="$HOME/Library/LaunchAgents/com.tiller.${SERVICE_NAME}.plist"
  launchctl unload "$plist_path" >/dev/null 2>&1 || true
  rm -f "$plist_path"
  info "Removed launchd agent: com.tiller.${SERVICE_NAME}"
}

install_service() {
  case "$(os_name)" in
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        install_systemd_service
      else
        warn "systemd not available on this Linux. Auto-registration skipped."
        warn "Run manually with: $INSTALL_DIR/.tiller-run.sh"
      fi
      ;;
    Darwin)
      install_launchd_service
      ;;
    *)
      die "Unsupported Unix platform: $(os_name)"
      ;;
  esac
}

uninstall_service() {
  case "$(os_name)" in
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        uninstall_systemd_service
      else
        warn "systemd not available on this Linux. No service registration to remove automatically."
      fi
      ;;
    Darwin)
      uninstall_launchd_service
      ;;
    *)
      die "Unsupported Unix platform: $(os_name)"
      ;;
  esac
}

handle_mode() {
  case "$MODE" in
    install)
      if is_installed; then
        if ! select_mode_interactively; then
          die "Tiller is already installed at $INSTALL_DIR. Available modes: upgrade, reinstall, uninstall. Re-run with TILLER_INSTALL_MODE=<mode> in non-interactive environments."
        fi
        handle_mode
        return
      fi
      ;;
    upgrade)
      if ! is_installed; then
        die "No existing Tiller installation found at $INSTALL_DIR. Re-run with TILLER_INSTALL_MODE=install."
      fi
      info "Existing installation detected — upgrading in place."
      ;;
    reinstall)
      if is_installed; then
        info "Existing installation detected — reinstalling in place."
      else
        warn "No existing installation found — proceeding with fresh install."
      fi
      ;;
    uninstall)
      uninstall_service
      rm -rf "$INSTALL_DIR"
      success "Tiller uninstalled"
      info "Install dir removed: $INSTALL_DIR"
      info "Config preserved at: $CONFIG_PATH"
      info "Logs preserved at: $LOG_DIR"
      exit 0
      ;;
  esac
}

main() {
  validate_mode
  handle_mode
  ensure_uv
  download_source
  ensure_runtime
  ensure_gh
  run_setup
  write_runner_script
  install_service
  success "Tiller installation completed"
  info "Mode: $MODE"
  info "Install dir: $INSTALL_DIR"
  info "Config: $CONFIG_PATH"
  info "Logs: $LOG_DIR"
}

main "$@"
