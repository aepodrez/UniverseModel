# Outputs
output "lambda_function_arn" {
  description = "ARN of the universe Lambda function"
  value       = aws_lambda_function.this.arn
}

output "lambda_function_name" {
  description = "Name of the universe Lambda function"
  value       = aws_lambda_function.this.function_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for universe Lambda"
  value       = aws_ecr_repository.this.repository_url
}

output "ecr_repository_arn" {
  description = "ARN of the ECR repository for universe Lambda"
  value       = aws_ecr_repository.this.arn
}
