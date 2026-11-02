terraform {
  required_version = ">= 1.9.0"
  required_providers {
    docker = { source = "kreuzwerker/docker", version = "4.6.0" }
  }
}
provider "docker" {}
variable "apps" {
  type = map(object({ image = string, release = string, subnet = string, address = string }))
}
resource "docker_network" "workload" {
  for_each = var.apps
  name     = "cp-toy-${each.key}"
  internal = true
  ipam_config {
    subnet  = each.value.subnet
    gateway = cidrhost(each.value.subnet, 1)
  }
  labels {
    label = "lab.owner"
    value = "control-plane-workloads"
  }
}
resource "docker_container" "workload" {
  for_each = var.apps
  name     = "cp-toy-${each.key}"
  image    = each.value.image
  env      = ["TOY_APP=${each.key}"]
  networks_advanced {
    name         = docker_network.workload[each.key].id
    ipv4_address = each.value.address
  }
  read_only = true
  user      = "65534:65534"
  capabilities { drop = ["ALL"] }
  security_opts = ["no-new-privileges:true"]
  memory        = 64
  memory_swap   = 128
  cpus          = "0.25"
  restart       = "unless-stopped"
  log_opts      = { "max-size" = "2m", "max-file" = "2" }
  labels {
    label = "lab.owner"
    value = "control-plane-workloads"
  }
  labels {
    label = "lab.app"
    value = each.key
  }
  labels {
    label = "lab.release"
    value = each.value.release
  }
  healthcheck {
    test         = ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=1)"]
    interval     = "5s"
    timeout      = "2s"
    retries      = 2
    start_period = "3s"
  }
}
