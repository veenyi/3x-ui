#!/usr/bin/env bash
# xui-client-setup — run this on the machine that will use the proxy.
#
# Probes every node port in your 3x-ui subscription from the client side, keeps
# only the ones that actually answer, and writes a Clash/Mihomo config whose
# PROXY group is a url-test over the reachable set. This is the part the server
# cannot do for you: only the client knows which ports its own network blocks.
#
#   ./xui-client-setup.sh --host 20.30.40.50 --path /gnl3w7lgb8uoo98k/ --id <subId>
#
set -uo pipefail

HOST="" SUBPATH="" SUBID="" OUT="./xui-live.yaml" PORTS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=$2; shift 2 ;;
    --path) SUBPATH=$2; shift 2 ;;
    --id)   SUBID=$2; shift 2 ;;
    --out)  OUT=$2; shift 2 ;;
    --ports) PORTS=$2; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$HOST" ] && [ -n "$SUBID" ] || { echo "usage: --host H [--path P] --id SUBID" >&2; exit 2; }

SUB_URL="https://${HOST}:2096${SUBPATH}${SUBID}"
echo "==> fetching subscription"
# mktemp, not a hardcoded /tmp: Windows python cannot see the git-bash /tmp.
TMPFILE=$(mktemp 2>/dev/null || echo "${TEMP:-.}/xui-sub.yaml")
trap 'rm -f "$TMPFILE"' EXIT
curl -sk -m 25 -o "$TMPFILE" "$SUB_URL" || { echo "subscription fetch failed" >&2; exit 1; }
grep -q '^proxies:' "$TMPFILE" || { echo "not a clash config; check --path" >&2; exit 1; }

# On Windows `python3` is a Microsoft Store stub that prints an error instead of
# running, so probe for something that actually executes.
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys' >/dev/null 2>&1; then PY=$cand; break; fi
done
[ -n "$PY" ] || { echo "needs python3 (or python) on PATH" >&2; exit 3; }
echo "==> using interpreter: $PY ($("$PY" -V 2>&1))"

"$PY" - "$HOST" "$SUB_URL" "$OUT" "$PORTS" "$TMPFILE" <<'PYEOF'
import re, socket, sys

host, sub_url, out, forced, src = sys.argv[1:6]
text = open(src, encoding="utf-8").read()

# Split the proxies list into items by top-level "- ", then read name/port from
# each item. The first key of an item is not `name` (3x-ui emits it in sorted
# order, so `- client-fingerprint:` opens the entry), which is why matching on
# "- name:" finds nothing.
block = text.split("proxies:", 1)[1].split("\nproxy-groups:", 1)[0]
items, cur = [], None
for line in block.split("\n"):
    if re.match(r"^- ", line):
        if cur:
            items.append(cur)
        cur = [line]
    elif cur is not None and re.match(r"^[^#\s]", line):
        cur = None                      # dedent ends the list
    elif cur is not None:
        cur.append(line)
if cur:
    items.append(cur)

nodes = []
for it in items:
    body = "\n".join(it)
    nm = re.search(r"^\s*name:\s*['\"]?(.+?)['\"]?\s*$", body, re.M)
    pt = re.search(r"^\s*port:\s*(\d+)", body, re.M)
    if nm and pt:
        nodes.append((nm.group(1).strip(), int(pt.group(1))))
if not nodes:
    sys.exit("no nodes parsed")

def reachable(port, timeout=3.0):
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return True
    except OSError:
        return False

print("==> probing node ports from this machine")
good = []
for n, p in nodes:
    if forced and str(p) not in forced.split(","):
        continue
    ok = reachable(p)
    print("    %-40s port %-6d %s" % (n, p, "OPEN" if ok else "blocked/filtered"))
    if ok:
        good.append((n, p))

if not good:
    sys.exit("no node port reachable from here - the server is unreachable, not the client config")

names = [n for n, _ in sorted(good, key=lambda x: x[1])]
print("==> keeping %d/%d node(s)" % (len(names), len(nodes)))

groups = (
    "proxy-groups:\n"
    "- name: PROXY\n"
    "  type: url-test\n"
    "  proxies:\n" + "".join("  - %s\n" % n for n in names) +
    "  url: http://www.gstatic.com/generate_204\n"
    "  interval: 300\n"
    "  tolerance: 50\n"
    "- name: AUTO\n"
    "  type: select\n"
    "  proxies:\n  - PROXY\n  - DIRECT\n"
)

# keep the server-supplied rules, drop the upstream proxy-groups block
head = text.split("proxy-groups:", 1)[0]
rules = text.split("\nrules:", 1)[1] if "\nrules:" in text else "\n- MATCH,PROXY\n"
new = head + groups + "\nrules:" + rules

open(out, "w", encoding="utf-8").write(new)
print("==> wrote %s" % out)
print("    subscription (regenerate anytime): %s" % sub_url)
print("    import this file into Clash Verge / Mihomo; PROXY auto-selects the fastest live node")
PYEOF
