# The gateway image: Ubuntu 24.04 arm64, Ubuntu's nginx, gateway-auth with its own
# Python, systemd units. Built by .github/workflows/gateway-image.yml in the default VPC
# (the gateway itself never gets internet; only this temporary build server does).
# The gateway's settings (table, lab bucket, public origin) come from user data at boot,
# so one image works for any environment. See gateway/image/README.md.

packer {
  required_version = ">= 1.16.0"
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = "~> 1.8"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "app_archive" {
  type        = string
  description = "The gateway/ folder as a .tar.gz (git archive), made by the workflow"
}

variable "availability_zone" {
  type        = string
  default     = ""
  description = "Zone for the build server; empty picks a random one. The workflow tries each zone in turn when AWS is out of capacity."
}

variable "build_ref" {
  type        = string
  default     = "local"
  description = "Platform repo commit, recorded in the image and its tags"
}

locals {
  stamp = formatdate("YYYYMMDD-hhmm", timestamp())

  tags = {
    project   = "incidenttrainer"
    env       = "main"
    itr-image = "gateway"
    build-ref = var.build_ref
  }
}

data "amazon-ami" "ubuntu" {
  region      = var.region
  owners      = ["099720109477"] # Canonical
  most_recent = true

  filters = {
    name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"
    virtualization-type = "hvm"
    root-device-type    = "ebs"
  }
}

source "amazon-ebs" "gateway" {
  region        = var.region
  source_ami    = data.amazon-ami.ubuntu.id
  instance_type = "t4g.micro"
  ssh_username  = "ubuntu"

  # The default VPC's subnet in the chosen zone; the default VPC has internet.
  availability_zone = var.availability_zone
  subnet_filter {
    filters = { "default-for-az" = "true" }
    random  = true
  }
  associate_public_ip_address               = true
  temporary_security_group_source_public_ip = true # SSH only from this runner

  ami_name        = "itr-gateway-${local.stamp}"
  ami_description = "IncidentTrainer gateway: nginx + gateway-auth"
  imds_support    = "v2.0"
  ena_support     = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # 8 GB: a deploy swaps the server's root disk for this image's, and the new disk may
  # not be smaller than the old one, so keep this size fixed.
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  run_tags      = merge(local.tags, { Name = "itr-image-build", cost = "image-build" })
  tags          = merge(local.tags, { Name = "itr-gateway-${local.stamp}" })
  snapshot_tags = local.tags
}

build {
  sources = ["source.amazon-ebs.gateway"]

  provisioner "shell" {
    inline = ["cloud-init status --wait > /dev/null"]
  }

  provisioner "file" {
    source      = var.app_archive
    destination = "/tmp/gateway.tgz"
  }

  provisioner "shell" {
    script           = "${path.root}/setup.sh"
    execute_command  = "sudo -E bash '{{ .Path }}'"
    environment_vars = ["BUILD_REF=${var.build_ref}"]
  }

  # Last: forget this build's identity, keys and logs, so the gateway boots fresh
  # (cloud-init then runs again and writes the settings from user data).
  provisioner "shell" {
    inline = [
      "sudo cloud-init clean --logs --seed",
      "sudo truncate -s 0 /etc/machine-id",
      "sudo rm -f /home/ubuntu/.ssh/authorized_keys /root/.ssh/authorized_keys",
      "sudo journalctl --rotate --vacuum-time=1s > /dev/null",
      "sudo rm -f /var/lib/landscape/landscape-sysinfo.cache /run/motd.dynamic",
      "sudo rm -rf /tmp/* /var/tmp/*",
    ]
  }

  post-processor "manifest" {
    output     = "manifest.json"
    strip_path = true
  }
}
