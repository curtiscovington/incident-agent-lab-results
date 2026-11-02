terraform {
  required_version = ">= 1.9.0"
  required_providers {
    docker = { source = "kreuzwerker/docker", version = "4.6.0" }
  }
}
provider "docker" {}
variable "apps" { type = map(object({ image = string, release = string })) }
locals { addresses = { api-1 = "21", api-2 = "22" } }
resource "docker_container" "api" {
  for_each       = var.apps
  name           = "cp-${each.key}"
  image          = each.value.image
  env            = ["NODE_NAME=${each.key}", "ROLE=api"]
  remove_volumes = false
  read_only      = true
  user           = "1000:1000"
  capabilities { drop = ["ALL"] }
  security_opts         = ["no-new-privileges:true"]
  tmpfs                 = { "/tmp" = "" }
  memory                = 384
  memory_swap           = 768
  cpus                  = "0.75"
  restart               = "unless-stopped"
  destroy_grace_seconds = 1
  log_driver            = "json-file"
  log_opts              = { "max-size" = "5m", "max-file" = "2" }
  networks_advanced {
    name         = "cp-control"
    ipv4_address = "10.78.40.${local.addresses[each.key]}"
    aliases      = [each.key]
  }
  networks_advanced {
    name         = "cp-data"
    ipv4_address = "10.78.50.${local.addresses[each.key]}"
    aliases      = [each.key]
  }
  volumes {
    host_path      = abspath("${path.module}/../../.runtime/control-plane/db-password")
    container_path = "/run/secrets/db_password"
    read_only      = true
  }
  labels {
    label = "com.docker.compose.project"
    value = "bigspark-plane"
  }
  labels {
    label = "com.docker.compose.service"
    value = each.key
  }
  labels {
    label = "lab.owner"
    value = "control-plane-releases"
  }
  labels {
    label = "lab.role"
    value = "api"
  }
  labels {
    label = "lab.release"
    value = each.value.release
  }
  healthcheck {
    test         = ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8080/health',timeout=2)"]
    interval     = "5s"
    timeout      = "3s"
    retries      = 2
    start_period = "10s"
  }
}
