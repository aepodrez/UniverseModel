# Outputs
output "ecs_task_definition_arn" {
  description = "ARN of the universe ECS task definition"
  value       = aws_ecs_task_definition.this.arn
}

output "ecs_task_family" {
  description = "Family name of the universe ECS task definition"
  value       = aws_ecs_task_definition.this.family
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for universe container image"
  value       = aws_ecr_repository.this.repository_url
}

output "ecr_repository_arn" {
  description = "ARN of the ECR repository for universe container image"
  value       = aws_ecr_repository.this.arn
}
