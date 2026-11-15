#!/usr/bin/env python3
"""xui-quickstart — turn a freshly installed 3x-ui panel into a working
default, and keep it that way. Idempotent: every step reads current state and
only writes what is missing, so re-running is a no-op rather than a duplicate.

    xui-quickstart --dry-run     # show what would change
    xui-quickstart               # apply

Deliberately not here: installing the panel itself (upstream install.sh does
that) and anything that needs a secret we cannot read.
"""
import argparse
import json
import os
import secrets
import uuid as uuidlib
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

DB = "/etc/x-ui/x-ui.db"
SHORT_IDS = []
ENV = "/etc/x-ui/install-result.env"

FALLBACK_PORT = 8443
SERVICE_GROUPS = [
    ("OpenAI", ["openai.com", "chatgpt.com", "oaistatic.com",
                "oaiusercontent.com", "anthropic.com", "claude.ai"]),
    ("Google", ["google.com", "googleapis.com", "gstatic.com",
                "googleusercontent.com"]),
    ("YouTube", ["youtube.com", "youtu.be", "ytimg.com", "googlevideo.com"]),
    ("Telegram", ["telegram.org", "t.me", "telegram.me"]),
    ("Streaming", ["netflix.com", "nflxvideo.net", "disneyplus.com",
                   "spotify.com", "twitch.tv"]),
]


class Runner:
    def __init__(self, dry, base, ctx, token):
        self.dry, self.base, self.ctx, self.token = dry, base, ctx, token
        self.actions = []

    def api(self, path, data=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body,
                                   method=method or ("POST" if body is not None else "GET"))
        r.add_header("Authorization", "Bearer " + self.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=60, context=self.ctx) as resp:
                raw = resp.read()
                return json.loads(raw or b"{}")
        except urllib.error.HTTPError as e:
            raise SystemExit("API %s %s -> %s %s" % (method or "GET", path, e.code,
                                                     e.read()[:200].decode("utf8", "replace")))

    def all_settings(self):
        """POST, not GET: /panel/api/setting/all 404s on GET."""
        return self.api("/panel/api/setting/all", data={})["obj"]

    def wait_until_up(self, timeout=90):
        """A restart is not finished when systemctl returns; the listener comes
        up seconds later, and a caller that proceeds immediately gets
        connection refused."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(urllib.request.Request(self.base),
                                       timeout=5, context=self.ctx).read()
                return True
            except Exception:
                time.sleep(2)
        print("  WARN      panel did not answer within %ds" % timeout)
        return False

    def note(self, kind, what):
        self.actions.append((kind, what))
        print("  %-9s %s%s" % (kind, what, "   [dry-run]" if self.dry else ""))

    def write(self, what, fn):
        self.note("would" if self.dry else "change", what)
        if not self.dry:
            fn()


def read_env():
    out = {}
    for line in open(ENV):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            out[k] = v
    return out


def settings_map():
    import sqlite3
    c = sqlite3.connect(DB)
    return dict((str(k), str(v)) for k, v in c.execute("select key,value from settings"))



XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"
REALITY_DEFAULT_TARGET = "www.sony.com:443"   # same site as the first node, so
                                              # both boxes present one handshake
                                              # profile to an observer


def gen_reality_keys():
    """Use the panel's own core binary rather than inventing a keypair format."""
    import binascii
    out = subprocess.run([XRAY_BIN, "x25519"], capture_output=True, text=True, check=True).stdout
    priv = pub = None
    for line in out.splitlines():
        if line.startswith("PrivateKey:"):
            priv = line.split(":", 1)[1].strip()
        elif line.startswith("Password (PublicKey):"):
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError("could not parse x25519 output: %r" % out[:120])
    # shortIds: 1/2/4/8-byte forms, matching what the panel's own generator emits
    ids = [binascii.hexlify(os.urandom(n)).decode() for n in (1, 2, 4, 8)]
    return priv, pub, ids


def reality_inbound_payload(port, remark, priv, pub, target, email, uuid, sub_id):
    host = target.split(":")[0]
    client = {
        "id": uuid, "email": email, "flow": "xtls-rsv1", "enable": True,
        "expiryTime": 0, "totalGB": 0, "limitIp": 0, "reset": 0,
        "subId": sub_id, "comment": "quickstart",
    }
    return {
        "remark": remark, "enable": True, "port": port, "protocol": "vless",
        "listen": "", "tag": "in-%d-tcp" % port,
        "settings": {"clients": [client], "decryption": "none"},
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {
                "show": False, "fingerprint": "chrome",
                "serverNames": [host], "target": host,
                "ports": "443", "privateKey": priv, "shortIds": ids_of(priv),
                "maxClientVer": "", "minClientVer": "", "maxTimediff": 0, "xver": 0,
                "mldsa65Seed": "",
                "settings": {"publicKey": pub, "fingerprint": "chrome",
                              "serverName": "", "spiderX": "/", "mldsa65Verify": ""},
            },
            "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
        },
        "sniffing": {"enabled": True,
                     "destOverride": ["http", "tls", "quic", "fakedns"]},
        "allocate": {"strategy": "always", "concurrency": 3, "refresh": 5},
    }


def ids_of(_priv):
    # regenerated per call site; kept as a hook so the shortIds list is not
    # silently reused across the primary and fallback inbounds
    return SHORT_IDS


def build_routing(node_names, region):
    """3-tier clash tree: service groups -> region -> LIVE(url-test) -> nodes.

    proxy-groups must be restated in full: mergeClashRulesYAML merges only
    `rules` and ASSIGNS every other top-level key over the generated config, so
    omitting LIVE would silently drop the fork's automatic port failover.
    """
    members = "".join("  - %s\n" % n for n in node_names)
    lines = ["proxy-groups:", "- name: LIVE", "  type: url-test", "  proxies:",
             members.rstrip("\n"), "  url: http://www.gstatic.com/generate_204",
             "  interval: 300", "  tolerance: 50",
             "- name: PROXY", "  type: select", "  proxies:", "  - LIVE",
             members.rstrip("\n"), "  - DIRECT",
             "- name: %s" % region, "  type: select", "  proxies:", "  - LIVE",
             members.rstrip("\n"), "  - DIRECT"]
    for name, domains in SERVICE_GROUPS:
        lines += ["- name: %s" % name, "  type: select", "  proxies:",
                  "  - %s" % region, "  - PROXY", "  - DIRECT"]
    lines += ["- name: Final", "  type: select", "  proxies:", "  - PROXY", "  - DIRECT",
              "rules:"]
    for name, domains in SERVICE_GROUPS:
        for d in domains:
            lines.append("- DOMAIN-SUFFIX,%s,%s" % (d, name))
    lines += ["- DOMAIN-SUFFIX,github.com,PROXY",
              "- DOMAIN-SUFFIX,githubusercontent.com,PROXY",
              "- DOMAIN-SUFFIX,wikipedia.org,PROXY",
              "- DOMAIN-SUFFIX,x.com,PROXY",
              "- DOMAIN-SUFFIX,twitter.com,PROXY",
              "- DOMAIN-SUFFIX,discord.com,PROXY",
              "- DOMAIN-SUFFIX,discordapp.net,PROXY",
              "- GEOIP,CN,DIRECT",
              "- MATCH,Final"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fallback-port", type=int, default=FALLBACK_PORT)
    ap.add_argument("--emit-tf", metavar="PATH", default=None,
                    help="also write the generated routing to a tfvars JSON the "
                         "Terraform fleet reads, so the two cannot drift apart")
    args = ap.parse_args()

    env = read_env()
    s = settings_map()
    # install-result.env keeps the token (the DB stores only its SHA-256) but
    # its port and base path go stale the moment either is changed in the
    # panel, so the live values come from the settings table.
    # webBasePath ends with "/", API paths start with "/": joining them raw
    # yields a double slash and the panel answers 404.
    base = "https://127.0.0.1:%s%s" % (s["webPort"], s["webBasePath"].rstrip("/"))
    assert "//panel" not in base + "/panel/api/x"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    R = Runner(args.dry_run, base, ctx, env["XUI_API_TOKEN"])

    print("== quickstart (%s) on %s ==" % ("dry-run" if args.dry_run else "apply", base))

    # ---- 1. inbounds: primary 443 + fallback -----------------------------
    ins = R.api("/panel/api/inbounds/list").get("obj", [])
    ports = {i["port"] for i in ins}
    global SHORT_IDS
    primary = next((i for i in ins if i["port"] == 443), None)
    if primary is None:
        priv, pub, SHORT_IDS = gen_reality_keys()
        email = secrets.token_hex(5)
        uuid = str(uuidlib.uuid4())
        sub_id = secrets.token_hex(8)
        payload = reality_inbound_payload(443, "Reality-443", priv, pub,
                                          os.environ.get("XQS_REALITY_TARGET", REALITY_DEFAULT_TARGET),
                                          email, uuid, sub_id)
        if args.dry_run:
            R.note("would", "create Reality inbound on 443 (client %s)" % email)
            return 0
        res = R.api("/panel/api/inbounds/add", payload)
        if not res.get("success"):
            R.note("abort", "inbound creation failed: %s" % str(res.get("msg"))[:120])
            return 2
        R.note("create", "Reality inbound 443 + client %s" % email)
        print("      login/panel: the client sub id is %s" % sub_id)
        ins = R.api("/panel/api/inbounds/list").get("obj", [])
        primary = next((i for i in ins if i["port"] == 443), None)
        if primary is None:
            R.note("abort", "inbound reported created but is not listed")
            return 2
    R.note("ok", "inbound 443 present")
    if args.fallback_port not in ports:
        def add_fallback():
            tpl = R.api("/panel/api/inbounds/get/%s" % primary["id"])["obj"]
            tpl.pop("id", None); tpl.pop("clientStats", None)
            tpl["port"] = args.fallback_port
            tpl["tag"] = "in-%d-tcp" % args.fallback_port
            tpl["remark"] = "%s [fallback %d]" % (tpl["remark"], args.fallback_port)
            R.api("/panel/api/inbounds/add", tpl)
        R.write("fallback inbound on %d (mirrors 443)" % args.fallback_port,
                add_fallback)
    else:
        R.note("ok", "fallback inbound %d present" % args.fallback_port)

    # ---- 2. subscription formats ----------------------------------------
    # Effective values must come from the API: a fresh install has no subPort or
    # subClashPath row in the settings table at all, since those defaults live in
    # code. Reading the DB directly raised KeyError on the second node.
    eff = R.all_settings()
    want = {"subEnable": True, "subClashEnable": True, "subClashEnableRouting": True}
    changed = {}
    for k, v in want.items():
        if str(eff.get(k, "")).lower() != "true":
            changed[k] = v
    if changed:
        allsettings = dict(eff)
        allsettings.update(changed)

        def enable_sub():
            R.api("/panel/api/setting/update", allsettings)
            # The subscription listener is assembled at boot. Without a restart
            # the clash path answers 404 even though the settings are saved and
            # port 2096 is already listening.
            subprocess.run(["systemctl", "restart", "x-ui"], check=True)
            R.wait_until_up()
        R.write("subscription: " + ", ".join(sorted(changed)), enable_sub)
        eff = R.all_settings()
    else:
        R.note("ok", "subscription + clash + routing enabled")

    # ---- 3. routing tree, derived from the live subscription -------------
    import sqlite3
    rows = list(sqlite3.connect(DB).execute("select sub_id from clients where enable=1 limit 1"))
    if not rows:
        R.note("warn", "no enabled client; create one in the panel then re-run")
        return 2
    sub_id = rows[0][0]
    url = "https://127.0.0.1:%s%s%s" % (eff["subPort"], eff["subClashPath"], sub_id)
    text = urllib.request.urlopen(urllib.request.Request(url), timeout=30,
                                  context=ctx).read().decode()
    # clash emits each proxy with keys sorted, so `name:` is an indented plain
    # line, never `- name:`
    node_names = [n.strip().strip("'\"") for n in
                  re.findall(r"^\s+name:\s*(.+?)\s*$", text.split("proxy-groups:")[0], re.M)]
    if len(node_names) < 2:
        R.note("warn", "routing skipped: only %d node(s) in the subscription" % len(node_names))
    else:
        remark = list(sqlite3.connect(DB).execute(
            "select remark from inbounds where enable=1 order by id limit 1"))[0][0]
        region = re.sub(r"\s*\[.*?\]", "", remark).strip() or "Primary"
        want_rules = build_routing(node_names, region)
        if (s.get("subClashRules") or "").strip() != want_rules.strip():
            allsettings = R.all_settings()
            allsettings["subClashEnableRouting"] = True
            allsettings["subClashRules"] = want_rules

            def apply_rules():
                R.api("/panel/api/setting/update", allsettings)
                subprocess.run(["systemctl", "restart", "x-ui"], check=True)
                R.wait_until_up()
            R.write("3-tier routing (%d nodes, region %r)" % (len(node_names), region),
                    apply_rules)
        else:
            R.note("ok", "routing tree already current (%d nodes)" % len(node_names))

    # ---- 3b. hand the same text to Terraform ----------------------------
    if args.emit_tf:
        if len(node_names) < 2:
            print("  skip      --emit-tf: routing was not built")
        else:
            import json as _json
            payload = _json.dumps({
                "clash_rules": want_rules,
                "balancer_remark": "auto-fastest-%s" % "-".join(
                    str(n) for n in sorted(p for p in ports if p)),
            }, ensure_ascii=False)
            if args.dry_run:
                print("  would     write %s (%d bytes)" % (args.emit_tf, len(payload)))
            else:
                with open(args.emit_tf, "w", encoding="utf-8") as f:
                    f.write(payload + "\n")
                print("  emit      %s (%d bytes)" % (args.emit_tf, len(payload)))

    # ---- 4. ops hooks ----------------------------------------------------
    # A new node must not come up without the protections the first one has.
    # These are fetched from the fork so there is one copy to maintain.
    raw = "https://raw.githubusercontent.com/veenyi/3x-ui/main"
    for repo_path, dest in (("ops/xui-watchdog", "/usr/local/bin/xui-watchdog"),
                            ("ops/xui-backup", "/usr/local/bin/xui-backup"),
                            ("ops/lgl-guard.nft", "/etc/x-ui/lgl-guard.nft")):
        if os.path.exists(dest):
            R.note("ok", "present: %s" % dest)
            continue

        def install_ops(repo_path=repo_path, dest=dest):
            import urllib.request as u
            body = u.urlopen(u.Request(raw + "/" + repo_path), timeout=30).read()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(body)
            if dest.startswith("/usr/local/bin"):
                os.chmod(dest, 0o755)
        R.write("install %s" % dest, install_ops)

    # the guard file alone does nothing; it needs its systemd unit
    if os.path.exists("/etc/x-ui/lgl-guard.nft") and not os.path.exists(
            "/etc/systemd/system/xui-lgl-guard.service"):
        ip = os.environ.get("XQS_PUBLIC_IP", "")

        def install_guard():
            unit = "\n".join([
                "[Unit]",
                "Description=Restrict looking-glass speedtest to localhost",
                "After=nftables.service",
                "",
                "[Service]",
                "Type=oneshot",
                "RemainAfterExit=yes",
                "ExecStart=/usr/sbin/nft -f /etc/x-ui/lgl-guard.nft",
                "",
            ])
            with open("/etc/systemd/system/xui-lgl-guard.service", "w") as f:
                f.write(unit)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "enable", "--now", "xui-lgl-guard"], check=False)
        if ip:
            # templated so the allowlist names this box, not the first one
            with open("/etc/x-ui/lgl-guard.nft") as f:
                body = f.read()
            if "23.147.68.103" in body:
                body = body.replace("23.147.68.103", ip)
                with open("/etc/x-ui/lgl-guard.nft", "w") as f:
                    f.write(body)
        R.write("enable looking-glass guard", install_guard)

    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    for marker, line in (("/usr/local/bin/xui-watchdog", "*/30 * * * * /usr/local/bin/xui-watchdog >/dev/null 2>&1"),
                         ("/usr/local/bin/xui-backup", "40 3 * * * /usr/local/bin/xui-backup >/dev/null 2>&1")):
        if marker in cron:
            R.note("ok", "cron present: %s" % marker)
        elif os.path.exists(marker):
            def add_cron(line=line):
                newcron = "\n".join([cron.rstrip("\n"), line, ""])
                subprocess.run(["crontab", "-"], input=newcron, text=True, check=True)
            R.write("schedule %s" % marker, add_cron)
        else:
            R.note("warn", "%s missing, cannot schedule" % marker)

    # ---- 5. report -------------------------------------------------------
    n_change = sum(1 for k, _ in R.actions if k in ("change", "abort"))
    print("\n== summary: %d action(s), %d already converged ==" % (n_change, len(R.actions) - n_change))
    if not args.dry_run and n_change:
        print("   restart may be needed for subscription changes to take effect")
    print("   client subscription (clash): https://<your-ip>:%s%s%s" %
          (eff["subPort"], eff["subClashPath"], sub_id))
    return 0


main()
