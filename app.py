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
    exit(1)

# --- Main Infrastructure Stack ---
main_stack_name = "MyMainInfrastructureStack"
MainOrchestratorStack(app, main_stack_name,
    description="Orchestrator stack for deploying multiple VPCs and resources (v5).",
    config=config,
    env=env
)

# --- CI/CD Pipeline Stack Configuration ---
GITHUB_CONNECTION_ARN = "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1"
GITHUB_REPO_OWNER = "chaithrashreekr7901"
GITHUB_REPO_NAME = "CDK-project"
GITHUB_BRANCH = "CRMP-cdk"

pipeline_stack_name = "MyCDKApplicationPipelineStack"
PipelineStack(app, pipeline_stack_name,
    source_connection_arn=GITHUB_CONNECTION_ARN,
    source_repo_owner=GITHUB_REPO_OWNER,
    source_repo_name=GITHUB_REPO_NAME,
    source_branch_name=GITHUB_BRANCH,
    cdk_app_stack_name=main_stack_name,
    env=env
)

app.synth()
