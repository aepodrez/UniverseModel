terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Using local backend - can be changed to S3 for team collaboration
  backend "local" {
    path = "terraform.tfstate"
  }
}

# Data sources for account and region information
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Read S3 bucket information from parent infrastructure SSM parameters
data "aws_ssm_parameter" "s3_bucket_name" {
  name = "/${var.project_name}${local.env_path}/s3_bucket_name"
}

data "aws_ssm_parameter" "s3_bucket_arn" {
  name = "/${var.project_name}${local.env_path}/s3_bucket_arn"
}

# Common tags for all resources
locals {
  env_suffix = var.environment != "" ? "-${var.environment}" : ""
  env_path   = var.environment != "" ? "/${var.environment}" : ""

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Component   = "universe"
  }
}
