resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.project_name}-universe${local.env_suffix}"
  retention_in_days = 30

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.project_name}-universe${local.env_suffix}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution_role.arn
  task_role_arn            = aws_iam_role.task_role.arn

  container_definitions = jsonencode([
    {
      name      = "universe"
      image     = "${aws_ecr_repository.this.repository_url}:${var.container_image_tag}"
      essential = true

      environment = [
        {
          name  = "S3_BUCKET"
          value = data.aws_ssm_parameter.s3_bucket_name.value
        },
        {
          name  = "S3_PREFIX"
          value = "universe/"
        },
        {
          name  = "DATA_INGRESS_UNIVERSE_KEY"
          value = "data-ingress/Static/universe.csv"
        },
        {
          name  = "ENVIRONMENT"
          value = var.environment
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "universe"
        }
      }
    }
  ])

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy_attachment.execution_role_policy
  ]

  tags = local.common_tags
}
