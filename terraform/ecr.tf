# ECR repository for universe Lambda function
#
# TEMPORARY: re-added with force_delete = true after the prior removal of this
# file failed to apply ("RepositoryNotEmptyException" — the repo still has
# images from when CI built this now-dead-ECS-path image). This first restores
# the resource so Terraform updates force_delete in place (no destroy), then a
# follow-up commit removes this file again — that destroy will then succeed
# since force_delete is now true. See UniverseModel commit 99429db and
# EuclideanInfra/DataIngressModel session notes for why this ECR repo/image is
# unused (the Lambdas are zip-packaged, not container images).
resource "aws_ecr_repository" "this" {
  name                 = "${var.project_name}/universe"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

# Lifecycle policy to keep only the last 5 images
resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy     = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
