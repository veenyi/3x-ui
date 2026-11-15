#!/usr/bin/env bash
# One command from a fresh VPS to a working default:
#
#   curl -Ls https://raw.githubusercontent.com/veenyi/3x-ui/main/quickstart.sh | bash
#
# Installs the panel through this fork's install.sh when it is absent, then runs
# quickstart.py to converge a Reality inbound on 443, a fallback port, the
# subscription formats and the clash routing tree.
#
# Flags (env vars, because a piped script cannot take argv):
#   XQS_DRY_RUN=1        show what quickstart would change, install nothing
#   XQS_FALLBACK_PORT=n  alternate port for port-443 blocking (default 8443)
#   XQS_SKIP_INSTALL=1   never touch the panel installation, only converge config
set -euo pipefail

RAW="https://raw.githubusercontent.com/veenyi/3x-ui/main"
DRY="${XQS_DRY_RUN:-0}"
FALLBACK="${XQS_FALLBACK_PORT:-8443}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "run as root (the installer writes /usr/local/x-ui and systemd units)" >&2
    exit 1
  fi
}

panel_installed() { [ -x /usr/local/x-ui/x-ui ] || systemctl is-active --quiet x-ui; }

install_panel() {
  echo "==> panel not present; installing from this fork"
  if [ "$DRY" = "1" ]; then
    echo "    [dry-run] would run: bash <(curl -Ls $RAW/install.sh)"
    return
  fi
  # install.sh is interactive (db choice, port, path, acme). Feed it the same
  # answers the documented flow uses so one command really is one command.
  bash <(curl -Ls "$RAW/install.sh") <<'ANSWERS'
1


2

ANSWERS
}

QS_FILE=$(mktemp /tmp/xui-quickstart.XXXXXX.py)
fetch_quickstart() {
  # Not a command substitution: an `exit` inside $( ) only leaves the subshell,
  # so a failed download would let the script carry on with an empty file.
  if ! curl -fsSL -o "$QS_FILE" "$RAW/quickstart.py"; then
    echo "could not fetch $RAW/quickstart.py" >&2
    return 1
  fi
}

main() {
  need_root
  if ! panel_installed; then
    if [ "${XQS_SKIP_INSTALL:-0}" = "1" ]; then
      echo "no panel installed and XQS_SKIP_INSTALL=1; nothing to do" >&2
      exit 1
    fi
    install_panel
    # the installer prints credentials once; they are also in /etc/x-ui/install-result.env
    echo "==> panel installed. Login details: /etc/x-ui/install-result.env"
  else
    echo "==> panel already installed, skipping install"
  fi

  # Wait for the API before converging: right after an install the service may
  # still be coming up, and quickstart reads live settings over HTTP.
  local port
  port=$(sed -n 's/^XUI_PANEL_PORT=//p' /etc/x-ui/install-result.env 2>/dev/null | head -1)
  [ -n "$port" ] || port=$(python3 -c "
import sqlite3
print(list(sqlite3.connect('/etc/x-ui/x-ui.db').execute('select value from settings where key=?',('webPort',)))[0][0])" 2>/dev/null || echo "")
  if [ -n "$port" ]; then
    echo "==> waiting for the panel API on :$port"
    for _ in $(seq 1 30); do
      curl -sk -m 4 -o /dev/null "https://127.0.0.1:$port/" && break
      sleep 2
    done
  fi

  echo "==> converging defaults"
  fetch_quickstart || exit 1
  [ -s "$QS_FILE" ] || { echo "fetched quickstart.py is empty" >&2; exit 1; }
  local args=()
  if [ "$DRY" = "1" ]; then args+=(--dry-run); fi
  args+=(--fallback-port "$FALLBACK")
  python3 "$QS_FILE" "${args[@]}"
}

trap 'rm -f "${QS_FILE:-}"' EXIT


main "$@"
