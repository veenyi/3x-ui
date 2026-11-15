# fleet/ — declarative management of the panel

Uses `batonogov/terraform-provider-threexui` (MIT) against this fork's REST API.
This is the layer that makes "let an AI agent change the config" safe: the agent
edits `.tf`, runs `plan`, and the diff is reviewable before anything touches the
panel.

## Credentials

Never in these files. Pass them through the environment:

```bash
export TF_VAR_panel_endpoint='https://<ip>:64265/64265'
export TF_VAR_panel_username="$(sed -n 's/^XUI_USERNAME=//p' /etc/x-ui/install-result.env)"
export TF_VAR_panel_password="$(sed -n 's/^XUI_PASSWORD=//p' /etc/x-ui/install-result.env)"
export TF_VAR_client_email=... TF_VAR_client_uuid=... TF_VAR_client_sub_id=...
export TF_VAR_clash_path=/gnl3w7lgb8uoo98k/ TF_VAR_singbox_path=/09zswwzrohemcwfk/
```

`panel_endpoint` must include the **web base path**, and should use the address
the panel's certificate is valid for — this box has a Let's Encrypt cert for the
public IP, so `https://<public-ip>:<port>` verifies and `https://127.0.0.1` does not.

## Adopting an already-running panel

Terraform starts with empty state, so a bare `plan` proposes to **create**
resources that already exist. Applying that would duplicate inbounds and clients.
Import first:

```bash
terraform init
terraform plan                      # read-only; expect "N to add" before import
terraform import threexui_inbound.primary 1
terraform import threexui_inbound.fallback 2
terraform import threexui_sub_balancer.auto_fastest 1
# inbound_client and panel_subscription have no numeric id; check
# `terraform state` guidance in the provider docs before importing them.
terraform plan                      # should now converge toward a small diff
```

`terraform apply` is deliberately not run by any script here.

## Two provider constraints worth knowing

* `threexui_node` create/update calls `ensureReachable` server-side, so a node
  whose API is unreachable from the central panel **fails at apply time**. There
  is no bypass flag; this is the 3x-ui server's own behaviour.
* Changing a node's `outbound_tag` makes the panel restart the Xray core itself.

## Adding a second VPS

Two ways, and they solve different problems:

* `threexui_node` registers the remote panel as a cluster member: the central
  panel probes it, syncs its inbounds, and can push config. Use this when you
  want one control plane over several boxes.
* `threexui_outbound_subscription` (see provider docs) pulls a remote panel's
  subscription as outbounds. Use this when you only want its nodes in your
  client configs.

Either way the clash groups in `main.tf` are built from `local.node_names`, so a
new region needs its own `threexui_inbound` plus a group entry — the panel does
not auto-discover nodes into custom groups.
