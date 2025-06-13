# cdk_project/vpc/vpc_deployments_group_nested_stack.py
import logging
import typing
import aws_cdk as cdk
from constructs import Construct
from aws_cdk import NestedStack, aws_ec2 as ec2, Tags, aws_iam as iam

from .vpc_instance_nested_stack import VpcInstanceNestedStack # Ensure this path is correct

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s: %(message)s')

class VpcDeploymentsGroupNestedStack(NestedStack):
    created_vpcs_map: typing.Dict[str, ec2.IVpc] # To store created/looked-up IVpc objects
    created_vpc_stacks_map: typing.Dict[str, VpcInstanceNestedStack] # Map logical ID to the actual VpcInstanceNestedStack object

    def __init__(self, scope: Construct, id: str, *,
                 vpc_deployments_section_config: typing.Dict, # Renamed for clarity, expects the content of config["vpcs"]
                 created_iam_roles_map: typing.Dict[str, iam.IRole], # Passed here from MainOrchestratorStack
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:

        nested_stack_valid_kwargs = {k: v for k, v in kwargs.items() if k in ['env', 'stack_name', 'synthesizer', 'termination_protection', 'description']}
        super().__init__(scope, id, description=description, **nested_stack_valid_kwargs)

        logger.info(f"VpcDeploymentsGroup: Initializing for: {vpc_deployments_section_config.get('description', 'VPC deployments')}")
        cdk.Tags.of(self).add("CDKResourceGroup", "VPCs")

        self.vpc_deployments_config = vpc_deployments_section_config
        self.created_iam_roles_map = created_iam_roles_map # Store it to pass to child VpcInstanceNestedStack
        self.created_vpcs_map = {} # Initialize empty maps
        self.created_vpc_stacks_map = {}

        vpc_definitions_list = self.vpc_deployments_config.get("instances", [])

        if not vpc_definitions_list:
            logger.warning("VpcDeploymentsGroup: No VPC definitions found in 'vpc_deployments_section_config.instances'.")
            return

        for vpc_definition_block in vpc_definitions_list:
            if not isinstance(vpc_definition_block, dict):
                logger.error(f"Invalid VPC definition block: {vpc_definition_block}. Skipping.")
                continue

            deploy_this_vpc = vpc_definition_block.get("enabled", False)
            vpc_config_id = vpc_definition_block.get("id")

            if not vpc_config_id:
                logger.error(f"VpcDeploymentsGroup: Skipping VPC definition due to missing 'id': {vpc_definition_block}")
                continue

            if deploy_this_vpc:
                nested_stack_cdk_id = f"{vpc_config_id}InstanceNestedStack"
                vpc_name_for_desc = vpc_definition_block.get("config",{}).get('name', vpc_config_id)

                logger.info(f"VpcDeploymentsGroup: Defining VpcInstanceNestedStack for VPC '{vpc_config_id}' (Name: {vpc_name_for_desc}) with CDK ID '{nested_stack_cdk_id}'.")
                
                try:
                    instance_stack = VpcInstanceNestedStack(
                        self,
                        nested_stack_cdk_id,
                        vpc_specific_config=vpc_definition_block,
                        created_iam_roles_map=self.created_iam_roles_map, # Pass IAM roles to VpcInstanceNestedStack
                        description=f"Nested Stack for VPC: {vpc_name_for_desc} (Config ID: {vpc_config_id})"
                    )
                    
                    if instance_stack.public_vpc:
                        self.created_vpcs_map[vpc_config_id] = instance_stack.public_vpc
                        self.created_vpc_stacks_map[vpc_config_id] = instance_stack # Store the stack object itself
                        logger.info(f"VpcDeploymentsGroup: Stored IVpc for '{vpc_config_id}'.")
                    else:
                        logger.error(f"VpcDeploymentsGroup: VpcInstanceNestedStack for '{vpc_config_id}' did not expose a 'public_vpc' attribute.")

                    Tags.of(instance_stack).add("VPCConfigID", vpc_config_id)
                    Tags.of(instance_stack).add("VPCName", vpc_name_for_desc)

                except Exception as e:
                    logger.error(f"VpcDeploymentsGroup: Failed to instantiate VpcInstanceNestedStack for '{vpc_config_id}': {e}", exc_info=True)
                    # Clean up maps if creation failed
                    if vpc_config_id in self.created_vpcs_map:
                        del self.created_vpcs_map[vpc_config_id]
                    if vpc_config_id in self.created_vpc_stacks_map:
                        del self.created_vpc_stacks_map[vpc_config_id]
            else:
                logger.info(f"VpcDeploymentsGroup: Skipping VPC definition '{vpc_config_id}' (enabled: false).")

        logger.info("VpcDeploymentsGroup: Initialization complete.")