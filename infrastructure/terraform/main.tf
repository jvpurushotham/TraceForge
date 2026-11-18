# OPTIONAL: short-lived EKS cluster for the cloud demo. NOT executed in the authoring environment - review it,
# check current AWS pricing, and run `terraform destroy` when finished (EKS, NAT gateways and load balancers cost money).
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
variable "region" { default = "ap-south-1" }
variable "cluster_name" { default = "traceforge-demo" }
provider "aws" { region = var.region }

data "aws_availability_zones" "available" { state = "available" }

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  name    = "${var.cluster_name}-vpc"
  cidr    = "10.0.0.0/16"
  azs     = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets    = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets     = ["10.0.101.0/24", "10.0.102.0/24"]
  enable_nat_gateway = true
  single_nat_gateway = true            # cheaper; not highly available
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"
  cluster_name    = var.cluster_name
  cluster_version = "1.31"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets
  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true
  eks_managed_node_groups = {
    default = { instance_types = ["t3.large"], min_size = 2, max_size = 3, desired_size = 2 }
  }
}

output "update_kubeconfig" { value = "aws eks update-kubeconfig --region ${var.region} --name ${var.cluster_name}" }
