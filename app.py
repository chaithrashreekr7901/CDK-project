#!/usr/bin/env python3
# app.py (located in the root of your CDK_PROJECT, alongside cdk.json)

import os
import logging
import aws_cdk as cdk

# Direct import as main_orchestrator_stack.py is in the same directory
from main_orchestrator_stack import MainOrchestratorStack
from deployment_config import get_deployment_configurations # Assuming this loads your app config

# Assuming your pipeline_stack.py is in a 'pipeline' directory
from pipeline.pipeline_stack import PipelineStack 

# Configure basic logging for the CDK app
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = cdk.App()

# --- Determine AWS Account and Region ---
# Using try_get_context first, then environment variables as fallback or primary
aws_account_from_context = app.node.try_get_context("aws_account")
aws_region_from_context = app.node.try_get_context("aws_region")

aws_account_from_env = os.environ.get("CDK_DEFAULT_ACCOUNT")
aws_region_from_env = os.environ.get("CDK_DEFAULT_REGION")

final_aws_account = aws_account_from_env if aws_account_from_env else aws_account_from_context
final_aws_region = aws_region_from_env if aws_region_from_env else aws_region_from_context

if aws_account_from_env:
    logger.info(f"Using AWS Account ID from environment variable CDK_DEFAULT_ACCOUNT: {aws_account_from_env}")
elif aws_account_from_context:
    logger.info(f"Using AWS Account ID from CDK context (cdk.json): {aws_account_from_context}")

if aws_region_from_env:
    logger.info(f"Using AWS Region from environment variable CDK_DEFAULT_REGION: {aws_region_from_env}")
elif aws_region_from_context:
    logger.info(f"Using AWS Region from CDK context (cdk.json): {aws_region_from_context}")


if not final_aws_account or not final_aws_region:
    logger.error(
        "CRITICAL: AWS Account ID and Region are NOT configured. Deployment will likely fail.\n"
        "Please ensure they are set using ONE of the following methods:\n"
        "  1. Environment Variables: Set CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION in your shell.\n"
        "  2. CDK Context: Define 'aws_account' and 'aws_region' in the 'context' section of your cdk.json file."
    )
    # It's often better to let the CDK CLI handle this error if env isn't fully resolved
    # For pipeline context, these are usually injected.
    # For local, 'cdk deploy --profile yourprofile' or env vars are common.
    # If you must proceed with placeholders for synth, ensure they are clearly marked.
    # However, for actual deployment, these must be valid.
    # For now, we'll proceed assuming they will be resolved by the execution environment.
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=os.environ.get("CDK_DEFAULT_REGION"))
    if not env.account or not env.region:
         raise ValueError("AWS Account and Region are not configured. Check cdk.json or environment variables. See logs for details.")
else:
    logger.info(f"CDK App will target AWS Account: {final_aws_account}, Region: {final_aws_region}")
    env = cdk.Environment(account=str(final_aws_account), region=str(final_aws_region))

# --- Configuration Loading for Main Application Stack ---
try:
    full_config = get_deployment_configurations()
    if not isinstance(full_config, dict):
        logger.error("get_deployment_configurations() did not return a dictionary. Exiting.")
        exit(1) 
except Exception as e:
    logger.error(f"Failed to load deployment configurations for main app: {e}", exc_info=True)
    exit(1) 


# --- Instantiate your Main Infrastructure Stack ---
main_app_stack_name = "MyMainInfrastructureStack" 
main_stack = MainOrchestratorStack(app, main_app_stack_name,
    description="Main orchestrator stack for deploying multiple VPCs and other resources via nested stacks. (v5)",
    config=full_config, 
    env=env
)

# --- Instantiate your CI/CD Pipeline Stack ---

# !!! IMPORTANT: Replace these placeholder values with your actual GITHUB details !!!
# 1. Create a CodeStar Connection to your GITHUB account in the AWS Console and get its ARN.
#    (Developer Tools > CodeStar Connections > Create connection)
GITHUB_CONNECTION_ARN = "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1"  # <<< REPLACE
# 2. Your GitHub username or organization name that owns the repo
GITHUB_REPO_OWNER = "chaithrashreekr7901"  # <<< YOUR GITHUB USERNAME/ORG
# 3. The name of your repository in GitHub
GITHUB_REPO_NAME = "CDK-project"  # <<< YOUR GITHUB REPO NAME
# 4. The branch to trigger the pipeline from
GITHUB_BRANCH = "CRMP-cdk" # Or "main" if that's your default branch in the GitHub repo

pipeline_stack_name = "MyCDKApplicationPipelineStack" 

PipelineStack(app, pipeline_stack_name,
    source_connection_arn=GITHUB_CONNECTION_ARN, # Pass the GitHub connection ARN
    source_repo_owner=GITHUB_REPO_OWNER,         # Pass the GitHub owner
    source_repo_name=GITHUB_REPO_NAME,           # Pass the GitHub repo name
    source_branch_name=GITHUB_BRANCH,            # Pass the GitHub branch name
    cdk_app_stack_name=main_app_stack_name, 
    env=env 
)

app.synth()
