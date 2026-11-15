provider "threexui" {
  endpoint               = var.panel_endpoint
  base_path              = var.panel_base_path
  username               = var.panel_username
  password               = var.panel_password
  insecure_skip_verify   = var.panel_insecure
}

locals {
  # Node names are what the panel generates: "<inbound remark>-<client email>".
  primary_node  = "${threexui_inbound.primary.remark}-${var.client_email}"
  fallback_node = "${threexui_inbound.fallback.remark}-${var.client_email}"
  node_names    = [local.primary_node, local.fallback_node]
  members       = join("\n", [for n in local.node_names : "  - ${n}"])

  # mergeClashRulesYAML merges only `rules`; every other top-level key is
  # assigned over the generated config. So proxy-groups must be restated in
  # full, including LIVE, or declaring groups silently deletes the fork's
  # automatic port failover.
  clash_rules = <<-EOT
    proxy-groups:
    - name: LIVE
      type: url-test
      proxies:
    ${local.members}
      url: http://www.gstatic.com/generate_204
      interval: 300
      tolerance: 50
    - name: PROXY
      type: select
      proxies:
      - LIVE
    ${local.members}
      - DIRECT
    - name: ${threexui_inbound.primary.remark}
      type: select
      proxies:
      - LIVE
    ${local.members}
      - DIRECT
    - name: Final
      type: select
      proxies:
      - PROXY
      - DIRECT
    rules:
    - DOMAIN-SUFFIX,openai.com,${threexui_inbound.primary.remark}
    - DOMAIN-SUFFIX,chatgpt.com,${threexui_inbound.primary.remark}
    - DOMAIN-SUFFIX,github.com,PROXY
    - DOMAIN-SUFFIX,wikipedia.org,PROXY
    - GEOIP,CN,DIRECT
    - MATCH,Final
  EOT
}

resource "threexui_inbound" "primary" {
  port     = var.primary_port
  protocol = "vless"
  enable   = true
  remark   = "TechnoVMBGP Lite"

  vless_settings {
    decryption = "none"
  }

  stream_settings {
    network  = "tcp"
    security = "reality"
    reality_settings {
      target       = var.reality_target
      server_names = [split(":", var.reality_target)[0]]
    }
  }

  sniffing {
    enabled       = true
    dest_override = ["http", "tls", "quic", "fakedns"]
  }
}

resource "threexui_inbound" "fallback" {
  port     = var.fallback_port
  protocol = "vless"
  enable   = true
  remark   = "TechnoVMBGP Lite [fallback 8443]"

  vless_settings {
    decryption = "none"
  }

  stream_settings {
    network  = "tcp"
    security = "reality"
    reality_settings {
      target       = var.reality_target
      server_names = [split(":", var.reality_target)[0]]
    }
  }
}

resource "threexui_inbound_client" "primary" {
  inbound_id  = threexui_inbound.primary.id
  email       = var.client_email
  client_id   = var.client_uuid
  flow        = "xtls-rsv1"
  enable      = true
  total_gb    = var.total_gb
  sub_id      = var.client_sub_id
  expiry_time = 0
}

resource "threexui_inbound_client" "fallback" {
  inbound_id  = threexui_inbound.fallback.id
  email       = var.client_email
  client_id   = var.client_uuid
  flow        = "xtls-rsv1"
  enable      = true
  total_gb    = var.total_gb
  sub_id      = var.client_sub_id
  expiry_time = 0
}

# sing-box clients get automatic fastest-port selection from this upstream
# feature; clash clients get it from the LIVE url-test group above.
resource "threexui_sub_balancer" "auto_fastest" {
  remark      = "auto-fastest"
  strategy    = "leastPing"
  inbound_ids = [threexui_inbound.primary.id, threexui_inbound.fallback.id]
  enabled     = true
}

resource "threexui_panel_subscription" "settings" {
  sub_enable              = true
  sub_port                = 2096
  sub_clash_enable        = true
  sub_clash_path          = var.clash_path
  sub_json_enable         = true
  sub_json_path           = var.singbox_path
  sub_clash_enable_routing  = true
  sub_clash_rules         = local.clash_rules
  remark_template         = "{{INBOUND}}-{{EMAIL}}"
}
