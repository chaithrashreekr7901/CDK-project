# CRMP-PROJECT/cdk_project/rds/rds_deployments_group_nested_stack.py
import logging
from aws_cdk import (
    NestedStack,
    Tags,
    aws_ec2 as ec2 
)
from constructs import Construct
from .db_instance_stack import DbInstanceStack 
from .aurora_cluster_stack import AuroraClusterStack # <<< ADDED IMPORT

logger = logging.getLogger(__name__)

class RdsDeploymentsGroupNestedStack(NestedStack):
    def __init__(self, scope: Construct, id: str, 
                 rds_deployments_config: dict, 
                 created_vpcs_map: dict[str, ec2.IVpc], 
                 **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        self.created_vpcs_map = created_vpcs_map 

        group_description = rds_deployments_config.get("description", "RDS Deployments Group")
        logger.info(f"RdsDeploymentsGroupNestedStack: Initializing for: {group_description}")
        Tags.of(self).add("CDKResourceGroup", "RDSDeployments")

        self.rds_outputs = {} 
        rds_configurations_list = rds_deployments_config.get("instances", [])

        if not rds_configurations_list:
            logger.warning("RdsDeploymentsGroupNestedStack: No RDS configurations found in 'rds_deployments_config.instances'. No RDS resources will be deployed by this group.")
            return

        for rds_cfg_entry in rds_configurations_list:
            if not rds_cfg_entry.get("enabled", False): 
                skipped_rds_id = rds_cfg_entry.get('id', 'UnknownID_DeployFalse')
                logger.info(f"RdsDeploymentsGroupNestedStack: Skipping RDS configuration '{skipped_rds_id}' (enabled: false).")
                continue

            config_id = rds_cfg_entry.get("id")
            rds_specific_config = rds_cfg_entry.get("config", {}) 
            
            # deployment_architecture is still useful for clarity if provided explicitly
            deployment_architecture = rds_cfg_entry.get("deployment_architecture", "").upper() 
            # engine_type is now primary for logic if deployment_architecture is missing
            engine_type = rds_cfg_entry.get("engine_type", "").upper() 

            if not config_id:
                logger.error(f"RdsDeploymentsGroupNestedStack: Skipping RDS deployment due to missing 'id' in configuration entry: {rds_cfg_entry}")
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
            if not deployment_architecture: # Infer if not explicitly set
                deployment_architecture = "CLUSTER" if is_aurora_cluster else "INSTANCE"
            
            # Ensure consistency if both are set
            if deployment_architecture == "CLUSTER" and not is_aurora_cluster:
                logger.warning(f"Deployment architecture for '{config_id}' is CLUSTER, but engine_type '{engine_type}' is not Aurora. Treating as INSTANCE.")
                deployment_architecture = "INSTANCE"
            elif deployment_architecture == "INSTANCE" and is_aurora_cluster:
                 logger.warning(f"Deployment architecture for '{config_id}' is INSTANCE, but engine_type '{engine_type}' is Aurora. Treating as CLUSTER.")
                 deployment_architecture = "CLUSTER"


            vpc_to_use_for_rds: ec2.IVpc | None = None
            vpc_config_for_rds = rds_specific_config.get("vpc_config", {})
            cdk_created_vpc_logical_id_ref = vpc_config_for_rds.get("use_cdk_created_vpc_id")
            existing_vpc_id_to_lookup = vpc_config_for_rds.get("lookup_existing_vpc_by_id")

            if cdk_created_vpc_logical_id_ref:
                vpc_to_use_for_rds = self.created_vpcs_map.get(cdk_created_vpc_logical_id_ref)
                if vpc_to_use_for_rds:
                    logger.info(f"For RDS '{config_id}', using CDK-created VPC '{cdk_created_vpc_logical_id_ref}' (IVpc object passed).")
                else:
                    logger.error(f"For RDS '{config_id}', could not find CDK-created VPC with logical ID '{cdk_created_vpc_logical_id_ref}' in provided map. Child stack will attempt lookup if 'lookup_existing_vpc_by_id' is also set in its config.")
            elif existing_vpc_id_to_lookup:
                logger.info(f"For RDS '{config_id}', child stack will attempt lookup of existing VPC ID '{existing_vpc_id_to_lookup}'.")
            else:
                logger.error(f"For RDS '{config_id}', VPC configuration is missing 'use_cdk_created_vpc_id' or 'lookup_existing_vpc_by_id'. Cannot determine VPC. Skipping RDS.")
                continue

            sanitized_config_id = ''.join(filter(str.isalnum, config_id))
            if not sanitized_config_id: 
                sanitized_config_id = f"RdsDeployment{sum(1 for _ in self.rds_outputs)}"
            
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
                        vpc=vpc_to_use_for_rds, 
                        description=f"Nested Stack for RDS Instance: {rds_name_for_desc} (Config ID: {config_id})"
                    )
                elif deployment_architecture == "CLUSTER": 
                    logger.info(f"RdsDeploymentsGroupNestedStack: Creating AuroraClusterStack for '{config_id}'.")
                    # --- UNCOMMENTED AND USING AuroraClusterStack ---
                    instance_or_cluster_stack = AuroraClusterStack(
                        self,
                        nested_stack_cdk_id,
                        rds_config=rds_specific_config, 
                        vpc=vpc_to_use_for_rds, 
                        description=f"Nested Stack for Aurora Cluster: {rds_name_for_desc} (Config ID: {config_id})"
                    )
                    # --- END UNCOMMENT ---
                else:
                    logger.error(f"RdsDeploymentsGroupNestedStack: Unsupported deployment_architecture '{deployment_architecture}' for RDS '{config_id}'. Skipping.")
                    continue
                
                if instance_or_cluster_stack:
                    Tags.of(instance_or_cluster_stack).add("RDSConfigID", config_id)
                    Tags.of(instance_or_cluster_stack).add("RDSEngineType", engine_type)
                    Tags.of(instance_or_cluster_stack).add("RDSDeploymentArchitecture", deployment_architecture)

            except Exception as e:
                logger.error(f"RdsDeploymentsGroupNestedStack: Failed to instantiate RDS stack for config ID '{config_id}': {e}", exc_info=True)
