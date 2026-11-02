terraform { required_version = ">= 1.9.0" }
variable "denied" { type = bool }
variable "api_ids" { type = map(string) }
resource "terraform_data" "database_route" {
  triggers_replace = [var.denied, var.api_ids]
  provisioner "local-exec" {
    command = "${abspath(path.module)}/../../.venv/bin/python ${abspath(path.module)}/apply.py"
  }
}
