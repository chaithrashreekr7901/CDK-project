from aws_cdk import (
    Stack, Tags, Environment,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
    aws_autoscaling as autoscaling
)
from constructs import Construct
from deployment_config import get_deployment_configurations

from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack

import logging
import typing

logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    public_codedeploy_application: typing.Optional[codedeploy.IServerApplication] = None
    public_codedeploy_deployment_group: typing.Optional[codedeploy.IServerDeploymentGroup] = None

    def __init__(self, scope: Construct, id: str,
                 config: dict,
                 target_asg_for_codedeploy: typing.Optional[autoscaling.AutoScalingGroup] = None, 
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None:

        super().__init__(scope, id, description=description, env=env, **additional_kwargs)

        logger.info(f"MainOrchestratorStack '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = config.get("project_version", "8.0.0-direct-codedeploy")
        Tags.of(self).add("Project", config.get("project_name", "MultiResourcePlatform"))
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)
        config = get_deployment_configurations()

        vpc_group_stack_construct = None
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

        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info(f"MainOrchestrator: VPC Peering deployment group is enabled.")
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup",
                    vpc_peerings_config=peering_config_group
                )
            else:
                logger.warning("MainOrchestrator: Peering group enabled, but no connections defined.")
        else:
            logger.info("MainOrchestrator: VPC Peering deployment group is disabled.")

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

        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestrator: EC2 Instance deployment group is enabled.")
                Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group
                )
            else:
                logger.warning("MainOrchestrator: EC2 group enabled, but no EC2 instance definitions found.")
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")



        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")
