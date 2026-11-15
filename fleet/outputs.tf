locals {
  # Three separate traps produced the wrong links here:
  #   * joining var.panel_endpoint with a path that may or may not start with
  #     "/" yields a double slash, and the panel answers 404;
  #   * replace() is literal, NOT regex, so "^https?://" left the scheme in
  #     place and gave https://https://...;
  #   * the subscription is served on sub_port (2096) and WITHOUT the panel's
  #     web base path. Reusing panel_endpoint's port and base path 404s every
  #     link even though the panel itself is healthy.
  # regex() with a capture group returns a tuple, not a string, so indexing is
  # required or the interpolation fails with "is tuple with 1 element".
  sub_host = regex("^[a-z]+://([^:/]+)", var.panel_endpoint)[0]
  sub_base = "https://${local.sub_host}:${var.subscription_port}"
}

output "clash_subscription" {
  value = "${local.sub_base}${trimsuffix(var.clash_path, "/")}/${var.client_sub_id}"
}

output "singbox_subscription" {
  value = "${local.sub_base}${trimsuffix(var.singbox_path, "/")}/${var.client_sub_id}"
}

output "managed_inbound_ports" {
  value = [threexui_inbound.primary.port, threexui_inbound.fallback.port]
}
