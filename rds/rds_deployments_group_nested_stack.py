# CRMP-PROJECT/cdk_project/rds/rds_deployments_group_nested_stack.py
import logging
import typing
import aws_cdk as cdk
from constructs import Construct
from aws_cdk import NestedStack, aws_ec2 as ec2, Tags,aws_iam as iam, aws_rds as rds # Import iam and rds

from .db_instance_stack import DbInstanceStack 
from .aurora_cluster_stack import AuroraClusterStack 

logger = logging.getLogger(__name__)

class RdsDeploymentsGroupNestedStack(NestedStack):
    """
    A nested stack that orchestrates the creation of multiple RDS Database Instances and Clusters.
    """
    def __init__(self, scope: Construct, id: str, *, # Added '*' for keyword-only args for clarity
                 rds_deployments_config: typing.Dict,
                 created_vpcs_map: typing.Dict[str, ec2.IVpc],
                 created_iam_roles_map: typing.Dict[str, iam.IRole], # Explicitly accept this parameter
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:
        
        # Filter kwargs to only pass those valid for NestedStack.
        nested_stack_valid_kwargs = {k: v for k, v in kwargs.items() if k in ['env', 'stack_name', 'synthesizer', 'termination_protection', 'description']}
        super().__init__(scope, id, description=description, **nested_stack_valid_kwargs) # Apply the filter here

        logger.info(f"RdsDeploymentsGroupNestedStack '{id}': Initializing.")
        cdk.Tags.of(self).add("ResourceGroup", "RDS")

        self.rds_deployments_config = rds_deployments_config
        self.created_vpcs_map = created_vpcs_map
        self.created_iam_roles_map = created_iam_roles_map # Stored for passing to child stacks

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
            
            if "engine_type" not in rds_specific_config: # Ensure engine_type is in the nested config for child stacks
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
                if not vpc_to_use_for_rds:
                    logger.error(f"For RDS '{config_id}', could not find CDK-created VPC with logical ID '{cdk_created_vpc_logical_id_ref}' in provided map. Child stack will attempt lookup if 'lookup_existing_vpc_by_id' is also set in its config.")
            
            if not vpc_to_use_for_rds and existing_vpc_id_to_lookup:
                 logger.info(f"For RDS '{config_id}', child stack will attempt lookup of existing VPC ID '{existing_vpc_id_to_lookup}'.")
                 # Child stack will perform the lookup as VPC object is None here

            if not vpc_to_use_for_rds and not existing_vpc_id_to_lookup:
                logger.error(f"For RDS '{config_id}', VPC configuration is missing 'use_cdk_created_vpc_id' or 'lookup_existing_vpc_by_id'. Cannot determine VPC. Skipping RDS.")
                continue

            # --- Resolve Security Groups in this parent stack (RdsDeploymentsGroupNestedStack) ---
            rds_sg_config = vpc_config_for_rds.get("security_group_config", {})
            rds_security_group: typing.Optional[ec2.ISecurityGroup] = None
            
            if rds_sg_config.get("source") == "CREATE_NEW":
                new_sg_options = rds_sg_config.get("create_new_options", {})
                sg_name = new_sg_options.get("name", f"{config_id}-sg")
                sg_description = new_sg_options.get("description", f"Security group for RDS {config_id}")
                
                # VPC is required to create a SecurityGroup
                if not vpc_to_use_for_rds: # If VPC couldn't be resolved by object, try lookup by ID for SG creation
                     vpc_id_for_sg = vpc_config_for_rds.get("lookup_existing_vpc_by_id")
                     if vpc_id_for_sg:
                         try: vpc_to_use_for_rds = ec2.Vpc.from_lookup(self, f"RdsSgVpcLookup{config_id}", vpc_id=vpc_id_for_sg)
                         except Exception as e: logger.error(f"Failed to lookup VPC for SG {sg_name}: {e}"); vpc_to_use_for_rds = None
                     else: logger.error(f"Cannot create SG {sg_name}: No VPC object resolved and no lookup ID provided.")

                if vpc_to_use_for_rds:
                    rds_security_group = ec2.SecurityGroup(self, f"{config_id}RdsSg",
                        vpc=vpc_to_use_for_rds,
                        security_group_name=sg_name,
                        description=sg_description,
                        allow_all_outbound=new_sg_options.get("allow_all_outbound", True)
                    )
                    logger.info(f"RDS '{config_id}': Created new security group '{sg_name}'.")

                    db_port_for_sg_rule = rds_specific_config.get("port", 3306 if engine_type == "MYSQL" else 5432) # Default to common ports
                    if db_port_for_sg_rule is None:
                         raise ValueError(f"Cannot determine DB port for SG rules of {config_id}")

                    for ingress_sg_id in new_sg_options.get("allow_ingress_from_sg_ids", []):
                        try:
                            peer_sg = ec2.SecurityGroup.from_security_group_id(self, f"{config_id}SgPeer{ingress_sg_id[:8]}", ingress_sg_id)
                            rds_security_group.add_ingress_rule(peer_sg, ec2.Port.tcp(db_port_for_sg_rule), f"Allow from {ingress_sg_id}")
                            logger.info(f"RDS '{config_id}': Added ingress rule from SG '{ingress_sg_id}' to port {db_port_for_sg_rule}.")
                        except Exception as e:
                            logger.warning(f"RDS '{config_id}': Failed to add ingress rule from SG '{ingress_sg_id}': {e}")
                    for ingress_cidr in new_sg_options.get("allow_ingress_from_cidrs", []):
                         try:
                             rds_security_group.add_ingress_rule(ec2.Peer.ipv4(ingress_cidr), ec2.Port.tcp(db_port_for_sg_rule), f"Allow from CIDR {ingress_cidr}")
                         except Exception as e:
                             logger.warning(f"RDS '{config_id}': Failed to add ingress rule from CIDR '{ingress_cidr}': {e}")
                    if new_sg_options.get("allow_ingress_from_self", False):
                         rds_security_group.add_ingress_rule(rds_security_group, ec2.Port.all_traffic(), "Allow traffic from other members of this SG")

                else:
                    logger.error(f"RDS '{config_id}': Failed to resolve VPC for creating new security group. Skipping SG creation.")

            elif rds_sg_config.get("source") == "USE_EXISTING_IDS":
                existing_sg_ids = rds_sg_config.get("existing_ids", [])
                if existing_sg_ids:
                    # For RDS, you typically pass a list of existing ISecurityGroup objects or their IDs.
                    # CDK RDS L2 constructs accept security_groups which expects a list.
                    # We will create ISecurityGroup objects from IDs.
                    for sg_id_val in existing_sg_ids:
                        try:
                            # If a security group was passed from Ec2DeploymentsGroup, check if it matches
                            # This is for the case where security group is resolved in a different group stack
                            # and passed as a real object.
                            # The created_iam_roles_map is not the correct map for SGs.
                            # It should be resolved_sgs_map from ec2_deployments_group_nested_stack.
                            # For simplicity, we just import it directly here.
                            rds_security_group = ec2.SecurityGroup.from_security_group_id(self, f"RdsExistSg{config_id}{sg_id_val[:8]}", sg_id_val)
                            logger.info(f"RDS '{config_id}': Using existing security group by ID '{sg_id_val}'.")
                            break # Assuming only one existing SG needed for primary assignment
                        except Exception as e:
                            logger.warning(f"RDS '{config_id}': Failed to import existing SG '{sg_id_val}': {e}. Skipping this SG.")
                else:
                    logger.error(f"RDS '{config_id}': 'USE_EXISTING_IDS' enabled but no 'existing_ids' provided. Skipping.")
                    continue
            else:
                logger.error(f"RDS '{config_id}': Invalid security_group_config source '{rds_sg_config.get('source')}'. Skipping.")
                continue

            # --- End Security Group Resolution ---

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
                        security_group=rds_security_group, # Pass the resolved SG object
                        created_iam_roles_map=self.created_iam_roles_map, # Pass the IAM roles map
                        description=f"Nested Stack for RDS {engine_type} Instance: {rds_name_for_desc} (Config ID: {config_id})"
                    )
                elif deployment_architecture == "CLUSTER": 
                    logger.info(f"RdsDeploymentsGroupNestedStack: Creating AuroraClusterStack for '{config_id}'.")
                    instance_or_cluster_stack = AuroraClusterStack(
                        self,
                        nested_stack_cdk_id,
                        rds_config=rds_specific_config, 
                        vpc=vpc_to_use_for_rds, # Pass the resolved VPC object
                        security_group=rds_security_group, # Pass the resolved SG object
                        created_iam_roles_map=self.created_iam_roles_map, # Pass the IAM roles map
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