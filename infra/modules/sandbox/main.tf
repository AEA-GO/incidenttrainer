# Game servers (contract 4): the launch template the API's launcher starts every game from.
#
# - Image: whatever SSM /itr/worlds/<world>/ami says at launch time (labs CI updates it), so
#   a new world image needs no template change.
# - No subnet here: the launcher picks one of the sandbox subnets (zones a, b, c) and, if
#   AWS is out of t4g.medium everywhere, m7g.medium or m6g.medium (decided 21 Nov). IAM lets
#   it choose only those subnets and types; everything else comes from this template.
# - No IAM role, no user data, no public IP, IMDSv2 with hop limit 1 and no tags in
#   metadata; shutting down terminates (the image's 45-minute self-destruct deletes it).
# - Disks come from the image: root 16 GB + /data 4 GB, gp3, encrypted, deleted with it.
#
# Cost: the template is free. A game: t4g.medium ~$0.029 (45 min) + disks ~$0.002;
# m7g.medium ~$0.036. CPU credits unlimited (the default: no throttling while it boots).

resource "aws_launch_template" "world" {
  for_each = toset(var.worlds)

  name                                 = "${var.name}-${each.key}"
  description                          = "IncidentTrainer game server, world ${each.key} (contract 4)"
  image_id                             = "resolve:ssm:/itr/worlds/${each.key}/ami"
  instance_type                        = "t4g.medium"
  vpc_security_group_ids               = [var.security_group_id]
  instance_initiated_shutdown_behavior = "terminate"
  update_default_version               = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  credit_specification {
    cpu_credits = "unlimited"
  }

  monitoring {
    enabled = false
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { env = "main", cost = "game", Name = "itr-game", itr-world = each.key }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { env = "main", cost = "game", Name = "itr-game", itr-world = each.key }
  }

  tags = { itr-world = each.key }
}
