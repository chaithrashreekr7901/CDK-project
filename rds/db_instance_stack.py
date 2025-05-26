# CRMP-PROJECT/cdk_project/rds/db_instance_stack.py
import logging
import json 
import re 
from aws_cdk import (
    NestedStack, Tags, RemovalPolicy, Duration, CfnOutput, Stack, Aws, Fn,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam, 
    aws_kms as kms  
)
from constructs import Construct

logger = logging.getLogger(__name__)

class DbInstanceStack(NestedStack):
    db_instance: rds.IDatabaseInstance 

    def __init__(self, scope: Construct, construct_id: str, 
                 rds_config: dict, 
                 vpc: ec2.IVpc | None, 
                 **kwargs) -> None:
        
        # Explicitly remove rds_config and vpc from kwargs if they are present,
        # to prevent them from being passed to the NestedStack base class.
        # This is a defensive measure, as they are named args and shouldn't be in kwargs.
        kwargs.pop('rds_config', None) 
        kwargs.pop('vpc', None)

        super().__init__(scope, construct_id, **kwargs) # Pass through only relevant kwargs

        self.config = rds_config # Now assign after super call
        # self.passed_vpc = vpc # Store the passed vpc object if needed for clarity

        instance_identifier = self.config.get('instance_identifier')
        if not instance_identifier:
            base_id = construct_id.replace("RdsNestedStack", "") 
            instance_identifier = f"{Stack.of(self).stack_name.lower()}-{base_id.lower()}"[:60]
            instance_identifier = re.sub(r"[^a-z0-9-]", "-", instance_identifier)
            logger.warning(f"RDS instance_identifier not provided for {construct_id}, generated: {instance_identifier}")
        
        engine_type = self.config.get("engine_type", "").upper()
        if not engine_type:
            raise ValueError(f"engine_type is required in rds_config for {instance_identifier}")

        logger.info(f"DbInstanceStack '{construct_id}': Initializing for RDS instance ID '{instance_identifier}' with engine type '{engine_type}'.")

        # --- Resolve VPC ---
        resolved_vpc: ec2.IVpc | None = vpc # Use the passed VPC object directly
        if not resolved_vpc: 
            logger.info(f"No VPC object passed for {instance_identifier}, attempting lookup based on its own config.")
            resolved_vpc = self._resolve_vpc_via_lookup(instance_identifier, construct_id)
        
        if not resolved_vpc:
             raise ValueError(f"VPC could not be resolved for RDS instance {instance_identifier}. Ensure 'vpc_config' is correctly set with 'lookup_existing_vpc_by_id' or that an IVpc is passed from parent.")
        # --- End VPC Resolution ---

        db_engine = self._get_db_instance_engine(engine_type, self.config)
        default_engine_port = self._get_default_port(engine_type)
        
        db_credentials, generated_secret_resource = self._resolve_credentials(instance_identifier, construct_id)
        
        vpc_cfg_for_subnet = self.config.get("vpc_config", {}) 
        subnet_type_str = vpc_cfg_for_subnet.get("subnet_type_for_rds", "PRIVATE_WITH_EGRESS").upper()
        rds_subnet_type = getattr(ec2.SubnetType, subnet_type_str, ec2.SubnetType.PRIVATE_WITH_EGRESS)
        if not hasattr(ec2.SubnetType, subnet_type_str): logger.warning(f"Invalid subnet_type_for_rds: '{subnet_type_str}'. Defaulting to PRIVATE_WITH_EGRESS.")
        
        rds_subnet_selection = ec2.SubnetSelection(subnet_type=rds_subnet_type)
        
        db_security_groups = self._resolve_security_groups(resolved_vpc, instance_identifier, default_engine_port, construct_id)
        parameter_group = self._resolve_parameter_group(instance_identifier, db_engine, engine_type, construct_id)
        option_group = self._resolve_option_group(instance_identifier, db_engine, construct_id)
        storage_type_str = self.config.get("storage_type", "gp3").upper()
        rds_storage_type = getattr(rds.StorageType, storage_type_str, rds.StorageType.GP3)
        if storage_type_str not in [st.value for st in rds.StorageType]: logger.warning(f"Potentially invalid storage_type: {storage_type_str}.")

        max_allocated_storage = None
        if self.config.get("enable_storage_autoscaling", False):
            max_allocated_storage = self.config.get("max_allocated_storage_gb")
            if max_allocated_storage is not None and max_allocated_storage <= self.config.get("allocated_storage_gb", 0):
                logger.warning(f"max_allocated_storage_gb for {instance_identifier} must be > allocated_storage_gb. Disabling autoscaling.")
                max_allocated_storage = None

        monitoring_config = self.config.get("monitoring", {})
        pi_retention, pi_kms_key = self._parse_performance_insights(monitoring_config, instance_identifier, construct_id)
        monitoring_interval, monitoring_role = self._parse_enhanced_monitoring(monitoring_config, instance_identifier, construct_id)
        if monitoring_config.get("enable_devops_guru"): logger.info(f"DevOps Guru requested for {instance_identifier}. Ensure enabled in account.")

        backup_retention = Duration.days(0); preferred_backup_window = None
        if self.config.get("enable_automated_backups", True):
            backup_retention_days_val = self.config.get("backup_retention_days", 7)
            if backup_retention_days_val > 0: 
                 backup_retention = Duration.days(backup_retention_days_val)
                 preferred_backup_window = self.config.get("preferred_backup_window")
        
        replicated_backups = None
        if self.config.get("enable_backup_replication", False) and backup_retention.to_days() > 0 :
            rep_region = self.config.get("replicate_automated_backups_to_region")
            if rep_region:
                rep_kms_arn = self.config.get("replicated_automated_backups_kms_key_arn")
                rep_kms_key = kms.Key.from_key_arn(self, f"ReplBackupKmsKey{construct_id.replace('-','')}", rep_kms_arn) if rep_kms_arn else None
                replicated_backups = [rds.BackupProps(region=rep_region, kms_key=rep_kms_key)]
            else: logger.warning(f"Backup replication enabled for {instance_identifier} but target region missing.")
        
        storage_kms_key = None
        if self.config.get("storage_encrypted", True) and self.config.get("enable_custom_kms_encryption", False):
            kms_key_id_for_storage = self.config.get("kms_key_id")
            if kms_key_id_for_storage:
                try: storage_kms_key = kms.Key.from_key_arn(self, f"DbStorageKmsKey{construct_id.replace('-','')}", kms_key_id_for_storage)
                except Exception as e: logger.error(f"Failed import storage KMS key {kms_key_id_for_storage}: {e}")
            else: logger.warning(f"Custom KMS encryption enabled for {instance_identifier} but kms_key_id missing.")

        final_db_port = self.config.get("port", default_engine_port)
        if final_db_port is None: raise ValueError(f"Port not specified/determinable for {engine_type}")

        instance_props = {
            "engine": db_engine, "credentials": db_credentials,
            "instance_type": ec2.InstanceType(self.config.get("instance_type", "db.t3.micro")),
            "vpc": resolved_vpc, 
            "vpc_subnets": rds_subnet_selection, "security_groups": db_security_groups,
            "instance_identifier": instance_identifier, "database_name": self.config.get("database_name"),
            "allocated_storage": self.config.get("allocated_storage_gb", 20),
            "max_allocated_storage": max_allocated_storage, "storage_type": rds_storage_type,
            "iops": self.config.get("iops") if storage_type_str in ["IO1", "IO2", "GP3"] else None,
            "storage_throughput": self.config.get("storage_throughput") if storage_type_str == "GP3" else None,
            "port": final_db_port, "multi_az": self.config.get("multi_az_deployment", False),
            "availability_zone": self.config.get("availability_zone") if not self.config.get("multi_az_deployment", False) and self.config.get("availability_zone") else None,
            "backup_retention": backup_retention, "preferred_backup_window": preferred_backup_window,
            "preferred_maintenance_window": self.config.get("preferred_maintenance_window"),
            "auto_minor_version_upgrade": self.config.get("auto_minor_version_upgrade"),
            "allow_major_version_upgrade": self.config.get("allow_major_version_upgrade", False),
            "parameter_group": parameter_group, "option_group": option_group,
            "deletion_protection": self.config.get("deletion_protection", False),
            "publicly_accessible": self.config.get("publicly_accessible", False),
            "copy_tags_to_snapshot": self.config.get("copy_tags_to_snapshot", True),
            "cloudwatch_logs_exports": monitoring_config.get("cloudwatch_logs_exports"),
            "performance_insight_retention": pi_retention,
            "performance_insight_encryption_key": pi_kms_key,
            "monitoring_interval": monitoring_interval, "monitoring_role": monitoring_role,
            "iam_authentication": self.config.get("iam_database_authentication_enabled", False),
            "storage_encrypted": self.config.get("storage_encrypted", True),
            "kms_key": storage_kms_key,
            "removal_policy": RemovalPolicy.RETAIN if self.config.get("deletion_protection", False) else RemovalPolicy.SNAPSHOT,
            "license_model": self._get_license_model(engine_type, self.config.get("license_model")),
            "replicate_automated_backups": replicated_backups
        }
        final_instance_props = {k: v for k, v in instance_props.items() if v is not None}
        self.db_instance = rds.DatabaseInstance(self, "DatabaseInstanceResource", **final_instance_props)
        logger.info(f"RDS DatabaseInstance resource '{self.db_instance.instance_identifier}' defined.")
        if "tags" in self.config:
            for key, value in self.config["tags"].items(): Tags.of(self.db_instance).add(str(key), str(value))
        
        clean_construct_id = construct_id.replace("-","").replace("_","")
        CfnOutput(self, f"DbInstanceIdentifierOutput{clean_construct_id}", value=self.db_instance.instance_identifier)
        CfnOutput(self, f"DbInstanceEndpointAddressOutput{clean_construct_id}", value=self.db_instance.db_instance_endpoint_address)
        CfnOutput(self, f"DbInstanceEndpointPortOutput{clean_construct_id}", value=self.db_instance.db_instance_endpoint_port)
        if generated_secret_resource : CfnOutput(self, f"DbMasterCredentialsSecretArnOutput{clean_construct_id}", value=generated_secret_resource.secret_arn)

    def _resolve_vpc_via_lookup(self, instance_identifier: str, construct_id_suffix: str) -> ec2.IVpc:
        vpc_cfg = self.config.get("vpc_config", {})
        vpc_id_to_lookup = vpc_cfg.get("lookup_existing_vpc_by_id")
        safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if vpc_id_to_lookup:
            logger.info(f"For {instance_identifier}, looking up existing VPC by ID (fallback in DbInstanceStack): {vpc_id_to_lookup}")
            try:
                return ec2.Vpc.from_lookup(self, f"VpcLookupForRds{safe_suffix}", vpc_id=vpc_id_to_lookup)
            except Exception as e:
                 logger.error(f"VPC lookup by ID '{vpc_id_to_lookup}' failed for {instance_identifier}: {e}", exc_info=True)
                 raise ValueError(f"VPC lookup by ID '{vpc_id_to_lookup}' failed for {instance_identifier}.") from e
        else:
            raise ValueError(f"VPC could not be resolved for {instance_identifier}. No IVpc passed and 'lookup_existing_vpc_by_id' not configured in its vpc_config.")

    def _resolve_credentials(self, instance_identifier: str, construct_id_suffix: str) -> tuple[rds.Credentials, secretsmanager.ISecret | None]:
        creds_config = self.config.get("credentials", {}); credential_source = creds_config.get("source", "GENERATE_NEW_SECRET").upper(); master_username = creds_config.get("master_username")
        secret_resource_for_output: secretsmanager.ISecret | None = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if not master_username:
            if credential_source == "GENERATE_NEW_SECRET": master_username = "dbadmin"; logger.warning(f"Master username not specified for {instance_identifier} (generating secret), defaulting to '{master_username}'.")
            else: logger.warning(f"Master username not specified for {instance_identifier} (using existing secret).")
        if credential_source == "USE_EXISTING_SECRET_ARN":
            existing_secret_arn = creds_config.get("existing_secret_arn")
            if not existing_secret_arn: raise ValueError(f"Credentials source is USE_EXISTING_SECRET_ARN but 'existing_secret_arn' is missing for {instance_identifier}.")
            logger.info(f"Using existing Secrets Manager secret ARN: {existing_secret_arn} for {instance_identifier}")
            imported_secret = secretsmanager.Secret.from_secret_complete_arn(self, f"ImportedMasterUserSecret{safe_suffix}", existing_secret_arn)
            secret_resource_for_output = imported_secret
            return rds.Credentials.from_secret(imported_secret, username=master_username if master_username else None), secret_resource_for_output
        elif credential_source == "GENERATE_NEW_SECRET":
            if not master_username: master_username = "dbadmin"
            gen_opts = creds_config.get("generate_new_secret_options", {}); secret_name_prefix = gen_opts.get("secret_name_prefix", f"rds/{instance_identifier.lower()}")
            valid_secret_name = re.sub(r"[^a-zA-Z0-9/_+=.@-]", "-", secret_name_prefix.rstrip('/')); valid_secret_name = f"{valid_secret_name}/master-credentials"
            generated_secret = secretsmanager.Secret(self, f"GeneratedMasterUserSecret{safe_suffix}", secret_name=valid_secret_name, generate_secret_string=secretsmanager.SecretStringGenerator(secret_string_template=json.dumps({"username": master_username}), generate_string_key="password", password_length=gen_opts.get("password_length", 16), exclude_characters=gen_opts.get("exclude_characters", "\"@/\\' ")), description=f"Master user credentials for RDS instance {instance_identifier}", removal_policy=RemovalPolicy.DESTROY )
            secret_resource_for_output = generated_secret
            return rds.Credentials.from_secret(generated_secret), secret_resource_for_output
        else: raise ValueError(f"Invalid credentials 'source': {credential_source} for {instance_identifier}.")

    def _resolve_security_groups(self, vpc: ec2.IVpc, instance_identifier: str, default_engine_port: int | None, construct_id_suffix: str) -> list[ec2.ISecurityGroup]:
        vpc_cfg = self.config.get("vpc_config", {}); sg_config = vpc_cfg.get("security_group_config", {}); sg_source = sg_config.get("source", "CREATE_NEW").upper(); db_sgs = []; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if sg_source == "USE_EXISTING_IDS":
            existing_ids = sg_config.get("existing_ids", [])
            if not existing_ids: logger.warning(f"SG source USE_EXISTING_IDS for {instance_identifier} but no 'existing_ids'. Creating default."); db_sgs.append(ec2.SecurityGroup(self, f"DefaultDbSg{safe_suffix}", vpc=vpc, description=f"Default SG for {instance_identifier}"))
            else:
                for i, sg_id in enumerate(existing_ids):
                    try: db_sgs.append(ec2.SecurityGroup.from_security_group_id(self, f"ImportedDbSg{i}{safe_suffix}", sg_id))
                    except Exception as e: logger.error(f"Failed import SG {sg_id} for {instance_identifier}: {e}")
        elif sg_source == "CREATE_NEW":
            new_sg_opts = sg_config.get("create_new_options", {}); db_sg_name = new_sg_opts.get("name", f"{instance_identifier}-sg")
            db_sg = ec2.SecurityGroup(self, f"DbInstanceSecurityGroup{safe_suffix}", vpc=vpc, security_group_name=db_sg_name, description=new_sg_opts.get("description", f"SG for RDS {instance_identifier}"), allow_all_outbound=new_sg_opts.get("allow_all_outbound", True))
            db_port = self.config.get("port", default_engine_port);
            if db_port is None: raise ValueError(f"Cannot determine DB port for SG rules of {instance_identifier}")
            for i, source_sg_id in enumerate(new_sg_opts.get("allow_ingress_from_sg_ids", [])):
                try: source_sg = ec2.SecurityGroup.from_security_group_id(self, f"SourceSgForDb{i}{safe_suffix}", source_sg_id); db_sg.add_ingress_rule(source_sg, ec2.Port.tcp(db_port), f"Allow DB access from SG {source_sg_id}")
                except Exception as e: logger.error(f"Failed lookup source SG ID '{source_sg_id}': {e}")
            for i, cidr_ip in enumerate(new_sg_opts.get("allow_ingress_from_cidrs", [])):
                try: db_sg.add_ingress_rule(ec2.Peer.ipv4(cidr_ip), ec2.Port.tcp(db_port), f"Allow DB access from CIDR {cidr_ip}")
                except Exception as e: logger.error(f"Invalid CIDR '{cidr_ip}': {e}")
            if new_sg_opts.get("allow_ingress_from_self", False): db_sg.add_ingress_rule(db_sg, ec2.Port.all_traffic(), "Allow traffic from other members of this SG")
            db_sgs.append(db_sg)
        else: raise ValueError(f"Invalid security_group_config.source: {sg_source} for {instance_identifier}")
        if not db_sgs: db_sgs.append(ec2.SecurityGroup(self, f"FallbackDefaultDbSg{safe_suffix}", vpc=vpc, description=f"Fallback Default SG for {instance_identifier}"))
        return db_sgs

    def _resolve_parameter_group(self, instance_identifier: str, db_engine: rds.IInstanceEngine, engine_type: str, construct_id_suffix: str) -> rds.IParameterGroup | None:
        pg_cfg = self.config.get("parameter_group", {}); pg_source = pg_cfg.get("source", "DEFAULT").upper(); safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if pg_source == "EXISTING_NAME":
            name = pg_cfg.get("name");
            if not name: raise ValueError(f"PG source EXISTING_NAME but 'name' missing for {instance_identifier}")
            return rds.ParameterGroup.from_parameter_group_name(self, f"DbPgImport{safe_suffix}", name)
        elif pg_source == "CREATE_NEW":
            create_opts = pg_cfg.get("create_new_options", {}); family = create_opts.get("family")
            if not family: family = self._get_parameter_group_family(engine_type, self.config.get("engine_version"))
            if not family: raise ValueError(f"PG 'family' undetermined for {instance_identifier}")
            return rds.ParameterGroup(self, f"DbPgCreate{safe_suffix}", engine=db_engine, name=create_opts.get("name_prefix", f"{instance_identifier.lower().replace('_','-')}-pg"), description=create_opts.get("description", f"Custom PG for {instance_identifier}"), parameters=create_opts.get("parameters"))
        return None 

    def _resolve_option_group(self, instance_identifier: str, db_engine: rds.IInstanceEngine, construct_id_suffix: str) -> rds.IOptionGroup | None:
        og_cfg = self.config.get("option_group", {}); og_source = og_cfg.get("source", "DEFAULT").upper(); safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if og_source == "EXISTING_NAME":
            name = og_cfg.get("name");
            if not name: raise ValueError(f"OG source EXISTING_NAME but 'name' missing for {instance_identifier}")
            return rds.OptionGroup.from_option_group_name(self, f"DbOgImport{safe_suffix}", name)
        elif og_source == "CREATE_NEW":
            create_opts = og_cfg.get("create_new_options", {}); option_configurations = []
            for opt_conf_dict in create_opts.get("configurations", []):
                try: option_configurations.append(rds.OptionConfiguration(**opt_conf_dict))
                except Exception as e: logger.error(f"Failed OptionConfiguration from {opt_conf_dict} for {instance_identifier}: {e}")
            return rds.OptionGroup(self, f"DbOgCreate{safe_suffix}", engine=db_engine, configurations=option_configurations, name=create_opts.get("name_prefix", f"{instance_identifier.lower().replace('_','-')}-og"), description=create_opts.get("description", f"Custom OG for {instance_identifier}"))
        return None

    def _parse_performance_insights(self, monitoring_config: dict, instance_identifier: str, construct_id_suffix: str) -> tuple[rds.PerformanceInsightRetention | None, kms.IKey | None]:
        pi_kms_key = None; pi_retention = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if monitoring_config.get("enable_performance_insights", False):
            pi_retention = rds.PerformanceInsightRetention.DEFAULT 
            kms_key_id = monitoring_config.get("performance_insights_kms_key_id")
            if kms_key_id:
                try: pi_kms_key = kms.Key.from_key_arn(self, f"PerfInsightsKmsKey{safe_suffix}", kms_key_id)
                except Exception as e: logger.error(f"Failed import PI KMS key {kms_key_id}: {e}")
        return pi_retention, pi_kms_key

    def _parse_enhanced_monitoring(self, monitoring_config: dict, instance_identifier: str, construct_id_suffix: str) -> tuple[Duration | None, iam.IRole | None]:
        interval = None; role = None; safe_suffix = construct_id_suffix.replace("-","").replace("_","")
        if monitoring_config.get("enable_enhanced_monitoring", False):
            interval_seconds = monitoring_config.get("monitoring_interval_seconds", 60)
            if isinstance(interval_seconds, int) and interval_seconds > 0:
                interval = Duration.seconds(interval_seconds)
                role_arn = monitoring_config.get("monitoring_role_arn")
                if role_arn:
                    try: role = iam.Role.from_role_arn(self, f"ImportedMonitoringRole{safe_suffix}", role_arn)
                    except Exception as e: logger.error(f"Failed import monitoring role {role_arn}: {e}")
            else: interval = None 
        return interval, role

    def _get_db_instance_engine(self, engine_type: str, rds_config: dict) -> rds.IInstanceEngine:
        version_str = rds_config.get("engine_version")
        if not version_str: raise ValueError(f"engine_version required for {engine_type}")
        if engine_type == "MYSQL":
            engine_map = { "8.0.36": rds.MysqlEngineVersion.VER_8_0_36, "8.0.35": rds.MysqlEngineVersion.VER_8_0_35, "8.0.33": rds.MysqlEngineVersion.VER_8_0_33, "8.0.32": rds.MysqlEngineVersion.VER_8_0_32 }
            ver = engine_map.get(version_str)
            if not ver: raise ValueError(f"Unsupported MySQL version: {version_str}")
            return rds.DatabaseInstanceEngine.mysql(version=ver)
        elif engine_type == "POSTGRESQL":
            engine_map = { "16.2": rds.PostgresEngineVersion.VER_16_2, "16.1": rds.PostgresEngineVersion.VER_16_1, "15.6": rds.PostgresEngineVersion.VER_15_6, "15.5": rds.PostgresEngineVersion.VER_15_5 }
            ver = engine_map.get(version_str)
            if not ver: raise ValueError(f"Unsupported PostgreSQL version: {version_str}")
            return rds.DatabaseInstanceEngine.postgres(version=ver)
        else: raise ValueError(f"Unsupported engine_type: {engine_type}")

    def _get_default_port(self, engine_type: str) -> int | None:
        port_map = {"MYSQL": 3306, "POSTGRESQL": 5432, "MARIADB": 3306, "ORACLE_SE2": 1521, "SQL_SERVER_EX": 1433}
        return port_map.get(engine_type)

    def _get_license_model(self, engine_type: str, license_model_str: str | None) -> rds.LicenseModel | None:
        if engine_type.startswith("ORACLE_") or engine_type.startswith("SQL_SERVER_"):
            if not license_model_str:
                if engine_type.startswith("SQL_SERVER_"): return rds.LicenseModel.LICENSE_INCLUDED
                raise ValueError(f"license_model required for {engine_type}")
            lm_map = {"LICENSE_INCLUDED": rds.LicenseModel.LICENSE_INCLUDED, "BRING_YOUR_OWN_LICENSE": rds.LicenseModel.BRING_YOUR_OWN_LICENSE, "GENERAL_PUBLIC_LICENSE": rds.LicenseModel.GENERAL_PUBLIC_LICENSE }
            lm_enum = lm_map.get(license_model_str.upper().replace('-', '_'))
            if not lm_enum: raise ValueError(f"Invalid license_model: {license_model_str}")
            return lm_enum
        return None

    def _get_parameter_group_family(self, engine_type: str, engine_version: str | None) -> str | None:
        if not engine_version: return None
        parts = engine_version.split('.'); major = parts[0]; minor = parts[1] if len(parts) > 1 else "0"
        if engine_type == "MYSQL": return f"mysql{major}.{minor}"
        if engine_type == "POSTGRESQL": return f"postgres{major}"
        if engine_type == "MARIADB": return f"mariadb{major}.{minor}"
        if engine_type == "ORACLE_SE2": return f"oracle-se2-{major}"
        if engine_type == "SQL_SERVER_EX": return f"sqlserver-ex-{major}.{minor}"
        logger.warning(f"Could not determine PG family for {engine_type} {engine_version}.")
        return None
