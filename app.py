#!/usr/bin/env python3
import os
import logging
import aws_cdk as cdk

# Direct imports from root directory
from main_orchestrator_stack import MainOrchestratorStack
from deployment_config import get_deployment_configurations

# Import Pipeline Stack from pipeline module
from pipeline.pipeline_stack import PipelineStack
from pipeline.app_pipeline_stack import AppDeploymentPipelineStack

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

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
    raise

# --- Main Infrastructure Stack ---
# This stack will define CodeDeploy Application and DeploymentGroup
main_stack_name = config.get("main_stack_name", "MyMainInfrastructureStack")
main_infra_stack_description = config.get("main_stack_description", "Orchestrator stack for deploying multiple VPCs, resources, and CodeDeploy setup.")

main_infra_stack = MainOrchestratorStack(app, main_stack_name,
    description=main_infra_stack_description,
    config=config,
    env=env
)
logger.info(f"Main infrastructure stack '{main_stack_name}' defined.")

# --- CI/CD Pipeline Stack Configuration ---

# GitHub Configuration - Consider moving these to your deployment_config.py or CDK context
GITHUB_CONNECTION_ARN = config.get("github_connection_arn", "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1")
GITHUB_REPO_OWNER = config.get("github_repo_owner", "chaithrashreekr7901")
GITHUB_REPO_NAME = config.get("github_repo_name", "CDK-project")
GITHUB_BRANCH = config.get("github_branch_name", "CRMP-cdk")

# Names for CodeDeploy resources - these should match what MainOrchestratorStack creates
# You might want to get these from config as well, or ensure they are consistently named.
# Example: If MainOrchestratorStack names them based on its own stack name.
CODEPLOY_APPLICATION_NAME = f"{main_stack_name}-EC2App" # Example name
CODEPLOY_DEPLOYMENT_GROUP_NAME = f"{main_stack_name}-EC2-DG" # Example name

pipeline_stack_name = config.get("pipeline_stack_name", "MyCDKDirectCodeDeployPipeline") # Renamed for clarity
pipeline_description = config.get("pipeline_stack_description", f"CI/CD Pipeline for {main_stack_name} with direct CodeDeploy action.")

app_pipeline_stack_name = config.get("app_pipeline_stack_name", "MyAppDeployPipeline")
app_pipeline_description = config.get("app_pipeline_stack_description", "App Deployment Pipeline for EC2 via CodeDeploy")

PipelineStack(app, pipeline_stack_name,
    source_connection_arn=GITHUB_CONNECTION_ARN,
    source_repo_owner=GITHUB_REPO_OWNER,
    source_repo_name=GITHUB_REPO_NAME,
    source_branch_name=GITHUB_BRANCH,
    cdk_infra_stack_name=main_stack_name,
    codedeploy_application_name=CODEPLOY_APPLICATION_NAME,       # New parameter
    codedeploy_deployment_group_name=CODEPLOY_DEPLOYMENT_GROUP_NAME, # New parameter
    env=env,
    description=pipeline_description
)
logger.info(f"Pipeline stack '{pipeline_stack_name}' defined.")
AppDeploymentPipelineStack(app, app_pipeline_stack_name,
    github_connection_arn=GITHUB_CONNECTION_ARN,
    github_repo_owner=GITHUB_REPO_OWNER,
    github_repo_name=GITHUB_REPO_NAME,
    github_branch=GITHUB_BRANCH,
    codedeploy_application_name=CODEPLOY_APPLICATION_NAME,
    codedeploy_deployment_group_name=CODEPLOY_DEPLOYMENT_GROUP_NAME,
    env=env,
    description=app_pipeline_description
)
logger.info(f"App deployment pipeline stack '{app_pipeline_stack_name}' defined.")


try:
    app.synth()
    logger.info("CDK synthesis successful. CloudFormation templates are in 'cdk.out/'.")
except Exception as e:
    logger.error(f"CDK synthesis failed: {e}", exc_info=True)
    raise
