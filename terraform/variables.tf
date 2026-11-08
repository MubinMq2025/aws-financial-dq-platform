variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "ap-southeast-2"
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Short project name used in resource names"
  type        = string
  default     = "fin-dq"
}
