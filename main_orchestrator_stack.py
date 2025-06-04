# cdk_project/main_orchestrator_stack.py

from aws_cdk import (
    Stack, Tags, Environment,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
    aws_autoscaling as autoscaling,
    aws_codebuild as codebuild
)
from constructs import Construct

# Import all group nested stacks
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack
from pipeline.application_pipelines_group_nested_stack import ApplicationPipelinesGroupNestedStack
from iam.iam_roles_group_nested_stack import IamRolesGroupNestedStack

import logging
import typing

logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    public_codedeploy_application: typing.Optional[codedeploy.IServerApplication] = None
    public_codedeploy_deployment_group: typing.Optional[codedeploy.IServerDeploymentGroup] = None

    def __init__(self, scope: Construct, id: str,
                 config: dict,
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None:

        super().__init__(scope, id, description=description, env=env, **additional_kwargs)

        logger.info(f"MainOrchestratorStack '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = config.get("project_version", "8.0.0-direct-codedeploy")
        Tags.of(self).add("Project", config.get("project_name", "MultiResourcePlatform"))
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        # Maps to store references to created resources (for passing to dependent stacks)
        created_vpcs_map: typing.Dict[str, ec2.IVpc] = {}
        created_ec2_instances_map: typing.Dict[str, ec2.Instance] = {}
        created_asgs_map: typing.Dict[str, autoscaling.AutoScalingGroup] = {}
        # Changed type hint to store iam.IRole objects directly
        created_iam_roles_map: typing.Dict[str, iam.IRole] = {}

        # Initialize stack constructs to None outside conditional blocks
        iam_roles_group_stack_construct: typing.Optional[IamRolesGroupNestedStack] = None
        vpc_group_stack_construct: typing.Optional[VpcDeploymentsGroupNestedStack] = None
        ec2_deployments_group_stack_construct: typing.Optional[Ec2DeploymentsGroupNestedStack] = None
        application_pipelines_group_stack_construct: typing.Optional[ApplicationPipelinesGroupNestedStack] = None


        # --- IAM Roles Deployments (Must come early if other stacks depend on them) ---
        iam_roles_config_group = config.get("iam_roles", {})
        if iam_roles_config_group.get("deploy", False):
            if iam_roles_config_group.get("roles"):
                logger.info("MainOrchestrator: IAM Roles deployment group is enabled.")
                iam_roles_group_stack_construct = IamRolesGroupNestedStack(
                    self, "IamRolesGroup",
                    iam_roles_config=iam_roles_config_group,
                    description="Nested Stack for all configured IAM Roles."
                )
                if hasattr(iam_roles_group_stack_construct, 'created_roles_map'):
                    # Populate created_iam_roles_map with iam.IRole objects directly
                    created_iam_roles_map = iam_roles_group_stack_construct.created_roles_map
                    logger.info(f"MainOrchestrator: Populated created_iam_roles_map with {len(created_iam_roles_map)} IAM Role objects.")
                else:
                    logger.warning("MainOrchestrator: IamRolesGroupNestedStack does not expose 'created_roles_map'.")
            else:
                logger.warning("MainOrchestrator: IAM Roles group enabled, but no role definitions found.")
        else:
            logger.info("MainOrchestrator: IAM Roles deployment group is disabled.")

        # --- VPC Deployments ---
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
                    logger.info(f"MainOrchestrator: Populated created_vpcs_map with {len(created_vpcs_map)} VPCs.")
                else:
                    logger.warning("MainOrchestrator: VpcDeploymentsGroupNestedStack does not expose 'created_vpcs_map'.")
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
                    created_vpcs_map=created_vpcs_map
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
                    created_vpcs_map=created_vpcs_map,
                    created_iam_roles_map=created_iam_roles_map # This map now contains IAM Role objects
                )
            else:
                logger.warning("MainOrchestrator: RDS group enabled, but no instances defined.")
        else:
            logger.info("MainOrchestrator: RDS deployment group is disabled.")

        # --- S3 Deployments ---
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

        # --- EC2 Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances") or \
               ec2_config_group.get("launch_templates") or \
               ec2_config_group.get("security_groups") or \
               ec2_config_group.get("application_load_balancers") or \
               ec2_config_group.get("network_load_balancers") or \
               ec2_config_group.get("target_groups") or \
               ec2_config_group.get("auto_scaling_groups"):

                logger.info("MainOrchestrator: EC2 instance deployment group is enabled.")
                ec2_deployments_group_stack_construct = Ec2DeploymentsGroupNestedStack(
                    self, "Ec2DeploymentsGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=created_vpcs_map,
                    created_ec2_instances_map=created_ec2_instances_map,
                    created_asgs_map=created_asgs_map,
                    created_iam_roles_map=created_iam_roles_map, # This map now contains IAM Role objects
                    description="Nested Stack for all configured EC2-related resources."
                )
                if hasattr(ec2_deployments_group_stack_construct, 'deployed_instance_stacks'):
                    created_ec2_instances_map = ec2_deployments_group_stack_construct.deployed_instance_stacks
                    logger.info(f"MainOrchestrator: Populated created_ec2_instances_map with {len(created_ec2_instances_map)} instances.")
                else:
                    logger.warning("MainOrchestrator: Ec2DeploymentsGroupNestedStack does not expose 'deployed_instance_stacks'.")
                if hasattr(ec2_deployments_group_stack_construct, 'deployed_asg_stacks'):
                    created_asgs_map = ec2_deployments_group_stack_construct.deployed_asg_stacks
                    logger.info(f"MainOrchestrator: Populated created_asgs_map with {len(created_asgs_map)} ASGs.")
                else:
                    logger.warning("MainOrchestrator: Ec2DeploymentsGroupNestedStack does not expose 'deployed_asg_stacks'.")
            else:
                logger.warning("MainOrchestrator: EC2 group enabled, but no EC2 instance, LT, SG, LB, TG, or ASG definitions found.")
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")

        # --- Pipeline Deployments (New Section) ---
        pipeline_config_group = config.get("pipeline_deployments", {})
        if pipeline_config_group.get("deploy", False):
            pipelines_list = pipeline_config_group.get("pipelines", [])
            if pipelines_list:
                logger.info("MainOrchestrator: Application pipeline deployment group is enabled.")
                application_pipelines_group_stack_construct = ApplicationPipelinesGroupNestedStack(
                    self,
                    "ApplicationPipelinesGroup",
                    pipeline_deployments_config=pipelines_list,
                    created_vpcs_map=created_vpcs_map,
                    created_ec2_instances_map=created_ec2_instances_map,
                    created_asgs_map=created_asgs_map,
                    created_iam_roles_map=created_iam_roles_map, # This map now contains IAM Role objects
                    description="Nested Stack for all configured application deployment pipelines."
                )
                # ADDED: Explicitly add dependency to break potential circularity
                if iam_roles_group_stack_construct: # Check if the IAM roles stack was actually created
                    application_pipelines_group_stack_construct.add_dependency(iam_roles_group_stack_construct)
                    logger.info("MainOrchestrator: Added explicit dependency: ApplicationPipelinesGroup depends on IamRolesGroup.")
            else:
                logger.warning("MainOrchestrator: Pipeline group enabled, but no pipeline definitions found in 'pipelines' list.")
        else:
            logger.info("MainOrchestrator: Application pipeline deployment group is disabled.")

        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")
