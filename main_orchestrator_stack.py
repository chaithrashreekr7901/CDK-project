# CRMP-PROJECT/cdk_project/main_orchestrator_stack.py
from aws_cdk import (
    Stack, Tags, Environment, # Removed CfnParameter
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
    aws_autoscaling as autoscaling
)
from constructs import Construct
from deployment_config import get_deployment_configurations
from vpc.vpc_deployments_group_nested_stack import VpcDeploymentsGroupNestedStack
from peering.vpc_peerings_group_nested_stack import VpcPeeringsGroupNestedStack # Assuming this exists
from rds.rds_deployments_group_nested_stack import RdsDeploymentsGroupNestedStack
from s3.s3_deployments_group_nested_stack import S3DeploymentsGroupNestedStack # Assuming this exists
from ec2.ec2_deployments_group_nested_stack import Ec2DeploymentsGroupNestedStack # <<< ADDED IMPORT

import logging
import typing # Added for type hinting
logger = logging.getLogger(__name__)

class MainOrchestratorStack(Stack):
    # Make CodeDeploy application and group names available if needed by other stacks or for output
    public_codedeploy_application: typing.Optional[codedeploy.IServerApplication] = None
    public_codedeploy_deployment_group: typing.Optional[codedeploy.IServerDeploymentGroup] = None
    def __init__(self, scope: Construct, id: str, 
                 config: dict, # Explicitly accept the config dictionary
                 target_asg_for_codedeploy: typing.Optional[autoscaling.AutoScalingGroup] = None, 
                 description: typing.Optional[str] = None,
                 env: typing.Optional[Environment] = None,
                 **additional_kwargs) -> None: # Use a different name for other CDK Stack kwargs
        
        # Pass only the recognized keyword arguments to the base Stack class
        super().__init__(scope, id, 
                         description=description,
                         env=env,
                         # Add other standard Stack props if you use them from additional_kwargs
                         # (e.g., stack_name, tags, termination_protection)
                         **additional_kwargs) 

        logger.info(f"MainOrchestrator '{id}': Initializing for environment: {self.region} in account {self.account}")

        current_project_version = "5.4.0" # Increment version
        Tags.of(self).add("Project", "MultiResourcePlatform") # Updated project name
        Tags.of(self).add("ManagedBy", "CDK-MainOrchestrator")
        Tags.of(self).add("Version", current_project_version)

        config = get_deployment_configurations()

        # --- VPC Instance Deployments ---
        vpc_group_stack_construct = None
        created_vpcs_map: dict[str, ec2.IVpc] = {}
        # created_sgs_map: dict[str, ec2.ISecurityGroup] = {} # <<< If VpcStack will output SGs

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
                # if hasattr(vpc_group_stack_construct, 'created_sgs_map'): # Example if VPC stack exposes SGs
                #     created_sgs_map = vpc_group_stack_construct.created_sgs_map
            else:
                logger.warning("MainOrchestrator: VPC group enabled, but no VPC definitions found in 'instances' list.")
        else:
            logger.info("MainOrchestrator: VPC instance deployment group is disabled.")

        # --- VPC Peering Deployments ---
        peering_config_group = config.get("vpc_peerings", {})
        if peering_config_group.get("deploy", False):
            if peering_config_group.get("connections"):
                logger.info(f"MainOrchestrator: VPC Peering deployment group is enabled.")
                # Assuming VpcPeeringsGroupNestedStack constructor takes vpc_peerings_config
                # and potentially created_vpcs_map if it needs to reference VPCs created in this app.
                VpcPeeringsGroupNestedStack(
                    self, "VpcPeeringsGroup",
                    vpc_peerings_config=peering_config_group
                    # created_vpcs_map=created_vpcs_map # Pass if needed by your peering stack
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
                    created_vpcs_map=created_vpcs_map
                )
            else:
                logger.warning("MainOrchestrator: RDS group enabled, but no instances defined.")
        else:
            logger.info("MainOrchestrator: RDS deployment group is disabled.")


        # --- S3 Bucket Deployments ---
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


        # --- EC2 Instance Deployments ---
        ec2_config_group = config.get("ec2_deployments", {})
        if ec2_config_group.get("deploy", False):
            if ec2_config_group.get("instances"):
                logger.info("MainOrchestrator: EC2 Instance deployment group is enabled.")
                Ec2DeploymentsGroupNestedStack(
                    self, "Ec2InstancesGroup",
                    ec2_deployments_config=ec2_config_group
                    # No longer passing created_vpcs_map here
                )
            else:
                logger.warning("MainOrchestrator: EC2 group enabled, but no EC2 instance definitions found.")
        else:
            logger.info("MainOrchestrator: EC2 Instance deployment group is disabled.")
    
    
        
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


