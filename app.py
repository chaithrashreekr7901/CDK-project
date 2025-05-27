#!/usr/bin/env python3
import os
import logging
import aws_cdk as cdk

# Direct imports from root directory
from main_orchestrator_stack import MainOrchestratorStack
from deployment_config import get_deployment_configurations

# Import Pipeline Stack from pipeline module
from pipeline.pipeline_stack import PipelineStack

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', # Added %(name)s for logger context
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__) # Use __name__ for the logger

app = cdk.App()

# --- AWS Environment Setup ---
aws_account = app.node.try_get_context("aws_account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
aws_region = app.node.try_get_context("aws_region") or os.environ.get("CDK_DEFAULT_REGION")

if not aws_account or not aws_region:
    logger.error("AWS Account ID and Region must be specified via context or environment variables (e.g., CDK_DEFAULT_ACCOUNT, CDK_DEFAULT_REGION).")
    raise ValueError("AWS Account or Region not configured properly. Please set them in cdk.json context or as environment variables.")

env = cdk.Environment(account=aws_account, region=aws_region)
logger.info(f"Using AWS Account: {aws_account}, Region: {aws_region}")

# --- Load Configuration ---
try:
    config = get_deployment_configurations()
    if not isinstance(config, dict):
        logger.error(f"Configuration loaded is not a dictionary. Type: {type(config)}")
        raise TypeError("Configuration must be a dictionary.")
    logger.info("Deployment configuration loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load deployment configuration: {e}", exc_info=True)
    raise # Re-raise the exception to stop execution if config is critical

# --- Main Infrastructure Stack ---
# This stack will be modified to include CodeDeploy resources and parameters
main_stack_name = config.get("main_stack_name", "MyMainInfrastructureStack") # Get stack name from config or default
main_infra_stack_description = config.get("main_stack_description", "Orchestrator stack for deploying multiple VPCs, resources, and CodeDeploy setup.")

main_infra_stack = MainOrchestratorStack(app, main_stack_name,
    description=main_infra_stack_description,
    config=config,
    env=env
)
logger.info(f"Main infrastructure stack '{main_stack_name}' defined.")

# --- CI/CD Pipeline Stack Configuration ---

# S3 bucket where application bundles will be uploaded by the pipeline's synth stage
# IMPORTANT: Replace this with the actual name of an S3 bucket you have created.
# This bucket should be in the same AWS account and region as your pipeline.
# Ensure it's globally unique. Example: "mycompany-crmp-app-bundles-12345-us-east-1"
APP_BUNDLE_S3_BUCKET_NAME = config.get("app_bundle_s3_bucket_name", "mychaithrabucket") # Get from config or default

if APP_BUNDLE_S3_BUCKET_NAME == "your-unique-app-bundle-bucket-crmp":
    logger.critical("CRITICAL: Default APP_BUNDLE_S3_BUCKET_NAME is used. "
                    "Please replace this with your actual unique S3 bucket name in your deployment_config.py or directly in app.py.")
    # Consider raising an error to prevent deployment with a placeholder bucket name.
    # raise ValueError("APP_BUNDLE_S3_BUCKET_NAME must be set to a unique S3 bucket name.")

# GitHub Configuration - Consider moving these to your deployment_config.py or CDK context
GITHUB_CONNECTION_ARN = config.get("github_connection_arn", "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1")
GITHUB_REPO_OWNER = config.get("github_repo_owner", "chaithrashreekr7901")
GITHUB_REPO_NAME = config.get("github_repo_name", "CDK-project")
GITHUB_BRANCH = config.get("github_branch_name", "CRMP-cdk") # Changed from GITHUB_BRANCH to avoid conflict if it was a global

pipeline_stack_name = config.get("pipeline_stack_name", "MyCDKApplicationPipelineStack")
pipeline_description = config.get("pipeline_stack_description", f"CI/CD Pipeline for deploying the {main_stack_name} and its application.")

PipelineStack(app, pipeline_stack_name,
    source_connection_arn=GITHUB_CONNECTION_ARN,
    source_repo_owner=GITHUB_REPO_OWNER,
    source_repo_name=GITHUB_REPO_NAME,
    source_branch_name=GITHUB_BRANCH,
    cdk_infra_stack_name=main_stack_name, # This is the MainOrchestratorStack
    app_bundle_s3_bucket_name=APP_BUNDLE_S3_BUCKET_NAME, # Pass the S3 bucket name
    env=env,
    description=pipeline_description
)
logger.info(f"Pipeline stack '{pipeline_stack_name}' defined.")

# Synthesize the CDK app
try:
    app.synth()
    logger.info("CDK synthesis successful. CloudFormation templates are in 'cdk.out/'.")
except Exception as e:
    logger.error(f"CDK synthesis failed: {e}", exc_info=True)
    raise
