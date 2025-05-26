# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, aws_ec2 as ec2, # aws_ec2 might not be needed directly here
    Environment # Added for type hinting env
)
from constructs import Construct
from deployment_config import get_deployment_configurations # Assuming this is where full_config comes from in app.py
# Assuming your nested stack for EC2 deployments is in the 'ec2' subdirectory
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack # <<< CORRECTED IMPORT
# Add imports for other group nested stacks (VPC, S3, RDS) if you create and use them
# from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
# from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack
# from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
# from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack

import logging
import typing # Added for type hinting

logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    def __init__(self, scope: Construct, id: str, 
                 config: dict, # Explicitly accept the config dictionary
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None: # Use a different name for other CDK Stack kwargs
        
        # Pass only the recognized keyword arguments to the base Stack class
        super().__init__(scope, id, 
                         description=description,
                         env=env,
                         # Add other standard Stack props if you use them from additional_kwargs
                         # (e.g., stack_name, tags, termination_protection)
                         **additional_kwargs) 

        logger.info(f"MainOrchestrator '{id}': Initializing for environment: {self.region} in account {self.account}")
        
        current_project_version = "5.4.0" 
        Tags.of(self).add("Project", "MultiResourcePlatform") 
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        # 'config' is now directly available as a parameter passed during instantiation
        # No need to call get_deployment_configurations() here again if it's passed from app.py

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct = None
        created_vpcs_map: dict[str, ec2.IVpc] = {} 
        
        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            # Assuming VpcDeploymentsGroupNestedStack is defined and imported
            # For now, this part is commented out as per your original file structure if it's disabled.
            # if vpc_deployments_section.get("instances"):
            #     logger.info("MainOrchestrator: VPC instance deployment group is enabled.")
            #     vpc_group_stack_construct = VpcDeploymentsGroupNestedStack(
            #         self,
            #         "VpcDeploymentsGroup",
            #         vpc_deployments_section_config=vpc_deployments_section,
            #         description="Nested Stack for all configured VPC instances."
            #     )
            #     if hasattr(vpc_group_stack_construct, 'created_vpcs_map'):
            #         created_vpcs_map = vpc_group_stack_construct.created_vpcs_map
            # else:
            #     logger.warning("MainOrchestrator: VPC group enabled, but no VPC definitions found in 'instances' list.")
            pass # Placeholder if VPC stack is not being used yet
        else:
            logger.info("MainOrchestrator: VPC instance deployment group is disabled.")

        # --- VPC Peering Deployments ---
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            # if peering_config_group.get("connections"):
            #     logger.info(f"MainOrchestrator: VPC Peering deployment group is enabled.")
            #     VpcPeeringsGroupNestedStack(
            #         self, "VpcPeeringsGroup",
            #         vpc_peerings_config=peering_config_group
            #     )
            # else:
            #     logger.warning("MainOrchestrator: Peering group enabled, but no connections defined.")
            pass
        else:
            logger.info("MainOrchestrator: VPC Peering deployment group is disabled.")

        # --- RDS Deployments ---
        rds_config_group = config.get("rds_deployments", {})
        if rds_config_group.get("deploy", False):
            # if rds_config_group.get("instances"):
            #     logger.info(f"MainOrchestrator: RDS deployment group is enabled.")
            #     RdsDeploymentsGroupNestedStack(
            #         self, "RdsDeploymentsGroup",
            #         rds_deployments_config=rds_config_group,
            #         created_vpcs_map=created_vpcs_map
            #     )
            # else:
            #     logger.warning("MainOrchestrator: RDS group enabled, but no instances defined.")
            pass
        else:
            logger.info("MainOrchestrator: RDS deployment group is disabled.")


        # --- S3 Bucket Deployments ---
        s3_config_group = config.get("s3_deployments", {})
        if s3_config_group.get("deploy", False):
            # if s3_config_group.get("buckets"):
            #     logger.info(f"MainOrchestrator: S3 Bucket deployment group is enabled.")
            #     S3DeploymentsGroupNestedStack(
            #         self, "S3BucketsGroup", s3_deployments_config=s3_config_group
            #     )
            # else:
            #     logger.warning(f"MainOrchestrator: S3 group enabled, but no buckets defined.")
            pass
        else:
            logger.info("MainOrchestrator: S3 Bucket deployment group is disabled.")


        # --- EC2 Instance Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            # The check for "instances" or other specific keys within ec2_config_group
            # will be handled by Ec2DeploymentsGroupNestedStack itself.
            logger.info("MainOrchestrator: EC2 Instance deployment group is enabled.")
            Ec2DeploymentsGroupNestedStack(
                self, "Ec2InstancesGroup", # This is the id for the NestedStack construct
                ec2_deployments_config=ec2_config_group
                # description can be set here if desired for this specific nested stack
            )
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")
