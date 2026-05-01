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

variable "task_cpu" {
  description = "CPU units for the ECS task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Memory for the ECS task in MB"
  type        = number
  default     = 4096
}

variable "container_image_tag" {
  description = "Docker image tag for the ECS task container"
  type        = string
  default     = "latest"
}

variable "alpaca_api_key" {
  description = "Alpaca API key for shortability enrichment"
  type        = string
  sensitive   = true
}

variable "alpaca_api_secret" {
  description = "Alpaca API secret for shortability enrichment"
  type        = string
  sensitive   = true
}

variable "alpaca_base_url" {
  description = "Alpaca base URL (live or paper API)"
  type        = string
  default     = "https://paper-api.alpaca.markets"
}
