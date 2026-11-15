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
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

DB = "/etc/x-ui/x-ui.db"
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
    primary = next((i for i in ins if i["port"] == 443), None)
    if primary is None:
        # Generating a Reality keypair and picking a dest is a judgement call;
        # silently inventing one is worse than stopping.
        R.note("abort", "no inbound on 443 - create the Reality inbound first")
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
    want = {"subEnable": True, "subClashEnable": True, "subClashEnableRouting": True}
    changed = {}
    for k, v in want.items():
        if str(s.get(k, "")).lower() != "true":
            changed[k] = v
    if changed:
        allsettings = R.all_settings()
        allsettings.update(changed)
        R.write("subscription: " + ", ".join(sorted(changed)),
                lambda: R.api("/panel/api/setting/update", allsettings))
    else:
        R.note("ok", "subscription + clash + routing enabled")

    # ---- 3. routing tree, derived from the live subscription -------------
    import sqlite3
    rows = list(sqlite3.connect(DB).execute("select sub_id from clients where enable=1 limit 1"))
    if not rows:
        R.note("warn", "no enabled client; create one in the panel then re-run")
        return 2
    sub_id = rows[0][0]
    url = "https://127.0.0.1:%s%s%s" % (s["subPort"], s["subClashPath"], sub_id)
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

    # ---- 4. ops hooks ----------------------------------------------------
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    for marker, line in (("/usr/local/bin/xui-watchdog", "*/30 * * * * /usr/local/bin/xui-watchdog >/dev/null 2>&1"),
                         ("/usr/local/bin/xui-backup", "40 3 * * * /usr/local/bin/xui-backup >/dev/null 2>&1")):
        if marker in cron:
            R.note("ok", "cron present: %s" % marker)
        else:
            def add_cron(line=line):
                newcron = cron.rstrip("\n") + "\n" + line + "\n"
                subprocess.run(["crontab", "-"], input=newcron, text=True, check=True)
            if subprocess.run(["test", "-x", marker]).returncode == 0:
                R.write("schedule %s" % marker, add_cron)
            else:
                R.note("skip", "%s not installed on this host" % marker)

    if subprocess.run(["test", "-f", "/etc/x-ui/lgl-guard.nft"]).returncode == 0:
        R.note("ok", "looking-glass guard file present")
    else:
        R.note("skip", "no /etc/x-ui/lgl-guard.nft; run the guard installer if you "
                       "expose a speedtest port")

    # ---- 5. report -------------------------------------------------------
    n_change = sum(1 for k, _ in R.actions if k in ("change", "abort"))
    print("\n== summary: %d action(s), %d already converged ==" % (n_change, len(R.actions) - n_change))
    if not args.dry_run and n_change:
        print("   restart may be needed for subscription changes to take effect")
    print("   client subscription (clash): https://<your-ip>:%s%s%s" %
          (s["subPort"], s["subClashPath"], sub_id))
    return 0


main()
