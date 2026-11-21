output "launch_templates" {
  description = "world -> {id, arn, name}"
  value = {
    for world, lt in aws_launch_template.world : world => { id = lt.id, arn = lt.arn, name = lt.name }
  }
}
