# cdk_project/iam/iam_roles_group_nested_stack.py

import logging
import typing
import aws_cdk as cdk
from aws_cdk import NestedStack, aws_iam as iam
from constructs import Construct

# Import the new IamRoleConstruct
from .iam_role_construct import IamRoleConstruct

logger = logging.getLogger(__name__)

class IamRolesGroupNestedStack(NestedStack):
    """
    A nested stack that creates IAM Roles based on the configuration.
    It exposes a map of created roles for other stacks to reference.
    """
    created_roles_map: typing.Dict[str, iam.IRole]

    def __init__(self, scope: Construct, id: str, *,
                 iam_roles_config: typing.Dict,
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:
        # Filter kwargs to only pass what NestedStack's __init__ expects.
        # This prevents unexpected parameters from being created in the CloudFormation template
        # which can cause circular dependencies.
        nested_stack_valid_kwargs = {}
        for key, value in kwargs.items():
            if key in ['env', 'stack_name', 'synthesizer', 'termination_protection']: # Add other valid NestedStack kwargs if used
                nested_stack_valid_kwargs[key] = value

        super().__init__(scope, id, description=description, **nested_stack_valid_kwargs) # Apply the filter here

        logger.info(f"IamRolesGroupNestedStack '{id}': Initializing.")
        cdk.Tags.of(self).add("ResourceGroup", "IAMRoles")

        self.created_roles_map = {}
        roles_definitions = iam_roles_config.get("roles", [])

        if not roles_definitions:
            logger.warning("IamRolesGroupNestedStack: No IAM role definitions found.")
            return

        for role_def in roles_definitions:
            if role_def.get("enabled", False):
                role_id = role_def["id"]
                role_config = role_def["config"]

                try:
                    iam_role_construct = IamRoleConstruct(
                        self,
                        f"{role_id}Construct", # Unique ID for the construct within this stack
                        role_config=role_config,
                        description=f"IAM Role for {role_id}"
                    )
                    # Store the actual IAM Role object from the construct
                    self.created_roles_map[role_id] = iam_role_construct.public_role
                    # CfnOutput removed in a previous step to break implicit dependencies
                except Exception as e:
                    logger.error(f"IamRolesGroupNestedStack: FAILED to create IAM Role '{role_id}': {e}", exc_info=True)
            else:
                logger.info(f"IamRolesGroupNestedStack: Skipping disabled IAM Role: {role_def['id']}.")

        logger.info(f"IamRolesGroupNestedStack '{id}': Initialization complete.")
