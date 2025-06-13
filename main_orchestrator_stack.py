# cdk_project/main_orchestrator_stack.py
import logging
import typing

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

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s: %(message)s')


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

        self.created_vpcs_map: typing.Dict[str, ec2.IVpc] = {}
        self.created_vpc_stacks_map: typing.Dict[str, VpcDeploymentsGroupNestedStack] = {}
        self.created_ec2_instances_map: typing.Dict[str, ec2.Instance] = {}
        self.created_asgs_map: typing.Dict[str, autoscaling.AutoScalingGroup] = {}
        self.created_iam_roles_map: typing.Dict[str, iam.IRole] = {}

        iam_roles_group_stack_construct: typing.Optional[IamRolesGroupNestedStack] = None
        vpc_group_stack_construct: typing.Optional[VpcDeploymentsGroupNestedStack] = None
        ec2_deployments_group_stack_construct: typing.Optional[Ec2DeploymentsGroupNestedStack] = None
        application_pipelines_group_stack_construct: typing.Optional[ApplicationPipelinesGroupNestedStack] = None

        # --- IAM Roles Deployments ---
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
                    self.created_iam_roles_map.update(iam_roles_group_stack_construct.created_roles_map)
                    logger.info(f"MainOrchestrator: Populated created_iam_roles_map with {len(self.created_iam_roles_map)} IAM Role objects.")

        # --- VPC Deployments ---
        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            if vpc_deployments_section.get("instances"):
                logger.info("MainOrchestrator: VPC instance deployment group is enabled.")
                vpc_group_stack_construct = VpcDeploymentsGroupNestedStack(
                    self, "VpcDeploymentsGroup",
                    vpc_deployments_section_config=vpc_deployments_section,
                    created_iam_roles_map=self.created_iam_roles_map,
                    description="Nested Stack for all configured VPC instances."
                )
                if hasattr(vpc_group_stack_construct, 'created_vpcs_map'):
                    self.created_vpcs_map.update(vpc_group_stack_construct.created_vpcs_map)
                if hasattr(vpc_group_stack_construct, 'created_vpc_stacks_map'):
                    self.created_vpc_stacks_map.update(vpc_group_stack_construct.created_vpc_stacks_map)

        # --- VPC Peering Deployments ---
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info("MainOrchestrator: VPC Peering deployment group is enabled.")
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup",
                    vpc_peerings_config=peering_config_group,
                    created_vpcs_map=self.created_vpcs_map,
                    description="Nested Stack for all configured VPC Peerings."
                )

        # --- S3 Deployments ---
        s3_config_group = config.get("s3_deployments", {})
        if s3_config_group.get("deploy", False):
            if s3_config_group.get("buckets"):
                logger.info("MainOrchestrator: S3 Bucket deployment group is enabled.")
                S3DeploymentsGroupNestedStack(
                    self, "S3BucketsGroup",
                    s3_deployments_config=s3_config_group,
                    description="Nested Stack for all configured S3 Buckets."
                )

        # --- EC2 Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances") or ec2_config_group.get("launch_templates"):
                logger.info("MainOrchestrator: EC2 instance deployment group is enabled.")
                ec2_deployments_group_stack_construct = Ec2DeploymentsGroupNestedStack(
                    self, "Ec2DeploymentsGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=self.created_vpcs_map,
                    created_iam_roles_map=self.created_iam_roles_map,
                    created_ec2_instances_map=self.created_ec2_instances_map,
                    created_asgs_map=self.created_asgs_map,
                    description="Nested Stack for all configured EC2-related resources."
                )
                if hasattr(ec2_deployments_group_stack_construct, 'deployed_instance_stacks'):
                    self.created_ec2_instances_map.update(ec2_deployments_group_stack_construct.deployed_instance_stacks)
                if hasattr(ec2_deployments_group_stack_construct, 'deployed_asg_stacks'):
                    self.created_asgs_map.update(ec2_deployments_group_stack_construct.deployed_asg_stacks)

        # --- Pipeline Deployments ---
        pipeline_config_group = config.get("pipeline_deployments", {})
        if pipeline_config_group.get("deploy", False):
            if pipeline_config_group.get("pipelines"):
                logger.info("MainOrchestrator: Application pipeline deployment group is enabled.")
                application_pipelines_group_stack_construct = ApplicationPipelinesGroupNestedStack(
                    self,
                    "ApplicationPipelinesGroup",
                    pipeline_deployments_config=pipeline_config_group.get("pipelines", []),
                    created_vpcs_map=self.created_vpcs_map,
                    created_ec2_instances_map=self.created_ec2_instances_map,
                    created_asgs_map=self.created_asgs_map,
                    created_iam_roles_map=self.created_iam_roles_map,
                    description="Nested Stack for all configured application deployment pipelines."
                )
                if iam_roles_group_stack_construct:
                    application_pipelines_group_stack_construct.add_dependency(iam_roles_group_stack_construct)
                if ec2_deployments_group_stack_construct:
                    application_pipelines_group_stack_construct.add_dependency(ec2_deployments_group_stack_construct)

        # --- RDS Deployments --- (Moved to the end)
        rds_config_group = config.get("rds_deployments", {})
        if rds_config_group.get("deploy", False):
            if rds_config_group.get("instances"):
                logger.info("MainOrchestrator: RDS deployment group is enabled.")
                RdsDeploymentsGroupNestedStack(
                    self, "RdsDeploymentsGroup",
                    rds_deployments_config=rds_config_group,
                    created_vpcs_map=self.created_vpcs_map,
                    created_iam_roles_map=self.created_iam_roles_map,
                    created_vpc_stacks_map=self.created_vpc_stacks_map,
                    description="Nested Stack for RDS Deployment Group."
                )

        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")
