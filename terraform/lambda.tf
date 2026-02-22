# CloudWatch Log Group for Lambda function
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-universe-${var.environment}"
  retention_in_days = 30

  tags = local.common_tags
}

# Lambda function for universe step
resource "aws_lambda_function" "this" {
  function_name = "${var.project_name}-universe-${var.environment}"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.this.repository_url}:${var.lambda_image_tag}"
  memory_size   = var.lambda_memory_size
  timeout       = var.lambda_timeout

  environment {
    variables = {
      S3_BUCKET   = data.aws_ssm_parameter.s3_bucket_name.value
      S3_PREFIX   = "universe/"
      ENVIRONMENT = var.environment
    }
  }

  # CI/CD will update the image_uri, so we ignore changes to it
  lifecycle {
    ignore_changes = [image_uri]
  }

  # Ensure log group is created before Lambda function
  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy_attachment.lambda_basic_execution
  ]

  tags = local.common_tags
}
