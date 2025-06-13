import logging
import json
import re
import typing

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
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s: %(message)s')

class AuroraClusterStack(NestedStack):
    db_cluster: rds.IDatabaseCluster

    def __init__(self, scope: Construct, construct_id: str, *,
                 rds_config: dict,
                 vpc: ec2.IVpc | None,
                 security_group_config: typing.Dict,
                 public_cfn_subnets: typing.List[ec2.CfnSubnet],
                 private_cfn_subnets: typing.List[ec2.CfnSubnet],
                 isolated_cfn_subnets: typing.List[ec2.CfnSubnet],
                 created_iam_roles_map: typing.Dict[str, iam.IRole] = None,
                 **kwargs) -> None:

        nested_stack_valid_kwargs = {k: v for k, v in kwargs.items() if k in ['env', 'stack_name', 'synthesizer', 'description', 'termination_protection']}
        super().__init__(scope, construct_id, **nested_stack_valid_kwargs)

        self.config = rds_config
        self.passed_vpc = vpc
        self.security_group_config = security_group_config
        self.public_cfn_subnets = public_cfn_subnets
        self.private_cfn_subnets = private_cfn_subnets
        self.isolated_cfn_subnets = isolated_cfn_subnets
        self.created_iam_roles_map = created_iam_roles_map if created_iam_roles_map is not None else {}

        cluster_identifier = self.config.get('cluster_identifier')
        if not cluster_identifier:
            base_id = construct_id.replace("AuroraClusterNestedStack", "").replace("RdsNestedStack", "").lower()
            if not base_id:
                base_id = Stack.of(self).stack_name.lower()
            cluster_identifier = f"{base_id}-cluster"[:60]
            cluster_identifier = re.sub(r"[^a-z0-9-]", "-", cluster_identifier)
            logger.warning(f"RDS cluster_identifier not provided for {construct_id}, generated: {cluster_identifier}")

        self.cluster_identifier = cluster_identifier

        engine_type_str = self.config.get("engine_type", "").upper()
        if not engine_type_str.startswith("AURORA_"):
            raise ValueError(f"engine_type '{engine_type_str}' is not a valid Aurora engine for AuroraClusterStack.")

        logger.info(f"AuroraClusterStack '{construct_id}': Initializing for RDS Cluster ID '{self.cluster_identifier}' with engine type '{engine_type_str}'.")

        resolved_vpc: ec2.IVpc | None = self.passed_vpc
        if not resolved_vpc:
            logger.info(f"No VPC object passed for {self.cluster_identifier}, attempting lookup based on its own config.")
            resolved_vpc = self._resolve_vpc_via_lookup(self.cluster_identifier, construct_id)
        if not resolved_vpc:
            raise ValueError(f"VPC could not be resolved for RDS cluster {self.cluster_identifier}.")

        aurora_engine = self._get_aurora_cluster_engine(engine_type_str, self.config)
        default_engine_port = self._get_default_aurora_port(engine_type_str)

        db_credentials, generated_secret_resource = self._resolve_credentials(self.cluster_identifier, construct_id)

        vpc_cfg_for_subnet = self.config.get("vpc_config", {})
        subnet_type_str = vpc_cfg_for_subnet.get("subnet_type_for_rds", "PRIVATE_WITH_EGRESS").upper()

        subnet_ids_for_db_group: typing.List[str] = []
        l2_subnets_for_instance_props: typing.List[ec2.ISubnet] = []

        if self.public_cfn_subnets or self.private_cfn_subnets or self.isolated_cfn_subnets:
            logger.info(f"Using explicitly passed CfnSubnets for DB Subnet Group for {self.cluster_identifier}.")
            target_cfn_subnets_for_rds = []
            if subnet_type_str == "PUBLIC":
                target_cfn_subnets_for_rds = self.public_cfn_subnets
            elif subnet_type_str == "PRIVATE_WITH_EGRESS":
                target_cfn_subnets_for_rds = self.private_cfn_subnets
            elif subnet_type_str == "PRIVATE_ISOLATED":
                target_cfn_subnets_for_rds = self.isolated_cfn_subnets
            else:
                logger.warning(f"Invalid subnet_type_for_rds: '{subnet_type_str}'. Defaulting to PRIVATE_WITH_EGRESS CfnSubnets.")
                target_cfn_subnets_for_rds = self.private_cfn_subnets

            if not target_cfn_subnets_for_rds:
                raise ValueError(f"No {subnet_type_str.lower()} CfnSubnets found for RDS cluster {self.cluster_identifier} in newly created VPC '{resolved_vpc.vpc_id}'.")

            subnet_ids_for_db_group = [s.ref for s in target_cfn_subnets_for_rds]
            l2_subnets_for_instance_props = [ec2.Subnet.from_subnet_id(self, f"InstPropsSubnet{i}{construct_id}", s_id) for i, s_id in enumerate(subnet_ids_for_db_group)]
        else:
            explicit_subnet_ids = vpc_cfg_for_subnet.get("subnet_ids_for_rds")
            if explicit_subnet_ids:
                logger.info(f"Using explicit subnet_ids_for_rds from config for {self.cluster_identifier} (existing VPC).")
                if not isinstance(explicit_subnet_ids, list) or not all(isinstance(s, str) for s in explicit_subnet_ids):
                    raise ValueError(f"subnet_ids_for_rds must be a list of strings for existing VPC. Found: {explicit_subnet_ids}")
                subnet_ids_for_db_group = explicit_subnet_ids
                l2_subnets_for_instance_props = [ec2.Subnet.from_subnet_id(self, f"InstPropsSubnet{i}{construct_id}", s_id) for i, s_id in enumerate(subnet_ids_for_db_group)]
            else:
                logger.info(f"Using L2 select_subnets for DB Subnet Group for {self.cluster_identifier} in existing VPC (no explicit IDs).")
                selected_l2_subnets: typing.List[ec2.ISubnet]
                if subnet_type_str == "PUBLIC":
                    selected_l2_subnets = resolved_vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC).subnets
                elif subnet_type_str == "PRIVATE_WITH_EGRESS":
                    selected_l2_subnets = resolved_vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnets
                elif subnet_type_str == "PRIVATE_ISOLATED":
                    selected_l2_subnets = resolved_vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnets
                else:
                    logger.warning(f"Invalid subnet_type_for_rds: '{subnet_type_str}'. Falling back to PrivateWithEgress L2 Subnets.")
                    selected_l2_subnets = resolved_vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnets

                if not selected_l2_subnets:
                    raise ValueError(f"No {subnet_type_str.lower()} L2 subnets found for RDS cluster {self.cluster_identifier} in existing VPC '{resolved_vpc.vpc_id}'.")

                subnet_ids_for_db_group = [s.subnet_id for s in selected_l2_subnets]
                l2_subnets_for_instance_props = selected_l2_subnets

        if not subnet_ids_for_db_group or not l2_subnets_for_instance_props:
            raise ValueError(f"Critical: No subnets resolved for RDS cluster {self.cluster_identifier}.")

        db_subnet_group = rds.CfnDBSubnetGroup(
            self, f"DbSubnetGroup{construct_id}",
            db_subnet_group_description=f"Subnet group for {self.cluster_identifier}",
            subnet_ids=subnet_ids_for_db_group,
            db_subnet_group_name=f"{self.cluster_identifier.lower().replace('_','-')}-sng"
        )
        logger.info(f"Created DB Subnet Group '{db_subnet_group.db_subnet_group_name}' for RDS in '{subnet_type_str}' subnets.")

        db_security_groups = self._resolve_security_groups(resolved_vpc, self.cluster_identifier, default_engine_port, construct_id, self.security_group_config)

        cluster_pg = self._resolve_cluster_parameter_group(self.cluster_identifier, aurora_engine, engine_type_str, construct_id)
        instance_pg_config = self.config.get("instance_parameter_group", {})
        instance_parameter_group = None
        if instance_pg_config:
            instance_parameter_group = self._resolve_instance_parameter_group(self.cluster_identifier, aurora_engine, engine_type_str, construct_id, instance_pg_config)

        instances_cfg = self.config.get("instances_config", {})
        num_instances = instances_cfg.get("count", 1)

        serverless_v2_scaling_config = None
        if self.config.get("enable_serverless_v2_scaling", False):
            min_acu = self.config.get("serverless_v2_min_capacity_acu")
            max_acu = self.config.get("serverless_v2_max_capacity_acu")
            if min_acu is not None and max_acu is not None:
                serverless_v2_scaling_config = rds.ServerlessV2ScalingConfiguration(min_capacity=min_acu, max_capacity=max_acu)
                logger.info(f"Configuring Serverless V2 scaling for {self.cluster_identifier}: MinACU={min_acu}, MaxACU={max_acu}")

        common_instance_props = {
            "instance_type": ec2.InstanceType(instances_cfg.get("instance_type", "db.t3.medium")),
            "auto_minor_version_upgrade": instances_cfg.get("auto_minor_version_upgrade_instances", True),
            "publicly_accessible": instances_cfg.get("publicly_accessible_instances", False),
            "parameter_group": instance_parameter_group,
            "enable_performance_insights": instances_cfg.get("enable_performance_insights_instances", False),
            "performance_insight_retention": rds.PerformanceInsightRetention.DEFAULT if instances_cfg.get("enable_performance_insights_instances", False) else None,
            "vpc": resolved_vpc,
            "vpc_subnets": ec2.SubnetSelection(subnets=l2_subnets_for_instance_props),
            "security_groups": db_security_groups,
        }
        common_instance_props = {k: v for k, v in common_instance_props.items() if v is not None}

        backup_props = None
        if self.config.get("enable_automated_backups", True):
            retention_days = self.config.get("backup_retention_days", 7)
            if retention_days > 0:
                backup_props = rds.BackupProps(retention=Duration.days(retention_days), preferred_window=self.config.get("preferred_backup_window"))

        backtrack_window = None
        if self.config.get("enable_backtrack", False):
            hours = self.config.get("backtrack_window_hours")
            if isinstance(hours, int) and hours > 0:
                backtrack_window = Duration.hours(hours)

        cluster_kms_key = None
        if self.config.get("storage_encrypted", True) and self.config.get("enable_custom_kms_encryption", False):
            kms_key_id_for_cluster = self.config.get("kms_key_id")
            if kms_key_id_for_cluster:
                try:
                    cluster_kms_key = kms.Key.from_key_arn(self, f"AuroraClusterKmsKey{construct_id.replace('-','')}", kms_key_id_for_cluster)
                except Exception as e:
                    logger.error(f"Failed to import Aurora cluster KMS key {kms_key_id_for_cluster}: {e}")

        cluster_props = {
            "engine": aurora_engine,
            "credentials": db_credentials,
            "cluster_identifier": self.cluster_identifier,
            "default_database_name": self.config.get("default_database_name"),
            "instance_props": common_instance_props,
            "instances": num_instances if not serverless_v2_scaling_config and num_instances > 0 else None,
            "port": self.config.get("port", default_engine_port),
            "parameter_group": cluster_pg,
            "cloudwatch_logs_exports": self.config.get("monitoring", {}).get("cloudwatch_logs_exports"),
            "cloudwatch_logs_retention": self._get_retention_enum(self.config.get("monitoring", {}).get("cloudwatch_logs_retention_days")),
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
        }

        final_cluster_props = {k: v for k, v in cluster_props.items() if v is not None}

        self.db_cluster = rds.DatabaseCluster(
            self, "DatabaseClusterResource",
            **final_cluster_props
        )
        logger.info(f"RDS DatabaseCluster resource '{self.db_cluster.cluster_identifier}' defined.")

        if "tags" in self.config:
            for key, value in self.config["tags"].items():
                Tags.of(self.db_cluster).add(str(key), str(value))

        clean_construct_id = construct_id.replace("-","").replace("_","")
        CfnOutput(self, f"DbClusterIdentifierOutput{clean_construct_id}", value=self.db_cluster.cluster_identifier)
        CfnOutput(self, f"DbClusterEndpointAddressOutput{clean_construct_id}", value=self.db_cluster.cluster_endpoint.hostname)

        # ================== FINAL FIX IS HERE ==================
        # Use Python's built-in str() function. This works on both
        # standard numbers and CDK Token objects.
        CfnOutput(self, f"DbClusterEndpointPortOutput{clean_construct_id}", value=str(self.db_cluster.cluster_endpoint.port))
        # =======================================================

        if self.db_cluster.cluster_read_endpoint:
                CfnOutput(self, f"DbClusterReadEndpointAddressOutput{clean_construct_id}", value=self.db_cluster.cluster_read_endpoint.hostname)

        if generated_secret_resource:
                CfnOutput(self, f"DbClusterMasterCredentialsSecretArnOutput{clean_construct_id}", value=generated_secret_resource.secret_arn)

    # ... (the rest of the helper methods are unchanged and correct) ...
    def _resolve_vpc_via_lookup(self, cluster_identifier: str, construct_id_suffix: str) -> ec2.IVpc:
        vpc_cfg = self.config.get("vpc_config", {}); vpc_id_to_lookup = vpc_cfg.get("lookup_existing_vpc_by_id"); safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if vpc_id_to_lookup:
            logger.info(f"For {cluster_identifier}, looking up existing VPC by ID (fallback in AuroraClusterStack): {vpc_id_to_lookup}")
            try: return ec2.Vpc.from_lookup(self, f"VpcLookupForAurora{safe_suffix}", vpc_id=vpc_id_to_lookup)
            except Exception as e: raise ValueError(f"VPC lookup by ID '{vpc_id_to_lookup}' failed for {cluster_identifier}.") from e
        else: raise ValueError(f"VPC could not be resolved for {cluster_identifier}. No IVpc passed and 'lookup_existing_vpc_by_id' not configured.")

    def _resolve_credentials(self, cluster_identifier: str, construct_id_suffix: str) -> tuple[rds.Credentials, secretsmanager.ISecret | None]:
        creds_config = self.config.get("credentials", {}); credential_source = creds_config.get("source", "GENERATE_NEW_SECRET").upper(); master_username = creds_config.get("master_username"); secret_resource_for_output: secretsmanager.ISecret | None = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if not master_username:
            if credential_source == "GENERATE_NEW_SECRET": master_username = "dbadmin"; logger.warning(f"Master username not specified for {cluster_identifier} (generating secret), defaulting to '{master_username}'.")
            else: logger.warning(f"Master username not specified for {cluster_identifier} (using existing secret).")
        if credential_source == "USE_EXISTING_SECRET_ARN":
            existing_secret_arn = creds_config.get("existing_secret_arn")
            if not existing_secret_arn: raise ValueError(f"Credentials source is USE_EXISTING_SECRET_ARN but 'existing_secret_arn' is missing for {cluster_identifier}.")
            logger.info(f"Using existing Secrets Manager secret ARN: {existing_secret_arn} for {cluster_identifier}")
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

    def _resolve_security_groups(self, vpc: ec2.IVpc, cluster_identifier: str, default_engine_port: int | None, construct_id_suffix: str, sg_config: typing.Dict) -> list[ec2.ISecurityGroup]:
        db_sgs = []
        sg_source = sg_config.get("source", "CREATE_NEW").upper(); safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        
        if sg_source == "USE_EXISTING_IDS":
            existing_ids = sg_config.get("existing_ids", [])
            if not existing_ids:
                raise ValueError(f"SG source USE_EXISTING_IDS for {cluster_identifier} but no 'existing_ids'.")
            else:
                for i, sg_id in enumerate(existing_ids):
                    try: 
                        imported_sg = ec2.SecurityGroup.from_security_group_id(self, f"ImportedDbSgAurora{i}{safe_suffix}", sg_id)
                        db_sgs.append(imported_sg)
                        logger.info(f"Imported SG {sg_id} for {cluster_identifier}.")
                    except Exception as e: 
                        logger.error(f"Failed import SG {sg_id} for {cluster_identifier}: {e}")
                        raise
        elif sg_source == "CREATE_NEW":
            new_sg_opts = sg_config.get("create_new_options", {}); db_sg_name = new_sg_opts.get("name", f"{cluster_identifier}-sg")
            
            db_sg = ec2.SecurityGroup(self, f"DbClusterSecurityGroup{safe_suffix}",
                vpc=vpc,
                security_group_name=db_sg_name,
                description=new_sg_opts.get("description", f"SG for RDS Cluster {cluster_identifier}"),
                allow_all_outbound=new_sg_opts.get("allow_all_outbound", True)
            )
            logger.info(f"Created new SG '{db_sg_name}' for {cluster_identifier}.")

            db_port = self.config.get("port", default_engine_port)
            if db_port is None: raise ValueError(f"Cannot determine DB port for SG rules of {cluster_identifier}")
            
            for i, source_sg_id in enumerate(new_sg_opts.get("allow_ingress_from_sg_ids", [])):
                try: 
                    source_sg = ec2.SecurityGroup.from_security_group_id(self, f"SourceSgForAuroraRule{i}{safe_suffix}", source_sg_id)
                    db_sg.add_ingress_rule(source_sg, ec2.Port.tcp(db_port), f"Allow DB access from SG {source_sg_id}")
                    logger.info(f"Added ingress rule from SG '{source_sg_id}'.")
                except Exception as e: 
                    logger.error(f"Failed lookup/add rule for source SG ID '{source_sg_id}': {e}")
                    raise
            
            for i, cidr in enumerate(new_sg_opts.get("allow_ingress_from_cidrs", [])):
                try:
                    db_sg.add_ingress_rule(ec2.Peer.ipv4(cidr), ec2.Port.tcp(db_port), f"Allow DB access from CIDR {cidr}")
                    logger.info(f"Added ingress rule from CIDR '{cidr}'.")
                except Exception as e:
                    logger.error(f"Failed add rule for CIDR '{cidr}': {e}")
                    raise

            if new_sg_opts.get("allow_ingress_from_self", False):
                db_sg.add_ingress_rule(db_sg, ec2.Port.all_traffic(), "Allow traffic from other members of this SG")
                logger.info(f"Added ingress rule from self to SG '{db_sg_name}'.")

            db_sgs.append(db_sg)
        else: 
            raise ValueError(f"Invalid security_group_config.source: '{sg_source}' for {cluster_identifier}.")
        
        if not db_sgs:
            raise ValueError(f"No Security Groups resolved/created for {cluster_identifier}.")

        return db_sgs

    def _resolve_cluster_parameter_group(self, cluster_identifier: str, db_engine: rds.IClusterEngine, engine_type: str, construct_id_suffix: str) -> rds.IParameterGroup | None:
        pg_cfg = self.config.get("cluster_parameter_group", {}); pg_source = pg_cfg.get("source", "DEFAULT").upper(); safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if pg_source == "EXISTING_NAME":
            name = pg_cfg.get("name")
            if not name: raise ValueError(f"Cluster PG source is EXISTING_NAME but 'name' is missing for {cluster_identifier}")
            return rds.ParameterGroup.from_parameter_group_name(self, f"DbClusterPgImport{safe_suffix}", name)
        elif pg_source == "CREATE_NEW":
            create_opts = pg_cfg.get("create_new_options", {}); family = create_opts.get("family")
            if not family: family = self._get_aurora_parameter_group_family(engine_type, self.config.get("engine_version"))
            if not family: raise ValueError(f"Cluster PG 'family' could not be determined for {cluster_identifier}")

            return rds.ParameterGroup(self, f"DbClusterPgCreate{safe_suffix}",
                                      engine=db_engine,
                                      parameter_group_name=create_opts.get("name_prefix", f"{cluster_identifier.lower().replace('_','-')}-cluster-pg"),
                                      description=create_opts.get("description", f"Custom Cluster PG for {cluster_identifier}"),
                                      parameters=create_opts.get("parameters"))
        return None

    def _resolve_instance_parameter_group(self, cluster_identifier: str, db_engine: rds.IClusterEngine, engine_type: str, construct_id_suffix: str, instance_pg_config:dict ) -> rds.IParameterGroup | None:
        pg_source = instance_pg_config.get("source", "DEFAULT").upper(); safe_suffix = f"Inst{construct_id_suffix.replace('-','').replace('_','')}"

        if pg_source == "EXISTING_NAME":
            name = instance_pg_config.get("name")
            if not name: raise ValueError(f"Instance PG source is EXISTING_NAME but 'name' is missing for {cluster_identifier}")
            return rds.ParameterGroup.from_parameter_group_name(self, f"DbInstPgImport{safe_suffix}", name)
        elif pg_source == "CREATE_NEW":
            create_opts = instance_pg_config.get("create_new_options", {}); family = create_opts.get("family")
            if not family: family = self._get_aurora_parameter_group_family(engine_type, self.config.get("engine_version"))
            if not family: raise ValueError(f"Instance PG 'family' could not be determined for {cluster_identifier}")

            return rds.ParameterGroup(self, f"DbInstPgCreate{safe_suffix}",
                                      engine=db_engine,
                                      parameter_group_name=create_opts.get("name_prefix", f"{cluster_identifier.lower().replace('_','-')}-instance-pg"),
                                      description=create_opts.get("description", f"Custom Instance PG for {cluster_identifier}"),
                                      parameters=create_opts.get("parameters"))
        return None

    def _get_aurora_cluster_engine(self, engine_type_str: str, rds_config: dict) -> rds.IClusterEngine:
        version_str = rds_config.get("engine_version")
        if not version_str:
            raise ValueError(f"engine_version is required for Aurora engine type {engine_type_str}")

        if engine_type_str == "AURORA_MYSQL":
            aurora_mysql_version_map = {
                "8.0.mysql_aurora.3.06.0": rds.AuroraMysqlEngineVersion.VER_3_06_0,
                "8.0.mysql_aurora.3.05.0": rds.AuroraMysqlEngineVersion.VER_3_05_0,
                "5.7.mysql_aurora.2.11.2": rds.AuroraMysqlEngineVersion.VER_2_11_2,
            }
            engine_ver = aurora_mysql_version_map.get(version_str)
            if not engine_ver: raise ValueError(f"Unsupported Aurora MySQL version: {version_str}")
            return rds.DatabaseClusterEngine.aurora_mysql(version=engine_ver)

        elif engine_type_str == "AURORA_POSTGRESQL":
            aurora_pg_version_map = {
                "16.2": rds.AuroraPostgresEngineVersion.VER_16_2,
                "15.6": rds.AuroraPostgresEngineVersion.VER_15_6,
                "15.5": rds.AuroraPostgresEngineVersion.VER_15_5,
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
        if not engine_version: return None
        major_version_part = engine_version.split('.')[0]
        if engine_type == "AURORA_MYSQL":
            if engine_version.startswith("8.0"): return "aurora-mysql8.0"
            if engine_version.startswith("5.7"): return "aurora-mysql5.7"
            logger.warning(f"Could not precisely determine Aurora MySQL PG family for version {engine_version}.")
            return f"aurora-mysql{major_version_part}.0"
        if engine_type == "AURORA_POSTGRESQL":
            return f"aurora-postgresql{major_version_part}"
        logger.warning(f"Could not determine Aurora PG family for {engine_type} {engine_version}.")
        return None

    def _get_retention_enum(self, days: int | None) -> logs.RetentionDays | None:
        if days is None: return None
        mapping = {
            1: logs.RetentionDays.ONE_DAY, 3: logs.RetentionDays.THREE_DAYS, 5: logs.RetentionDays.FIVE_DAYS,
            7: logs.RetentionDays.ONE_WEEK, 14: logs.RetentionDays.TWO_WEEKS, 30: logs.RetentionDays.ONE_MONTH,
            60: logs.RetentionDays.TWO_MONTHS, 90: logs.RetentionDays.THREE_MONTHS, 120: logs.RetentionDays.FOUR_MONTHS,
            150: logs.RetentionDays.FIVE_MONTHS, 180: logs.RetentionDays.SIX_MONTHS, 365: logs.RetentionDays.ONE_YEAR,
            400: logs.RetentionDays.THIRTEEN_MONTHS, 545: logs.RetentionDays.EIGHTEEN_MONTHS, 731: logs.RetentionDays.TWO_YEARS,
            1827: logs.RetentionDays.FIVE_YEARS, 3653: logs.RetentionDays.TEN_YEARS
        }
        if days in mapping: return mapping[days]
        logger.warning(f"Retention period {days} days not mapped. Using closest standard.")
        if days > 1: return logs.RetentionDays.ONE_DAY
        return None