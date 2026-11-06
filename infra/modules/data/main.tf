# One DynamoDB table for the whole MVP (single-table design):
#   pk / sk strings, e.g. SESSION#<id> / META, SESSION#<id> / ACT#<time>, CODE#<code> / META.
# Items with an `expires_at` number (Unix seconds) are deleted by DynamoDB TTL (30 days for games).
#
# Cost: on-demand, pay per request (cents at our volume); 25 GB storage free; point-in-time
# recovery ~$0.20 per GB-month (our table is tiny).

resource "aws_dynamodb_table" "this" {
  name                        = var.name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
