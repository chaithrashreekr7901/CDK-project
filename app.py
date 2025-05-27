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
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = cdk.App()

# --- AWS Environment Setup ---
aws_account = app.node.try_get_context("aws_account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
aws_region = app.node.try_get_context("aws_region") or os.environ.get("CDK_DEFAULT_REGION")

if not aws_account or not aws_region:
    logger.error("AWS Account ID and Region must be specified via context or environment variables.")
    raise ValueError("AWS Account or Region not configured properly.")

env = cdk.Environment(account=aws_account, region=aws_region)
logger.info(f"Using AWS Account: {aws_account}, Region: {aws_region}")

# --- Load Configuration ---
try:
    config = get_deployment_configurations()
    if not isinstance(config, dict):
        raise TypeError("Configuration must be a dictionary.")
except Exception as e:
    logger.error(f"Failed to load deployment configuration: {e}", exc_info=True)
    # It's generally better to let the app crash here if config is critical
    raise # Re-raise the exception to stop execution

# --- Main Infrastructure Stack ---
# This stack will be modified to include CodeDeploy resources and parameters
main_stack_name = "MyMainInfrastructureStack"
main_infra_stack = MainOrchestratorStack(app, main_stack_name, # Store the instance if needed later
    description="Orchestrator stack for deploying multiple VPCs, resources, and CodeDeploy setup (v6).",
    config=config,
    env=env
)
logger.info(f"Main infrastructure stack '{main_stack_name}' defined.")

# --- CI/CD Pipeline Stack Configuration ---
# S3 bucket where application bundles will be uploaded by the pipeline's synth stage
# IMPORTANT: Replace this with the actual name of an S3 bucket you have created or will create.
# This bucket should be in the same AWS account and region as your pipeline.
APP_BUNDLE_S3_BUCKET_NAME = "mychaithrabucket" # <<< --- REPLACE THIS

if APP_BUNDLE_S3_BUCKET_NAME == "your-unique-app-bundle-bucket-crmp":
    logger.warning("Default APP_BUNDLE_S3_BUCKET_NAME is used. Please replace with your actual bucket name.")
    # Consider raising an error or exiting if a default placeholder is used for critical infra.
    # raise ValueError("APP_BUNDLE_S3_BUCKET_NAME must be set to a unique S3 bucket name.")


GITHUB_CONNECTION_ARN = "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1" # Replace if different
GITHUB_REPO_OWNER = "chaithrashreekr7901" # Replace if different
GITHUB_REPO_NAME = "CDK-project" # Replace if different
GITHUB_BRANCH = "CRMP-cdk" # Replace if different

pipeline_stack_name = "MyCDKApplicationPipelineStack"
PipelineStack(app, pipeline_stack_name,
    source_connection_arn=GITHUB_CONNECTION_ARN,
    source_repo_owner=GITHUB_REPO_OWNER,
    source_repo_name=GITHUB_REPO_NAME,
    source_branch_name=GITHUB_BRANCH,
    cdk_infra_stack_name=main_stack_name, # This is the MainOrchestratorStack
    app_bundle_s3_bucket_name=APP_BUNDLE_S3_BUCKET_NAME, # Pass the S3 bucket name
    env=env,
    description=f"CI/CD Pipeline for deploying the {main_stack_name} and its application."
)
logger.info(f"Pipeline stack '{pipeline_stack_name}' defined.")

# Synthesize the CDK app
try:
    app.synth()
    logger.info("CDK synthesis successful.")
except Exception as e:
    logger.error(f"CDK synthesis failed: {e}", exc_info=True)
    raise
