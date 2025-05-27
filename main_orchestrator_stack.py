# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, Environment, CfnParameter,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
    aws_autoscaling as autoscaling # Assuming your EC2 stack creates an ASG
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
    def __init__(self, scope: Construct, id: str,
                 config: dict,
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None:

        super().__init__(scope, id, description=description, env=env, **additional_kwargs)

        logger.info(f"MainOrchestratorStack '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = config.get("project_version", "7.0.0-codedeploy-via-cfn")
        Tags.of(self).add("Project", config.get("project_name", "MultiResourcePlatform"))
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        # --- CloudFormation Parameters for S3 Application Bundle Location ---
        app_bundle_s3_bucket_param = CfnParameter(self, "AppBundleS3Bucket",
            type="String",
            description="S3 bucket for the CodeDeploy application bundle."
        )
        app_bundle_s3_key_param = CfnParameter(self, "AppBundleS3Key",
            type="String",
            description="S3 key for the CodeDeploy application bundle."
        )
        logger.info("Defined CfnParameters for AppBundleS3Bucket and AppBundleS3Key.")

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct_instance = None # Renamed for clarity
        created_vpcs_map: dict[str, ec2.IVpc] = {}
        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            if vpc_deployments_section.get("instances"):
                logger.info("MainOrchestratorStack: VPC instance deployment group is enabled.")
                vpc_group_stack_construct_instance = VpcDeploymentsGroupNestedStack(
                    self, "VpcDeploymentsGroup",
                    vpc_deployments_section_config=vpc_deployments_section,
                    description="Nested Stack for all configured VPC instances."
                )
                if hasattr(vpc_group_stack_construct_instance, 'created_vpcs_map'):
                    created_vpcs_map = vpc_group_stack_construct_instance.created_vpcs_map
            else:
                logger.warning("MainOrchestratorStack: VPC group enabled, but no VPC definitions found.")
        else:
            logger.info("MainOrchestratorStack: VPC instance deployment group is disabled.")

        # --- Other Nested Stack Deployments (Peering, RDS, S3) ---
        # (Your existing logic for these - assumed no direct changes for this CodeDeploy setup)
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False) and peering_config_group.get("connections"):
            logger.info("MainOrchestratorStack: VPC Peering deployment group is enabled.")
            VpcPeeringsGroupNestedStack(self, "VpcPeeringsGroup", vpc_peerings_config=peering_config_group)
        else:
            logger.info("MainOrchestratorStack: VPC Peering deployment group is disabled or no connections.")

        rds_config_group = config.get("rds_deployments", {})
        if rds_config_group.get("deploy", False) and rds_config_group.get("instances"):
            logger.info("MainOrchestratorStack: RDS deployment group is enabled.")
            RdsDeploymentsGroupNestedStack(self, "RdsDeploymentsGroup", rds_deployments_config=rds_config_group, created_vpcs_map=created_vpcs_map)
        else:
            logger.info("MainOrchestratorStack: RDS deployment group is disabled or no instances.")

        s3_config_group = config.get("s3_deployments", {})
        if s3_config_group.get("deploy", False) and s3_config_group.get("buckets"):
            logger.info("MainOrchestratorStack: S3 Bucket deployment group is enabled.")
            S3DeploymentsGroupNestedStack(self, "S3BucketsGroup", s3_deployments_config=s3_config_group)
        else:
            logger.info("MainOrchestratorStack: S3 Bucket deployment group is disabled or no buckets.")

        # --- EC2 Instance Deployments (This is where compute for CodeDeploy is expected) ---
        ec2_config_group = config.get("ec2_deployments", {})
        ec2_group_stack_instance = None # Renamed for clarity
        target_asg_for_codedeploy: typing.Optional[autoscaling.IAutoScalingGroup] = None

        if ec2_config_group.get("deploy", False): # Ensure EC2 deployment is enabled in your config
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestratorStack: EC2 Instance deployment group is enabled.")
                ec2_group_stack_instance = Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=created_vpcs_map,
                    description="Nested Stack for EC2 instances and ASGs."
                )
                # --- !!! IMPORTANT: ADAPT THIS LOGIC !!! ---
                # How you get the ASG depends on how Ec2DeploymentsGroupNestedStack exposes it.
                # Option 1: Nested stack has a direct attribute for the primary ASG
                if hasattr(ec2_group_stack_instance, 'primary_auto_scaling_group'): # Example attribute name
                    target_asg_for_codedeploy = ec2_group_stack_instance.primary_auto_scaling_group
                    if target_asg_for_codedeploy:
                         logger.info(f"Target ASG for CodeDeploy identified via 'primary_auto_scaling_group' attribute: {target_asg_for_codedeploy.auto_scaling_group_name}")

                # Option 2: Nested stack has a method to retrieve an ASG by a logical name from config
                elif hasattr(ec2_group_stack_instance, 'get_auto_scaling_group_by_name'):
                    primary_asg_config_name = ec2_config_group.get("primary_asg_logical_name_for_codedeploy")
                    if primary_asg_config_name:
                        target_asg_for_codedeploy = ec2_group_stack_instance.get_auto_scaling_group_by_name(primary_asg_config_name)
                        if target_asg_for_codedeploy:
                             logger.info(f"Target ASG for CodeDeploy identified via method and name '{primary_asg_config_name}': {target_asg_for_codedeploy.auto_scaling_group_name}")
                        else:
                            logger.warning(f"Could not find ASG with logical name '{primary_asg_config_name}' for CodeDeploy using get_auto_scaling_group_by_name.")
                    else:
                        logger.warning("No 'primary_asg_logical_name_for_codedeploy' defined in EC2 config for CodeDeploy target lookup.")
                else:
                    logger.warning("Ec2DeploymentsGroupNestedStack instance does not have a recognized way to expose its ASG (e.g., 'primary_auto_scaling_group' attribute or 'get_auto_scaling_group_by_name' method). "
                                   "Cannot automatically determine target ASG for CodeDeploy.")
                # --- !!! END OF ADAPTATION SECTION !!! ---
            else:
                logger.warning("MainOrchestratorStack: EC2 group enabled, but no EC2 instance configurations found.")
        else:
            logger.info("MainOrchestratorStack: EC2 Instance deployment group is disabled in config.")

        # --- CodeDeploy Setup (Only if a target ASG was identified) ---
        if target_asg_for_codedeploy:
            logger.info(f"Proceeding with CodeDeploy setup targeting ASG: {target_asg_for_codedeploy.auto_scaling_group_name}")

            codedeploy_service_role = iam.Role(
                self, "CodeDeployServiceRoleForEC2", # More specific name
                assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSCodeDeployRole")
                ],
                description="Service role for AWS CodeDeploy to interact with EC2, ASG, ELB."
            )
            logger.info(f"CodeDeploy Service Role created: {codedeploy_service_role.role_name}")

            cd_application_name = f"{self.stack_name}-EC2App"
            cd_application = codedeploy.ServerApplication(self, "MyEC2CodeDeployApplication",
                application_name=cd_application_name,
                compute_platform=codedeploy.ComputePlatform.SERVER
            )
            logger.info(f"CodeDeploy Application created: {cd_application.application_name}")

            cd_deployment_group_name = f"{self.stack_name}-EC2-DG"
            deployment_group = codedeploy.ServerDeploymentGroup(self, "MyEC2CodeDeployDeploymentGroup",
                application=cd_application,
                deployment_group_name=cd_deployment_group_name,
                auto_scaling_groups=[target_asg_for_codedeploy],
                install_agent=True,
                deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE, # Or your preferred
                service_role=codedeploy_service_role,
                # Ensure EC2 instances in the ASG have an IAM instance profile with permissions for
                # CodeDeploy agent to communicate and S3 access to download the bundle.
            )
            logger.info(f"CodeDeploy Deployment Group created: {deployment_group.deployment_group_name}")

            cfn_deployment_trigger = codedeploy.CfnDeployment(self, "TriggerAppDeploymentViaCloudFormation",
                application_name=cd_application.application_name,
                deployment_group_name=deployment_group.deployment_group_name,
                description="CloudFormation-triggered application deployment via CodeDeploy",
                revision={
                    "revision_type": "S3",
                    "s3_location": {
                        "bucket": app_bundle_s3_bucket_param.value_as_string,
                        "key": app_bundle_s3_key_param.value_as_string,
                        "bundle_type": "zip"
                    }
                },
                # deployment_config_name="CodeDeployDefault.OneAtATime", # Optional
            )
            # Ensure CfnDeployment depends on the DeploymentGroup
            if deployment_group.node.default_child: # Check if default_child exists (it's a CfnResource)
                 cfn_deployment_trigger.add_dependency(deployment_group.node.default_child) # type: ignore
            logger.info("CfnDeployment resource for triggering CodeDeploy defined.")
        else:
            logger.warning("CodeDeploy setup skipped as no target ASG was identified from EC2 deployments. "
                           "Ensure EC2 deployment is enabled in config and the nested stack exposes its ASG correctly.")

        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")
