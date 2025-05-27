# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, aws_ec2 as ec2,
    Environment
)
from constructs import Construct
from deployment_config import get_deployment_configurations # Assuming this is where full_config comes from in app.py

# Assuming your nested stacks are in their respective subdirectories
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack
# Ensure these imports are correct and the files exist:
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack

import logging
import typing

logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    def __init__(self, scope: Construct, id: str,
                 config: dict, # Explicitly accept the config dictionary
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None:

        super().__init__(scope, id,
                         description=description,
                         env=env,
                         **additional_kwargs)

        logger.info(f"MainOrchestrator '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = "5.4.0"
        Tags.of(self).add("Project", "MultiResourcePlatform")
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        # 'config' is now directly available as a parameter passed during instantiation

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct = None # To store the construct if needed later, e.g., for outputs
        created_vpcs_map: dict[str, ec2.IVpc] = {}

        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            if vpc_deployments_section.get("instances"):
                logger.info("MainOrchestrator: VPC instance deployment group is enabled.")
                vpc_group_stack_construct = VpcDeploymentsGroupNestedStack(
                    self,
                    "VpcDeploymentsGroup",
                    vpc_deployments_section_config=vpc_deployments_section,
                    description="Nested Stack for all configured VPC instances."
                )
                if hasattr(vpc_group_stack_construct, 'created_vpcs_map'):
                    created_vpcs_map = vpc_group_stack_construct.created_vpcs_map
            else:
                logger.warning("MainOrchestrator: VPC group enabled, but no VPC definitions found in 'instances' list.")
        else:
            logger.info("MainOrchestrator: VPC instance deployment group is disabled.")

        # --- VPC Peering Deployments ---
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info(f"MainOrchestrator: VPC Peering deployment group is enabled.")
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup",
                    vpc_peerings_config=peering_config_group,
                    # Pass created_vpcs_map if your peering stack needs it for lookups
                    # created_vpcs_map=created_vpcs_map # Example
                    description="Nested Stack for VPC Peering connections."
                )
            else:
                logger.warning("MainOrchestrator: Peering group enabled, but no connections defined.")
        else:
            logger.info("MainOrchestrator: VPC Peering deployment group is disabled.")

        # --- RDS Deployments ---
        rds_config_group = config.get("rds_deployments", {})
        if rds_config_group.get("deploy", False):
            if rds_config_group.get("instances"):
                logger.info(f"MainOrchestrator: RDS deployment group is enabled.")
                RdsDeploymentsGroupNestedStack(
                    self, "RdsDeploymentsGroup",
                    rds_deployments_config=rds_config_group,
                    created_vpcs_map=created_vpcs_map, # RDS instances will need VPC information
                    description="Nested Stack for RDS database instances."
                )
            else:
                logger.warning("MainOrchestrator: RDS group enabled, but no instances defined.")
        else:
            logger.info("MainOrchestrator: RDS deployment group is disabled.")


        # --- S3 Bucket Deployments ---
        s3_config_group = config.get("s3_deployments", {})
        if s3_config_group.get("deploy", False):
            if s3_config_group.get("buckets"):
                logger.info(f"MainOrchestrator: S3 Bucket deployment group is enabled.")
                S3DeploymentsGroupNestedStack(
                    self, "S3BucketsGroup",
                    s3_deployments_config=s3_config_group,
                    description="Nested Stack for S3 buckets."
                )
            else:
                logger.warning(f"MainOrchestrator: S3 group enabled, but no buckets defined.")
        else:
            logger.info("MainOrchestrator: S3 Bucket deployment group is disabled.")


        # --- EC2 Instance Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances"): # Added a check for instances similar to other services
                logger.info("MainOrchestrator: EC2 Instance deployment group is enabled.")
                Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=created_vpcs_map, # EC2 instances might need VPC information
                    description="Nested Stack for EC2 instances."
                )
            else:
                logger.warning("MainOrchestrator: EC2 group enabled, but no EC2 definitions found in 'instances' list.")
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")