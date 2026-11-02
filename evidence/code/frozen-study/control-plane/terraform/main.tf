terraform {
  required_version = ">= 1.9.0"
  required_providers {
    docker = { source = "kreuzwerker/docker", version = "4.6.0" }
  }
}
provider "docker" {}

# Inventory adoption only: preserve existing configuration during migration.
# New experiment deployments use infra/modules/deployment, with full configuration.
variable "existing_containers" {
  type = map(object({ id = string, image = string }))
}
resource "docker_container" "control_plane" {
  for_each = var.existing_containers
  name     = "cp-${each.key}"
  image    = each.value.image
  must_run = false
  lifecycle {
    ignore_changes  = all
    prevent_destroy = true
  }
}
import {
  for_each = var.existing_containers
  to       = docker_container.control_plane[each.key]
  id       = each.value.id
}
variable "agent_enabled" {
  type    = bool
  default = true
}
variable "agent_image" {
  type        = string
  description = "Pinned, locally available Python runtime image ID."
  default     = "sha256:7efd45553a98be3d940b73600550669831c0be34f30c521159e7030a91fcdee6"
}
resource "docker_network" "agent_access" {
  name     = "cp-agent"
  internal = true
  ipam_config {
    subnet  = "10.78.80.0/24"
    gateway = "10.78.80.1"
  }
  lifecycle { create_before_destroy = true }
}
resource "docker_container" "incident_agent" {
  count   = var.agent_enabled ? 1 : 0
  name    = "lab-incident-agent"
  image   = var.agent_image
  command = ["python", "-u", "/app/incident-agent.py"]
  env     = ["LAB_AGENT_URL=http://lab.local/api/incident-agent", "PYTHONDONTWRITEBYTECODE=1"]
  host {
    host = "lab.local"
    ip   = "10.78.80.1"
  }
  networks_advanced {
    name         = docker_network.agent_access.name
    ipv4_address = "10.78.80.11"
  }
  volumes {
    host_path      = abspath("${path.module}/../../agents/incident-agent.py")
    container_path = "/app/incident-agent.py"
    read_only      = true
  }
  volumes {
    host_path      = abspath("${path.module}/../../.runtime/incident-agent/credential")
    container_path = "/run/secrets/agent-token"
    read_only      = true
  }
  read_only = true
  user      = "65534:65534"
  capabilities { drop = ["ALL"] }
  security_opts = ["no-new-privileges:true"]
  memory        = 64
  memory_swap   = 128
  cpus          = "0.25"
  restart       = "unless-stopped"
  labels {
    label = "lab.system"
    value = "control-plane"
  }
  labels {
    label = "lab.role"
    value = "incident-agent"
  }
  healthcheck {
    test     = ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8080/health',timeout=2)"]
    interval = "10s"
    timeout  = "3s"
    retries  = 3
  }
}

variable "existing_networks" {
  type = map(object({ id = string, name = string }))
}
resource "docker_network" "control_plane" {
  for_each = var.existing_networks
  name     = each.value.name
  lifecycle {
    ignore_changes  = all
    prevent_destroy = true
  }
}
import {
  for_each = var.existing_networks
  to       = docker_network.control_plane[each.key]
  id       = each.value.id
}
variable "existing_volumes" { type = map(string) }
resource "docker_volume" "control_plane" {
  for_each = var.existing_volumes
  name     = each.value
  lifecycle {
    ignore_changes  = all
    prevent_destroy = true
  }
}
import {
  for_each = var.existing_volumes
  to       = docker_volume.control_plane[each.key]
  id       = each.value
}
