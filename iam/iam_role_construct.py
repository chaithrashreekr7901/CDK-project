# cdk_project/iam/iam_role_construct.py

import logging
import typing
import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

logger = logging.getLogger(__name__)

class IamRoleConstruct(Construct):
    """
    A reusable construct that creates a single AWS IAM Role based on provided configuration.
    """
    public_role: iam.IRole # Expose the created role

    def __init__(self, scope: Construct, id: str, *,
                 role_config: typing.Dict,
                 description: typing.Optional[str] = None) -> None:
        super().__init__(scope, id)

        role_name = role_config.get("role_name", id) # Use id as fallback for role_name
        logger.info(f"IamRoleConstruct '{id}': Creating IAM Role '{role_name}'.")

        # Convert assumed_by string to ServicePrincipal
        assumed_by_principal = iam.ServicePrincipal(role_config["assumed_by"])

        # Create Managed Policies from ARNs
        managed_policies = [
            iam.ManagedPolicy.from_managed_policy_arn(self, f"{id}ManagedPolicy{i}", arn)
            for i, arn in enumerate(role_config.get("managed_policies", []))
        ]

        # Process inline policies
        inline_policies_dict = {}
        for policy_def in role_config.get("inline_policies", []):
            policy_name = policy_def.get("name")
            policy_document = policy_def.get("document")
            if policy_name and policy_document:
                inline_policies_dict[policy_name] = iam.PolicyDocument.from_json(policy_document)
            else:
                logger.warning(f"IamRoleConstruct '{id}': Skipping malformed inline policy definition: {policy_def}")


        # Create the IAM Role
        self.public_role = iam.Role(self, "Role", # Use a generic ID like "Role" within the construct
            role_name=role_name,
            assumed_by=assumed_by_principal,
            managed_policies=managed_policies,
            inline_policies=inline_policies_dict # Add inline policies here
        )

        # Add tags to the role
        cdk.Tags.of(self.public_role).add("Construct", "IamRoleConstruct")
        cdk.Tags.of(self.public_role).add("RoleName", role_name)

        logger.info(f"IamRoleConstruct '{id}': IAM Role '{role_name}' created (ARN: {self.public_role.role_arn}).")
