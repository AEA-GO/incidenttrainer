output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "origin_domain" {
  description = "API Gateway hostname, used as a CloudFront origin."
  value       = replace(aws_apigatewayv2_api.api.api_endpoint, "https://", "")
}
