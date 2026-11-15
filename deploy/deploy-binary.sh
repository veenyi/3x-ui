#!/usr/bin/env bash
# Swap the panel binary for a locally built (no-sponsors) one.
# Keeps a timestamped backup and aborts if the panel does not come back healthy.
#
#   ./deploy-binary.sh ./x-ui root@23.147.68.103
#
set -euo pipefail

NEW="${1:?usage: deploy-binary.sh <path-to-x-ui-binary> [user@host]}"
HOST="${2:?usage: deploy-binary.sh <path-to-x-ui-binary> [user@host]}"

file "$NEW" | grep -q 'ELF 64-bit.*x86-64' || {
  echo "refusing: $NEW is not a linux/amd64 ELF binary" >&2
  exit 1
}

scp -q "$NEW" "$HOST:/root/x-ui.new"

ssh "$HOST" 'set -euo pipefail
  test -f /root/x-ui.new || { echo "upload missing" >&2; exit 1; }
  STAMP=$(date +%Y%m%d-%H%M%S)
  cp -a /usr/local/x-ui/x-ui "/root/x-ui.backup-$STAMP"
  echo "backup: /root/x-ui.backup-$STAMP"

  # Verify before swapping: the new binary must at least report a version.
  chmod +x /root/x-ui.new
  /root/x-ui.new -v >/dev/null 2>&1 || { echo "new binary will not run; aborting" >&2; exit 1; }

  systemctl stop x-ui
  mv /root/x-ui.new /usr/local/x-ui/x-ui
  chmod +x /usr/local/x-ui/x-ui
  systemctl start x-ui

  for i in $(seq 1 20); do
    sleep 2
    systemctl is-active --quiet x-ui || continue
    PORT=$(python3 -c "
import sqlite3
print(list(sqlite3.connect(\"/etc/x-ui/x-ui.db\").execute(
  \"select value from settings where key=?\",(\"webPort\",)))[0][0])")
    if curl -sk -m 5 -o /dev/null "https://127.0.0.1:${PORT}/"; then
      echo "panel healthy on ${PORT}"
      exit 0
    fi
  done

  echo "panel did not come back; restoring backup" >&2
  systemctl stop x-ui || true
  cp -a "/root/x-ui.backup-$STAMP" /usr/local/x-ui/x-ui
  systemctl start x-ui
  exit 1'

echo "done. Now set XUI_DISABLE_SPONSORS=true via systemd/20-x-ui-local.conf and restart."
