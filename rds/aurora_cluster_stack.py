# CRMP-PROJECT/cdk_project/rds/aurora_cluster_stack.py
import logging
import json
import re
from aws_cdk import (
    NestedStack, Tags, RemovalPolicy, Duration, CfnOutput, Stack, Aws, Fn,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam,
    aws_kms as kms
)
from constructs import Construct

logger = logging.getLogger(__name__)

class AuroraClusterStack(NestedStack):
    db_cluster: rds.IDatabaseCluster # Expose the cluster

    def __init__(self, scope: Construct, construct_id: str,
                 rds_config: dict, # Expects engine_type, engine_version etc. inside
                 vpc: ec2.IVpc | None,
                 **kwargs) -> None:
        
        # Explicitly remove rds_config and vpc from kwargs before passing to super
        kwargs.pop('rds_config', None)
        kwargs.pop('vpc', None)
        super().__init__(scope, construct_id, **kwargs)

        self.config = rds_config # Now assign after super call

        cluster_identifier = self.config.get('cluster_identifier')
        if not cluster_identifier:
            base_id = construct_id.replace("RdsNestedStack", "") # Assuming group stack appends this
            cluster_identifier = f"{Stack.of(self).stack_name.lower()}-{base_id.lower()}-cluster"[:60]
            cluster_identifier = re.sub(r"[^a-z0-9-]", "-", cluster_identifier)
            logger.warning(f"RDS cluster_identifier not provided for {construct_id}, generated: {cluster_identifier}")

        engine_type_str = self.config.get("engine_type", "").upper() # e.g., AURORA_MYSQL, AURORA_POSTGRESQL
        if not engine_type_str.startswith("AURORA_"):
            raise ValueError(f"engine_type '{engine_type_str}' is not a valid Aurora engine for AuroraClusterStack.")

        logger.info(f"AuroraClusterStack '{construct_id}': Initializing for RDS Cluster ID '{cluster_identifier}' with engine type '{engine_type_str}'.")

        # --- Resolve VPC ---
        resolved_vpc: ec2.IVpc | None = vpc
        if not resolved_vpc:
            logger.info(f"No VPC object passed for {cluster_identifier}, attempting lookup based on its own config.")
            resolved_vpc = self._resolve_vpc_via_lookup(cluster_identifier, construct_id)
        if not resolved_vpc:
             raise ValueError(f"VPC could not be resolved for RDS cluster {cluster_identifier}.")

        # --- Aurora Engine ---
        aurora_engine = self._get_aurora_cluster_engine(engine_type_str, self.config)
        default_engine_port = self._get_default_aurora_port(engine_type_str)

        # --- Credentials ---
        db_credentials, generated_secret_resource = self._resolve_credentials(cluster_identifier, construct_id)

        # --- Subnet Group ---
        # Aurora typically uses a DBSubnetGroup. The L2 construct can create one implicitly
        # if vpc_subnets are provided, or you can create/specify one.
        # For simplicity, we'll rely on the L2 construct's default behavior with vpc_subnets.
        vpc_cfg_for_subnet = self.config.get("vpc_config", {})
        subnet_type_str = vpc_cfg_for_subnet.get("subnet_type_for_rds", "PRIVATE_WITH_EGRESS").upper()
        rds_subnet_type = getattr(ec2.SubnetType, subnet_type_str, ec2.SubnetType.PRIVATE_WITH_EGRESS)
        if not hasattr(ec2.SubnetType, subnet_type_str): logger.warning(f"Invalid subnet_type_for_rds: '{subnet_type_str}'. Defaulting.")
        
        rds_subnet_selection = ec2.SubnetSelection(subnet_type=rds_subnet_type)
        logger.info(f"Aurora cluster {cluster_identifier} will use subnets of type: {rds_subnet_type.name}")

        # --- Security Groups ---
        db_security_groups = self._resolve_security_groups(resolved_vpc, cluster_identifier, default_engine_port, construct_id)

        # --- Cluster Parameter Group ---
        cluster_pg = self._resolve_cluster_parameter_group(cluster_identifier, aurora_engine, engine_type_str, construct_id)
        
        # --- DB Instance Parameter Group (for instances in the cluster) ---
        instance_pg_config = self.config.get("instance_parameter_group", {})
        instance_parameter_group = None
        if instance_pg_config: # Only resolve if section exists
            instance_parameter_group = self._resolve_instance_parameter_group(cluster_identifier, aurora_engine, engine_type_str, construct_id, instance_pg_config)


        # --- Instances Configuration ---
        instances_cfg = self.config.get("instances_config", {})
        instance_props_list = []
        num_instances = instances_cfg.get("count", 1) # Default to 1 writer instance

        # Serverless v2 Scaling
        serverless_v2_scaling_config = None
        if self.config.get("enable_serverless_v2_scaling", False):
            min_acu = self.config.get("serverless_v2_min_capacity_acu")
            max_acu = self.config.get("serverless_v2_max_capacity_acu")
            if min_acu is not None and max_acu is not None:
                serverless_v2_scaling_config = rds.ServerlessV2ScalingConfiguration(
                    min_capacity=min_acu,
                    max_capacity=max_acu
                )
                logger.info(f"Configuring Serverless V2 scaling for {cluster_identifier}: MinACU={min_acu}, MaxACU={max_acu}")
                # If serverless, instance count and type might be managed differently or not specified.
                # The rds.DatabaseCluster L2 construct handles this if serverless_v2_scaling_configuration is set.
                # We might not need to define explicit instances if serverless is fully utilized.
                # However, for provisioned instances within a serverless-capable cluster, or mixed, this needs care.
                # For now, if serverless is enabled, we might skip explicit instance provisioning below,
                # or the user must ensure instance_type is serverless-compatible (e.g., db.serverless).
                if num_instances > 0 and instances_cfg.get("instance_type") != "db.serverless": # A common serverless instance "type"
                    logger.warning(f"ServerlessV2 scaling enabled for {cluster_identifier}, but {num_instances} provisioned instances of type "
                                   f"'{instances_cfg.get('instance_type')}' are also configured. Review compatibility.")
            else:
                logger.warning(f"enable_serverless_v2_scaling is True for {cluster_identifier} but min/max ACU not fully specified. Ignoring.")

        # Instance definitions (if not purely serverless or if mixed)
        if not serverless_v2_scaling_config or instances_cfg.get("instance_type") == "db.serverless": # Allow db.serverless instance type with serverless scaling
            if "instance_definitions" in instances_cfg: # Granular instance definitions
                for i_def in instances_cfg["instance_definitions"]:
                    instance_props_list.append(rds.InstanceProps(
                        instance_type=ec2.InstanceType(i_def.get("instance_type", "db.r5.large")), # Default if not in def
                        publicly_accessible=i_def.get("publicly_accessible_instances", instances_cfg.get("publicly_accessible_instances", False)),
                        allow_major_version_upgrade=i_def.get("allow_major_version_upgrade_instances", instances_cfg.get("allow_major_version_upgrade_instances", False)),
                        auto_minor_version_upgrade=i_def.get("auto_minor_version_upgrade_instances", instances_cfg.get("auto_minor_version_upgrade_instances", True)),
                        parameter_group=instance_parameter_group, # Apply common instance PG
                        # promotion_tier=i_def.get("promotion_tier"), # For read replicas
                        ca_certificate=rds.CaCertificate.identifier(i_def.get("ca_certificate_identifier_instances", instances_cfg.get("ca_certificate_identifier_instances"))) if i_def.get("ca_certificate_identifier_instances", instances_cfg.get("ca_certificate_identifier_instances")) else None,
                        enable_performance_insights=i_def.get("enable_performance_insights_instances", instances_cfg.get("enable_performance_insights_instances", False)),
                        performance_insight_retention=rds.PerformanceInsightRetention.DEFAULT if i_def.get("enable_performance_insights_instances", instances_cfg.get("enable_performance_insights_instances", False)) else None,
                    ))
            elif num_instances > 0 : # Common config for all instances
                common_instance_type_str = instances_cfg.get("instance_type")
                if not common_instance_type_str:
                    raise ValueError(f"instance_type is required in instances_config for {cluster_identifier} when not using instance_definitions or serverless.")
                
                common_instance_props = rds.InstanceProps(
                    instance_type=ec2.InstanceType(common_instance_type_str),
                    publicly_accessible=instances_cfg.get("publicly_accessible_instances", False),
                    allow_major_version_upgrade=instances_cfg.get("allow_major_version_upgrade_instances", False),
                    auto_minor_version_upgrade=instances_cfg.get("auto_minor_version_upgrade_instances", True),
                    parameter_group=instance_parameter_group,
                    ca_certificate=rds.CaCertificate.identifier(instances_cfg.get("ca_certificate_identifier_instances")) if instances_cfg.get("ca_certificate_identifier_instances") else None,
                    enable_performance_insights=instances_cfg.get("enable_performance_insights_instances", False),
                    performance_insight_retention=rds.PerformanceInsightRetention.DEFAULT if instances_cfg.get("enable_performance_insights_instances", False) else None,
                )
                # For DatabaseCluster, instance_props is a single InstanceProps for the writer,
                # and readers are specified by 'readers' or 'instance_identifier_suffix'.
                # The L2 construct rds.DatabaseCluster is simpler.
                # If using serverless_v2_scaling_config, instance_props might not be needed or should be for db.serverless.
                # This part needs careful alignment with how rds.DatabaseCluster handles instances vs serverless.
                # For now, we assume if serverless_v2_scaling_config is set, we don't pass explicit instance_props
                # unless instance_type is 'db.serverless'.
                if not serverless_v2_scaling_config or common_instance_type_str == "db.serverless":
                     instance_props_list.append(common_instance_props) # This will be the writer if num_instances=1
                     # For readers, you'd typically add them separately or use cluster.add_read_replica
                     # The rds.DatabaseCluster's 'instances' prop is for number of instances, not their detailed props.
                     # The 'instance_props' on DatabaseCluster is for the primary. Readers are scaled.

        # --- Backup & Recovery ---
        backup_props = None
        if self.config.get("enable_automated_backups", True):
            retention_days = self.config.get("backup_retention_days", 7)
            if retention_days > 0:
                backup_props = rds.BackupProps(
                    retention=Duration.days(retention_days),
                    preferred_window=self.config.get("preferred_backup_window")
                )
        
        backtrack_window = None
        if self.config.get("enable_backtrack", False):
            hours = self.config.get("backtrack_window_hours")
            if isinstance(hours, int) and hours > 0:
                backtrack_window = Duration.hours(hours)
            else:
                logger.warning(f"enable_backtrack is True for {cluster_identifier} but backtrack_window_hours is invalid. Disabling backtrack.")

        # --- Encryption ---
        cluster_kms_key = None
        if self.config.get("storage_encrypted", True) and self.config.get("enable_custom_kms_encryption", False):
            kms_key_id_for_cluster = self.config.get("kms_key_id")
            if kms_key_id_for_cluster:
                try:
                    cluster_kms_key = kms.Key.from_key_arn(self, f"AuroraClusterKmsKey{construct_id.replace('-','')}", kms_key_id_for_cluster)
                except Exception as e:
                    logger.error(f"Failed to import Aurora cluster KMS key {kms_key_id_for_cluster}: {e}")
            else:
                logger.warning(f"enable_custom_kms_encryption is True for {cluster_identifier} but kms_key_id is missing.")


        # --- Assemble Cluster Properties ---
        cluster_props = {
            "engine": aurora_engine,
            "credentials": db_credentials,
            "cluster_identifier": cluster_identifier,
            "default_database_name": self.config.get("default_database_name"),
            "instance_props": { # Props for instances in the cluster
                "vpc": resolved_vpc,
                "vpc_subnets": rds_subnet_selection,
                "security_groups": db_security_groups,
                # Instance type for provisioned instances (if not fully serverless)
                "instance_type": ec2.InstanceType(instances_cfg.get("instance_type")) if instances_cfg.get("instance_type") and not serverless_v2_scaling_config else (ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.SMALL) if serverless_v2_scaling_config else None), # Default for serverless or if mixed
                "auto_minor_version_upgrade": instances_cfg.get("auto_minor_version_upgrade_instances", True),
                "publicly_accessible": instances_cfg.get("publicly_accessible_instances", False),
                "parameter_group": instance_parameter_group, # Instance-level PG
                "ca_certificate": rds.CaCertificate.identifier(ca_id) if (ca_id := instances_cfg.get("ca_certificate_identifier_instances")) else None,
                "enable_performance_insights": instances_cfg.get("enable_performance_insights_instances", False),
                "performance_insight_retention": rds.PerformanceInsightRetention.DEFAULT if instances_cfg.get("enable_performance_insights_instances", False) else None,
                # performance_insight_encryption_key for instances if needed
            },
            "instances": num_instances if not serverless_v2_scaling_config and num_instances > 0 else (1 if serverless_v2_scaling_config and instances_cfg.get("instance_type") == "db.serverless" else None), # Number of instances
            "port": self.config.get("port", default_engine_port),
            "parameter_group": cluster_pg, # Cluster-level PG
            "cloudwatch_logs_exports": self.config.get("monitoring", {}).get("cloudwatch_logs_exports"),
            "cloudwatch_logs_retention": self._get_retention_enum(self.config.get("monitoring", {}).get("cloudwatch_logs_retention_days")), # For logs exported by cluster
            "copy_tags_to_snapshot": self.config.get("copy_tags_to_snapshot", True),
            "deletion_protection": self.config.get("deletion_protection", False),
            "iam_authentication": self.config.get("iam_database_authentication_enabled", False),
            "storage_encrypted": self.config.get("storage_encrypted", True),
            "kms_key": cluster_kms_key,
            "backup": backup_props,
            "backtrack_window": backtrack_window,
            "serverless_v2_scaling_configuration": serverless_v2_scaling_config,
            "preferred_maintenance_window": self.config.get("preferred_maintenance_window"),
            "removal_policy": RemovalPolicy.RETAIN if self.config.get("deletion_protection", False) else RemovalPolicy.SNAPSHOT,
            # Add other cluster specific properties from self.config as needed
            # e.g., s3_import_role, s3_export_role, domain, domain_role
        }
        
        # Filter out None values before passing to the constructor
        final_cluster_props = {k: v for k, v in cluster_props.items() if v is not None}
        if "instance_props" in final_cluster_props and final_cluster_props["instance_props"] is not None:
             final_cluster_props["instance_props"] = {k:v for k,v in final_cluster_props["instance_props"].items() if v is not None}


        # --- Create the DB Cluster ---
        self.db_cluster = rds.DatabaseCluster(
            self, "DatabaseClusterResource",
            **final_cluster_props
        )
        logger.info(f"RDS DatabaseCluster resource '{self.db_cluster.cluster_identifier}' defined.")

        # Apply Tags
        if "tags" in self.config:
            for key, value in self.config["tags"].items():
                Tags.of(self.db_cluster).add(str(key), str(value))
        
        # Outputs
        clean_construct_id = construct_id.replace("-","").replace("_","")
        CfnOutput(self, f"DbClusterIdentifierOutput{clean_construct_id}", value=self.db_cluster.cluster_identifier)
        CfnOutput(self, f"DbClusterEndpointAddressOutput{clean_construct_id}", value=self.db_cluster.cluster_endpoint.hostname)
        CfnOutput(self, f"DbClusterEndpointPortOutput{clean_construct_id}", value=self.db_cluster.cluster_endpoint.port_as_string)
        if self.db_cluster.cluster_read_endpoint:
             CfnOutput(self, f"DbClusterReadEndpointAddressOutput{clean_construct_id}", value=self.db_cluster.cluster_read_endpoint.hostname)
        
        if generated_secret_resource:
             CfnOutput(self, f"DbClusterMasterCredentialsSecretArnOutput{clean_construct_id}", value=generated_secret_resource.secret_arn)


    # --- Helper methods (some can be reused/adapted from DbInstanceStack) ---
    def _resolve_vpc_via_lookup(self, cluster_identifier: str, construct_id_suffix: str) -> ec2.IVpc:
        # ... (similar to DbInstanceStack._resolve_vpc_via_lookup) ...
        vpc_cfg = self.config.get("vpc_config", {})
        vpc_id_to_lookup = vpc_cfg.get("lookup_existing_vpc_by_id")
        safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if vpc_id_to_lookup:
            logger.info(f"For {cluster_identifier}, looking up existing VPC by ID (fallback in AuroraClusterStack): {vpc_id_to_lookup}")
            try: return ec2.Vpc.from_lookup(self, f"VpcLookupForAurora{safe_suffix}", vpc_id=vpc_id_to_lookup)
            except Exception as e: raise ValueError(f"VPC lookup by ID '{vpc_id_to_lookup}' failed for {cluster_identifier}.") from e
        else: raise ValueError(f"VPC could not be resolved for {cluster_identifier}. No IVpc passed and 'lookup_existing_vpc_by_id' not configured.")

    def _resolve_credentials(self, cluster_identifier: str, construct_id_suffix: str) -> tuple[rds.Credentials, secretsmanager.ISecret | None]:
        # ... (Can reuse or adapt DbInstanceStack._resolve_credentials) ...
        creds_config = self.config.get("credentials", {}); credential_source = creds_config.get("source", "GENERATE_NEW_SECRET").upper(); master_username = creds_config.get("master_username")
        secret_resource_for_output: secretsmanager.ISecret | None = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if not master_username:
            if credential_source == "GENERATE_NEW_SECRET": master_username = "dbadmin"; logger.warning(f"Master username not specified for {cluster_identifier} (generating secret), defaulting to '{master_username}'.")
            else: logger.warning(f"Master username not specified for {cluster_identifier} (using existing secret).")
        if credential_source == "USE_EXISTING_SECRET_ARN":
            existing_secret_arn = creds_config.get("existing_secret_arn")
            if not existing_secret_arn: raise ValueError(f"Credentials source is USE_EXISTING_SECRET_ARN but 'existing_secret_arn' is missing for {cluster_identifier}.")
            imported_secret = secretsmanager.Secret.from_secret_complete_arn(self, f"ImportedMasterUserSecretAurora{safe_suffix}", existing_secret_arn)
            secret_resource_for_output = imported_secret
            return rds.Credentials.from_secret(imported_secret, username=master_username if master_username else None), secret_resource_for_output
        elif credential_source == "GENERATE_NEW_SECRET":
            if not master_username: master_username = "dbadmin"
            gen_opts = creds_config.get("generate_new_secret_options", {}); secret_name_prefix = gen_opts.get("secret_name_prefix", f"rds/{cluster_identifier.lower()}")
            valid_secret_name = re.sub(r"[^a-zA-Z0-9/_+=.@-]", "-", secret_name_prefix.rstrip('/')); valid_secret_name = f"{valid_secret_name}/cluster-master-credentials"
            generated_secret = secretsmanager.Secret(self, f"GeneratedMasterUserSecretAurora{safe_suffix}", secret_name=valid_secret_name, generate_secret_string=secretsmanager.SecretStringGenerator(secret_string_template=json.dumps({"username": master_username}), generate_string_key="password", password_length=gen_opts.get("password_length", 16), exclude_characters=gen_opts.get("exclude_characters", "\"@/\\' ")), description=f"Master user credentials for RDS cluster {cluster_identifier}", removal_policy=RemovalPolicy.DESTROY )
            secret_resource_for_output = generated_secret
            return rds.Credentials.from_secret(generated_secret), secret_resource_for_output
        else: raise ValueError(f"Invalid credentials 'source': {credential_source} for {cluster_identifier}.")


    def _resolve_security_groups(self, vpc: ec2.IVpc, cluster_identifier: str, default_engine_port: int | None, construct_id_suffix: str) -> list[ec2.ISecurityGroup]:
        # ... (Can reuse or adapt DbInstanceStack._resolve_security_groups) ...
        vpc_cfg = self.config.get("vpc_config", {}); sg_config = vpc_cfg.get("security_group_config", {}); sg_source = sg_config.get("source", "CREATE_NEW").upper(); db_sgs = []; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if sg_source == "USE_EXISTING_IDS":
            existing_ids = sg_config.get("existing_ids", [])
            if not existing_ids: logger.warning(f"SG source USE_EXISTING_IDS for {cluster_identifier} but no 'existing_ids'. Creating default."); db_sgs.append(ec2.SecurityGroup(self, f"DefaultDbSgAurora{safe_suffix}", vpc=vpc, description=f"Default SG for {cluster_identifier}"))
            else:
                for i, sg_id in enumerate(existing_ids):
                    try: db_sgs.append(ec2.SecurityGroup.from_security_group_id(self, f"ImportedDbSgAurora{i}{safe_suffix}", sg_id))
                    except Exception as e: logger.error(f"Failed import SG {sg_id} for {cluster_identifier}: {e}")
        elif sg_source == "CREATE_NEW":
            new_sg_opts = sg_config.get("create_new_options", {}); db_sg_name = new_sg_opts.get("name", f"{cluster_identifier}-sg")
            db_sg = ec2.SecurityGroup(self, f"DbClusterSecurityGroup{safe_suffix}", vpc=vpc, security_group_name=db_sg_name, description=new_sg_opts.get("description", f"SG for RDS Cluster {cluster_identifier}"), allow_all_outbound=new_sg_opts.get("allow_all_outbound", True))
            db_port = self.config.get("port", default_engine_port);
            if db_port is None: raise ValueError(f"Cannot determine DB port for SG rules of {cluster_identifier}")
            for i, source_sg_id in enumerate(new_sg_opts.get("allow_ingress_from_sg_ids", [])):
                try: source_sg = ec2.SecurityGroup.from_security_group_id(self, f"SourceSgForAurora{i}{safe_suffix}", source_sg_id); db_sg.add_ingress_rule(source_sg, ec2.Port.tcp(db_port), f"Allow DB access from SG {source_sg_id}")
                except Exception as e: logger.error(f"Failed lookup source SG ID '{source_sg_id}': {e}")
            db_sgs.append(db_sg)
        else: raise ValueError(f"Invalid security_group_config.source: {sg_source} for {cluster_identifier}")
        if not db_sgs: db_sgs.append(ec2.SecurityGroup(self, f"FallbackDefaultDbSgAurora{safe_suffix}", vpc=vpc, description=f"Fallback Default SG for {cluster_identifier}"))
        return db_sgs

    def _resolve_cluster_parameter_group(self, cluster_identifier: str, db_engine: rds.IClusterEngine, engine_type: str, construct_id_suffix: str) -> rds.IParameterGroup | None:
        pg_cfg = self.config.get("cluster_parameter_group", {}) # Key name specific to cluster
        pg_source = pg_cfg.get("source", "DEFAULT").upper()
        safe_suffix = construct_id_suffix.replace("-","").replace("_","")

        if pg_source == "EXISTING_NAME":
            name = pg_cfg.get("name")
            if not name: raise ValueError(f"Cluster PG source is EXISTING_NAME but 'name' is missing for {cluster_identifier}")
            return rds.ParameterGroup.from_parameter_group_name(self, f"DbClusterPgImport{safe_suffix}", name)
        elif pg_source == "CREATE_NEW":
            create_opts = pg_cfg.get("create_new_options", {})
            family = create_opts.get("family") # For Aurora, family is like 'aurora-mysql8.0'
            if not family: family = self._get_aurora_parameter_group_family(engine_type, self.config.get("engine_version"))
            if not family: raise ValueError(f"Cluster PG 'family' could not be determined for {cluster_identifier}")
            
            return rds.ParameterGroup(self, f"DbClusterPgCreate{safe_suffix}", 
                                      engine=db_engine, # This needs to be the IClusterEngine
                                      name=create_opts.get("name_prefix", f"{cluster_identifier.lower().replace('_','-')}-cluster-pg"),
                                      description=create_opts.get("description", f"Custom Cluster PG for {cluster_identifier}"),
                                      parameters=create_opts.get("parameters"))
        return None 

    def _resolve_instance_parameter_group(self, cluster_identifier: str, db_engine: rds.IClusterEngine, engine_type: str, construct_id_suffix: str, instance_pg_config:dict ) -> rds.IParameterGroup | None:
        # Similar to _resolve_cluster_parameter_group but for instance-level PG in a cluster
        pg_source = instance_pg_config.get("source", "DEFAULT").upper()
        safe_suffix = f"Inst{construct_id_suffix.replace('-','').replace('_','')}"

        if pg_source == "EXISTING_NAME":
            name = instance_pg_config.get("name")
            if not name: raise ValueError(f"Instance PG source is EXISTING_NAME but 'name' is missing for {cluster_identifier}")
            return rds.ParameterGroup.from_parameter_group_name(self, f"DbInstPgImport{safe_suffix}", name)
        elif pg_source == "CREATE_NEW":
            create_opts = instance_pg_config.get("create_new_options", {})
            family = create_opts.get("family") 
            if not family: family = self._get_aurora_parameter_group_family(engine_type, self.config.get("engine_version")) # Same family for cluster and instance
            if not family: raise ValueError(f"Instance PG 'family' could not be determined for {cluster_identifier}")
            
            return rds.ParameterGroup(self, f"DbInstPgCreate{safe_suffix}", 
                                      engine=db_engine, # Use cluster engine
                                      name=create_opts.get("name_prefix", f"{cluster_identifier.lower().replace('_','-')}-instance-pg"),
                                      description=create_opts.get("description", f"Custom Instance PG for {cluster_identifier}"),
                                      parameters=create_opts.get("parameters"))
        return None

    def _parse_performance_insights(self, monitoring_config: dict, cluster_identifier: str, construct_id_suffix: str) -> tuple[rds.PerformanceInsightRetention | None, kms.IKey | None]:
        # ... (Can reuse or adapt DbInstanceStack._parse_performance_insights) ...
        pi_kms_key = None; pi_retention = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        # For clusters, PI is often configured per-instance. This helper might be for cluster-level defaults if applicable
        # or needs to be called for each instance. The L2 DatabaseCluster.instance_props applies to all.
        if monitoring_config.get("enable_performance_insights_instances", monitoring_config.get("enable_performance_insights", False)): # Check instance specific first
            pi_retention = rds.PerformanceInsightRetention.DEFAULT 
            kms_key_id = monitoring_config.get("performance_insights_kms_key_id_instances", monitoring_config.get("performance_insights_kms_key_id"))
            if kms_key_id:
                try: pi_kms_key = kms.Key.from_key_arn(self, f"PerfInsightsKmsKeyAurora{safe_suffix}", kms_key_id)
                except Exception as e: logger.error(f"Failed import PI KMS key {kms_key_id}: {e}")
        return pi_retention, pi_kms_key


    def _parse_enhanced_monitoring(self, monitoring_config: dict, cluster_identifier: str, construct_id_suffix: str) -> tuple[Duration | None, iam.IRole | None]:
        # ... (Can reuse or adapt DbInstanceStack._parse_enhanced_monitoring) ...
        # Enhanced monitoring is typically per-instance.
        interval = None; role = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        # This would apply if there was a cluster-level enhanced monitoring setting,
        # but it's usually configured on the rds.InstanceProps for the cluster instances.
        # For now, this helper is less relevant at cluster stack level unless config structure changes.
        return interval, role


    def _get_aurora_cluster_engine(self, engine_type_str: str, rds_config: dict) -> rds.IClusterEngine:
        version_str = rds_config.get("engine_version")
        if not version_str:
            raise ValueError(f"engine_version is required for Aurora engine type {engine_type_str}")

        if engine_type_str == "AURORA_MYSQL":
            # Map your version string to CDK's AuroraMysqlEngineVersion
            # Example: "5.7.mysql_aurora.2.11.1" -> rds.AuroraMysqlEngineVersion.VER_2_11_1
            #          "8.0.mysql_aurora.3.06.0" -> rds.AuroraMysqlEngineVersion.VER_8_0_3_06_0 (check exact enum name)
            aurora_mysql_version_map = {
                "8.0.mysql_aurora.3.06.0": rds.AuroraMysqlEngineVersion.VER_8_0_AURORA_3_06_0, # Check exact enum name in CDK docs
                "8.0.mysql_aurora.3.05.0": rds.AuroraMysqlEngineVersion.VER_8_0_AURORA_3_05_0,
                "5.7.mysql_aurora.2.11.2": rds.AuroraMysqlEngineVersion.VER_2_11_2,
                # Add more versions as needed
            }
            engine_ver = aurora_mysql_version_map.get(version_str)
            if not engine_ver: raise ValueError(f"Unsupported Aurora MySQL version: {version_str}")
            return rds.DatabaseClusterEngine.aurora_mysql(version=engine_ver)

        elif engine_type_str == "AURORA_POSTGRESQL":
            # Example: "15.5" -> rds.AuroraPostgresEngineVersion.VER_15_5
            aurora_pg_version_map = {
                "16.2": rds.AuroraPostgresEngineVersion.VER_16_2, # Check exact enum name
                "15.6": rds.AuroraPostgresEngineVersion.VER_15_6,
                "15.5": rds.AuroraPostgresEngineVersion.VER_15_5,
                # Add more versions
            }
            engine_ver = aurora_pg_version_map.get(version_str)
            if not engine_ver: raise ValueError(f"Unsupported Aurora PostgreSQL version: {version_str}")
            return rds.DatabaseClusterEngine.aurora_postgres(version=engine_ver)
        else:
            raise ValueError(f"Unsupported Aurora engine_type for AuroraClusterStack: {engine_type_str}")

    def _get_default_aurora_port(self, engine_type_str: str) -> int | None:
        if engine_type_str == "AURORA_MYSQL": return 3306
        if engine_type_str == "AURORA_POSTGRESQL": return 5432
        return None

    def _get_aurora_parameter_group_family(self, engine_type: str, engine_version: str | None) -> str | None:
        """
        Determines the CloudFormation parameter group family string for Aurora.
        Examples: aurora-mysql8.0, aurora-postgresql15
        """
        if not engine_version: return None
        
        # Aurora versions are often like "major.minor.aurora.cluster_version.patch"
        # Or for PG "major.minor"
        # The family usually just needs the engine name and major version.
        
        major_version_part = engine_version.split('.')[0] # Get the '8' from '8.0.mysql_aurora.3.06.0' or '15' from '15.5'

        if engine_type == "AURORA_MYSQL":
            # For Aurora MySQL 8.0, family is 'aurora-mysql8.0'
            # For Aurora MySQL 5.7, family is 'aurora-mysql5.7'
            if engine_version.startswith("8.0"): return "aurora-mysql8.0"
            if engine_version.startswith("5.7"): return "aurora-mysql5.7"
            # Add other specific Aurora MySQL family mappings if needed
            logger.warning(f"Could not precisely determine Aurora MySQL PG family for version {engine_version}. Using generic.")
            return f"aurora-mysql{major_version_part}.0" # Fallback, might not be exact

        if engine_type == "AURORA_POSTGRESQL":
            # For Aurora PostgreSQL 15, family is 'aurora-postgresql15'
            return f"aurora-postgresql{major_version_part}"
        
        logger.warning(f"Could not determine Aurora PG family for {engine_type} {engine_version}.")
        return None

    def _get_retention_enum(self, days: int | None) -> logs.RetentionDays | None: 
        # ... (Copied from DbInstanceStack, ensure it's complete) ...
        if days is None: return None
        mapping = {1: logs.RetentionDays.ONE_DAY, 7: logs.RetentionDays.ONE_WEEK, 30:logs.RetentionDays.ONE_MONTH, 365: logs.RetentionDays.ONE_YEAR} 
        return mapping.get(days)
