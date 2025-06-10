# cdk_project/vpc/vpc_deployments_group_nested_stack.py
import logging
from aws_cdk import (
    NestedStack,
    Tags,
    aws_ec2 as ec2 # For type hinting IVpc
)
from constructs import Construct
from .vpc_instance_nested_stack import VpcInstanceNestedStack # Ensure this path is correct

logger = logging.getLogger(__name__)

class VpcDeploymentsGroupNestedStack(NestedStack):
    created_vpcs_map: dict[str, ec2.IVpc] # To store created/looked-up IVpc objects

    def __init__(self, scope: Construct, id: str, 
                 vpc_deployments_section_config: dict, # Renamed for clarity, expects the content of config["vpcs"]
                 **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        self.created_vpcs_map = {} 

        group_description = vpc_deployments_section_config.get("description", "VPC Deployments Group")
        logger.info(f"VpcDeploymentsGroup: Initializing for: {group_description}")
        Tags.of(self).add("CDKResourceGroup", "VPCs")

        # The list of VPCs is directly under the "instances" key
        vpc_definitions_list = vpc_deployments_section_config.get("instances", [])

        if not vpc_definitions_list:
            logger.warning("VpcDeploymentsGroup: No VPC definitions found in 'vpc_deployments_section_config.instances'.")
            return

        for vpc_definition_block in vpc_definitions_list:
            if not isinstance(vpc_definition_block, dict):
                logger.error(f"Invalid VPC definition block: {vpc_definition_block}. Skipping.")
                continue

            # --- CRITICAL FIX 1: Use 'enabled' flag from config ---
            deploy_this_vpc = vpc_definition_block.get("enabled", False) # <--- CHANGED THIS LINE
            # --- END CRITICAL FIX 1 ---

            vpc_config_id = vpc_definition_block.get("id") 

            if not vpc_config_id:
                logger.error(f"VpcDeploymentsGroup: Skipping VPC definition due to missing 'id': {vpc_definition_block}")
                continue

            if deploy_this_vpc:
                # Construct a unique CDK ID for the VpcInstanceNestedStack
                nested_stack_cdk_id = f"{vpc_config_id}InstanceNestedStack"
                # Access 'name' from the nested 'config' dict
                vpc_name_for_desc = vpc_definition_block.get("config",{}).get('vpc_core_config',{}).get('name', vpc_config_id)
                
                logger.info(f"VpcDeploymentsGroup: Defining VpcInstanceNestedStack for VPC '{vpc_config_id}' (Name: {vpc_name_for_desc}) with CDK ID '{nested_stack_cdk_id}'.")
                
                try:
                    instance_stack = VpcInstanceNestedStack(
                        self,
                        nested_stack_cdk_id,
                        # Pass the entire configuration block for this specific VPC
                        vpc_specific_config=vpc_definition_block, 
                        description=f"Nested Stack for VPC: {vpc_name_for_desc} (Config ID: {vpc_config_id})"
                    )
                    Tags.of(instance_stack).add("VPCConfigID", vpc_config_id)

                    # --- CRITICAL FIX 2: Use 'public_vpc' attribute ---
                    if hasattr(instance_stack, 'public_vpc') and instance_stack.public_vpc:
                        self.created_vpcs_map[vpc_config_id] = instance_stack.public_vpc
                        logger.info(f"VpcDeploymentsGroup: Stored IVpc for '{vpc_config_id}'.")
                    else:
                        logger.error(f"VpcInstanceNestedStack for '{vpc_config_id}' did not expose a 'public_vpc' attribute.")
                    # --- END CRITICAL FIX 2 ---

                except Exception as e:
                    logger.error(f"VpcDeploymentsGroup: Failed to instantiate VpcInstanceNestedStack for '{vpc_config_id}': {e}", exc_info=True)
            else:
                # Updated log message for clarity, reflects the 'enabled' flag
                logger.info(f"VpcDeploymentsGroup: Skipping VPC definition '{vpc_config_id}' (enabled: false).")