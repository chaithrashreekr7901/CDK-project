# CRMP-PROJECT/cdk_project/rds/rds_deployments_group_nested_stack.py
import logging
import typing
import aws_cdk as cdk
from constructs import Construct
from aws_cdk import NestedStack, aws_ec2 as ec2, Tags, aws_iam as iam, aws_rds as rds

# Import the VpcInstanceNestedStack to access its exposed subnet lists
from vpc.vpc_instance_nested_stack import VpcInstanceNestedStack

from .db_instance_stack import DbInstanceStack
from .aurora_cluster_stack import AuroraClusterStack

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s: %(message)s')


class RdsDeploymentsGroupNestedStack(NestedStack):
    """
    A nested stack that orchestrates the creation of multiple RDS Database Instances and Clusters.
    """
    def __init__(self, scope: Construct, id: str, *,
                 rds_deployments_config: typing.Dict,
                 created_vpcs_map: typing.Dict[str, ec2.IVpc],
                 created_iam_roles_map: typing.Dict[str, iam.IRole],
                 created_vpc_stacks_map: typing.Dict[str, VpcInstanceNestedStack], # Map logical ID to VpcInstanceNestedStack object
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:

        nested_stack_valid_kwargs = {k: v for k, v in kwargs.items() if k in ['env', 'stack_name', 'synthesizer', 'termination_protection', 'description']}
        super().__init__(scope, id, description=description, **nested_stack_valid_kwargs)

        logger.info(f"RdsDeploymentsGroupNestedStack '{id}': Initializing.")
        cdk.Tags.of(self).add("ResourceGroup", "RDS")

        self.rds_deployments_config = rds_deployments_config
        self.created_vpcs_map = created_vpcs_map
        self.created_iam_roles_map = created_iam_roles_map
        self.created_vpc_stacks_map = created_vpc_stacks_map

        instances_definitions = self.rds_deployments_config.get("instances", [])

        if not instances_definitions:
            logger.warning("RdsDeploymentsGroupNestedStack: No RDS instance or cluster definitions found.")
            return

        for instance_def in instances_definitions:
            if not instance_def.get("enabled", False):
                skipped_rds_id = instance_def.get('id', 'UnknownID_DeployFalse')
                logger.info(f"RdsDeploymentsGroupNestedStack: Skipping disabled RDS instance/cluster: {skipped_rds_id} (enabled: false).")
                continue

            config_id = instance_def["id"]
            rds_specific_config = instance_def.get("config", {})

            deployment_architecture = instance_def.get("deployment_architecture", "").upper()
            engine_type = instance_def.get("engine_type", "").upper()

            if not config_id:
                logger.error(f"RdsDeploymentsGroupNestedStack: Skipping RDS deployment due to missing 'id' in configuration entry: {instance_def}")
                continue
            if not rds_specific_config:
                logger.error(f"RdsDeploymentsGroupNestedStack: Skipping RDS '{config_id}' due to missing 'config' block.")
                continue
            if not engine_type:
                logger.error(f"RdsDeploymentsGroupNestedStack: Skipping RDS '{config_id}' due to missing 'engine_type'. This is now required.")
                continue

            if "engine_type" not in rds_specific_config:
                rds_specific_config["engine_type"] = engine_type

            is_aurora_cluster = engine_type.startswith("AURORA_")
            if not deployment_architecture:
                deployment_architecture = "CLUSTER" if is_aurora_cluster else "INSTANCE"

            if deployment_architecture == "CLUSTER" and not is_aurora_cluster:
                logger.warning(f"Deployment architecture for '{config_id}' is CLUSTER, but engine_type '{engine_type}' is not Aurora. Treating as INSTANCE.")
                deployment_architecture = "INSTANCE"
            elif deployment_architecture == "INSTANCE" and is_aurora_cluster:
                 logger.warning(f"Deployment architecture for '{config_id}' is INSTANCE, but engine_type '{engine_type}' is Aurora. Treating as CLUSTER.")
                 deployment_architecture = "CLUSTER"


            # --- VPC Resolution for RDS ---
            resolved_vpc: ec2.IVpc | None = None
            rds_public_cfn_subnets: typing.List[ec2.CfnSubnet] = []
            rds_private_cfn_subnets: typing.List[ec2.CfnSubnet] = []
            rds_isolated_cfn_subnets: typing.List[ec2.CfnSubnet] = []

            vpc_config_for_rds = rds_specific_config.get("vpc_config", {})
            cdk_created_vpc_logical_id_ref = vpc_config_for_rds.get("use_cdk_created_vpc_id")
            existing_vpc_id_to_lookup = vpc_config_for_rds.get("lookup_existing_vpc_by_id")

            if cdk_created_vpc_logical_id_ref:
                resolved_vpc = self.created_vpcs_map.get(cdk_created_vpc_logical_id_ref)
                vpc_stack_for_rds_subnets = self.created_vpc_stacks_map.get(cdk_created_vpc_logical_id_ref)
                
                if vpc_stack_for_rds_subnets and resolved_vpc:
                    # VPC was created by this app, so get its L1 CfnSubnet references
                    rds_public_cfn_subnets = vpc_stack_for_rds_subnets.public_cfn_subnets
                    rds_private_cfn_subnets = vpc_stack_for_rds_subnets.private_cfn_subnets
                    rds_isolated_cfn_subnets = vpc_stack_for_rds_subnets.isolated_cfn_subnets
                    logger.info(f"For RDS '{config_id}', resolved VPC '{cdk_created_vpc_logical_id_ref}' and its CfnSubnet lists from created_vpc_stacks_map.")
                elif cdk_created_vpc_logical_id_ref not in self.created_vpcs_map:
                    logger.error(f"For RDS '{config_id}', specified CDK-created VPC '{cdk_created_vpc_logical_id_ref}' not found in created_vpcs_map. Check if VPC deployment failed or ID is incorrect.")
                    # If not found, fall through to check lookup_existing_vpc_by_id
                    resolved_vpc = None # Ensure it's None so lookup path is taken

            if not resolved_vpc and existing_vpc_id_to_lookup:
                # If VPC is being looked up (not passed as object), try to get IVpc object
                try:
                    resolved_vpc = ec2.Vpc.from_lookup(self, f"RdsVpcLookupForExisting{config_id}", vpc_id=existing_vpc_id_to_lookup)
                    logger.info(f"For RDS '{config_id}', successfully looked up existing VPC '{existing_vpc_id_to_lookup}'.")
                    # For existing VPCs, rds_public_cfn_subnets etc. lists remain empty.
                    # The child RDS stack will then know to use resolved_vpc.select_subnets()
                    # or require explicit subnet_ids_for_rds in the config.
                except Exception as e:
                    logger.error(f"For RDS '{config_id}', lookup of existing VPC ID '{existing_vpc_id_to_lookup}' failed: {e}. Skipping RDS.")
                    continue


            if not resolved_vpc:
                logger.error(f"For RDS '{config_id}', VPC could not be resolved. Ensure 'use_cdk_created_vpc_id' or 'lookup_existing_vpc_by_id' is correctly set. Skipping RDS.")
                continue

            # --- Security Group Resolution (Pass full config to child) ---
            # This orchestrator will NO LONGER create the L2 ec2.SecurityGroup.
            # Instead, it passes the config, and the child RDS stack handles creation/import.
            rds_sg_config = vpc_config_for_rds.get("security_group_config", {})
            # No rds_security_group object created here.


            # --- End Security Group Resolution ---

            sanitized_config_id = ''.join(filter(str.isalnum, config_id))
            if not sanitized_config_id:
                sanitized_config_id = f"RdsDeploymentAutoGen{len(instances_definitions) + len(self.node.find_all('DbInstanceNestedStack'))}"

            stack_suffix = "AuroraClusterNestedStack" if deployment_architecture == "CLUSTER" else "RdsInstanceNestedStack"
            nested_stack_cdk_id = f"{sanitized_config_id}{stack_suffix}"

            rds_name_for_desc = rds_specific_config.get('instance_identifier') or rds_specific_config.get('cluster_identifier') or config_id

            logger.info(f"RdsDeploymentsGroupNestedStack: Defining stack for RDS '{config_id}' (Arch: {deployment_architecture}, Engine: {engine_type}) with CDK ID '{nested_stack_cdk_id}'.")

            instance_or_cluster_stack = None
            try:
                if deployment_architecture == "INSTANCE":
                    logger.info(f"RdsDeploymentsGroupNestedStack: Creating DbInstanceStack for '{config_id}'.")
                    instance_or_cluster_stack = DbInstanceStack(
                        self,
                        nested_stack_cdk_id,
                        rds_config=rds_specific_config,
                        vpc=resolved_vpc, # Passed L2 VPC object
                        security_group_config=rds_sg_config, # <-- NEW: Pass the full config here
                        public_cfn_subnets=rds_public_cfn_subnets, # Pass CfnSubnet lists (for new VPCs)
                        private_cfn_subnets=rds_private_cfn_subnets,
                        isolated_cfn_subnets=rds_isolated_cfn_subnets,
                        created_iam_roles_map=self.created_iam_roles_map,
                        description=f"Nested Stack for RDS {engine_type} Instance: {rds_name_for_desc} (Config ID: {config_id})"
                    )
                elif deployment_architecture == "CLUSTER":
                    logger.info(f"RdsDeploymentsGroupNestedStack: Creating AuroraClusterStack for '{config_id}'.")
                    instance_or_cluster_stack = AuroraClusterStack(
                        self,
                        nested_stack_cdk_id,
                        rds_config=rds_specific_config,
                        vpc=resolved_vpc, # Pass L2 VPC object
                        security_group_config=rds_sg_config, # <-- NEW: Pass the full config here
                        public_cfn_subnets=rds_public_cfn_subnets, # Pass CfnSubnet lists (for new VPCs)
                        private_cfn_subnets=rds_private_cfn_subnets,
                        isolated_cfn_subnets=rds_isolated_cfn_subnets,
                        created_iam_roles_map=self.created_iam_roles_map,
                        description=f"Nested Stack for Aurora Cluster: {rds_name_for_desc} (Config ID: {config_id})"
                    )
                else:
                    logger.error(f"RdsDeploymentsGroupNestedStack: Unsupported deployment_architecture '{deployment_architecture}' for RDS '{config_id}'. Skipping.")
                    continue

                if instance_or_cluster_stack:
                    Tags.of(instance_or_cluster_stack).add("RDSConfigID", config_id)
                    Tags.of(instance_or_cluster_stack).add("RDSEngineType", engine_type)
                    Tags.of(instance_or_cluster_stack).add("RDSDeploymentArchitecture", deployment_architecture)

            except Exception as e:
                logger.error(f"RdsDeploymentsGroupNestedStack: Failed to instantiate RDS stack for config ID '{config_id}': {e}", exc_info=True)

        logger.info(f"RdsDeploymentsGroupNestedStack '{id}': Initialization complete.")