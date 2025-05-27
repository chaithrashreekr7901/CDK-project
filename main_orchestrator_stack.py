# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, Environment, # Removed CfnParameter
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
    aws_autoscaling as autoscaling
)
from constructs import Construct

# Assuming your nested stacks are in their respective subdirectories
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack

import logging
import typing

logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    # Make CodeDeploy application and group names available if needed by other stacks or for output
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

        # --- CloudFormation Parameters for S3 Application Bundle Location ARE REMOVED ---
        # logger.info("S3 bundle parameters are no longer defined in this stack for CodeDeploy trigger.")

        # (Your VPC, Peering, RDS, S3 deployment logic remains the same as before)
        # ... (omitted for brevity, assume it's the same as your last version) ...
        vpc_group_stack_construct_instance = None
        created_vpcs_map: dict[str, ec2.IVpc] = {}
        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            if vpc_deployments_section.get("instances"):
                logger.info("MainOrchestratorStack: VPC instance deployment group is enabled.")
                vpc_group_stack_construct_instance = VpcDeploymentsGroupNestedStack(
                    self, "VpcDeploymentsGroup", vpc_deployments_section_config=vpc_deployments_section
                )
                if hasattr(vpc_group_stack_construct_instance, 'created_vpcs_map'):
                    created_vpcs_map = vpc_group_stack_construct_instance.created_vpcs_map
        # ... (other non-EC2 nested stack instantiations)


        # --- EC2 Instance Deployments (Target for CodeDeploy) ---
        ec2_config_group = config.get("ec2_deployments", {})
        ec2_group_stack_instance = None
        target_asg_for_codedeploy: typing.Optional[autoscaling.IAutoScalingGroup] = None

        if ec2_config_group.get("deploy", True): # Default to True or get from config
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestratorStack: EC2 Instance deployment group is enabled.")
                ec2_group_stack_instance = Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=created_vpcs_map,
                    description="Nested Stack for EC2 instances and ASGs."
                )
                # --- !!! IMPORTANT: ADAPT THIS LOGIC to get your ASG !!! ---
                if hasattr(ec2_group_stack_instance, 'primary_auto_scaling_group'): # Example
                    target_asg_for_codedeploy = ec2_group_stack_instance.primary_auto_scaling_group
                    if target_asg_for_codedeploy:
                         logger.info(f"Target ASG for CodeDeploy identified: {target_asg_for_codedeploy.auto_scaling_group_name}")
                # ... (other options for getting the ASG as discussed before) ...
                else:
                    logger.warning("Could not determine target ASG from EC2 stack for CodeDeploy.")
            else:
                logger.warning("MainOrchestratorStack: EC2 group enabled, but no instance configurations.")
        else:
            logger.info("MainOrchestratorStack: EC2 Instance deployment group is disabled.")

        # --- CodeDeploy Application and Deployment Group Setup ---
        # (This setup is still needed, but no CfnDeployment trigger resource)
        if target_asg_for_codedeploy:
            logger.info(f"Setting up CodeDeploy Application and DeploymentGroup targeting ASG: {target_asg_for_codedeploy.auto_scaling_group_name}")

            codedeploy_service_role = iam.Role(
                self, "CodeDeployServiceRoleForEC2",
                assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSCodeDeployRole")
                ]
            )

            # Use names consistent with what app.py might expect or make them configurable
            cd_application_name = f"{self.stack_name}-EC2App" # Matches example in app.py
            self.public_codedeploy_application = codedeploy.ServerApplication(self, "MyEC2CodeDeployApplication",
                application_name=cd_application_name,
                compute_platform=codedeploy.ComputePlatform.SERVER
            )
            logger.info(f"CodeDeploy Application created: {self.public_codedeploy_application.application_name}")

            cd_deployment_group_name = f"{self.stack_name}-EC2-DG" # Matches example in app.py
            self.public_codedeploy_deployment_group = codedeploy.ServerDeploymentGroup(self, "MyEC2CodeDeployDeploymentGroup",
                application=self.public_codedeploy_application,
                deployment_group_name=cd_deployment_group_name,
                auto_scaling_groups=[target_asg_for_codedeploy],
                install_agent=True,
                deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
                service_role=codedeploy_service_role,
            )
            logger.info(f"CodeDeploy Deployment Group created: {self.public_codedeploy_deployment_group.deployment_group_name}")

            # --- The CfnDeployment resource (trigger) is REMOVED from this stack ---
            logger.info("AWS::CodeDeploy::Deployment resource for CFN trigger is NOT defined in this stack. Pipeline will use direct CodeDeploy action.")
        else:
            logger.warning("CodeDeploy Application and DeploymentGroup setup skipped as no target ASG was identified.")

        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")

