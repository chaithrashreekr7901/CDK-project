# CRMP-Project/cdk_project/peering/vpc_peerings_group_nested_stack.py
import logging
from aws_cdk import (
    NestedStack,
    Tags
)
from constructs import Construct
# Ensure this relative import points to the correct location of VpcPeeringInstanceStack
# This assumes vpc_peering_instance_stack.py is in the same 'peering' directory.
from .vpc_peering_instance_stack import VpcPeeringInstanceStack 

logger = logging.getLogger(__name__)

class VpcPeeringsGroupNestedStack(NestedStack): # Ensure this class name is exact
    def __init__(self, scope: Construct, id: str, vpc_peerings_config: dict, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        group_description = vpc_peerings_config.get("description", "VPC Peerings Group")
        logger.info(f"VpcPeeringsGroupNestedStack: Initializing for: {group_description}")
        Tags.of(self).add("CDKResourceGroup", "VPCPeerings")

        self.peering_instance_outputs = {} # To store outputs like peering connection IDs
        peering_connections_to_deploy = vpc_peerings_config.get("connections", [])

        if not peering_connections_to_deploy:
            logger.warning("VpcPeeringsGroupNestedStack: No VPC peering connections found in 'vpc_peerings_config.connections'.")
            return

        for peering_cfg_block in peering_connections_to_deploy:
            if peering_cfg_block.get("enabled", False):
                peering_config_id = peering_cfg_block.get("id")
                if not peering_config_id:
                    logger.error(f"VpcPeeringsGroupNestedStack: Skipping VPC peering instance due to missing 'id' in config block: {peering_cfg_block.get('peering_connection_name', 'UnnamedPeering')}")
                    continue
                
                # Sanitize the peering_config_id for use as a CDK construct ID
                sanitized_peering_config_id = ''.join(filter(str.isalnum, peering_config_id))
                if not sanitized_peering_config_id:
                    # Create a fallback unique ID if the original ID was all non-alphanumeric
                    sanitized_peering_config_id = f"Peering{len(self.peering_instance_outputs)}"


                nested_stack_cdk_id = f"{sanitized_peering_config_id}PeeringInstanceStack" # Changed suffix for consistency
                peering_name_for_desc = peering_cfg_block.get("peering_connection_name", peering_config_id)
                
                logger.info(f"VpcPeeringsGroupNestedStack: Defining VpcPeeringInstanceStack for peering config ID '{peering_config_id}' with CDK construct ID '{nested_stack_cdk_id}'.")
                
                try:
                    instance_stack = VpcPeeringInstanceStack(
                        self,
                        nested_stack_cdk_id,
                        peering_config=peering_cfg_block,
                        description=f"Nested Stack for VPC Peering: {peering_name_for_desc} (Config ID: {peering_config_id})"
                    )
                    # Capture output if the VpcPeeringInstanceStack defines it
                    if hasattr(instance_stack, 'peering_connection_id_output') and instance_stack.peering_connection_id_output:
                        self.peering_instance_outputs[peering_config_id] = {
                           "PeeringConnectionID": instance_stack.peering_connection_id_output.value
                        }
                    Tags.of(instance_stack).add("VPCPeeringConfigID", peering_config_id)
                except Exception as e:
                    logger.error(f"VpcPeeringsGroupNestedStack: Failed to instantiate VpcPeeringInstanceStack for config ID '{peering_config_id}': {e}", exc_info=True) # Added exc_info for more details
                    # Depending on criticality, you might want to re-raise the exception
                    # raise
            else:
                skipped_peering_id = peering_cfg_block.get('id', 'UnknownID_DeployFalse')
                logger.info(f"VpcPeeringsGroupNestedStack: Skipping VPC peering with config ID '{skipped_peering_id}' (enabled: false).")
