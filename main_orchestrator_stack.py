# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, aws_ec2 as ec2
)
from constructs import Construct
from deployment_config import get_deployment_configurations
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack # Assuming this exists
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack # Assuming this exists
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack # <<< ADDED IMPORT

import logging
logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        current_project_version = "5.4.0" # Increment version
        Tags.of(self).add("Project", "MultiResourcePlatform") # Updated project name
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        config = get_deployment_configurations()

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct = None
        created_vpcs_map: dict[str, ec2.IVpc] = {}
        # created_sgs_map: dict[str, ec2.ISecurityGroup] = {} # <<< If VpcStack will output SGs

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
                # if hasattr(vpc_group_stack_construct, 'created_sgs_map'): # Example if VPC stack exposes SGs
                #     created_sgs_map = vpc_group_stack_construct.created_sgs_map
            else:
                logger.warning("MainOrchestrator: VPC group enabled, but no VPC definitions found in 'instances' list.")
        else:
            logger.info("MainOrchestrator: VPC instance deployment group is disabled.")

        # --- VPC Peering Deployments ---
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info(f"MainOrchestrator: VPC Peering deployment group is enabled.")
                # Assuming VpcPeeringsGroupNestedStack constructor takes vpc_peerings_config
                # and potentially created_vpcs_map if it needs to reference VPCs created in this app.
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup",
                    vpc_peerings_config=peering_config_group
                    # created_vpcs_map=created_vpcs_map # Pass if needed by your peering stack
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
                    created_vpcs_map=created_vpcs_map
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
                    self, "S3BucketsGroup", s3_deployments_config=s3_config_group
                )
            else:
                logger.warning(f"MainOrchestrator: S3 group enabled, but no buckets defined.")
        else:
            logger.info("MainOrchestrator: S3 Bucket deployment group is disabled.")


        # --- EC2 Instance Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestrator: EC2 Instance deployment group is enabled.")
                Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group
                    # No longer passing created_vpcs_map here
                )
            else:
                logger.warning("MainOrchestrator: EC2 group enabled, but no EC2 instance definitions found.")
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")
