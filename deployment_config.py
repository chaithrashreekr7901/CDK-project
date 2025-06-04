# CRMP-PROJECT/cdk_project/deployment_config.py

import logging 
logger = logging.getLogger(__name__) 

def _create_vpc_instance_config(
    instance_id: str, deploy_this_instance: bool, core_settings: dict, subnet_settings: dict,
    gateway_settings: dict, route_table_settings: dict, nacl_settings: dict, security_group_settings: dict,
    peering_settings: dict, dhcp_options_settings: dict, endpoint_settings: dict, client_vpn_settings: dict,
    transit_gateway_settings: dict, site_to_site_vpn_settings: dict, flow_log_settings: dict
) -> dict:
    """Helper function to assemble a complete configuration block for a single VPC instance."""
    return {
        "id": instance_id, "deploy": deploy_this_instance, "vpc_core_config": core_settings,
        "subnets_config": subnet_settings, "gateways_config": gateway_settings,
        "route_tables_config": route_table_settings, "network_acls_config": nacl_settings,
        "security_groups_config": security_group_settings, "vpc_peering_config": peering_settings,
        "dhcp_options_config": dhcp_options_settings, "vpc_endpoints_config": endpoint_settings,
        "client_vpn_config": client_vpn_settings, "transit_gateway_config": transit_gateway_settings,
        "site_to_site_vpn_config": site_to_site_vpn_settings, "vpc_flow_logs_config": flow_log_settings,
    }

def get_deployment_configurations() -> dict:
    """Returns the overall deployment configuration for the CDK application."""
    logger.info("DEBUG: Entering get_deployment_configurations() for VPC ONLY deployment.")

    # This VPC will be deployed.
    vpc1_core = {
        'manage_vpc':False, # <<< This VPC WILL BE DEPLOYED
        'creation_mode': 'NEW',
        'existing_vpc_lookup': {'enabled': False, 'by_id': None, 'by_tags': {}},
        'name': "PrimaryDevVPC", 'cidr': "10.10.0.0/16", 
        'dns_options': {'enable_dns_hostnames': True, 'enable_dns_support': True},
        'tags': {"Component": "Networking", "Environment": "dev-alpha", "CostCenter": "1001"}
    }
    vpc1_subnets = {
        'enabled': True,
        'availability_zones_config': [ 
            {'az_name_suffix': 'a',
             'public': {'enabled': True, 'cidr_mask': 24, 'count': 1, 'name_prefix': 'PublicAlpha'},
             'private': {'enabled': True, 'cidr_mask': 24, 'count': 1, 'name_prefix': 'PrivateAlpha'},
             'isolated': {'enabled': False, 'cidr_mask': 28, 'count': 1, 'name_prefix': 'IsolatedAlpha'}
            },
            {'az_name_suffix': 'b',
             'public': {'enabled': True, 'cidr_mask': 24, 'count': 1, 'name_prefix': 'PublicAlpha'},
             'private': {'enabled': True, 'cidr_mask': 24, 'count': 1, 'name_prefix': 'PrivateAlpha'},
             'isolated': {'enabled': False}
            },
        ]
    }
    vpc1_gateways = { 
        'internet_gateway': {'enabled': True},
        'nat_gateways': { 'enabled': True, 'count_per_az': 1 },
        'egress_only_internet_gateway': {'enabled': False}
    }
    vpc1_route_tables = {
        'enabled': True,
        'default_routes': { 'public_to_igw': True, 'private_to_nat': True },
        'categories': {
            'public': { 'enabled': True, 'count': 1, 'routes': [] },
            'private': { 'enabled': True, 'count': "PER_AZ_FOR_NAT", 'routes': [] },
            'isolated': { 'enabled': False, 'count': 1, 'routes': [] }
        }
    }
    vpc1_network_acls = { 
        'enabled': True,
        'rules': {
            'Public': { 
                'enabled': True, 'name_prefix': 'NaclPublicAlpha',
                'inbound': [{'rule': 100, 'cidr': '0.0.0.0/0', 'protocol': 'tcp', 'port': 443, 'action': 'allow'},
                              {'rule': 110, 'cidr': '0.0.0.0/0', 'protocol': 'tcp', 'port': 80, 'action': 'allow'},
                              {'rule': 2000, 'cidr': '0.0.0.0/0', 'protocol': 6, 'from_port': 1024, 'to_port': 65535, 'action': 'allow', 'description': 'Ephemeral TCP out reply'},
                              {'rule': 2001, 'cidr': '0.0.0.0/0', 'protocol': 17, 'from_port': 1024, 'to_port': 65535, 'action': 'allow', 'description': 'Ephemeral UDP out reply'}],
                'outbound': [{'rule': 100, 'cidr': '0.0.0.0/0', 'protocol': -1, 'action': 'allow'}]
            },
            'Private': { 
                'enabled': True, 'name_prefix': 'NaclPrivateAlpha',
                'inbound': [{'rule': 100, 'cidr': vpc1_core['cidr'], 'protocol': -1, 'action': 'allow'}], 
                'outbound': [{'rule': 100, 'cidr': '0.0.0.0/0', 'protocol': -1, 'action': 'allow'}]
            },
            'Isolated': { 'enabled': False } 
        }
    }
    vpc1_security_groups = { 'enabled': True, 'groups': {
        'web-sg': {'enabled': True, 'name': 'vpc1-web-sg', 'ingress': [{'peer_cidr': '0.0.0.0/0', 'protocol': 'tcp', 'port': 80}]},
        'internal-ssh': {'enabled': True, 'name': 'vpc1-internal-ssh', 'ingress': [{'peer_cidr': vpc1_core['cidr'], 'protocol': 'tcp', 'port': 22}]}
    }}
    vpc1_peering_internal = {'enabled': False} # This is for internal VPC features, not inter-VPC peering
    vpc1_dhcp_options = {'enabled': False}
    vpc1_endpoints = {'enabled': False}
    vpc1_client_vpn = {'enabled': False}
    vpc1_transit_gateway = {'enabled': False}
    vpc1_site_to_site_vpn = {'enabled': False}
    vpc1_flow_logs = {'enabled': True, 'destination_type': 'cloud-watch-logs', 'retention_days': 7}

    vpc_instance_1_config = _create_vpc_instance_config(
        instance_id="DevAlphaVPC", # This is the CDK construct ID suffix for the VpcInstanceNestedStack
        deploy_this_instance=vpc1_core.get('manage_vpc', False), # This flag controls if this specific instance stack is created
        core_settings=vpc1_core, subnet_settings=vpc1_subnets, gateway_settings=vpc1_gateways,
        route_table_settings=vpc1_route_tables, nacl_settings=vpc1_network_acls,
        security_group_settings=vpc1_security_groups, peering_settings=vpc1_peering_internal,
        dhcp_options_settings=vpc1_dhcp_options, endpoint_settings=vpc1_endpoints,
        client_vpn_settings=vpc1_client_vpn, transit_gateway_settings=vpc1_transit_gateway,
        site_to_site_vpn_settings=vpc1_site_to_site_vpn, flow_log_settings=vpc1_flow_logs
    )
    
    # --- Configuration for VPC Instance 2: AnalyticsVPC ---.
    vpc2_core = {
        'manage_vpc': False, # <<< This VPC WILL NOT BE DEPLOYED
        'creation_mode': 'NEW', 
        'name': "AnalyticsVPC",
        'cidr': "10.20.0.0/16", # Ensure this is unique if deploying multiple new VPCs
        'tags': {"Environment": "analytics"}
    }
    vpc2_subnets = { 'enabled': False } # Minimal config as it won't be deployed
    # ... (other vpc2 configs can be minimal or set to enabled: False) ...
    vpc_instance_2_config = _create_vpc_instance_config(
        instance_id="AnalyticsVPC",
        deploy_this_instance=vpc2_core.get('manage_vpc', False),
        core_settings=vpc2_core, 
        subnet_settings=vpc2_subnets, 
        gateway_settings={'enabled': False}, route_table_settings={'enabled': False}, 
        nacl_settings={'enabled': False}, security_group_settings={'enabled': False}, 
        peering_settings={'enabled': False}, dhcp_options_settings={'enabled': False}, 
        endpoint_settings={'enabled': False}, client_vpn_settings={'enabled': False}, 
        transit_gateway_settings={'enabled': False}, site_to_site_vpn_settings={'enabled': False}, 
        flow_log_settings={'enabled': False}
    )
    
    
    
    
    

    # --- VPC Peering Configuration (Globally Disabled) ---
    vpc_peering_definitions = [
        # Peering definitions can remain here, but the global deploy flag below will prevent action
        {
            'id': 'AppVPC-to-PrimaryServicesVPC-Peering', 
            'enabled': False, # This individual flag doesn't matter if the group is disabled
            'peering_connection_name': 'app-to-services-peer',
            'vpc_a': { 'id': 'vpc-05ade2cb0ee3994d0', 'account_id': '198484116691', 'region': 'us-east-1', 'cidr': '10.10.0.0/16', 'route_tables': {}},
            'vpc_b': { 'id': 'vpc-0682a04278f37a95c', 'account_id': '198484116691', 'region': 'us-east-1', 'cidr': '172.31.0.0/16', 'route_tables': {}},
            'tags': {}
        }
    ]
    
    # --- RDS Instance and Cluster Configurations ---
    rds_deployments_config = {
        "deploy": False, 
        "description": "Configuration group for all RDS Database Instances and Clusters.",
        "instances": [
            {
                "id": "MySQLExampleDB", 
                "enabled": True, 
                "deployment_architecture": "INSTANCE", # "INSTANCE" or "CLUSTER" (for Aurora)
                "engine_type": "MYSQL", 
                # Available engine_type options (non-Aurora): 
                # "MYSQL", "POSTGRESQL", "MARIADB", 
                # "ORACLE_SE1", "ORACLE_SE2", "ORACLE_EE", "ORACLE_EE_CDB",
                # "SQL_SERVER_EX", "SQL_SERVER_WEB", "SQL_SERVER_SE", "SQL_SERVER_EE"
                # For Aurora: "AURORA_MYSQL", "AURORA_POSTGRESQL" (handled by AuroraClusterStack)
                "config": {
                    # --- General Settings ---
                    "instance_identifier": "mysql-example-main", # Physical name in AWS console
                    "engine_version": "8.0.36", # Must match engine_type. E.g., "15.5" for PG, "19.0.0.0.ru-..." for Oracle
                    
                    # --- Instance Class ---
                    # Choose from: https://aws.amazon.com/rds/instance-types/
                    # Standard classes (includes m classes): e.g., "db.m5.large", "db.m6g.large" (Graviton)
                    # Memory optimized classes (includes r and x classes): e.g., "db.r5.large", "db.x2g.medium"
                    # Burstable classes (includes t classes): e.g., "db.t3.medium", "db.t4g.medium"
                    "instance_type": "t3.medium", 

                    # --- Storage ---
                    "allocated_storage_gb": 500, # In GiB
                    "storage_type": "gp3", # Options: "standard", "gp2", "gp3", "io1", "io2"
                    # "iops": 3000, # Required for "io1", "io2". Optional & configurable for "gp3".
                    # "storage_throughput": 125, # Only for "gp3". In MiBps.
	                "enable_storage_autoscaling": True, # Flag for storage autoscaling
	                "max_allocated_storage_gb": 200, # Used if enable_storage_autoscaling is True
	

                    # --- Credentials ---
                    "credentials": {
                        "source": "GENERATE_NEW_SECRET", # "GENERATE_NEW_SECRET" or "USE_EXISTING_SECRET_ARN"
                        "master_username": "adminuser", 
                        "generate_new_secret_options": { # Used if source is "GENERATE_NEW_SECRET"
                            # "secret_name_prefix": "rds/mysql-example", 
                            # "password_length": 20,
                            # "exclude_characters": "\"@/\\' " 
                        },
                        # "existing_secret_arn": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:NAME-SUFFIX" # Used if source is "USE_EXISTING_SECRET_ARN"
                    },

                    # --- Connectivity ---
                    "vpc_config": {
                        # Option 1: Use an existing VPC (looked up by ID)
                        "lookup_existing_vpc_by_id": "vpc-08e1a969d58a58b94", # <<< YOUR ACTUAL DevAlphaVPC ID or other existing VPC
                        # Option 2: Use a VPC created by this CDK app (referenced by its logical ID from vpcs section)
                        # "use_cdk_created_vpc_id": "DevAlphaVPC", # Matches 'id' from 'vpcs.instances' list
                        
                        # Note on "Create New VPC for this RDS": 
                        #   This is best handled by defining the VPC in the 'vpcs' section and then referencing it here
                        #   using 'use_cdk_created_vpc_id' or 'lookup_existing_vpc_by_id' after it's deployed.
                        #   An RDS stack should not typically create a whole new VPC itself.

                        "subnet_type_for_rds": "PRIVATE_WITH_EGRESS", # Options: "PRIVATE_WITH_EGRESS", "PRIVATE_ISOLATED"
                                                                    # DbInstanceStack uses this for ec2.SubnetSelection
		            "security_group_config": {
		                            "source": "CREATE_NEW", # Options: "CREATE_NEW", "USE_EXISTING_IDS"
		                            "create_new_options": { # Used if source is "CREATE_NEW"
		                                "name": "mysql-example-db-sg", 
		                                "description": "SG for MySQL Example DB",
		                                "allow_all_outbound": True,  # Default
		                                "allow_ingress_from_sg_ids": ["sg-06822dc0f98481e26"], # <<< YOUR APP SG ID(s)
					                    # "allow_ingress_from_cidrs": ["10.10.x.x/yy"], # Optional
					                    # "allow_ingress_from_self": False # Optional
					
		                            },
		                            # "existing_ids": ["sg-xxxxxxxxxxxxxxxxx, "sg-yyyyyyyyyyyyyyyyy""] # Used if source is "USE_EXISTING_IDS"
		                        }
                    },
                    "port": 3306, # Engine default will be used if not specified
                    "publicly_accessible": False, # Recommended: False

                    # --- Database Options ---
                    "database_name": "MyExampleDB", # Initial database to create
                    "parameter_group": {
                        "source": "DEFAULT", # "DEFAULT", "EXISTING_NAME", "CREATE_NEW"
                        # "name": "your-custom-mysql80-param-group", # Required if source is "EXISTING_NAME"
                        # "create_new_options": {
                        #     # "family": "mysql8.0", # Usually auto-detected by DbInstanceStack
                        #     "name_prefix": "mysql-example", 
                        #     "description": "Custom PG for MySQL Example",
                        #     "parameters": {"max_connections": "300", "innodb_buffer_pool_size": "2147483648"} # Example
                        # }
                    },
                    "option_group": {
                        "source": "DEFAULT", # "DEFAULT", "EXISTING_NAME", "CREATE_NEW"
                        # "name": "your-custom-mysql-option-group", # Required if source is "EXISTING_NAME"
                        # "create_new_options": {
                        #     # "engine_name_for_og": "mysql", # Usually auto-detected
                        #     # "major_engine_version_for_og": "8.0", # Usually auto-detected
                        #     "name_prefix": "mysql-example-og",
                        #     "description": "Custom OG for MySQL Example",
                        #     "configurations": [ # List of rds.OptionConfiguration props
                        #         {"name": "MARIADB_AUDIT_PLUGIN", "settings": {"SERVER_AUDIT_EVENTS": "CONNECT,QUERY"}}
                        #     ]
                        # }
                    },

                    # --- Availability & Durability ---
                    "multi_az": False, # Set to True for production/HA
                    # "availability_zone": "us-east-1c", # Specify if multi_az is False and you need a specific AZ# Only if multi_az_deployment is False

                    # --- Backup ---
	                "enable_automated_backups": True, # Flag for automated backups
                    "backup_retention_days": 14, # 0 to disable, 1-35 days
                    "preferred_backup_window": "06:00-07:00", # UTC, e.g., "hh:mm-hh:mm"
                    "copy_tags_to_snapshot": True,
                    # "replicate_automated_backups_to_region": "us-west-2", # Optional: ARN of KMS key in target region for encryption
                    # "replicated_automated_backups_kms_key_arn": "arn:aws:kms:us-west-2:ACCOUNT_ID:key/KEY_ID"

                    # --- Encryption ---
	               "storage_encrypted": True, # Default is True recommended, but good to be explicit
	               "enable_custom_kms_encryption": False, # Flag for custom KMS key
                    # "kms_key_id": "arn:aws:kms:YOUR_REGION:YOUR_ACCOUNT_ID:key/YOUR_RDS_KMS_KEY_ID", # For customer-managed KMS key

                    # --- Logging and Monitoring ---
                    "monitoring":{
                        "cloudwatch_logs_exports": ["error", "general", "slowquery", "audit"],
                        "enable_performance_insights": True, 
                        "performance_insights_retention_period_days": 7, # 7 (free) or up to 731 (2 years, paid)
                        # "performance_insights_kms_key_id": "arn:aws:kms:...", # Optional KMS key for PI
                        "enable_enhanced_monitoring": True, # Flag for Enhanced Monitoring
                        "monitoring_interval_seconds": 60, # 0 (disabled), 1, 5, 10, 15, 30, 60 for Enhanced Monitoring # Used if                       enable_enhanced_monitoring is True
                        # "monitoring_role_arn": "arn:aws:iam::ACCOUNT_ID:role/RDSEnhancedMonitoringRole" # Optional custom role
                        # "enable_devops_guru": True, # If supported and desired, False, # Flag for DevOps Guru
                        # "devops_guru_kms_key_id": "arn:aws:kms:..." # Optional KMS key for DevOps Guru
                    },

                    # --- Maintenance ---
                    "preferred_maintenance_window": "sun:07:30-sun:08:30", # UTC, e.g., "ddd:hh:mm-ddd:hh:mm"
                    "auto_minor_version_upgrade": True,
                    "allow_major_version_upgrade": False, # Caution with this in prod

                    # --- Additional Configuration ---
                    "deletion_protection": False, # Recommended for production
                    "iam_database_authentication_enabled": False,
                    # "license_model": "license-included", # Required for SQL Server, Oracle (e.g. "bring-your-own-license")
                    
                    "tags": {"CodeDeploy": "AppEC2", "Application": "MySQLExample"}
                }
            }
            # Add more RDS instance definitions here
        ]
    }
    
    
    # --- RDS Instance and Cluster Configurations ---
    rds_deployments_config = {
        "deploy": False, 
        "description": "Configuration group for all RDS Database Instances and Clusters.",
        "instances": [
            # ... (your existing DbInstanceStack configurations) ...
            {
                "id": "AuroraMySQLProdCluster", 
                "enabled": True, 
                "deployment_architecture": "CLUSTER", # <<< Key to differentiate
                "engine_type": "AURORA_MYSQL", # Options: "AURORA_MYSQL", "AURORA_POSTGRESQL"
                "config": {
                    # --- Cluster-Level Settings ---
                    "cluster_identifier": "aurora-mysql-prod-cluster", # Physical name for the cluster
                    "engine_version": "8.0.mysql_aurora.3.06.0", # Check AWS console for exact Aurora MySQL 8.0 compatible versions
                    "default_database_name": "MyApplicationDB", # Initial database to create in the cluster

                    # --- Credentials (same structure as DbInstanceStack) ---
                    "credentials": {
                        "source": "GENERATE_NEW_SECRET", 
                        "master_username": "auroraadmin", 
                        "generate_new_secret_options": { "secret_name_prefix": "rds/aurora-mysql-prod" },
                        # "existing_secret_arn": null 
                    },

                    # --- Connectivity (same structure as DbInstanceStack) ---
                    "vpc_config": {
                        # "use_cdk_created_vpc_id": "DevAlphaVPC", # Or "lookup_existing_vpc_by_id"
                        "lookup_existing_vpc_by_id":"vpc-08e1a969d58a58b94",
                        "subnet_type_for_rds": "PRIVATE_WITH_EGRESS", 
                        "security_group_config": {
                            "source": "CREATE_NEW", 
                            "create_new_options": { 
                                "name": "aurora-mysql-prod-cluster-sg", 
                                "description": "SG for Aurora MySQL Prod Cluster",
                                "allow_ingress_from_sg_ids": ["sg-06822dc0f98481e26"], 
                            }
                        }
                    },
                    "port": 3306,  #Default for Aurora MySQL

                    # --- Instances within the Cluster ---
                    "instances_config": {
                        "count": 2, # Number of DB instances in the cluster (e.g., 1 writer, 1 reader)
                        "instance_type": "db.r6g.large", # Common instance type for all instances in the cluster
                        # For more granular control, you could define a list of instance configs:
                        # "instance_definitions": [
                        #     {"instance_type": "db.r6g.xlarge", "publicly_accessible": False, "promotion_tier": 0}, # Writer
                        #     {"instance_type": "db.r6g.large", "publicly_accessible": False, "promotion_tier": 1}  # Reader
                        # ],
                        "auto_minor_version_upgrade_instances": True, # For instances in the cluster
                        "publicly_accessible_instances": False,  #Default for instances
                        "enable_performance_insights_instances": True,
                        "performance_insights_retention_period_instances": 7,
                        # "performance_insights_kms_key_id_instances": "arn:aws:kms:...",
                        "ca_certificate_identifier_instances": "rds-ca-rsa2048-g1", # Or specific cert
                    },

                    # --- Serverless v2 Scaling (Optional) ---
                    "enable_serverless_v2_scaling": False, # Set to True to enable
                    # "serverless_v2_min_capacity_acu": 0.5, # Min Aurora Capacity Units (ACUs)
                    # "serverless_v2_max_capacity_acu": 16.0, # Max Aurora Capacity Units (ACUs)

                    # --- Backup & Recovery ---
                    "enable_automated_backups": True,
                    "backup_retention_days": 14,
                    "preferred_backup_window": "04:00-05:00",
                    "copy_tags_to_snapshot": True,
                    "enable_backtrack": False, # Aurora-specific feature
                    # "backtrack_window_hours": 72, # If enable_backtrack is True (e.g., 24, 48, 72)

                    # --- Encryption ---
                    "storage_encrypted": True,
                    "enable_custom_kms_encryption": False,
                    # "kms_key_id": "arn:aws:kms:REGION:ACCOUNT:key/YOUR_AURORA_KMS_KEY_ID",

                    # --- Logging and Monitoring ---
                    "monitoring": {
                        "cloudwatch_logs_exports": ["audit", "error", "general", "slowquery"], #  MySQL specific
                        # For Aurora PostgreSQL: ["postgresql", "upgrade"]
                        "enable_http_endpoint": False, # For RDS Data API
                        # "monitoring_interval_seconds": 0, // Enhanced Monitoring for instances (set in instances_config or per instance)
                        # "monitoring_role_arn": "arn:aws:iam::ACCOUNT:role/RDSEnhancedMonitoringRole"
                    },
                    
                    # --- Cluster Parameter Group ---
                    "cluster_parameter_group": {
                        "source": "DEFAULT", # "DEFAULT", "EXISTING_NAME", "CREATE_NEW"
                        # "name": "custom-aurora-mysql80-cluster-pg",
                        # "create_new_options": {
                        #     "family": "aurora-mysql8.0", // Must be correct Aurora family
                        #     "description": "Custom Cluster PG for Aurora MySQL Prod",
                        #     "parameters": {"aurora_lab_mode": "1", "server_audit_logging": "1"}
                        # }
                    },
                    # --- DB Instance Parameter Group (for instances in the cluster) ---
                    "instance_parameter_group": { # Applied to all instances in the cluster
                        "source": "DEFAULT", 
                        # "name": "custom-aurora-mysql80-instance-pg",
                        # "create_new_options": { /* ... */ }
                    },
                    
                    # --- Maintenance & Deletion ---
                    "preferred_maintenance_window": "mon:05:30-mon:06:30",
                    "deletion_protection": True,
                    "iam_database_authentication_enabled": False,

                    "tags": {"Environment": "Production", "Application": "AuroraMySQLService"}
                }
            }
            # Add more RDS instance/cluster definitions here
        ]
    }
    
    # --- S3 Bucket Configurations (Structure based on S3_CONFIG reference) ---
    s3_deployments_config = {
        "deploy": False, # <<< ENABLE S3 DEPLOYMENTS GLOBALLY
        "description": "Group for S3 Bucket Deployments",

        # --- Default settings applied to ALL buckets unless overridden ---
        "defaults": {
            "creation_mode": "NEW", 
            "removal_policy": "RETAIN", 
            "auto_delete_objects": False, 
            "versioned": True, 
            "block_all_public_access": True, 
            "block_public_access": { 
                "block_public_acls": True,"ignore_public_acls": True,
                "block_public_policy": True, "restrict_public_buckets": True,
            },
            "object_ownership": "BucketOwnerEnforced", 
            "encryption": { "type": "S3_MANAGED" }, 
            "tags": { "cdk-managed-by": "s3-deployments" } 
        },

        # --- List of individual bucket definitions ---
        "buckets": [
            # == Example 1: Basic Counted Buckets (like basic_buckets_creation) ==
            {
                "id": "BasicCounted",
                "enabled": False,
                "count": 2, 
                "config": {
                    "bucket_name_prefix": "core-alpha-counted-bucket", 
                    "versioned": True, 
                    "removal_policy": "RETAIN", 
                    "auto_delete_objects": False, 
                    "tags": { "Purpose": "TemporaryBasic" }, 
                    "bucket_policy_statements": [ 
                        {
                            "sid": "AllowDevReadWrite", "effect": "Allow", "principal_type": "AWS",
                            "principals": ["arn:aws:iam::198484116691:user/Chaithra"], 
                            "actions": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                            "resources": ["{{BucketArn}}", "{{BucketArn}}/*"]
                        }
                    ]
                }
            },
            # == Example 2: Detailed Private Bucket (like detailed_buckets/ApplicationConfigStore) ==
            {
                "id": "AppConfigData",
                "enabled": True,
                "config": {
                    # Let CDK generate name (no bucket_name*)
                    "block_all_public_access": True, 
                    "encryption": { 
                        "type": "S3_MANAGED", 
                        #  "type": "KMS",
                        # "kms_key_arn": "arn:aws:kms:YOUR_REGION:YOUR_ACCOUNT_ID:key/YOUR_KMS_KEY_ID" # <<< REPLACE
                    },
                    "versioned": True,
                    "object_ownership": "BucketOwnerEnforced",
                    "removal_policy": "RETAIN", 
                    "auto_delete_objects": False,
                    "lifecycle_rules": [
                        { "id": "ExpireOldNonCurrent", "enabled": True, "noncurrent_version_expiration_days": 90 },
                        { "id": "AbortIncomplete", "enabled": True, "abort_incomplete_multipart_upload_after_days": 7 }
                    ],
                    "server_access_logs": { 
                         "target_bucket_name": "ct-bucket-access-logs-09-05-25",
                         "target_prefix": "logs/app-config-data/"
                    },
                    "bucket_policy_statements": [
                        {
                            "sid": "AllowAppServerReadWrite", "effect": "Allow", "principal_type": "AWS",
                            "principals": ["arn:aws:iam::198484116691:user/Chaithra"], 
                            "actions": ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket"],
                            "resources": ["{{BucketArn}}", "{{BucketArn}}/*"]
                        }
                    ],
                    "tags": { "DataClassification": "Confidential", "Application": "MyApp" }
                }
            },
             # == Example 3: Website Bucket (like website_buckets/MainPublicSite) ==
            {
                "id": "MainWebsite",
                "enabled": False,
                "config": {
                    # "bucket_name": "my-unique-public-website-for-app", 
                    "website_index_document": "index.html",
                    "website_error_document": "error.html",
                    "public_read_access": True, 
                    "block_all_public_access": False, 
                    "object_ownership": "BucketOwnerPreferred", 
                    "versioned": False, 
                    "removal_policy": "DESTROY", 
                    "auto_delete_objects": True,
                    "cors": [
                        {"allowed_methods": ["GET", "HEAD"], "allowed_origins": ["*"], "allowed_headers": ["*"]}
                    ],
                    "deployment": { 
                         "enabled": True,
                         "source_local_path": "./path/to/your/website/build/output", # <<< REPLACE
                         "prune": True,
                    },
                    "tags": { "Purpose": "PublicWebsite" }
                }
            },
            # == Example 4: Importing an Existing Bucket ==
             {
                "id": "ImportedLogsBucket",
                "enabled": False, # Enable processing for this definition
                "config": {
                    "creation_mode": "EXISTING", # Specify import
                    "existing_bucket_name": "mybucket-new-12345678" # <<< REPLACE
                }
            }
            # Add more bucket definitions here...
        ]
    }
    
 
    # --- EC2 Deployments (Includes direct Instances and Launch Templates) ---
    ec2_deployments_config = {
        "deploy": True,  # BOOLEAN: Master switch for all EC2-related deployments in this group (direct instances and LTs).
        "description": "Configuration group for EC2 Instance and Launch Template deployments.",
        
        "defaults": { # Default settings applied to all direct instances and launch templates below, unless overridden.
            "enabled": True, # BOOLEAN: Default for an individual definition (instance or LT) to be processed.
            "key_name": "my-cdk-keypair", # STRING (Optional): Default EC2 KeyPair name. <<< REPLACE
            "tags": { # OBJECT: Default tags. Merged with specific tags.
                "cdk-managed-by": "ec2-module-default",
                "DefaultEnvironment": "Development"
            },
            # Defaults specific to direct EC2 instances (can be overridden in instance's config)
            "instance_defaults": {
                "disable_api_termination": False,       # BOOLEAN
                "instance_initiated_shutdown_behavior": "stop", # STRING: "stop" or "terminate"
                "monitoring_detailed": False,           # BOOLEAN
                "tenancy": "default",                   # STRING: "default", "dedicated", "host"
                "metadata_options": {
                    "enabled": True,                    # BOOLEAN
                    "http_tokens": "required",          # STRING: "optional" or "required"
                    "http_endpoint": "enabled",         # STRING: "enabled" or "disabled"
                    "http_put_response_hop_limit": 1,   # INTEGER
                    "instance_metadata_tags": "enabled" # STRING: "enabled" or "disabled"
                },
                "security_group_definition": {
                    "enabled": True, # Default to creating a new SG if not specified by instance
                    "allow_all_outbound": True,
                    "ingress_rules": []
                },
            },
            # Defaults specific to Launch Templates (can be overridden in LT's config)
            "launch_template_defaults": {
                "version_description": "Default LT version", # STRING
                "template_data": { # Default values for template_data fields
                    "monitoring_detailed": False,
                    "disable_api_termination": False,
                    "instance_initiated_shutdown_behavior": "stop",
                    "ebs_optimized": False,
                    "metadata_options": { # Default metadata options for LTs
                        "enabled": True,
                        "http_tokens": "optional",
                        "http_endpoint": "enabled"
                    }
                },
                "tag_specifications": [] # Default empty list for LT resource tags
            },
            "ebs_volume_defaults": { # Defaults specific to EBS Volumes
                "enabled": False,
                "volume_type": "gp3",
                "size_gb": 20,
                "encrypted": True,
                "removal_policy": "RETAIN",
                "tags": {"DefaultStorageTier": "GeneralPurposeEBS"},
                "attachment_config": { "enabled": False }
            }
            
        },
        "security_groups": [
        {
            "id": "WebAppInstanceSG", # Logical ID for this SG in CDK & for referencing
            "enabled": False,         # Set to false to skip deploying this specific SG
            "config": {
                "security_group_name": "webapp-instance-sg", # Optional: Physical name in AWS
                "description": "Security group for web application instances",
                "vpc_id": "vpc-0682a04278f37a95c", # REQUIRED: VPC ID where this SG will be created
                "allow_all_outbound": True,       # Optional: Overrides default. True or False.
                "ingress_rules": [
                    {
                        "description": "Allow HTTP from MyWebAppALB",
                        "peer_type": "SECURITY_GROUP_ID", 
                        # Value should be the ID of the ALB's security group.
                        # This could be a literal sg-id if known, or a reference if created by another CDK stack.
                        # For CDK-managed SGs, you'd typically reference the object.
                        # For config-driven, you might need to resolve this sg-id if ALB is also config-driven.
                        # Example: "peer_value": "sg-07912ab450466bc24" 
                        "peer_value_ref_id": "MyWebAppALB", # Special key to indicate peer_value is an SG from another resource created by this config system (e.g. an ALB's SG)
                                                            # The SG stack would need logic to resolve this ref_id to an actual SG object or ID.
                                                            # For a simpler start, you might just use literal sg-ids if they are known/fixed.
                        "protocol": "tcp",
                        "port": 8080 # Port your web app instances listen on
                    },
                    {
                        "description": "Allow SSH from Bastion Host SG",
                        "peer_type": "SECURITY_GROUP_ID",
                        "peer_value": "sg-bastionhost123abc" # Example: ID of your bastion's security group
                    },
                    {
                        "description": "Allow SSH from specific IP (e.g., office)",
                        "peer_type": "CIDR_IPV4",
                        "peer_value": "YOUR_OFFICE_IP/32", # <<< REPLACE
                        "protocol": "tcp",
                        "port": 22
                    }
                ],
                "egress_rules": [ 
                    # Only define if allow_all_outbound is false.
                    # Example:
                    # {
                    #     "description": "Allow outbound to S3",
                    #     "peer_type": "PREFIX_LIST_ID", 
                    #     "peer_value": "pl-xxxxxxxx", # S3 Prefix List ID for your region
                    #     "protocol": "tcp",
                    #     "port": 443 
                    # }
                ],
                "tags": { # Optional: Specific tags for this SG, merged with/overrides defaults
                    "Application": "MyWebApp",
                    "Tier": "Application"
                }
            }
        },
        {
            "id": "DatabaseSG",
            "enabled": False,
            "config": {
                "security_group_name": "database-internal-sg",
                "description": "Security group for internal database servers",
                "vpc_id": "vpc-0682a04278f37a95c", # Ensure this VPC ID is correct
                "allow_all_outbound": False, # Example: Restrict outbound
                "ingress_rules": [
                    {
                        "description": "Allow DB Port from WebAppInstanceSG",
                        "peer_type": "SECURITY_GROUP_ID_REF", # Special type to reference another SG defined here by its 'id'
                        "peer_value": "WebAppInstanceSG", # References the 'id' of the SG above
                        "protocol": "tcp",
                        "port": 3306 # Example: MySQL port
                    }
                ],
                "egress_rules": [
                    {
                        "description": "Allow outbound to nowhere (example of very restrictive)",
                        "peer_type": "CIDR_IPV4",
                        "peer_value": "127.0.0.1/32", # Effectively blocks most outbound
                        "protocol": "-1" # All protocols
                    }
                ],
                "tags": {
                    "Tier": "Database"
                }
            }
        },
        {
                "id": "AlbSG",
                "enabled": True,
                "config": {
                    "security_group_name": "my-webapp-alb-sg",
                    "description": "Security group for MyWebAppALB",
                    "vpc_id": "vpc-0682a04278f37a95c", #Explicit VPC for clarity
                    "ingress_rules": [
                        {
                            "description": "Allow HTTP from Internet",
                            "peer_type": "ANY_IPV4",
                            "protocol": "tcp",
                            "port": 80
                        },
                        {
                            "description": "Allow HTTPS from Internet",
                            "peer_type": "ANY_IPV4",
                            "protocol": "tcp",
                            "port": 443
                        }
                    ],
                    "tags": {"Role": "LoadBalancer"}
                }
            },
            {
                "id": "InstanceSG",
                "enabled": True,
                "config": {
                    "security_group_name": "my-standalone-instance-sg",
                    "description": "Security group for standalone web instances",
                    "vpc_id": "vpc-0682a04278f37a95c",
                    "ingress_rules": [
                        {
                            "description": "Allow App Port 8080 from ALB SG",
                            "peer_type": "SECURITY_GROUP_ID_REF", 
                            "peer_value_ref_id": "AlbSG", # References the 'AlbSG' defined above
                            "protocol": "tcp",
                            "port": 8080
                        },
                        {
                            "description": "Allow SSH from specific IP",
                            "peer_type": "CIDR_IPV4",
                            "peer_value": "0.0.0.0/0", #<<< REPLACE
                            "protocol": "tcp",
                            "port": 22
                        }
                    ],
                    "tags": {"Role": "WebAppInstance"}
                }
            },
            {
                "id": "AsgInstanceSG",
                "enabled": True,
                "config": {
                    "security_group_name": "my-asg-instance-sg",
                    "description": "Security group for ASG web instances",
                    "vpc_id": "vpc-0682a04278f37a95c",
                    "ingress_rules": [
                        {
                            "description": "Allow App Port 8080 from ALB SG",
                            "peer_type": "SECURITY_GROUP_ID_REF", 
                            "peer_value_ref_id": "AlbSG", # References the 'AlbSG'
                            "protocol": "tcp",
                            "port": 8080
                        },
                        {
                            "description": "Allow SSH from specific IP",
                            "peer_type": "CIDR_IPV4",
                            "peer_value": "0.0.0.0/0", # <<< REPLACE
                            "protocol": "tcp",
                            "port": 22
                        }
                    ],
                    "tags": {"Role": "ASGWebAppInstance"}
                }
            }
        
        # Add more security group definitions here
    ],
        
        "instances": [
            {
                "id": "MyStandaloneWebServer1", # Logical ID for this EC2 configuration entry
                "enabled": True,    # Overrides group default if set to False
                "count": 1,         # Number of identical instances for this configuration
                "config": {
                    "instance_name": "tandalone-web-01", # Used for the 'Name' tag
                    "ami_config": {
                        # source (STRING, Required): Method to find the AMI.
                        # Options:
                        #   "LATEST_AMAZON_LINUX_2023": Latest Amazon Linux 2023.
                        #   "LATEST_AMAZON_LINUX_2": Latest Amazon Linux 2.
                        #   "LATEST_WINDOWS_CORE": Latest Windows Server Core (e.g., WINDOWS_SERVER_2022_ENGLISH_CORE_BASE).
                        #   "LATEST_WINDOWS_FULL": Latest Windows Server Full (e.g., WINDOWS_SERVER_2022_ENGLISH_FULL_BASE).
                        #   "ID": Use a specific AMI ID (requires 'id' field below).
                        #   "LOOKUP": Lookup AMI by name/filters (requires 'lookup_filters' field below).
                        "source": "LATEST_AMAZON_LINUX_2023",
                        # "id": "ami-0123456789abcdef0", # STRING: Required if source is "ID". Specific AMI ID.
                        # "os_type_hint": "LINUX", # STRING: "LINUX" or "WINDOWS". Helps generic_linux/windows for "ID" source.
                        # "lookup_filters": { # OBJECT: Required if source is "LOOKUP".
                        #     "name": "RHEL-9.?-HVM-*-x86_64-*-Hourly2-GP2", # STRING: AMI name pattern (can use wildcards).
                        #     "owners": ["309956199498"], # LIST of STRINGS: Account IDs or aliases like "amazon", "self".
                        #     "filters": {"architecture": ["x86_64"], "virtualization-type": ["hvm"]} # DICTIONARY: Additional filters.
                        # },
                        "architecture": "x86_64" # STRING: "x86_64" or "arm_64". Used with LATEST_AMAZON_LINUX*.
                    },

                    "instance_type": "t3.micro",# STRING (Required): EC2 instance type (e.g., "t3.micro", "m5.large", "c5.2xlarge").

                    "key_name": "my-cdk-keypair", # Specific key pair for this instance/group

                    "network_config": { # OBJECT (Required): Network placement details.
                        "vpc_id": "vpc-0682a04278f37a95c",    # STRING (Required): PHYSICAL ID of the EXISTING VPC to deploy into.
                        "subnet_id":"subnet-01068ebd6184d034b",# STRING (Required): PHYSICAL ID of the EXISTING Subnet within the specified VPC.
                        "associate_public_ip_address": True,
                        "security_group_refs": ["InstanceSG"],
                        # "security_group_ids": [],# BOOLEAN: True to assign a public IP (if in a public subnet). False for private.
                        "source_dest_check": True,            # BOOLEAN: Enable/disable source/destination BGP check. Default True. Set False for NAT instances.
                        # "private_ip_address": "10.0.1.50"  # STRING (Optional): Assign a specific primary private IP address from the subnet.
                        # "network_interfaces": [ # LIST of OBJECTS (Advanced - for multiple ENIs. CfnInstance only)
                        #     {
                        #         "device_index": 0, # Primary is 0
                        #         "subnet_id": "subnet-primary-id",
                        #         "associate_public_ip_address": True,
                        #         "groups": ["sg-primary1", "sg-primary2"],
                        #         "description": "Primary ENI",
                        #         "private_ip_address": "10.0.1.10",
                        #         "secondary_private_ip_address_count": 1
                        #     },
                        #     {
                        #         "device_index": 1,
                        #         "subnet_id": "subnet-secondary-id",
                        #         "groups": ["sg-secondary1"]
                        #     }
                        # ]
                    },
                    "security_group_definition": { # Specific SG for this instance
                        "enabled": False,
                        "name": "web-app-01-sg", # Optional: Physical name for the SG
                        "description": "Security group for MyWebAppServer EC2 instance",
                        "allow_all_outbound": True, # Default is True, can be set to False
                        "ingress_rules": [
                            {
                                "description": "Allow SSH from My IP",
                                "peer_type": "CIDR_IP",       # Options: CIDR_IP, SECURITY_GROUP, PREFIX_LIST, ANY_IPV4, ANY_IPV6, SELF
                                "peer_value": "0.0.0.0/0", # <<< REPLACE with your IP CIDR for SSH
                                "protocol": "tcp",            # "tcp", "udp", "icmp", "-1" (all), or protocol number
                                "port": 22                    # Single port
                                # "from_port": 22,            # Use from_port and to_port for ranges
                                # "to_port": 22,
                            },
                            # Example: Allow HTTP from anywhere
                            # {
                            #     "description": "Allow HTTP from Anywhere",
                            #     "peer_type": "ANY_IPV4",
                            #     "protocol": "tcp",
                            #     "port": 80
                            # }
                        ],
                        # "egress_rules": [] # Define only if allow_all_outbound is False
                    },
                     "storage_config": { # OBJECT: Configuration for instance storage.
                        "enabled": True,    # BOOLEAN: If False, this entire storage_config block (except what AMI dictates for root) is ignored.
                        "root_volume": {    # OBJECT: Configuration for the root EBS volume.
                            # "device_name_override": "/dev/sda1", # STRING (Optional): Override default root device name if known for specific AMI.
                            "size_gb": 30,          # INTEGER: Size in GiB.
                            "type": "gp3",          # STRING: "standard", "gp2", "gp3", "io1", "io2", "sc1", "st1".
                            # "iops": 3000,         # INTEGER: Provisioned IOPS (for io1, io2, gp3).
                            # "throughput_mbps": 125, # INTEGER: Throughput in MiBps (for gp3). Max 1000.
                            "encrypted": True,      # BOOLEAN: Whether the volume should be encrypted.
                            # "kms_key_id": "arn:aws:kms:...", # STRING: ARN of KMS key for encryption. Uses AWS managed if not set.
                            "delete_on_termination": True # BOOLEAN: Whether to delete the volume on instance termination.
                        },
                        "ebs_block_devices": [ # LIST of OBJECTS: Define additional EBS volumes.
                            {
                                "enabled": False,    # BOOLEAN: If False, this specific additional volume is skipped.
                                "device_name": "/dev/sdf", # STRING (Required): Device name (e.g., /dev/sd[f-p], /dev/xvd[f-p]).
                                "volume_name_tag": "ApplicationDataVol", # STRING (Optional): Value for 'Name' tag of this specific EBS volume.
                                "size_gb": 50,
                                "type": "gp3",
                                # "iops": 3000,
                                # "throughput_mbps": 200,
                                "encrypted": True,
                                "delete_on_termination": False, # Typically False for data volumes.
                                # "snapshot_id": "snap-0123456789abcdef0" # STRING: Create volume from this snapshot.
                                # "kms_key_id": "arn:aws:kms:..."
                            },
                        #     {
                        #     "enabled": True,
                        #     "device_name": "/dev/sdg",  #Attachment name for second data volume
                        #     "volume_name_tag": "ApplicationLogsVol", #// Added a name tag
                        #     "size_gb": 50,           #// Example size, adjust as needed
                        #     "type": "gp3",
                        #     "encrypted": True,
                        #     "delete_on_termination": False #// Typically false for data/log volumes
                        # }
                        ]
                    },

                   "iam_instance_profile": { # OBJECT: Configuration for the IAM role and instance profile.
                        "enabled": True,        # BOOLEAN: If False, no IAM instance profile is associated.
                        "create_new": True,     # BOOLEAN: If True, a new role and profile are created based on details below.
                        # --- If create_new is True: ---
                        "role_name": "MyExampleAppRole", # STRING (Optional): Physical name for the created IAM Role. Auto-generated if not provided.
                        "profile_name": "MyExampleAppInstanceProfile", # STRING (Optional): Physical name for the created IAM Instance Profile. Auto-generated if not provided.
                        "managed_policy_arns": [ # LIST of STRINGS: ARNs of AWS managed policies to attach.
                            "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                            "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
                            "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore", # Essential for SSM Agent
                            "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess", 
                        ],
                        "custom_policy_statements": [ # LIST of OBJECTS: Define inline policy statements.
                            # {
                            #     "sid": "AllowS3AppBucketRead",
                            #     "effect": "Allow", # "Allow" or "Deny"
                            #     "actions": ["s3:GetObject", "s3:ListBucket"],
                            #     "resources": ["arn:aws:s3:::my-application-bucket", "arn:aws:s3:::my-application-bucket/*"],
                            #     # "conditions": { "StringEquals": { "s3:x-amz-server-side-encryption": "AES256" } } # Optional conditions
                            # }
                        ],
                        # --- If create_new is False (use existing): ---
                        # "existing_profile_name": "MyExistingInstanceProfileName", # STRING: Name of an existing Instance Profile.
                        # "existing_profile_arn": "arn:aws:iam::ACCOUNTID:instance-profile/MyExistingInstanceProfileName" # STRING: ARN of an existing Instance Profile.
                        # Note: If using existing, ensure its role has necessary trust for ec2.amazonaws.com
                    },

                    "user_data": { # OBJECT: Configuration for instance user data.
                        "enabled": True,        # BOOLEAN: If False, no user data is applied.
                        "type": "RAW_TEXT",     # STRING: "SHELL_SCRIPT" (expects script_path or raw_commands with #!shebang), "RAW_TEXT".
                        # "script_path": "./user_data_scripts/my_bootstrap.sh", # STRING: Path to user data script file, relative to CDK app root.
                        # "raw_commands": "#!/bin/bash\nyum update -y\nyum install -y stress\necho 'User data executed' > /tmp/user_data.log", # STRING: Raw user data script content.
                        "raw_commands": "#!/bin/bash\nsudo yum update -y\nsudo yum install -y httpd\nsudo systemctl start httpd\nsudo systemctl enable httpd\necho \"OK\" | sudo tee /var/www/html/healthz\necho \"<html><body><h1>Hello from Standalone Instance: $(hostname -f)</h1></body></html>\" | sudo tee /var/www/html/index.html\n# Configure httpd to listen on 8080\nsudo sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf\n# Add a simple VirtualHost for 8080 if needed, or ensure default serves on 8080\n# This might be needed if default VirtualHost doesn't pick up the new Listen port:\n# echo \"<VirtualHost *:8080>\n#    DocumentRoot /var/www/html\n# </VirtualHost>\" | sudo tee /etc/httpd/conf.d/port8080.conf\nsudo systemctl restart httpd\nlogger \"User data for MyStandaloneWebServer1 completed.\""
                    },
                    
                    "mount_configurations": [
                        {
                            "enabled": False, 
                            "device_name_in_os": "/dev/xvdf", 
                            "mount_point": "/var/www/html/appdata", 
                            "filesystem_type": "ext4",      
                            "owner_user": "apache", "owner_group": "apache", "permissions": "775"
                        },
                        # {
                        #     "enabled": True,
                        #     "device_name_in_os": "/dev/xvdg", 
                        #     "mount_point": "/var/log/applogs",
                        #     "filesystem_type": "xfs",
                        # }
                    ],


                    "propagate_tags_to_volume_on_creation": True, # Propagates instance tags to EBS volumes at creation
                    "ssm_session_permissions": True, # If true and an IAM role is attached, adds permissions for SSM Session Manager

     # --- Optional Advanced Settings (controlled by their own "enabled" flags) ---
                    "cpu_options": { # OBJECT: Configure CPU core count and threads per core.
                        "enabled": False,
                        # "core_count": 2,        # INTEGER
                        # "threads_per_core": 1   # INTEGER: 1 or 2
                    },
                    "credit_specification": { # OBJECT: For T instances (burstable performance).
                        "enabled": False,
                        # "cpu_credits": "standard" # STRING: "standard" or "unlimited"
                    },
                    "placement_config": { # OBJECT: Specify EC2 placement group.
                        "enabled": False,
                        # "group_name": "my-compute-intensive-pg" # STRING: Name of an existing EC2 Placement Group.
                    },
                    "hibernation_options": { # OBJECT: Enable hibernation (instance type must support it).
                        "enabled": False # BOOLEAN
                    },
                    "enclave_options": { # OBJECT: Enable Nitro Enclaves (instance type must support it).
                        "enabled": False # BOOLEAN
                    },
                    "capacity_reservation_target": { # OBJECT: Target a capacity reservation.
                        "enabled": False,
                        # "capacity_reservation_id": "cr-0123456789abcdef0", # STRING: Specific CR ID.
                        # "capacity_reservation_resource_group_arn": "arn:aws:resource-groups:REGION:ACCOUNT:group/my-cr-group", # Target by CR group ARN.
                        # "preference": "open" # STRING: "open" (default, use if available) or "none" (do not use any CR).
                    },

                    "tags": { # OBJECT: Instance-specific tags. These are merged with and override default tags.
                        "Application": "MyExampleWebApp",
                        "CostCode": "XYZ789"
                    }
                } # End of 'config' for this instance definition
            }
        ],    
        "launch_templates": [
            {
                "id": "MyExampleLT",
                "enabled": True,
                "launch_template_name": "my-example-lt-v1",
                "version_description": "Comprehensive example launch template",

                "template_data": {
                    # --- Core Instance Configuration ---
                    "ami_config": { # OBJECT (Optional): Defines how to select the AMI. If not set, image_id must be set or AMI chosen at launch.
                        "source": "ID", # STRING: "LATEST_AMAZON_LINUX_2023", "LATEST_AMAZON_LINUX_2", "ID", "LOOKUP".
                         "id": "ami-0953476d60561c955",    # STRING: Required if source is "ID". Specific AMI ID.
                        "architecture": "x86_64"          # STRING: "x86_64" or "arm_64".
                        # "lookup_filters": { "name": "my-custom-ami-*", "owners": ["self"] } # If source is "LOOKUP"
                    },
                    # "image_id": "ami-xxxxxxxxxxxxxxxxx", # STRING (Optional): Direct AMI ID. Overrides ami_config if both present.

                    "instance_type": "t3.micro", # STRING (Optional): The instance type. If not set, must be provided at launch.

                    "key_name": "my-cdk-keypair", # STRING (Optional): Name of an existing EC2 KeyPair. <<< REPLACE

                    # UserData: Choose one method.
                    
                    
        "user_data": {
            "enabled": True,
            "type": "SHELL_SCRIPT", # Optional, defaults to SHELL_SCRIPT. Good to be explicit.
            # Option 1: Path to script (if you were using this)
            # "user_data_script_path": "./scripts/my_app_setup.sh", 

         # Option 2: Direct inline code (this is the new example)
            "user_data_code": "#!/bin/bash\nsudo yum update -y\nsudo yum install -y httpd\nsudo systemctl start httpd\nsudo systemctl enable httpd\necho \"OK\" | sudo tee /var/www/html/healthz\necho \"<html><body><h1>Hello from ASG Instance: $(hostname -f)</h1></body></html>\" | sudo tee /var/www/html/index.html\n# Configure httpd to listen on 8080\nsudo sed -i 's/Listen 80/Listen 8080/' /etc/httpd/conf/httpd.conf\nsudo systemctl restart httpd\nlogger \"User data for MyExampleLT (ASG) completed.\""
            
            # Option 3: Pre-base64 encoded (if you were using this)
            #"user_data_b64": "YOUR_PRE_ENCODED_SCRIPT_HERE"
        },
                    
                    # --- Networking ---
                    "network_interfaces": [ # LIST of OBJECTS (Optional): Define network interfaces.
                        {
                            "device_index": 0, # INTEGER (Required for list): Index of the ENI.
                            "subnet_id": "subnet-01068ebd6184d034b", # STRING (Optional): Subnet ID. If not set, must be provided at launch (e.g., by ASG). <<< REPLACE
                            "associate_public_ip_address": True, # BOOLEAN (Optional): Whether to associate a public IP.
                            "groups_ref_ids": ["AsgInstanceSG"],
                            # "groups": ["sg-07912ab450466bc24"], # LIST of STRINGS (Optional): List of Security Group IDs. <<< REPLACE
                            "delete_on_termination": True,  # BOOLEAN (Optional): Default is True for primary.
                            # "description": "Primary network interface for MyExampleLT", # STRING (Optional)
                            # "private_ip_address": "10.0.0.50", # STRING (Optional): Specific primary private IP.
                            # "private_ip_addresses": [ { "primary": False, "private_ip_address": "10.0.0.51" } ], # LIST of OBJECTS (Optional)
                            # "secondary_private_ip_address_count": 0, # INTEGER (Optional)
                            # "ipv6_address_count": 0, # INTEGER (Optional)
                            # "ipv6_addresses": [ { "ipv6_address": "2001:db8::1" } ], # LIST of OBJECTS (Optional)
                            # "network_interface_id": "eni-xxxxxxxxxxxxxxxxx", # STRING (Optional): Use an existing ENI.
                            # "network_card_index": 0 # INTEGER (Optional)
                        }
                    ],
                    # "security_group_ids": ["sg-xxxxxxxxxxxxxxxxx"], # LIST of STRINGS (Optional): Alternative if not using detailed network_interfaces.

                    # --- IAM ---
                      "iam_instance_profile": {
                        "enabled": True,
                        # Provide ARN or Name of an EXISTING Instance Profile.
                        "iam_instance_profile_arn": "arn:aws:iam::198484116691:instance-profile/ec2-new-ssm", # STRING (Optional) <<< REPLACE
                        # "iam_instance_profile_name": "YourExistingProfileForLT", # STRING (Optional)

                    },
                    

                    # --- Storage: Block Device Mappings ---
                    "block_device_mappings": { # OBJECT: Contains enabled flag and the list of mappings.
                        "enabled": False,       # BOOLEAN: If False, the "mappings" list below is ignored.  # If True, the "mappings" list is processed.# If this whole "block_device_mappings" object is absent, AMI defaults are used.
                        "mappings": [          # LIST of OBJECTS: Define EBS volumes or instance store.
                            {
                                "device_name": "/dev/xvda", 
                                "ebs": {
                                    "volume_size": 25, "volume_type": "gp3", "encrypted": True,
                                    "delete_on_termination": True
                                    # "iops": 3000, # INTEGER (Optional): For io1, io2, gp3.
                                    # "throughput": 125, # INTEGER (Optional): For gp3, in MiBps.
                                    # "snapshot_id": "snap-xxxxxxxxxxxxxxxxx", # STRING (Optional)
                                    # "kms_key_id": "arn:aws:kms:..." # STRING (Optional)
                                }
                            },
                            # {
                            #     "device_name": "/dev/sdb", # Example additional data volume
                            #     "ebs": {
                            #         "volume_size": 50, "volume_type": "gp2", "encrypted": True,
                            #         "delete_on_termination": False # Typically keep data volumes
                            #     }
                            # }
                        ]
                    },
                    # --- Advanced Instance Details ---
                    "monitoring_detailed": True, # BOOLEAN (Optional): True for 1-min monitoring. Default is False (5-min).
                    "disable_api_termination": False, # BOOLEAN (Optional): Default is False.
                    "disable_api_stop": False, # BOOLEAN (Optional): Only for instance-store AMIs. Default is False.
                    "instance_initiated_shutdown_behavior": "stop", # STRING (Optional): "stop" or "terminate". Default is "stop".
                    "ebs_optimized": False, # BOOLEAN (Optional): Default varies by instance type.

                    "metadata_options": { # OBJECT (Optional)
                        "enabled": True, # BOOLEAN: If False, this block is ignored.
                        "http_tokens": "required", # STRING: "optional" or "required".
                        "http_endpoint": "enabled", # STRING: "enabled" or "disabled".
                        "http_put_response_hop_limit": 1, # INTEGER
                        "instance_metadata_tags": "enabled" # STRING: "enabled" or "disabled".
                    },
                    "cpu_options": { # OBJECT (Optional)
                        "enabled": False, # BOOLEAN: If True, core_count and/or threads_per_core must be set.
                        # "core_count": 1,
                        # "threads_per_core": 1
                    },
                    "credit_specification": { # OBJECT (Optional): For T-instances.
                        "enabled": False, # BOOLEAN: If True, cpu_credits must be set.
                        # "cpu_credits": "standard" # "standard" or "unlimited"
                    },
                    "placement": { # OBJECT (Optional)
                        "enabled": False, # BOOLEAN: If True, at least one placement property should be set.
                        # "availability_zone": "us-east-1a",
                        # "affinity": "host", # "default" or "host"
                        # "group_name": "my-placement-group",
                        # "host_id": "h-xxxxxxxxxxxxxxxxx",
                        # "host_resource_group_arn": "arn:aws:resource-groups:...",
                        # "partition_number": 1,
                        # "tenancy": "dedicated" # "default", "dedicated", "host"
                    },
                    "capacity_reservation_specification": { # OBJECT (Optional)
                        "enabled": False, # BOOLEAN: If True, preference or target must be set.
                        # "capacity_reservation_preference": "open", # "open" or "none"
                        # "capacity_reservation_target": {
                        #     "capacity_reservation_id": "cr-xxxxxxxxxxxxxxxxx",
                        #     # OR "capacity_reservation_resource_group_arn": "arn:..."
                        # }
                    },
                    "hibernation_options": { # OBJECT (Optional)
                        "enabled": False, # BOOLEAN: If True, 'configured' must be true.
                        # "configured": True # BOOLEAN (Required if hib_options.enabled is True)
                    },
                    "license_specifications": { # OBJECT (Optional)
                        "enabled": False, # BOOLEAN: If True, 'specifications' list should be populated.
                        # "specifications": [ { "license_configuration_arn": "arn:aws:license-manager:..." } ]
                    },
                    # "elastic_gpu_specifications": { "enabled": False, "specifications": [ { "type": "eg1.medium" } ] },
                    # "elastic_inference_accelerators": { "enabled": False, "accelerators": [ { "type": "eia2.medium", "count": 1 } ]},
                    "enclave_options": { # OBJECT (Optional)
                        "enabled": False # BOOLEAN: If True, sets EnclaveOptions.Enabled to true.
                    }
                }, # End of template_data

                "tag_specifications": [
                    { "resource_type": "instance", "tags": {"Name": "InstanceFromMyExampleLT", "Application": "General"}},
                    { "resource_type": "volume", "tags": {"ManagedBy": "MyExampleLT"}}
                ],
                "tags": { "TemplateOwner": "DevTeam", "ReviewDate": "2025-12-31" }
            }
        
            # Add more EC2 instance configurations (dictionaries) here in the "instances" list
        ],
        # end of the launchtemplates
        "ebs_volumes": [ # LIST of OBJECTS: Each dictionary defines one EBS Volume.
            {
                "id": "MyApplicationDataVolume", # STRING (Required): Unique logical ID for this config entry. Used for CDK construct IDs.
                "enabled": False,                 # BOOLEAN: If False, this specific volume definition is skipped.
                "config": { # OBJECT: Contains all specific configurations for this volume.
                    "volume_name_tag": "app-server-data-vol-01", # STRING (Optional): Value for the "Name" tag of the EBS volume.
                    "availability_zone": "us-east-1a", # STRING (Required): The AZ for volume creation (e.g., "us-east-1a", "ap-south-1b"). <<< REPLACE
                    
                    # --- Size & Snapshot ---
                    "size_gb": 50,          # INTEGER (Optional if snapshot_id provided): Size in GiB.
                    # "snapshot_id": "snap-0123456789abcdef0", # STRING (Optional): If provided, volume is created from this snapshot.
                                                            # If snapshot_id is used, size_gb is optional (defaults to snapshot size or must be >= snapshot size).

                    # --- Type, IOPS, Throughput ---
                    "volume_type": "io1",   # STRING (Optional): Overrides default.
                                                                # Possible values: "standard" (magnetic), "gp2", "gp3" (General Purpose SSD),
                                                                # "io1", "io2" (Provisioned IOPS SSD), "sc1" (Cold HDD), "st1" (Throughput Optimized HDD).
                    "iops": 1000,          # INTEGER (Optional): Provisioned IOPS.
                                                                # Required for "io1", "io2".
                                                                # Configurable for "gp3" (default 3000 if not set). Ignored for other types.
                    # "throughput": 250,    # INTEGER (Optional): Throughput in MiBps (125-1000). Only applicable for "gp3".
                                                                # If 'volume_type' is "gp3" and 'throughput' is not set, it defaults to 125 MiBps.

                    # --- Encryption ---
                    "encrypted": True,      # BOOLEAN (Optional): Overrides default.
                    # "kms_key_id": "arn:aws:kms:us-east-1:123456789012:key/your-custom-ebs-kms-key-id", # STRING (Optional): ARN of a custom KMS key. <<< REPLACE
                                                                                           # If 'encrypted' is True and 'kms_key_id' is not provided, uses the AWS-managed KMS key for EBS.


                    # --- Other Volume Properties ---
                    # "multi_attach_enabled": False,  # BOOLEAN (Optional): Default is False.
                                                                # If True, enables Multi-Attach for "io1" or "io2" volumes, allowing attachment to multiple Nitro-based instances in the same AZ.

                    "removal_policy": "RETAIN",   # STRING (Optional): "RETAIN" or "DESTROY". Overrides default.
                                                                # Determines what happens to the volume when the CDK stack is deleted.


                    "tags": { # OBJECT (Optional): Additional tags for this specific EBS volume resource. Merged with default tags.
                        "Application": "OrderProcessing",
                        "Environment": "ProductionData"
                    },

                    # --- Attachment Configuration (for attaching this standalone volume to an EXISTING instance) ---
                    "attachment_config": {                     # OBJECT (Optional): Defines if and how to attach the volume.
                        "enabled": True,                       # BOOLEAN: If True, volume will be ATTACHED by the EbsVolumeStack.
                                                               # If False, volume is created but not attached.
                        "instance_id": "i-04e07f0dae94fe841", # STRING (Required if attachment_config.enabled is True): Physical ID of an EXISTING EC2 instance. <<< REPLACE
                        "device_name": "/dev/sdh",             # STRING (Required if attachment_config.enabled is True): Device name for attachment
                                                               # (e.g., /dev/sdf through /dev/sdp on Linux, or /dev/xvd[f-p]).
                                                               # Ensure this device name is not already in use on the target instance.
                        
                        # --- Parameters for your external mounting script/process (NOT used by CDK directly for attachment) ---
                       
                    }
                }
            },
            {
                "id": "AnotherVolumeExample",
                "enabled": False, # This volume will not be created or processed.
                "config": {
                    "volume_name_tag": "archive-vol",
                    "availability_zone": "us-east-1b", # <<< REPLACE
                    "size_gb": 500,
                    "volume_type": "sc1" # Cold HDD
                    # No attachment_config means it uses the default (enabled: False).
                }
            }
            # Add more EBS volume definitions here
        ],
       "application_load_balancers": [ # LIST of OBJECTS: Each object defines one Application Load Balancer.
        {
            "id": "MyWebAppALB",         # STRING (Required): Unique logical ID for this ALB configuration within the CDK app. Used for naming CDK constructs.
            "enabled": True,             # BOOLEAN (Required): Set to 'true' to deploy this ALB, 'false' to skip its creation.
            "config": {                  # OBJECT (Required): Contains all specific configurations for this ALB.
                "load_balancer_name": "my-web-app-alb-example", # STRING (Optional): The physical name of the Application Load Balancer. If omitted, a name is auto-generated by CloudFormation.
                "vpc_id": "vpc-0682a04278f37a95c",      # STRING (Required): The ID of the VPC in which to create the ALB.
                "internet_facing": True,                # BOOLEAN (Required): Set to 'true' for an internet-facing ALB, 'false' for an internal ALB.
                "ip_address_type": "IPV4",              # STRING (Optional): The IP address type. Default: "IPV4".
                                                        # Possible values: "IPV4", "DUALSTACK" (supports both IPv4 and IPv6).
                
                # Subnets for the ALB. The ALB will operate in the Availability Zones of these subnets.
                # For high availability, provide public subnet IDs from at least two different Availability Zones for an internet-facing ALB.
                # For internal ALBs, provide private subnet IDs from at least two different Availability Zones.
                "subnet_ids": ["subnet-0d0c5c4014c9737be", "subnet-01068ebd6184d034b"], # LIST of STRINGS (Required, if 'subnet_selection' is not used): Specific physical subnet IDs.
                
                # "subnet_selection": { # OBJECT (Optional): Alternative to 'subnet_ids' for selecting subnets based on type or group name (tags).
                #     "subnet_type": "PUBLIC",      # STRING (Optional): Type of subnets to select. 
                #                                   # Possible values: "PUBLIC", "PRIVATE_WITH_EGRESS", "PRIVATE_ISOLATED".
                #                                   # Defaults to "PUBLIC" if 'internet_facing' is true, else "PRIVATE_WITH_EGRESS".
                #     "subnet_group_name": "MyWebTierSubnets" # STRING (Optional): Selects subnets with the tag 'aws-cdk:subnet-name' matching this value.
                # },
                "security_group_refs": ["AlbSG"],
                # "security_group_ids": ["sg-07912ab450466bc24"], # LIST of STRINGS (Optional): List of existing security group IDs to associate with the ALB. 
                                          # If empty and 'custom_security_group.enabled' is false, CDK creates a default security group.
                                          # Note: ALB L2 construct directly takes one 'security_group' prop. Additional SGs are associated.

                "custom_security_group": { # OBJECT (Optional): Defines a new security group specifically for this ALB if 'security_group_ids' is empty or this is preferred.
                    "enabled": False,       # BOOLEAN (Required if 'custom_security_group' object is present): 'true' to create this SG if 'security_group_ids' is empty.
                    "name": "my-alb-sg",   # STRING (Optional): Physical name for the new security group. Auto-generated if omitted.
                    "description": "Security group for MyWebAppALB", # STRING (Optional): Description for the new SG.
                    "allow_all_outbound": True, # BOOLEAN (Optional): Default is 'true'. Set to 'false' to define specific egress rules.
                    "ingress_rules": [     # LIST of OBJECTS (Optional): Ingress rules for the new security group.
                        { 
                            "description": "Allow HTTP from Public", # STRING (Optional): Description of the rule.
                            "peer_type": "ANY_IPV4",  # STRING (Required): Source of traffic. 
                                                      # Values: "ANY_IPV4", "ANY_IPV6", "CIDR_IP", "SECURITY_GROUP", "PREFIX_LIST".
                            # "peer_value": "1.2.3.4/32", # STRING (Required for "CIDR_IP", "SECURITY_GROUP", "PREFIX_LIST").
                            "protocol": "tcp",        # STRING (Required): Protocol. Values: "tcp", "udp", "icmp", "-1" (all).
                            "port": 80                # INTEGER (Optional): Single port number. Use 'from_port' and 'to_port' for ranges.
                            # "from_port": 80,       # INTEGER (Optional): Start of port range.
                            # "to_port": 80          # INTEGER (Optional): End of port range.
                        },
                        { "description": "Allow HTTPS from Public", "peer_type": "ANY_IPV4", "protocol": "tcp", "port": 443 }
                    ]
                    # "egress_rules": [] # LIST of OBJECTS (Optional): Define only if 'allow_all_outbound' is false.
                },

                "deletion_protection": False,           # BOOLEAN (Optional): Default is 'false'. If 'true', prevents accidental deletion of the ALB.
                "http2_enabled": True,                  # BOOLEAN (Optional): Default is 'true'. Enables HTTP/2 support.
                "idle_timeout_seconds": 60,             # INTEGER (Optional): Default is 60 seconds. Range: 1-4000 seconds.
                "desync_mitigation_mode": "DEFENSIVE",  # STRING (Optional): Default: "DEFENSIVE". How the load balancer handles requests that might lead to HTTP desync.
                                                        # Possible values: "OFF", "MONITOR", "DEFENSIVE".
                "drop_invalid_header_fields": False,    # BOOLEAN (Optional): Default is 'false'. If 'true', drops HTTP headers with invalid characters.
                "preserve_host_header": False,          # BOOLEAN (Optional): Default is 'false'. If 'true', preserves the Host header of the incoming request when forwarding.
                "xff_header_processing_mode": "APPEND", # STRING (Optional): Default: "APPEND". Determines how X-Forwarded-For headers are handled.
                                                        # Possible values: "APPEND", "PRESERVE", "REMOVE".

                "access_logs": {                        # OBJECT (Optional): Configuration for ALB access logs.
                    "enabled": False,                   # BOOLEAN (Required if 'access_logs' object is present): 'true' to enable access logs.
                    "s3_bucket_name": "your-alb-access-logs-bucket-name", # STRING (Required if 'enabled' is true): Name of an S3 bucket that must already exist.
                                                                          # The ALB service principal needs PutObject permissions on this bucket/prefix.
                    "s3_prefix": "alb-logs/MyWebAppALB" # STRING (Optional): Prefix for log files within the S3 bucket.
                },
                
                # "waf_acl_arn": "arn:aws:wafv2:REGION:ACCOUNT:regional/webacl/MyWebACL/GUID", # STRING (Optional): ARN of an AWS WAFv2 WebACL to associate with this ALB.

                "attributes": [                         # LIST of OBJECTS (Optional): For setting various load balancer attributes.
                                                        # Refer to AWS documentation for available keys and values.
                    # Example: Enable X-Forwarded-For client port preservation
                    # {"key": "routing.http.xff_client_port.enabled", "value": "true"},
                    # Example: Enable connection draining
                    # {"key": "deregistration_delay.connection_termination.enabled", "value": "true"} 
                ],

                "listeners": [                          # LIST of OBJECTS (Required): Defines one or more listeners for the ALB.
                    {
                        "id": "HttpListener",           # STRING (Required): Logical ID for this listener within the ALB's configuration (used for CDK construct ID).
                        "enabled": True,                # BOOLEAN (Optional): Default is 'true'. 'true' to create this listener.
                        "port": 80,                     # INTEGER (Required): The port on which the load balancer listens.
                        "protocol": "HTTP",             # STRING (Required): Protocol for connections from clients to the load balancer.
                                                        # Possible values: "HTTP", "HTTPS".
                        # "certificates": [],          # LIST of OBJECTS (Required for "HTTPS" protocol): Specifies ACM certificates.
                                                        # Example: [{ "certificate_arn": "arn:aws:acm:region:account-id:certificate/your-cert-id" }]
                        # "ssl_policy": "ELBSecurityPolicy_2016_08", # STRING (Optional for "HTTPS"): Security policy for HTTPS listeners.
                                                        # Example values: "ELBSecurityPolicy_2016_08", "ELBSecurityPolicy_TLS_1_2_EXT_2018_06", 
                                                        # "ELBSecurityPolicy_FS_1_2_RES_2020_10". Defaults to a recommended AWS policy.
                        "default_action": {             # OBJECT (Required): The default action to take when a request matches the listener.
                            "type": "FORWARD",          # STRING (Required): Type of action.
                                                        # Possible values: "FORWARD", "REDIRECT", "FIXED_RESPONSE", 
                                                        # "AUTHENTICATE_COGNITO", "AUTHENTICATE_OIDC".
                            
                            # --- For type: "FORWARD" ---
                            "target_group_id": "AppServersTG", # STRING (Required if 'type' is "FORWARD" and not using 'existing_target_group_arn'): 
                                                               # Logical 'id' of a target group defined in the 'target_groups' section below.
                            # "existing_target_group_arn": "arn:aws:elasticloadbalancing:region:account:targetgroup/name/id", # STRING (Optional if 'type' is "FORWARD"):
                                                               # ARN of an existing target group to forward to. Use this OR 'target_group_id'.

                            # --- For type: "REDIRECT" ---
                            # "redirect_config": {      # OBJECT (Required if 'type' is "REDIRECT")
                            #     "protocol": "HTTPS",  # STRING (Optional): Default: "#{protocol}". Values: "HTTP", "HTTPS", "#{protocol}".
                            #     "port": "443",        # STRING (Optional): Default: "#{port}". Specific port number (as string) or "#{port}".
                            #     "host": "#{host}",    # STRING (Optional): Default: "#{host}". Specific host or "#{host}".
                            #     "path": "/#{path}",   # STRING (Optional): Default: "/#{path}". Specific path or "/#{path}".
                            #     "query": "#{query}",  # STRING (Optional): Default: "#{query}". Specific query string or "#{query}".
                            #     "status_code": "HTTP_301" # STRING (Required): "HTTP_301" (permanent) or "HTTP_302" (temporary).
                            # },

                            # --- For type: "FIXED_RESPONSE" ---
                            # "fixed_response_config": {# OBJECT (Required if 'type' is "FIXED_RESPONSE")
                            #     "status_code": "200", # STRING (Required): HTTP status code (e.g., "200", "403", "503").
                            #     "content_type": "text/plain", # STRING (Optional): MIME type (e.g., "text/plain", "application/json").
                            #     "message_body": "OK"  # STRING (Optional): Content of the response body.
                            # }

                            # --- For type: "AUTHENTICATE_COGNITO" (Details omitted for brevity) ---
                            # "authenticate_cognito_config": { # ... Cognito User Pool ARN, Client ID, etc. ... # }

                            # --- For type: "AUTHENTICATE_OIDC" (Details omitted for brevity) ---
                            # "authenticate_oidc_config": { # ... Issuer, Authorization Endpoint, Token Endpoint, Client ID, Client Secret, etc. ... # }
                        }
                        # "rules": [] # LIST of OBJECTS (Optional): Listener rules for more complex routing (not covered in this basic example).
                    },
                    {
                        "id": "HttpsListener",
                        "enabled": False,
                        "port": 443,
                        "protocol": "HTTPS",
                         "certificates": [ 
                             { "certificate_arn": "arn:aws:acm:us-east-1:198484116691:certificate/your-acm-certificate-id-for-alb" } # <<< REPLACE with your actual ACM cert ARN
                        ],
                         "ssl_policy": "ELBSecurityPolicy_TLS_1_2_EXT_2018_06", 
                        "default_action": {
                            "type": "FORWARD",
                            "target_group_id": "AppServersTG" 
                        }
                    }
                ],

                "target_groups": [                      # LIST of OBJECTS (Optional): Defines target groups for this ALB.
                    {
                        "id": "AppServersTG",           # STRING (Required): Logical ID for this target group within the ALB's config.
                        "enabled": True,                # BOOLEAN (Optional): Default is 'true'. 'true' to create this target group.
                        "config": {                     # OBJECT (Required): Configuration for this target group.
                            "target_group_name": "my-app-servers-tg-example", # STRING (Optional): Physical name. Auto-generated if omitted.
                            # "vpc_id": "vpc-xxxxxxxxxxxxxxxxx", # STRING (Optional): VPC ID for the target group. Defaults to the ALB's VPC if omitted.
                            "port": 8080,               # INTEGER (Required): Port on which targets receive traffic from the load balancer.
                            "protocol": "HTTP",         # STRING (Required): Protocol for traffic from ALB to targets. "HTTP" or "HTTPS".
                                                        # For LAMBDA target_type, this is not applicable and usually omitted (or use HTTP).
                            "protocol_version": "HTTP1",# STRING (Optional): Default: "HTTP1". Values: "HTTP1", "GRPC". 
                                                        # Applicable if protocol is HTTP or HTTPS.
                            "target_type": "INSTANCE",  # STRING (Required): Type of targets in this group. 
                                                        # Possible values: "INSTANCE", "IP", "LAMBDA", "ALB".
                            
                            "health_check": {           # OBJECT (Optional): Health check settings for targets. Defaults are applied if this object is present but fields omitted.
                                "enabled": True,        # BOOLEAN (Optional): Default is 'true'.
                                "protocol": "HTTP",     # STRING (Optional): Default: "HTTP" (if target group protocol is HTTP/HTTPS), else "TCP". 
                                                        # Values: "HTTP", "HTTPS", "TCP" (NLB also supports TLS, UDP, TCP_UDP).
                                "port": "8080", # STRING (Optional): Default: "traffic-port" (uses target's port). Or specific port number (as string).
                                "path": "/healthz",     # STRING (Optional): Default: "/". Required for HTTP/HTTPS health checks. Path to ping.
                                "interval_seconds": 35, # INTEGER (Optional): Default: 30 (instance/ip), 35 (Lambda). Range: 5-300.
                                "timeout_seconds": 30,  # INTEGER (Optional): Default: 5 (instance/ip), 30 (Lambda). Range: 2-120. Must be less than interval.
                                "healthy_threshold_count": 2,   # INTEGER (Optional): Default: 3 (instance/ip), 2 (Lambda). Range: 2-10.
                                "unhealthy_threshold_count": 2, # INTEGER (Optional): Default: 3 (instance/ip), 2 (Lambda). Range: 2-10.
                                "matcher_http_codes": "200" # STRING (Optional): Default: "200". For HTTP/HTTPS. Comma-separated or range (e.g., "200,202", "200-299").
                            },
                            "deregistration_delay_seconds": 60, # INTEGER (Optional): Default: 300. Time to wait for in-flight requests to complete on deregistering targets. Range: 0-3600.
                            
                            "stickiness": {             # OBJECT (Optional): Configures stickiness (session affinity).
                                "enabled": False,       # BOOLEAN (Required if 'stickiness' object is present).
                                # For ALB, only "LOAD_BALANCER_COOKIE" (duration-based) or "APPLICATION_COOKIE" types are common.
                                # L2 construct handles APPLICATION_COOKIE via `enable_cookie_stickiness`.
                                # For LOAD_BALANCER_COOKIE, you'd set via attributes.
                                "type": "APPLICATION_COOKIE", # STRING (Optional): "APPLICATION_COOKIE" or "LOAD_BALANCER_COOKIE".
                                "cookie_duration_seconds": 86400, # INTEGER (Optional if type is LB_COOKIE [session only], Required for APP_COOKIE or if type is LB_COOKIE and duration is desired): Max 7 days (604800s).
                                "cookie_name": "MyAppCookie" # STRING (Required if type is "APPLICATION_COOKIE").
                            },
                            
                            # "attributes": [],        # LIST of OBJECTS (Optional): For setting advanced target group attributes.
                                                        # Example: {"key": "stickiness.enabled", "value": "true"}, 
                                                        #          {"key": "stickiness.type", "value": "lb_cookie"},
                                                        #          {"key": "stickiness.lb_cookie.duration_seconds", "value": "86400"}
                                                        #          {"key": "load_balancing.algorithm.type", "value": "least_outstanding_requests"} (default ROUND_ROBIN)
                                                        #          {"key": "slow_start.duration_seconds", "value": "30"}

                            "targets": [                # LIST of OBJECTS (Optional): Register targets directly. All targets must match the TG's 'target_type'.
                                # --- For target_type: "INSTANCE" ---
                                { "instance_id_ref": "MyStandaloneWebServer1" },
                                # { "instance_id": "i-06c8d6474394df6d2" },
                                # { "instance_id": "i-064f96fa6ee7a12d" },
                                
                                # --- For target_type: "IP" ---
                                # { 
                                #     "ip_address": "10.0.1.100", # STRING (Required): IP address.
                                #     "port": 8080,              # INTEGER (Optional): Port for this specific IP target. Defaults to TG port.
                                #     "availability_zone": "us-east-1a" # STRING (Optional): For IPs outside VPC CIDR, or to specify AZ. "all" can be used for IPs in VPC.
                                # },
                                
                                # --- For target_type: "LAMBDA" ---
                                # { 
                                #     "lambda_function_arn": "arn:aws:lambda:region:account:function:my-function" # STRING (Required): ARN of the Lambda function.
                                # }
                            ]
                        }
                    }
                    # Define more target groups
                ]
            }
        }
        # Define more ALBs
     ],

     "network_load_balancers": [ # LIST of OBJECTS: Each object defines one Network Load Balancer.
        {
            "id": "MyCoreServicesNLB",   # STRING (Required): Unique logical ID for this NLB configuration within the CDK app.
            "enabled": False,             # BOOLEAN (Required): Set to 'true' to deploy this NLB, 'false' to skip.
            "config": {                  # OBJECT (Required): Contains all specific configurations for this NLB.
                "load_balancer_name": "my-core-nlb", # STRING (Optional): The physical name of the Network Load Balancer. Auto-generated if omitted.
                "vpc_id": "vpc-0682a04278f37a95c",   # STRING (Required): The ID of the VPC in which to create the NLB.
                "internet_facing": False,            # BOOLEAN (Required): Set to 'true' for an internet-facing NLB, 'false' for an internal NLB.
                                                     # Internet-facing NLBs require Elastic IP Addresses (EIPs) for static IPs, either allocated by AWS or provided by you.
                
                # Subnets for the NLB. The NLB will have a network interface (and potentially a static IP) in each specified subnet's AZ.
                # For HA, provide subnets from at least two different Availability Zones.
                # For internet-facing NLBs, these are typically public subnets. For internal, private subnets.
                "subnet_ids": ["subnet-01068ebd6184d034b","subnet-0d0c5c4014c9737be"], # LIST of STRINGS (Required if 'subnet_selection' not used): Specific physical subnet IDs.
                
                # "subnet_selection": { # OBJECT (Optional): Alternative to 'subnet_ids'.
          
                #     "subnet_type": "PRIVATE_WITH_EGRESS", # STRING (Optional): e.g., "PUBLIC", "PRIVATE_WITH_EGRESS", "PRIVATE_ISOLATED".
                #     "subnet_group_name": "MyNLBSubnets"   # STRING (Optional): Selects subnets by tag.
                # },

                # For internet-facing NLBs, you can allocate EIPs or have AWS do it.
                # For internal NLBs, you can assign private IP addresses.
                "subnet_mappings": [ # LIST of OBJECTS (Optional): Use for assigning specific EIP Allocation IDs or Private IPs per subnet.
                                     # The order should correspond to the order of subnets if using subnet_ids, or match subnets found by selection.
                    {
                        #"subnet_id": "subnet-az1-xxxxxxxx", # STRING (Required if using subnet_mappings)
                        # For Internet-facing NLB:
                        #"allocation_id": "eipalloc-xxxxxxxxxxxxxxxxx" # STRING (Optional): Allocation ID of an existing Elastic IP address.
                        # For Internal NLB:
                        # "private_ipv4_address": "10.0.1.100",      # STRING (Optional): A specific private IPv4 address from the subnet.
                        # "ipv6_address": "2600:xxxx::yyyy"       # STRING (Optional): A specific IPv6 address from the subnet (if VPC/subnet is IPv6 enabled).
                    }
                    # Add more mappings if needed for other subnets
                ],

                "ip_address_type": "IPV4",             # STRING (Optional): Default: "IPV4". Not directly set on NLB like ALB, but influences ENI IP.
                                                       # NLBs primarily use IPv4. Dual-stack for NLB means its DNS resolves to IPv4 and IPv6,
                                                       # and it can have IPv6 targets if the target group protocol is TCP/TLS.
                                                       # This setting is more about the target groups and DNS.

                "cross_zone_load_balancing": True,     # BOOLEAN (Optional): Default is 'false'. If 'true', distributes traffic across targets in all enabled AZs.
                                                       # If 'false', traffic is only routed to targets in the AZ of the NLB node that received the request.
                "deletion_protection": False,          # BOOLEAN (Optional): Default is 'false'.
                "client_ip_preservation": True,        # BOOLEAN (Optional): Default is 'false' for TCP/TLS listeners. If 'true', preserves client IP to targets.
                                                       # Requires targets to be in the same VPC or peered VPCs. Not applicable for UDP.
                "dns_record_type": "IPV4",             # STRING (Optional): Default is "IPV4". For NLB DNS name. "DUALSTACK" is also an option if NLB is configured for it.

                "access_logs": {                       # OBJECT (Optional): Configuration for NLB access logs (for TLS listeners only).
                    "enabled": False,                  # BOOLEAN (Required if 'access_logs' object is present).
                    "s3_bucket_name": "your-nlb-access-logs-bucket", # STRING (Required if 'enabled' is true).
                    "s3_prefix": "nlb-logs/MyCoreNLB"  # STRING (Optional).
                },

                "attributes": [                        # LIST of OBJECTS (Optional): For setting load balancer attributes.
                    # Example: {"key": "load_balancing.cross_zone.enabled", "value": "true"}
                ],

                "listeners": [                         # LIST of OBJECTS (Required): Defines listeners for the NLB.
                    {
                        "id": "TCPListener8080",       # STRING (Required): Logical ID for this listener.
                        "enabled": True,               # BOOLEAN (Optional): Default 'true'.
                        "port": 8080,                  # INTEGER (Required): Port for the listener.
                        "protocol": "TCP",             # STRING (Required): "TCP", "UDP", "TLS", "TCP_UDP".
                        
                        # For "TLS" protocol listeners:
                        # "certificates": [            # LIST of OBJECTS (Required for "TLS"): ACM certificates.
                        #     { "certificate_arn": "arn:aws:acm:region:account-id:certificate/your-cert-id" }
                        #     # Can add more for SNI if needed, or use add_certificates later.
                        # ],
                        # "alpn_policy": ["HTTP2Preferred"], # LIST of STRINGS (Optional for "TLS"): ALPN policies. 
                        #                                 # Values: "HTTP1Only", "HTTP2Only", "HTTP2Optional", "HTTP2Preferred", "None".
                        # "ssl_policy": "ELBSecurityPolicy_2016_08", # STRING (Optional for "TLS"): Security policy.

                        "default_action": {            # OBJECT (Required): Default action for the listener.
                                                       # NLB listeners primarily support FORWARD actions.
                            "type": "FORWARD",         # STRING (Required): Must be "FORWARD" for NLB default actions.
                            # Provide EITHER target_group_id (for TGs defined below) OR existing_target_group_arn
                            "target_group_id": "MyTCPTargetGroup" 
                            # "existing_target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:198484116691:targetgroup/my-web-app-instance-tg/8496ecaaabed724a"
                        }
                    }
                    # Example UDP Listener:
                    # {
                    #     "id": "UDPListener5000",
                    #     "enabled": true,
                    #     "port": 5000,
                    #     "protocol": "UDP",
                    #     "default_action": { "type": "FORWARD", "target_group_id": "MyUDPTargetGroup" }
                    # }
                ],

                "target_groups": [                     # LIST of OBJECTS (Optional): Defines target groups for this NLB.
                    {
                        "id": "MyTCPTargetGroup",      # STRING (Required): Logical ID for this target group.
                        "enabled": True,               # BOOLEAN (Optional): Default 'true'.
                        "config": {                    # OBJECT (Required): Configuration for this target group.
                            "target_group_name": "my-tcp-targets", # STRING (Optional): Physical name.
                            # "vpc_id": "vpc-xxxxxxxxxxxxxxxxx", # STRING (Optional): Defaults to NLB's VPC.
                            "port": 8080,              # INTEGER (Required): Port on which targets receive traffic.
                            "protocol": "TCP",         # STRING (Required): "TCP", "UDP", "TLS", "TCP_UDP".
                                                       # Must match or be compatible with listener protocol.
                                                       # For TLS target groups, NLB terminates TLS from client, then re-encrypts to target.
                            "target_type": "INSTANCE", # STRING (Required): "INSTANCE", "IP", "ALB". (NLB cannot target Lambda directly).
                            
                            "health_check": {          # OBJECT (Optional): Health check settings.
                                "enabled": True,       # BOOLEAN (Optional): Default 'true'.
                                "protocol": "TCP",     # STRING (Optional): Default "TCP". Values: "TCP", "HTTP", "HTTPS".
                                                       # For TCP, a successful TCP handshake is a pass.
                                                       # For HTTP/HTTPS, an HTTP 2xx-3xx response is a pass.
                                "port": "traffic-port",# STRING (Optional): Default "traffic-port". Or specific port number as string.
                                # "path": "/health",  # STRING (Optional): Only for HTTP/HTTPS health checks. Default "/".
                                "interval_seconds": 30,# INTEGER (Optional): Default 30. Range 5-300 for TCP/HTTP/HTTPS. 10 or 30 for others.
                                "healthy_threshold_count": 3,  # INTEGER (Optional): Default 3. Range 2-10.
                                "unhealthy_threshold_count": 3,# INTEGER (Optional): Default 3. Range 2-10.
                                # "timeout_seconds": 10, # INTEGER (Optional): Only for HTTP/HTTPS. Default 10 for instance/ip, 6 for ALB.
                                # "matcher_http_codes": "200" # STRING (Optional): Only for HTTP/HTTPS. Default "200".
                                "health_check_port_override": "traffic-port" # STRING (Optional): Port for health checks. Default is "traffic-port". Can be a specific port.
                            },
                            "deregistration_delay_seconds": 300, # INTEGER (Optional): Default 300. Range 0-3600.
                            "preserve_client_ip": True, # BOOLEAN (Optional): Default 'false'. Set to 'true' to preserve client IP to targets.
                                                        # Target security groups must allow client IPs. Only for TCP/TLS target groups.
                            
                            "attributes": [           # LIST of OBJECTS (Optional): For setting target group attributes.
                                # Example: {"key": "deregistration_delay.timeout_seconds", "value": "60"}
                                # Example: {"key": "preserve_client_ip.enabled", "value": "true"}
                            ],

                            "targets": [              # LIST of OBJECTS (Optional): Register targets.
                                # --- For target_type: "INSTANCE" ---
                                { "instance_id": "i-07095010b67244ed2" },
                                { "instance_id": "i-09af4fb853f9f0d70" },
                                # --- For target_type: "IP" ---
                                # { "ip_address": "10.0.2.200", "port": 8080, "availability_zone": "us-east-1a" },
                                # --- For target_type: "ALB" (ARN of an Application Load Balancer) ---
                                # { "alb_arn": "arn:aws:elasticloadbalancing:region:account:loadbalancer/app/my-app-alb/id" }
                            ]
                        }
                    }
                    # Example UDP Target Group
                    # {
                    #     "id": "MyUDPTargetGroup",
                    #     "enabled": true,
                    #     "config": {
                    #         "target_group_name": "my-udp-targets",
                    #         "port": 5000,
                    #         "protocol": "UDP",
                    #         "target_type": "INSTANCE",
                    #         "health_check": { "enabled": true, "protocol": "HTTP", "port": "80", "path": "/udp_health" } # UDP TGs can have HTTP/TCP health checks
                    #     }
                    # }
                ]
                
            }
        }
        # Define more NLBs
      ],
     "target_groups": [ # LIST of OBJECTS: Each object defines one Target Group.
            {
                "id": "MyWebAppInstanceTG",   # STRING (Required): Unique logical ID for this TG config. Used for CDK construct ID.
                "enabled": False,              # BOOLEAN (Required): 'true' to deploy this TG, 'false' to skip.
                "config": {                   # OBJECT (Required): Contains all configurations for this Target Group.
                    "target_group_name": "my-web-app-instance-tg", # STRING (Optional): Physical name. Auto-generated if omitted.
                    "load_balancer_type": "NETWORK", # STRING (Required): "APPLICATION" or "NETWORK". Determines if ALB or NLB TG is created.
                    
                    "vpc_id": "vpc-0682a04278f37a95c",   # STRING (Required): The ID of the VPC in which to create the Target Group.
                    
                    "port": 8080,                  # INTEGER (Required): The port on which the targets receive traffic.
                    "protocol": "TCP",            # STRING (Required): Protocol for traffic to targets.
                                                   # For "APPLICATION" LB type: "HTTP", "HTTPS".
                                                   # For "NETWORK" LB type: "TCP", "UDP", "TLS", "TCP_UDP".
                    
                    # "protocol_version": "HTTP1",   # STRING (Optional): Default: "HTTP1". Values: "HTTP1", "GRPC".
                                                   # Applicable if 'load_balancer_type' is "APPLICATION" and 'protocol' is HTTP or HTTPS.
                                                   
                    "target_type": "INSTANCE",     # STRING (Required): Type of targets in this group.
                                                   # For "APPLICATION" LB type: "INSTANCE", "IP", "LAMBDA", "ALB".
                                                   # For "NETWORK" LB type: "INSTANCE", "IP", "ALB". (NLB cannot target Lambda directly).

                    "health_check": {              # OBJECT (Optional): Health check settings for targets.
                        "enabled": True,           # BOOLEAN (Optional): Default is 'true'.
                        "protocol": "TCP",        # STRING (Optional): Protocol for health checks.
                                                   # For "APPLICATION" LB type (if TG protocol is HTTP/HTTPS): "HTTP", "HTTPS". Default: HTTP.
                                                   # For "NETWORK" LB type: "TCP", "HTTP", "HTTPS". Default: TCP.
                        "port": "traffic-port",    # STRING (Optional): Default: "traffic-port". Or specific port number (as string).
                                                   # For NLB, can also use "health-check-port" to specify a different port for health checks.
                        # "path": "/healthz",        # STRING (Optional): Default: "/". Required for HTTP/HTTPS health checks.
                        "interval_seconds": 30,    # INTEGEc:\\Users\\ChaithraShreeKR\\OneDrive - CloudThat\\Desktop\\CRMP-Project\\cdk_project\\package.json$0R (Optional): Default: 30s (ALB/NLB instance/ip), 35s (ALB Lambda). Range varies by LB type & HC protocol.
                        "timeout_seconds": 5,      # INTEGER (Optional): Default: 5s (ALB instance/ip), 6s (NLB HTTP/HTTPS), 10s (NLB TCP). Range varies. Must be < interval.
                        "healthy_threshold_count": 3,  # INTEGER (Optional): Default: 3 (ALB/NLB instance/ip), 2 (ALB Lambda). Range 2-10.
                        "unhealthy_threshold_count": 3,# INTEGER (Optional): Default: 3 (ALB/NLB instance/ip), 2 (ALB Lambda). Range 2-10.
                        "matcher_http_codes": "200" # STRING (Optional): Default: "200". For HTTP/HTTPS. Comma-separated or range (e.g., "200,202", "200-299").
                        # "health_check_port_override": "8081" # STRING (Optional, NLB specific): Overrides health check port.
                    },

                    "deregistration_delay_seconds": 60, # INTEGER (Optional): Default: 300. Range 0-3600.
                    
                    # --- Application Load Balancer Target Group Specific ---
                    "stickiness": {                # OBJECT (Optional, for "APPLICATION" LB type only): Configures stickiness.
                        "enabled": False,          # BOOLEAN (Required if 'stickiness' object is present).
                        "type": "APPLICATION_COOKIE", # STRING (Optional): "APPLICATION_COOKIE" or "LOAD_BALANCER_COOKIE" (via attributes for duration based).
                        "cookie_duration_seconds": 86400, # INTEGER (Optional): Duration for stickiness.
                        "cookie_name": "MyAppStickinessCookie" # STRING (Required if type is "APPLICATION_COOKIE").
                    },
                    "load_balancing_algorithm_type": "round_robin", # STRING (Optional, for "APPLICATION" LB type only): "ROUND_ROBIN" or "LEAST_OUTSTANDING_REQUESTS".
                                                                    # Can also be set via attributes.

                    # --- Network Load Balancer Target Group Specific ---
                    "preserve_client_ip": True,    # BOOLEAN (Optional, for "NETWORK" LB type with TCP/TLS protocol only): Default 'false'.
                                                   # If 'true', client IP is preserved to targets. Target SGs must allow client IPs.

                    "attributes": [                # LIST of OBJECTS (Optional): For setting advanced target group attributes.
                                                   # Key names vary between ALB and NLB. Refer to AWS docs.
                        # Example ALB TG attribute for LB-generated cookie stickiness:
                        # {"key": "stickiness.enabled", "value": "true"},
                        # {"key": "stickiness.type", "value": "lb_cookie"},
                        # {"key": "stickiness.lb_cookie.duration_seconds", "value": "3600"}
                        # Example NLB TG attribute:
                        # {"key": "deregistration_delay.connection_termination.enabled", "value": "true"}
                        # {"key": "preserve_client_ip.enabled", "value": "true"} # Alternative way to set for NLB
                    ],

                    "targets": [                   # LIST of OBJECTS (Optional): Register targets directly.
                        # --- For target_type: "INSTANCE" ---
                        { "instance_id": "i-09af4fb853f9f0d70" }, # STRING (Required): Physical ID of the EC2 instance.
                        
                        # --- For target_type: "IP" ---
                        # { 
                        #     "ip_address": "10.0.1.100", # STRING (Required): IP address.
                        #     "port": 8080,              # INTEGER (Optional): Port for this specific IP target. Defaults to TG port.
                        #     "availability_zone": "us-east-1a" # STRING (Optional): For IPs outside VPC CIDR, or to specify AZ. "all" can be used for IPs in VPC.
                        # },
                        
                        # --- For target_type: "LAMBDA" (Only for "APPLICATION" LB type) ---
                        # { 
                        #     "lambda_function_arn": "arn:aws:lambda:region:account:function:my-function" # STRING (Required): ARN of the Lambda function.
                        # },

                        # --- For target_type: "ALB" (Only for "NETWORK" LB type) ---
                        # { 
                        #     "alb_arn": "arn:aws:elasticloadbalancing:region:account:loadbalancer/app/my-app-alb/id", # STRING (Required)
                        #     "security_group_id_of_alb_target": "sg-xxxxxxxxxxxxxxxxx" # STRING (Recommended to provide for CDK import)
                        # }
                    ]
                }
            }
            # Add more target group definitions here
        ],
        "auto_scaling_groups": [ # LIST of OBJECTS: Each object defines one Auto Scaling Group.
            {
                "id": "MyWebAppASG",        # STRING (Required): Unique logical ID for this ASG config. Used for CDK construct ID.
                "enabled": True,            # BOOLEAN (Required): 'true' to deploy this ASG, 'false' to skip.
                "config": {                 # OBJECT (Required): Contains all configurations for this Auto Scaling Group.
                    "auto_scaling_group_name": "my-web-app-asg-example", # STRING (Optional): Physical name. Auto-generated if omitted.
                    "vpc_id": "vpc-0682a04278f37a95c", # STRING (Required): The VPC ID where instances will be launched.
                                                       # This is used to look up the VPC if vpc_subnets_config is by type/group.
                    
                    "vpc_subnets_config": { # OBJECT (Required): Specifies the subnets for the ASG.
                                            # Provide EITHER specific subnet_ids OR a subnet_selection_type.
                        "subnet_ids": ["subnet-01068ebd6184d034b", "subnet-0d0c5c4014c9737be", "subnet-003eb439f8392c89a"], # LIST of STRINGS (Optional): Specific physical subnet IDs.
                                                                                                             # Instances will be balanced across the AZs of these subnets.
                        # "subnet_selection": { # OBJECT (Optional): Alternative to 'subnet_ids'.
                        #     "subnet_type": "PRIVATE_WITH_EGRESS", # STRING (Optional): e.g., "PUBLIC", "PRIVATE_WITH_EGRESS", "PRIVATE_ISOLATED".
                        #     "subnet_group_name": "MyApplicationSubnets" # STRING (Optional): Selects subnets by tag.
                        # }
                    },

                    # --- Launch Configuration: Use EITHER launch_template OR mixed_instances_policy OR (legacy) launch_configuration_name ---
                    "launch_template": {    # OBJECT (Recommended): Specifies the Launch Template to use.
		
		               "launch_template_ref_id": "MyExampleLT", # Logical 'id' of an LT defined in your "launch_templates" JSON section
		                "version": "$Latest", # Version of the referenced LT to use
		
                    # "launch_template_id": "lt-08ff8e60f14343958", # STRING (Optional): ID of an existing Launch Template.
                    #     # # "launch_template_name": "my-app-launch-template", # STRING (Optional): Name of an existing Launch Template. (Use ID if possible)
                    # "version": "$Latest"  # STRING (Optional): Default: "$Default". Version of the LT (e.g., "$Latest", "$Default", "1", "2").
                    },

                    # "mixed_instances_policy": { # OBJECT (Optional): For using a mix of On-Demand and Spot Instances, and multiple instance types.
                                                  # If used, 'launch_template' above defines the base configuration.
                    #     "launch_template_overrides": [ # LIST of OBJECTS (Required if mixed_instances_policy is used)
                    #         {
                    #             "instance_type": "m5.large", # STRING (Required): EC2 instance type.
                    #             "weighted_capacity": "1"     # STRING (Optional): Weight for this instance type.
                    #         },
                    #         {
                    #             "instance_type": "c5.large",
                    #             "weighted_capacity": "1"
                    #         },
                    #         {
                    #             "instance_type": "r5.large",
                    #             "weighted_capacity": "2", # Example: r5.large counts as 2 units towards desired capacity
                    #             "launch_template_specification": { # OBJECT (Optional): Override parts of the main LT for this instance type.
                    #                 "launch_template_id": "lt-yyyyyyyyyyyyyyyyy", # Can use a different LT for this override
                    #                 "version": "$Latest"
                    #             }
                    #         }
                    #     ],
                    #     "instances_distribution": { # OBJECT (Optional): How to distribute On-Demand and Spot capacity.
                    #         "on_demand_base_capacity": 0, # INTEGER (Optional): Default 0. Absolute minimum number of On-Demand Instances.
                    #         "on_demand_percentage_above_base": 50, # INTEGER (Optional): Default 100 (meaning all above base are On-Demand). 
                    #                                                 # Percentage of remaining capacity to fill with On-Demand after base.
                    #         "spot_allocation_strategy": "lowest-price", # STRING (Optional): Default "lowest-price".
                    #                                                     # Values: "lowest-price", "capacity-optimized", "capacity-optimized-prioritized", "price-capacity-optimized".
                    #         "spot_instance_pools": 2, # INTEGER (Optional): Default 2. Number of Spot pools to use (diversification).
                    #         "spot_max_price": "0.50"  # STRING (Optional): Maximum hourly price for Spot Instances (e.g., "0.50" for $0.50/hr). If omitted, defaults to On-Demand price.
                    #     }
                    # },
                    
                    # "launch_configuration_name": "my-legacy-launch-config", # STRING (Optional, Legacy): Name of an existing Launch Configuration. Not recommended for new ASGs.

                    # --- Capacity Settings ---
                    "min_capacity": 1,          # INTEGER (Required): Minimum number of instances in the ASG.
                    "max_capacity": 5,          # INTEGER (Required): Maximum number of instances in the ASG.
                    "desired_capacity": 2,      # INTEGER (Optional): Desired number of instances. If omitted, defaults to 'min_capacity'.
                                                # Note: If using mixed instances with weighted capacity, desired_capacity refers to units.

                    # --- Health Check ---
                    "health_check_type": "ELB", # STRING (Optional): Default: "EC2". Type of health checks.
                                                # Possible values: "EC2", "ELB".
                                                # If "ELB", instances are considered unhealthy if ELB reports them as such.
                                                # Requires 'load_balancer_names' or 'target_group_arns' to be set.
                    "health_check_grace_period_seconds": 300, # INTEGER (Optional): Default: 300 seconds (5 minutes).
                                                              # Time ASG waits after an instance launches before checking its health.

                    # --- Scaling and Lifecycle ---
                    "default_cooldown_seconds": 300, # INTEGER (Optional): Default: 300 seconds. Time to wait after a scaling activity before another can begin.
                    "new_instances_protected_from_scale_in": False, # BOOLEAN (Optional): Default: 'false'. If 'true', new instances are protected from scale-in.
                    "termination_policies": ["Default"], # LIST of STRINGS (Optional): Default: ["Default"]. Order matters.
                                                         # Values: "OldestInstance", "NewestInstance", "OldestLaunchConfiguration", 
                                                         # "OldestLaunchTemplate", "ClosestToNextInstanceHour", "AllocationStrategy" (for Spot/mixed), "Default".
                    "capacity_rebalancing": False, # BOOLEAN (Optional): Default 'false'. For Spot Instances or mixed instances, enables proactive replacement of Spot Instances at higher risk of interruption.
                    
                    # "service_linked_role_arn": "arn:aws:iam::ACCOUNT_ID:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling", # STRING (Optional): ARN of the service-linked role. Usually auto-created and managed by AWS.

                    # --- Instance Maintenance Policy (for instance refresh, scheduled actions, etc.) ---
                    # "instance_maintenance_policy": { # OBJECT (Optional)
                    #     "min_healthy_percentage": 90, # INTEGER (Optional): Default 90. Range 0-100.
                    #     "max_healthy_percentage": 120 # INTEGER (Optional): Default 100. Range 100-200.
                    # },

                    # --- Load Balancer Integration ---
                       # --- Load Balancer Integration: Reference by logical ID ---
                    "target_group_attachment_config": { # New key for clarity and grouping
                        "target_group_refs": [          # LIST of OBJECTS: Reference Target Groups by their logical 'id'.
                                                        # These IDs should match the 'id' of Target Groups defined in your
                                                        # 'application_load_balancers[].target_groups', 
                                                        # 'network_load_balancers[].target_groups', or
                                                        # 'target_group_deployments.target_groups' sections.
                            { "id": "AppServersTG" },       # Example: Logical 'id' of a TG (perhaps from an ALB config)
                            # { "id": "MyStandaloneWebAppTG"} # Example: Logical 'id' of a standalone TG
                        ],

		
                    # "target_group_arns": [          # LIST of STRINGS (Optional): ARNs of ELB Target Groups (ALB or NLB) to register instances with.
                    #     "arn:aws:elasticloadbalancing:us-east-1:198484116691:targetgroup/my-app-servers-tg/5ae3aaa52eb1b4f8",
                    #     # "arn:aws:elasticloadbalancing:region:account-id:targetgroup/my-nlb-tg/yyyyyyyyyyyyyyy"
                    # ],
                    # "classic_load_balancer_names": [ # LIST of STRINGS (Optional, Legacy): Names of Classic Load Balancers to register instances with.
                    #     "my-classic-lb"
                    # ],
                    },
                    # --- Scaling Policies ---
                    "scaling_policies": [           # LIST of OBJECTS (Optional): Define scaling policies.
                        {
                            "id": "CpuTargetTracking", # STRING (Required): Logical ID for this scaling policy.
                            "enabled": True,           # BOOLEAN (Optional): Default 'true'.
                            "policy_type": "TARGET_TRACKING", # STRING (Required): "TARGET_TRACKING", "STEP_SCALING", "SIMPLE_SCALING" (legacy).
                            
                            # --- For "TARGET_TRACKING" ---
                            "target_tracking_config": { # OBJECT (Required if policy_type is "TARGET_TRACKING")
                                "target_value": 50.0,   # FLOAT (Required): The target value for the metric.
                                "predefined_metric_type": "ASGAverageCPUUtilization", # STRING (Optional): Use a predefined metric.
                                                                                    # Values: "ASGAverageCPUUtilization", "ASGAverageNetworkIn", 
                                                                                    # "ASGAverageNetworkOut", "ALBRequestCountPerTarget".
                                # "custom_metric_config": { # OBJECT (Optional): For using a custom CloudWatch metric.
                                #     "metric_name": "MyCustomMetric",
                                #     "namespace": "MyApplication",
                                #     "dimensions": [{"name": "DimensionName", "value": "DimensionValue"}],
                                #     "statistic": "Average", # Sum, Average, SampleCount, Minimum, Maximum
                                #     "unit": "Percent" # Count, Bytes, Percent, etc.
                                # },
                                "scale_out_cooldown_seconds": 60,  # INTEGER (Optional): Cooldown period for scaling out. Defaults to ASG's default cooldown.
                                "scale_in_cooldown_seconds": 300, # INTEGER (Optional): Cooldown period for scaling in. Defaults to ASG's default cooldown.
                                "disable_scale_in": False         # BOOLEAN (Optional): Default 'false'. If 'true', this policy will only scale out.
                            }
                            
                            # --- For "STEP_SCALING" ---
                            # "step_scaling_config": { # OBJECT (Required if policy_type is "STEP_SCALING")
                            #     "adjustment_type": "ChangeInCapacity", # STRING (Required): "ChangeInCapacity", "ExactCapacity", "PercentChangeInCapacity".
                            #     "metric_aggregation_type": "Average", # STRING (Optional): Default "Average". "Minimum", "Maximum", "Average".
                            #     "cooldown_seconds": 60,
                            #     "estimated_instance_warmup_seconds": 300, # INTEGER (Optional): Time until a newly launched instance contributes to metrics.
                            #     "min_adjustment_magnitude": 1, # INTEGER (Optional): Minimum number of instances to add/remove.
                            #     "step_adjustments": [ # LIST of OBJECTS (Required)
                            #         { # Scale Out
                            #             "metric_interval_lower_bound": 0,  # FLOAT (Optional): If metric > this value (and no upper bound), scale. For CPU, this is a percentage.
                            #             "scaling_adjustment": 1            # INTEGER (Required): How many instances/percent to add.
                            #         },
                            #         { # Scale In
                            #             "metric_interval_upper_bound": -10, # FLOAT (Optional): If metric < this value (and no lower bound), scale. (e.g. -10 means 10% below threshold for scale in)
                            #             "scaling_adjustment": -1           # INTEGER (Required): How many instances/percent to remove.
                            #         }
                            #     ],
                            #     # This usually requires a CloudWatch Alarm to trigger it. The alarm is often created separately
                            #     # and its ARN referenced, or the CDK can create one based on a metric.
                            #     # For simplicity, we'll assume the CDK stack will create the alarm if needed.
                            #     "metric_for_alarm": { # Define the metric that will trigger this step scaling
                            #          "metric_name": "CPUUtilization",
                            #          "namespace": "AWS/EC2",
                            #          "dimensions": [{"name": "AutoScalingGroupName", "value": "REF_TO_ASG_NAME"}], # CDK will substitute ASG name
                            #          "statistic": "Average",
                            #          "period_seconds": 300,
                            #          "threshold": 70, # Example: Alarm if CPU > 70%
                            #          "comparison_operator": "GreaterThanThreshold", # Or LessThanThreshold for scale-in alarms
                            #          "evaluation_periods": 1
                            #     }
                            # }

                            # --- For "SIMPLE_SCALING" (Legacy, not recommended) ---
                            # "simple_scaling_config": {
                            #     "adjustment_type": "ChangeInCapacity",
                            #     "scaling_adjustment": 1,
                            #     "cooldown_seconds": 300
                            #     # Requires a separate CloudWatch Alarm to trigger.
                            # }
                        }
                        # Add more scaling policies
                    ],

                    # --- Scheduled Actions ---
                    "scheduled_actions": [          # LIST of OBJECTS (Optional): Define scheduled scaling actions.
                        {
                            "id": "ScaleUpWeekdays",  # STRING (Required): Logical ID for this scheduled action.
                            "enabled": True,          # BOOLEAN (Optional): Default 'true'.
                            "schedule": "0 9 * * MON-FRI", # STRING (Required): Cron expression for when to run (UTC). This example: 9 AM UTC on weekdays.
                            "time_zone": "Etc/UTC",   # STRING (Optional): Time zone for the schedule. Default "Etc/UTC".
                            # "start_time": "2025-12-01T09:00:00Z", # ISO8601 STRING (Optional): Action only runs after this time.
                            # "end_time": "2026-12-01T09:00:00Z",   # ISO8601 STRING (Optional): Action does not run after this time.
                            "min_capacity": 2,        # INTEGER (Optional): New minimum capacity.
                            "max_capacity": 10,       # INTEGER (Optional): New maximum capacity.
                            "desired_capacity": 5     # INTEGER (Optional): New desired capacity.
                                                      # At least one of min, max, or desired must be specified.
                        }
                    ],

                    # --- Lifecycle Hooks ---
                    "lifecycle_hooks": [            # LIST of OBJECTS (Optional): Define lifecycle hooks.
                        {
                            "id": "InstanceLaunchingHook", # STRING (Required): Logical ID.
                            "enabled": False,             # BOOLEAN (Optional): Default 'true'.
                            "lifecycle_hook_name": "my-launching-hook", # STRING (Optional): Physical name.
                            "lifecycle_transition": "autoscaling:EC2_INSTANCE_LAUNCHING", # STRING (Required): "autoscaling:EC2_INSTANCE_LAUNCHING" or "autoscaling:EC2_INSTANCE_TERMINATING".
                            "default_result": "CONTINUE", # STRING (Optional): Default "CONTINUE". Values: "CONTINUE", "ABANDON".
                            "heartbeat_timeout_seconds": 300, # INTEGER (Optional): Default 3600. Time instance has to send heartbeat.
                            "notification_target_arn": "arn:aws:sns:region:account-id:my-sns-topic", # STRING (Optional): ARN of SNS topic or SQS queue.
                            "notification_metadata": "{\"key\":\"value\"}", # STRING (Optional): Custom info to pass to notification target.
                            "role_arn": "arn:aws:iam::198484116691:role/ec2-new-ssm" # STRING (Optional): Role for ASG to publish to SNS/SQS. Auto-created if target is given and role is not.
                        }
                    ],

                    # --- Notifications (for ASG events, different from lifecycle hook notifications) ---
                    # "notifications": [              # LIST of OBJECTS (Optional): Send notifications for ASG events.
                    #     {
                    #         "topic_arn": "arn:aws:sns:region:account-id:my-asg-events-topic", # STRING (Required): ARN of the SNS topic.
                    #         "notification_types": [ # LIST of STRINGS (Required): Which events to notify for.
                    #             "autoscaling:EC2_INSTANCE_LAUNCH",
                    #             "autoscaling:EC2_INSTANCE_TERMINATE",
                    #             "autoscaling:EC2_INSTANCE_LAUNCH_ERROR",
                    #             "autoscaling:EC2_INSTANCE_TERMINATE_ERROR"
                    #             # Other types: autoscaling:TEST_NOTIFICATION
                    #         ]
                    #     }
                    # ],
                    
                    "tags": [                       # LIST of OBJECTS (Optional): Tags for the Auto Scaling group itself.
                        {
                            "key": "ASG-Application", 
                            "value": "MyWebApp",
                            "propagate_at_launch": True # BOOLEAN (Optional): Default 'true'. If true, this tag is also applied to instances launched by this ASG.
                        },
                        {
                            "key": "ASG-Environment",
                            "value": "Production",
                            "propagate_at_launch": True
                        }
                    ]
                }
            }
        ]
    }
# cdk_project/deployment_config.py (Snippet for iam_roles_config)

    iam_roles_config = {
    "deploy": True,
    "description": "IAM Roles managed by the orchestrator for various services.",
    "roles": [
        {
            "id": "CodeDeployServiceRole", # Logical ID for this role
            "enabled": True,
            "config": {
                "role_name": "MyOrchestratorCodeDeployServiceRole", # Actual AWS IAM role name
                "assumed_by": "codedeploy.amazonaws.com",
                "managed_policies": ["arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole"]
            }
        },
        {
            "id": "CodePipelineServiceRole", # Logical ID for CodePipeline Service Role
            "enabled": True,
            "config": {
                "role_name": "MyApplicationCodePipelineServiceRole", # Actual AWS IAM role name
                "assumed_by": "codepipeline.amazonaws.com",
                "managed_policies": [
                    "arn:aws:iam::aws:policy/AWSCodePipeline_FullAccess", # Broad access for simplicity
                    # In production, consider limiting to:
                    # "arn:aws:iam::aws:policy/AWSCodeCommitReadOnly",
                    # "arn:aws:iam::aws:policy/AWSCodeBuildDeveloperAccess",
                    # "arn:aws:iam::aws:policy/AWSCodeDeployDeployerAccess",
                    # "arn:aws:iam::aws:policy/AmazonS3FullAccess", # For artifact bucket
                    # "arn:aws:iam::aws:policy/service-role/AWSCodeStarSourceConnection" # If using CodeStarSourceConnection
                ]
            }
        },
        # You could define other roles here, e.g., a custom CodeBuild service role
        # for infrastructure pipelines, or cross-account roles.
    ]
}

# The rest of your final_config would then include this:
# final_config = {
#     # ... other sections ...
#     "iam_roles": iam_roles_config, # <-- Now includes CodePipeline role
#     "pipeline_deployments": pipeline_deployments_config,
# }
    
    pipeline_deployments_config = {
        "deploy": True, # Global switch to enable/disable application pipelines
        "description": "Configuration group for CI/CD pipelines deploying applications to EC2/ASG.",
        "pipelines": [
            {
                "id": "WebAppDeploymentPipeline", # Logical ID for this pipeline within the config
                "enabled": True, # Enable/disable this specific pipeline
                "target_resource_type": "EC2_INSTANCE", # "EC2_INSTANCE" or "AUTOSCALING_GROUP"
                # "target_resource_ref_id": "MyStandaloneWebServer1", # Logical ID from ec2_deployments.instances or ec2_deployments.auto_scaling_groups
                # Alternatively, if you need to deploy to an existing resource not created by this CDK:
                "existing_target_resource_id": "i-0a73a8605a4a5baab", # Physical EC2 Instance ID or ASG Name/ARN
                "existing_target_vpc_id": "vpc-0682a04278f37a95c", # Required for existing targets to find the VPC
                "existing_target_instance_name_tag": "web-server -1", # <--- UPDATE THIS EXACTLY


                "source_config": {
                    "source_type": "GITHUB", # "GITHUB", "CODECOMMIT", "S3"
                    # --- GitHub Specific ---
                    "github_connection_arn": "arn:aws:codeconnections:us-east-1:198484116691:connection/477938bc-d5e5-47f0-9d40-3f6e927039e1", # REPLACE
                    "github_repo_owner": "chaithrashreekr7901",
                    "github_repo_name": "CDK-project", # The repository containing your application code
                    "github_branch_name": "my-feature-branch", # The branch to monitor for changes
                    # "github_build_spec_path": "buildspec.yml", # Optional: Path to buildspec file in repo, default is root
                    # "github_full_clone": False, # Optional: True for full history, False for shallow clone (faster)
                },
                "build_config": { # Optional: If you need a build step
                    "enabled": True,
                    "build_project_name": "SimpleWebAppBuild", # Optional: Name for the CodeBuild project
                    "build_compute_type": "SMALL", # e.g., "BUILD_GENERAL1_SMALL", "BUILD_GENERAL1_MEDIUM"
	                "build_image": "aws/codebuild/standard:5.0", # A common build image
                    #"build_image": "ubuntu/aws-codebuild-builder:latest", # Or a specific CodeBuild managed image ARN
		            "commands_build": ["echo 'No complex build steps for HTML, just copy artifacts.'"],
		            "artifacts_paths": ["webapp/**/*"], # Capture the entire simple-webapp folder as artifacts
		
                   # "environment_variables": { # Optional: Env vars for CodeBuild
                      #  "SOME_VAR": {"value": "some-value", "type": "PLAINTEXT"},
                        #"DB_SECRET_ARN": {"value": "arn:aws:secretsmanager:...", "type": "SECRETS_MANAGER"}
                    #},
                   # "commands_pre_build": ["echo 'Starting build...'"], # List of commands
                 #   "commands_build": ["npm install", "npm run build"],
                 #    "commands_post_build": ["echo 'Build complete'"],
                  #  "artifacts_paths": ["dist/**/*", "appspec.yml", "scripts/**/*"], # Paths to artifacts to be passed to deploy
                },
                "deploy_config": {
                    "enabled": True,
                    "deployment_strategy": "CODE_DEPLOY_EC2", # "CODE_DEPLOY_EC2", "SSM_RUN_COMMAND", "S3_SYNC"
                    
                    # --- CodeDeploy EC2 Specific ---
                    "codedeploy_application_name": "MySimpleWebApp", # Optional: Name for CodeDeploy Application
                    "codedeploy_deployment_group_name": "MySimpleWebAppDG", # Optional: Name for CodeDeploy Deployment Group
                    "codedeploy_appspec_path": "webapp/appspec.yml", # Path to appspec.yml in artifacts
                    "create_codedeploy_service_role": False, # <-- NEW FLAG: Set to True to create a new role
                    "codedeploy_service_role_ref_id": "CodeDeployServiceRole",
                    # "codedeploy_service_role_arn": "arn:aws:iam::198484116691:role/service-role/aws-codedeploy-service-role", # REPLACE: Ensure this role exists or is created by your infra stack
                    "codedeploy_install_agent": True, # Optional: Automatically install CodeDeploy agent via UserData (if target is EC2/ASG and not already done)
                    "codedeploy_alarm_arns": [], # Optional: List of CloudWatch Alarm ARNs for rollback on failure
                    "codedeploy_deployment_config_name": "CodeDeployDefault.OneAtATime", # e.g., "CodeDeployDefault.OneAtATime", "CodeDeployDefault.AllAtOnce"
                    
                    # --- SSM Run Command Specific (Alternative for simple deployments) ---
                    # "ssm_document_name": "AWS-RunShellScript", # e.g., "AWS-RunShellScript", "AWS-ApplyPatchBaseline"
                    # "ssm_commands": [ # List of commands to run on target instances
                    #     "sudo yum update -y",
                    #     "sudo systemctl restart my-app-service"
                    # ],
                    # "ssm_timeout_seconds": 600,
                    
                    # --- S3 Sync Specific (For static files, e.g., to a website bucket) ---
                    # "s3_target_bucket_id": "AppConfigData", # Logical ID from s3_deployments.buckets
                    # "s3_target_bucket_prefix": "app-static-files/",
                    # "s3_cloud_front_distribution_id": "MyWebAppCFDistribution", # Optional: If you have a CloudFront distribution to invalidate
                },
              
            "tags": {
                "WebAppName": "SimpleHtmlApp",
                "DeploymentManagedBy": "CodePipeline"
            },
            "codepipeline_service_role_ref_id": "CodePipelineServiceRole", # NEW FIELD
        },
            # Add more application deployment pipelines here
        ]
    }

    final_config = {
        "vpcs": {
            "deploy": False, # Example: VPCs are globally enabled
            "description": "Configuration group for all VPC instance deployments.",
            "instances": [vpc_instance_1_config, vpc_instance_2_config] # Assuming these are defined
        },
        "vpc_peerings": {
            "deploy": False, # Example: Peering globally disabled
            "description": "Configuration group for all VPC peering connections.",
            "connections": vpc_peering_definitions # Assuming this is defined
        },
        "rds_deployments": rds_deployments_config, # Assuming this is defined
        "s3_deployments": s3_deployments_config,   # Assuming this is defined
        "ec2_deployments": ec2_deployments_config,  # <<< Include the EC2 config
        "iam_roles": iam_roles_config,
        "pipeline_deployments": pipeline_deployments_config,
    }
    logger.info(f"DEBUG: Keys in final_config: {list(final_config.keys()) if isinstance(final_config, dict) else 'Not a dict'}")
    return final_config
    
