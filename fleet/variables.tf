variable "panel_endpoint" {
  type        = string
  description = "Panel origin, scheme://host:port, WITHOUT the base path."
  # No default: a wrong endpoint silently manages the wrong box.
}

variable "panel_base_path" {
  type        = string
  description = "Web base path, e.g. /64265/. The provider has a dedicated attribute for this; folding it into endpoint invites a double slash."
}

variable "subscription_port" {
  type        = number
  default     = 2096
  description = "sub_port, not the panel web port. Subscription links are served here."
}

variable "panel_insecure" {
  type        = bool
  default     = false
  description = "Only for panels with a self-signed cert. This fork's box uses a real LE cert for its public IP, so leave it false."
}

variable "panel_username" {
  type      = string
  sensitive = true
}

variable "panel_password" {
  type      = string
  sensitive = true
}

variable "reality_target" {
  type        = string
  default     = "www.sony.co.uk:443"
  description = "Handshake target. Must still speak TLS1.3 + X25519 or every client breaks silently."
}

variable "primary_port" {
  type    = number
  default = 443
}

variable "fallback_port" {
  type        = number
  default     = 8443
  description = "Second port for networks that block 443."
}

variable "client_email" {
  type        = string
  description = "Existing client email, so import lines up with live state."
}

variable "client_uuid" {
  type        = string
  description = "Existing client UUID. Changing it invalidates every imported subscription."
}

variable "client_sub_id" {
  type        = string
  description = "Existing subscription id."
}

variable "clash_path" {
  type        = string
  description = "Randomised clash subscription path prefix."
}

variable "singbox_path" {
  type        = string
  description = "Randomised sing-box subscription path prefix."
}

variable "total_gb" {
  type        = number
  default     = 0
  description = "Traffic cap in GB; 0 means unlimited."
}
