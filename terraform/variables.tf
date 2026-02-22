variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "euclidean"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "lambda_memory_size" {
  description = "Memory size for Lambda function in MB"
  type        = number
  default     = 1024
}

variable "lambda_timeout" {
  description = "Timeout for Lambda function in seconds (max 900)"
  type        = number
  default     = 900
}

variable "lambda_image_tag" {
  description = "Docker image tag for Lambda function"
  type        = string
  default     = "latest"
}
