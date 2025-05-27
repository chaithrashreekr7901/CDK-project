# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, Environment, CfnParameter, # Added CfnParameter
    aws_ec2 as ec2,
    aws_iam as iam, # Added IAM
    aws_codedeploy as codedeploy, # Added CodeDeploy
    aws_autoscaling as autoscaling # Added AutoScaling (example for target)
)
from constructs import Construct
# from deployment_config import get_deployment_configurations # Not used directly in this class if config is passed

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
                 config: dict, # Explicitly accept the config dictionary
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None:

        super().__init__(scope, id,
                         description=description,
                         env=env,
                         **additional_kwargs)

        logger.info(f"MainOrchestratorStack '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = config.get("project_version", "6.0.0-codedeploy") # Example version
        Tags.of(self).add("Project", config.get("project_name", "MultiResourcePlatform"))
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        # --- CloudFormation Parameters for S3 Application Bundle Location ---
        # These parameters will be set by the pipeline's 'cdk deploy' command
        app_bundle_s3_bucket_param = CfnParameter(self, "AppBundleS3Bucket",
            type="String",
            description="The S3 bucket where the application bundle for CodeDeploy is stored."
        )
        app_bundle_s3_key_param = CfnParameter(self, "AppBundleS3Key",
            type="String",
            description="The S3 key for the application bundle for CodeDeploy."
        )
        logger.info("Defined CfnParameters for AppBundleS3Bucket and AppBundleS3Key.")

        # 'config' is directly available as a parameter

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct = None
        created_vpcs_map: dict[str, ec2.IVpc] = {}
        vpc_deployments_section = config.get("vpcs", {})
        if vpc_deployments_section.get("deploy", False):
            if vpc_deployments_section.get("instances"):
                logger.info("MainOrchestratorStack: VPC instance deployment group is enabled.")
                vpc_group_stack_construct = VpcDeploymentsGroupNestedStack(
                    self, "VpcDeploymentsGroup",
                    vpc_deployments_section_config=vpc_deployments_section,
                    description="Nested Stack for all configured VPC instances."
                )
                if hasattr(vpc_group_stack_construct, 'created_vpcs_map'):
                    created_vpcs_map = vpc_group_stack_construct.created_vpcs_map
            else:
                logger.warning("MainOrchestratorStack: VPC group enabled, but no VPC definitions found.")
        else:
            logger.info("MainOrchestratorStack: VPC instance deployment group is disabled.")

        # --- VPC Peering Deployments ---
        # (Your existing peering logic - no changes needed here for CodeDeploy itself)
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info("MainOrchestratorStack: VPC Peering deployment group is enabled.")
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup", vpc_peerings_config=peering_config_group,
                    description="Nested Stack for VPC Peering connections."
                )
            else:
                logger.warning("MainOrchestratorStack: Peering group enabled, but no connections defined.")
        else:
            logger.info("MainOrchestratorStack: VPC Peering deployment group is disabled.")

        # --- RDS Deployments ---
        # (Your existing RDS logic)
        rds_config_group = config.get("rds_deployments", {})
        if rds_config_group.get("deploy", False):
            if rds_config_group.get("instances"):
                logger.info("MainOrchestratorStack: RDS deployment group is enabled.")
                RdsDeploymentsGroupNestedStack(
                    self, "RdsDeploymentsGroup", rds_deployments_config=rds_config_group,
                    created_vpcs_map=created_vpcs_map, description="Nested Stack for RDS database instances."
                )
            else:
                logger.warning("MainOrchestratorStack: RDS group enabled, but no instances defined.")
        else:
            logger.info("MainOrchestratorStack: RDS deployment group is disabled.")

        # --- S3 Bucket Deployments ---
        # (Your existing S3 logic)
        s3_config_group = config.get("s3_deployments", {})
        if s3_config_group.get("deploy", False):
            if s3_config_group.get("buckets"):
                logger.info("MainOrchestratorStack: S3 Bucket deployment group is enabled.")
                S3DeploymentsGroupNestedStack(
                    self, "S3BucketsGroup", s3_deployments_config=s3_config_group,
                    description="Nested Stack for S3 buckets."
                )
            else:
                logger.warning("MainOrchestratorStack: S3 group enabled, but no buckets defined.")
        else:
            logger.info("MainOrchestratorStack: S3 Bucket deployment group is disabled.")


        # --- EC2 Instance Deployments (This is where compute for CodeDeploy might be) ---
        ec2_config_group = config.get("ec2_deployments", {})
        ec2_group_stack_construct_instance = None # To hold the instance of the nested stack
        target_asg_for_codedeploy: typing.Optional[autoscaling.IAutoScalingGroup] = None

        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestratorStack: EC2 Instance deployment group is enabled.")
                ec2_group_stack_construct_instance = Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group,
                    created_vpcs_map=created_vpcs_map,
                    description="Nested Stack for EC2 instances."
                )
                # IMPORTANT ASSUMPTION: Your Ec2DeploymentsGroupNestedStack must expose the ASG
                # For example, if it has an attribute `self.auto_scaling_group_map` or similar
                # This is a placeholder - you need to adapt this to your actual nested stack structure.
                if hasattr(ec2_group_stack_construct_instance, 'get_auto_scaling_group_by_name'):
                    # Example: if your nested stack has a method to get an ASG by a logical name
                    # defined in your ec2_config_group
                    # This assumes your config has a primary ASG name or similar identifier
                    primary_asg_name_from_config = ec2_config_group.get("primary_asg_logical_name_for_codedeploy")
                    if primary_asg_name_from_config:
                        target_asg_for_codedeploy = ec2_group_stack_construct_instance.get_auto_scaling_group_by_name(primary_asg_name_from_config)
                        if target_asg_for_codedeploy:
                             logger.info(f"Target ASG for CodeDeploy identified: {target_asg_for_codedeploy.auto_scaling_group_name}")
                        else:
                            logger.warning(f"Could not find ASG with logical name '{primary_asg_name_from_config}' for CodeDeploy.")
                    else:
                        logger.warning("No 'primary_asg_logical_name_for_codedeploy' defined in EC2 config for CodeDeploy target.")
                else:
                    logger.warning("Ec2DeploymentsGroupNestedStack does not have 'get_auto_scaling_group_by_name' or similar. "
                                   "Cannot automatically determine target ASG for CodeDeploy.")
            else:
                logger.warning("MainOrchestratorStack: EC2 group enabled, but no EC2 definitions found.")
        else:
            logger.info("MainOrchestratorStack: EC2 Instance deployment group is disabled.")

        # --- CodeDeploy Setup (Only if a target ASG was identified) ---
        if target_asg_for_codedeploy:
            logger.info("Setting up CodeDeploy resources as a target ASG is available.")

            # 1. CodeDeploy Service Role
            # This role is assumed by CodeDeploy to interact with AWS services (EC2, ASG, ELB, S3)
            codedeploy_service_role = iam.Role(
                self, "CodeDeployServiceRole",
                assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSCodeDeployRole")
                ],
                description="Service role for AWS CodeDeploy"
            )
            logger.info(f"CodeDeploy Service Role created: {codedeploy_service_role.role_name}")

            # 2. CodeDeploy Application
            # (Using ServerApplication for EC2/On-Premises deployments)
            cd_application_name = f"{self.stack_name}-App" # Example naming
            cd_application = codedeploy.ServerApplication(self, "MyCodeDeployApplication",
                application_name=cd_application_name,
                compute_platform=codedeploy.ComputePlatform.SERVER # Or LAMBDA/ECS
            )
            logger.info(f"CodeDeploy Application created: {cd_application.application_name}")

            # 3. CodeDeploy Deployment Group
            # This group targets the ASG created by your Ec2DeploymentsGroupNestedStack
            cd_deployment_group_name = f"{self.stack_name}-DG" # Example naming
            deployment_group = codedeploy.ServerDeploymentGroup(self, "MyCodeDeployDeploymentGroup",
                application=cd_application,
                deployment_group_name=cd_deployment_group_name,
                auto_scaling_groups=[target_asg_for_codedeploy],
                install_agent=True, # Automatically install/update CodeDeploy agent on ASG instances
                # EC2 instances in the ASG must have an IAM instance profile allowing S3 access for bundle download
                # and communication with CodeDeploy agent.
                # Example: AmazonSSMManagedInstanceCore and a custom policy for S3/CodeDeploy agent.
                deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE, # Or other configs like ONE_AT_A_TIME, BLUE_GREEN
                service_role=codedeploy_service_role,
                # Optional: Add load balancer, auto-rollback, alarms etc.
                # load_balancer=codedeploy.LoadBalancer.classic(...),
                # auto_rollback=codedeploy.AutoRollbackConfig(failed_deployment=True),
            )
            logger.info(f"CodeDeploy Deployment Group created: {deployment_group.deployment_group_name}")

            # 4. AWS::CodeDeploy::Deployment Resource (L1 Construct)
            # This resource, when its 'revision' changes, triggers a new CodeDeploy deployment.
            # The pipeline's 'cdk deploy' command will update the S3 key parameter, causing this to update.
            cfn_deployment_trigger = codedeploy.CfnDeployment(self, "TriggerAppDeploymentViaCloudFormation",
                application_name=cd_application.application_name,
                deployment_group_name=deployment_group.deployment_group_name,
                description="CloudFormation-triggered application deployment via CodeDeploy",
                # The revision points to the S3 location of the application bundle
                # This bundle is uploaded by the pipeline's synth_bundle stage.
                revision={
                    "revision_type": "S3",
                    "s3_location": {
                        "bucket": app_bundle_s3_bucket_param.value_as_string,
                        "key": app_bundle_s3_key_param.value_as_string,
                        "bundle_type": "zip" # Or 'tar', 'tgz' - must match your bundle format
                    }
                },
                # Optional: Specify a deployment configuration (e.g., "CodeDeployDefault.Canary10Percent5Minutes")
                # If not specified, the deployment group's default configuration is used.
                # deployment_config_name="CodeDeployDefault.OneAtATime",

                # Optional: Configure auto-rollback behavior
                # auto_rollback_configuration={
                # "enabled": True,
                # "events": ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
                # }
            )
            # Ensure this CfnDeployment depends on the DeploymentGroup to avoid race conditions
            cfn_deployment_trigger.add_dependency(deployment_group.node.default_child) # type: ignore
            logger.info("CfnDeployment resource for triggering CodeDeploy defined.")
        else:
            logger.warning("CodeDeploy setup skipped as no target ASG was identified from EC2 deployments.")

        logger.info(f"MainOrchestratorStack '{id}': Initialization complete.")

