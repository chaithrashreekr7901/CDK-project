# CRMP-PROJECT/cdk_project/s3/s3_bucket_instance_stack.py
import logging
import json
from pathlib import Path 
from aws_cdk import (
    NestedStack, Tags, RemovalPolicy, Duration, CfnOutput, Stack, Fn, Aws,
    aws_s3 as s3,
    aws_kms as kms,
    aws_iam as iam,
    aws_s3_deployment as s3_deployment, 
    aws_cloudfront as cloudfront 
)
from constructs import Construct
# Helper for deep merging dictionaries if needed, though simple update might suffice
# import copy 

logger = logging.getLogger(__name__)

class S3BucketInstanceStack(NestedStack):
    # Store bucket reference for potential cross-stack use (e.g., logging)
    s3_bucket: s3.IBucket 

    def __init__(self, scope: Construct, construct_id: str, 
                 s3_merged_config: dict, # Expecting the fully merged config for this bucket
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = s3_merged_config
        self.bucket_outputs = {}
        logical_id_base = construct_id
        if construct_id[-1].isdigit():
             base_candidate = construct_id.rstrip('0123456789')
             if base_candidate: 
                 logical_id_base = base_candidate
        logical_id_base = logical_id_base.replace('S3BucketInstanceStack', '').replace('NestedStack', '')


        creation_mode = self.config.get("creation_mode", "NEW").upper()

        # --- Create or Import Bucket ---
        if creation_mode == "EXISTING":
            self._import_existing_bucket(logical_id_base)
        elif creation_mode == "NEW":
            self._create_new_bucket(logical_id_base)
        else:
            raise ValueError(f"Invalid creation_mode '{creation_mode}' for bucket {logical_id_base}")

        # --- Apply Common Post-Creation/Import Steps ---
        if hasattr(self, 's3_bucket') and self.s3_bucket:
             self._apply_bucket_policy(logical_id_base)
             self._apply_tags(logical_id_base, creation_mode == "EXISTING")
             if creation_mode == "NEW":
                 self._deploy_website_content(logical_id_base)
             self._setup_outputs(logical_id_base)
        else:
             logger.error(f"Bucket resource was not successfully created or imported for {logical_id_base}")

    def _import_existing_bucket(self, logical_id_base: str):
        """Imports an existing S3 bucket."""
        existing_bucket_name = self.config.get("existing_bucket_name")
        if not existing_bucket_name:
            raise ValueError(f"existing_bucket_name is required when creation_mode is EXISTING for {logical_id_base}")
        
        logger.info(f"Importing existing bucket: {existing_bucket_name} for CDK ID {logical_id_base}")
        try:
            self.s3_bucket = s3.Bucket.from_bucket_name(self, "ImportedBucket", existing_bucket_name)
        except Exception as e:
            logger.error(f"Failed to import bucket {existing_bucket_name}: {e}")
            raise e 

    def _create_new_bucket(self, logical_id_base: str):
        """Creates a new S3 bucket based on configuration."""
        logger.info(f"Creating new bucket based on config ID: {logical_id_base}")

        generated_bucket_name = self._determine_bucket_name(logical_id_base)
        block_public_access = self._parse_block_public_access()
        encryption_type, encryption_key = self._parse_encryption(logical_id_base)
        removal_policy = self._parse_removal_policy()
        auto_delete_objects = self.config.get("auto_delete_objects", False) if removal_policy == RemovalPolicy.DESTROY else False
        lifecycle_rules = self._parse_lifecycle_rules(logical_id_base) # <<< PARSE LIFECYCLE
        logs_bucket, logs_prefix = self._parse_access_logging(logical_id_base)
        website_props = self._parse_website_config()
        cors_rules = self._parse_cors_rules(logical_id_base)
        object_ownership = self._parse_object_ownership()
        intelligent_tiering_configs = self._parse_intelligent_tiering(logical_id_base)
        object_lock_enabled, object_lock_retention = self._parse_object_lock(logical_id_base)

        bucket_props = {
            "bucket_name": generated_bucket_name,
            "block_public_access": block_public_access,
            "encryption": encryption_type,
            "encryption_key": encryption_key,
            "bucket_key_enabled": self.config.get("bucket_key_enabled") if encryption_key else None, 
            "versioned": self.config.get("versioned"),
            "removal_policy": removal_policy,
            "auto_delete_objects": auto_delete_objects,
            "lifecycle_rules": lifecycle_rules if lifecycle_rules else None, # <<< ADD PARSED RULES
            "server_access_logs_bucket": logs_bucket,
            "server_access_logs_prefix": logs_prefix,
            **website_props,
            "cors": cors_rules if cors_rules else None,
            "public_read_access": self.config.get("public_read_access", False), 
            "access_control": getattr(s3.BucketAccessControl, self.config.get("access_control", "PRIVATE").upper(), s3.BucketAccessControl.PRIVATE),
            "object_ownership": object_ownership,
            "intelligent_tiering_configurations": intelligent_tiering_configs if intelligent_tiering_configs else None,
            "event_bridge_enabled": self.config.get("event_bridge_enabled"),
            "transfer_acceleration": self.config.get("transfer_acceleration"),
            "object_lock_enabled": object_lock_enabled,
            "object_lock_default_retention": object_lock_retention,
            "enforce_ssl": self.config.get("enforce_ssl")
        }
        final_bucket_props = {k: v for k, v in bucket_props.items() if v is not None}

        try:
            self.s3_bucket = s3.Bucket( self, "Resource", **final_bucket_props )
            logger.info(f"Defined S3 Bucket construct for {logical_id_base} (Physical: {self.s3_bucket.bucket_name})")
        except Exception as e:
             logger.error(f"Failed to instantiate s3.Bucket for {logical_id_base}: {e}", exc_info=True)
             raise e 

    def _determine_bucket_name(self, logical_id_base:str) -> str | None:
        """Determines bucket name based on config precedence."""
        # ... (Implementation from previous response) ...
        bucket_name = self.config.get("bucket_name")
        prefix = self.config.get("bucket_name_prefix")
        suffix = self.config.get("bucket_name_suffix")
        if bucket_name: return bucket_name
        account_id = Aws.ACCOUNT_ID; region = Aws.REGION; stack_name = Stack.of(self).stack_name 
        if prefix: return f"{prefix}-{logical_id_base.lower()}-{account_id}-{region}"[:63]
        if suffix: return f"{stack_name.lower()}-{logical_id_base.lower()}-{suffix}-{account_id}-{region}"[:63]
        return None 

    # --- Helper methods for parsing configuration ---

    def _parse_block_public_access(self) -> s3.BlockPublicAccess | None:
        """Parses block public access settings from config."""
        # ... (Implementation from previous response) ...
        if self.config.get("block_all_public_access", False): return s3.BlockPublicAccess.BLOCK_ALL
        setting = self.config.get("block_public_access")
        if isinstance(setting, str) and setting.upper() == "BLOCK_ALL": return s3.BlockPublicAccess.BLOCK_ALL
        elif isinstance(setting, dict): return s3.BlockPublicAccess(**setting) 
        elif setting is None: return None 
        else: return s3.BlockPublicAccess.BLOCK_ALL

    def _parse_encryption(self, logical_id_context: str) -> tuple[s3.BucketEncryption | None, kms.IKey | None]:
        """Parses encryption settings from config."""
        # ... (Implementation from previous response) ...
        enc_config = self.config.get("encryption", {}); enc_type_str = enc_config.get("type", "S3_MANAGED").upper(); kms_key_arn = enc_config.get("kms_key_arn")
        key=None; enc_type=s3.BucketEncryption.S3_MANAGED
        if enc_type_str == "KMS_MANAGED": enc_type = s3.BucketEncryption.KMS_MANAGED
        elif enc_type_str == "KMS":
            if not kms_key_arn: raise ValueError(f"kms_key_arn missing for KMS on {logical_id_context}")
            enc_type=s3.BucketEncryption.KMS; key=kms.Key.from_key_arn(self, f"KmsKey{logical_id_context}", kms_key_arn)
        elif enc_type_str == "UNENCRYPTED": enc_type = s3.BucketEncryption.UNENCRYPTED
        elif enc_type_str != "S3_MANAGED": logger.warning(f"Invalid enc type '{enc_type_str}', defaulting S3_MANAGED.")
        if enc_type == s3.BucketEncryption.KMS_MANAGED and kms_key_arn: logger.warning(f"KMS_MANAGED specified but kms_key_arn provided for {logical_id_context}. Ignoring ARN."); key = None
        return enc_type, key

    def _parse_removal_policy(self) -> RemovalPolicy:
        """Parses removal policy from config."""
        # ... (Implementation from previous response) ...
        rp_str = self.config.get("removal_policy", "RETAIN").upper()
        if rp_str == "DESTROY": return RemovalPolicy.DESTROY
        return RemovalPolicy.RETAIN

    # --- START LIFECYCLE RULE FIX ---
    def _parse_lifecycle_rules(self, logical_id_context: str) -> list[s3.LifecycleRule]:
        """Parses lifecycle rules from config, ensuring each rule has an action."""
        rules = []
        for i, rule_config in enumerate(self.config.get("lifecycle_rules", [])):
            if not isinstance(rule_config, dict): 
                logger.warning(f"Lifecycle rule at index {i} for {logical_id_context} is not a dictionary. Skipping.")
                continue

            rule_id = rule_config.get("id", f"{logical_id_context}Rule{i}")
            
            # --- Parse Transitions ---
            transitions = []
            for t_conf in rule_config.get("transitions", []):
                if not isinstance(t_conf, dict): continue
                sc_str = t_conf.get("storage_class", "").upper()
                sc_enum = getattr(s3.StorageClass, sc_str, None)
                days = t_conf.get("transition_after_days")
                if sc_enum and isinstance(days, int) and days >= 0:
                    transitions.append(s3.Transition(storage_class=sc_enum, transition_after=Duration.days(days)))
                else: logger.warning(f"Skipping invalid transition in rule {rule_id}: {t_conf}")
            
            # --- Parse Noncurrent Transitions ---
            noncurrent_transitions = []
            for t_conf in rule_config.get("noncurrent_version_transitions", []):
                 if not isinstance(t_conf, dict): continue
                 sc_str = t_conf.get("storage_class", "").upper()
                 sc_enum = getattr(s3.StorageClass, sc_str, None)
                 days = t_conf.get("noncurrent_version_transition_after_days") # Key name matches CDK prop
                 if sc_enum and isinstance(days, int) and days >= 0:
                     noncurrent_transitions.append(s3.NoncurrentVersionTransition(storage_class=sc_enum, transition_after=Duration.days(days)))
                 else: logger.warning(f"Skipping invalid noncurrent transition in rule {rule_id}: {t_conf}")

            # --- Parse Expirations & Abort ---
            abort_days = rule_config.get("abort_incomplete_multipart_upload_after_days")
            expiration_days = rule_config.get("expiration_days")
            noncurrent_expiration_days = rule_config.get("noncurrent_version_expiration_days")
            expired_marker = rule_config.get("expired_object_delete_marker") # boolean

            # --- Build Rule Args ---
            rule_args = {
                "id": rule_id,
                "enabled": rule_config.get("enabled", True),
                "prefix": rule_config.get("prefix"),
                "object_size_greater_than": rule_config.get("object_size_greater_than_bytes"),
                "object_size_less_than": rule_config.get("object_size_less_than_bytes"),
                "tag_filters": rule_config.get("tags") 
            }
            
            # Add actions only if they have valid configuration
            action_added = False
            if transitions: 
                rule_args["transitions"] = transitions; action_added = True
            if noncurrent_transitions: 
                rule_args["noncurrent_version_transitions"] = noncurrent_transitions; action_added = True
            if isinstance(abort_days, int) and abort_days > 0: 
                rule_args["abort_incomplete_multipart_upload_after"] = Duration.days(abort_days); action_added = True
            if isinstance(expiration_days, int) and expiration_days > 0: 
                rule_args["expiration"] = Duration.days(expiration_days); action_added = True
            if isinstance(noncurrent_expiration_days, int) and noncurrent_expiration_days > 0: 
                rule_args["noncurrent_version_expiration"] = Duration.days(noncurrent_expiration_days); action_added = True
            if isinstance(expired_marker, bool):
                 rule_args["expired_object_delete_marker"] = expired_marker; action_added = True
                 
            # --- Validate and Create Rule ---
            if not action_added:
                 logger.error(f"Lifecycle rule '{rule_id}' for {logical_id_context} has no valid actions (expiration, transition, abort, etc.). Skipping rule.")
                 continue # Skip this rule if no action is defined

            try:
                # Filter None values before creating
                final_rule_args = {k:v for k,v in rule_args.items() if v is not None}
                rule = s3.LifecycleRule(**final_rule_args)
                rules.append(rule)
            except Exception as e:
                 logger.error(f"Failed to create LifecycleRule '{rule_id}' for {logical_id_context}: {e}")

        return rules
    # --- END LIFECYCLE RULE FIX ---
        
    def _parse_access_logging(self, logical_id_context: str) -> tuple[s3.IBucket | None, str | None]:
        """Parses server access logging config."""
        # ... (Implementation from previous response) ...
        logging_config = self.config.get("server_access_logs", {}); target_bucket_name = logging_config.get("target_bucket_name"); target_prefix = logging_config.get("target_prefix"); logs_bucket = None
        if target_bucket_name:
            try: logs_bucket = s3.Bucket.from_bucket_name(self, f"AccessLogsTarget{logical_id_context}", target_bucket_name)
            except Exception as e: logger.error(f"Failed import log bucket {target_bucket_name}: {e}")
        return logs_bucket, target_prefix

    def _parse_website_config(self) -> dict:
        """Parses website hosting related properties."""
        # ... (Implementation from previous response) ...
        props = {}
        if self.config.get("website_index_document"): props["website_index_document"] = self.config["website_index_document"]; props["website_error_document"] = self.config.get("website_error_document")
        return props

    def _parse_cors_rules(self, logical_id_context: str) -> list[s3.CorsRule]:
        """Parses CORS rules from config."""
        # ... (Implementation from previous response) ...
        rules = []; # Add full parsing logic
        for i, cors_config in enumerate(self.config.get("cors", [])):
            if not isinstance(cors_config, dict): continue
            allowed_methods = [getattr(s3.HttpMethods, m.upper(), None) for m in cors_config.get("allowed_methods", [])]
            allowed_methods = [m for m in allowed_methods if m] # Filter None
            if allowed_methods and cors_config.get("allowed_origins"):
                 try: rules.append(s3.CorsRule(**cors_config)) # Pass directly if keys match
                 except Exception as e: logger.error(f"Failed CORS rule {i} for {logical_id_context}: {e}")
        return rules
        
    def _parse_object_ownership(self) -> s3.ObjectOwnership | None:
        """Parses object ownership setting."""
        # ... (Implementation from previous response) ...
        oo_str_raw = self.config.get("object_ownership"); oo_default = s3.ObjectOwnership.BUCKET_OWNER_PREFERRED if self.config.get("website_index_document") else s3.ObjectOwnership.BUCKET_OWNER_ENFORCED
        if not oo_str_raw or not isinstance(oo_str_raw, str): return oo_default
        oo_str = oo_str_raw.upper().replace("_",""); oo_map = {"BUCKETOWNERENFORCED": s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, "BUCKETOWNERPREFERRED": s3.ObjectOwnership.BUCKET_OWNER_PREFERRED, "OBJECTWRITER": s3.ObjectOwnership.OBJECT_WRITER}
        return oo_map.get(oo_str, oo_default)

    def _parse_intelligent_tiering(self, logical_id_context: str) -> list[s3.IntelligentTieringConfiguration]:
        """Parses intelligent tiering configurations."""
         # ... (Implementation from previous response) ...
        configs = []; # Add full parsing logic
        for i, it_config in enumerate(self.config.get("intelligent_tiering", [])):
            if not isinstance(it_config, dict): continue
            # ... parse archive/deep_archive days, tags ...
            try: configs.append(s3.IntelligentTieringConfiguration(**it_config)) # Adapt keys if needed
            except Exception as e: logger.error(f"Failed IT rule {i} for {logical_id_context}: {e}")
        return configs
         
    def _parse_object_lock(self, logical_id_context: str) -> tuple[bool | None, s3.ObjectLockRetention | None]:
        """Parses object lock configuration."""
         # ... (Implementation from previous response) ...
        enabled = self.config.get("object_lock_enabled", False); retention = None
        if self.config.get("creation_mode", "NEW").upper() == "EXISTING" and enabled: return None, None 
        if enabled and self.config.get("creation_mode", "NEW").upper() == "NEW":
            retention_mode_str = self.config.get("object_lock_default_retention_mode", "").upper()
            if retention_mode_str:
                mode = s3.ObjectLockMode.GOVERNANCE if retention_mode_str == "GOVERNANCE" else s3.ObjectLockMode.COMPLIANCE; duration = None
                if isinstance(d := self.config.get("object_lock_default_retention_days"), int) and d > 0: duration = Duration.days(d)
                elif isinstance(y := self.config.get("object_lock_default_retention_years"), int) and y > 0: duration = Duration.days(y * 365) 
                if duration: retention = s3.ObjectLockRetention(mode=mode, duration=duration)
            return enabled, retention
        return None, None

    def _apply_bucket_policy(self, logical_id_base: str):
        """Applies policy statements from config to the bucket."""
        # ... (Implementation from previous response, ensure robust parsing) ...
        if not isinstance(self.s3_bucket, s3.Bucket): return
        policy_statements_config = self.config.get("bucket_policy_statements", [])
        if self.config.get("public_read_access", False) and self.config.get("creation_mode", "NEW").upper() == "NEW":
            try: self.s3_bucket.grant_public_access(); logger.info(f"Applied grant_public_access() to {logical_id_base}")
            except Exception as grant_err: logger.error(f"FAILED grant_public_access() for {logical_id_base}: {grant_err}")
        if not policy_statements_config or not isinstance(policy_statements_config, list): return
        applied_count = 0
        for i, stmt_config in enumerate(policy_statements_config):
             if not isinstance(stmt_config, dict): continue
             try:
                 # ... (Full robust policy statement parsing and application) ...
                 sid=stmt_config.get("sid", f"{logical_id_base}Stmt{i}"); effect=iam.Effect.ALLOW if stmt_config.get("effect", "Allow").upper()=="ALLOW" else iam.Effect.DENY; actions=stmt_config.get("actions",[]); principals_cfg=stmt_config.get("principals",[]); p_type=stmt_config.get("principal_type","AWS").upper(); resources_cfg=stmt_config.get("resources",[]); conditions=stmt_config.get("conditions")
                 # Basic parsing - needs more robustness
                 principals = [iam.ArnPrincipal(p) for p in principals_cfg if isinstance(p, str)] if p_type=="AWS" else [] # Simplified
                 resources = [r.replace("{{BucketArn}}", self.s3_bucket.bucket_arn) for r in resources_cfg if isinstance(r, str)]
                 if principals and actions and resources:
                    statement = iam.PolicyStatement(sid=sid, effect=effect, principals=principals, actions=actions, resources=resources, conditions=conditions)
                    self.s3_bucket.add_to_resource_policy(statement); applied_count += 1
             except Exception as policy_ex: logger.error(f"Failed policy stmt {i} for {logical_id_base}: {policy_ex}")
        if applied_count > 0: logger.info(f"Applied {applied_count} specific policy statements to {logical_id_base}.")


    def _deploy_website_content(self, logical_id_base: str):
        """Deploys local content if website hosting and deployment config are enabled."""
        # ... (Implementation from previous response, ensure cloudfront import exists) ...
        deployment_config = self.config.get("deployment"); is_website = self.config.get("website_index_document") is not None 
        if is_website and deployment_config and isinstance(deployment_config, dict) and deployment_config.get("enabled", False):
            local_path = deployment_config.get("source_local_path")
            if local_path and Path(local_path).is_dir():
                 distribution = None; cf_dist_id = deployment_config.get("distribution_id")
                 if cf_dist_id:
                     try: cf_domain = deployment_config.get("distribution_domain_name", f"{cf_dist_id}.cloudfront.net"); distribution = cloudfront.Distribution.from_distribution_attributes(self, f"CfImport{logical_id_base}", distribution_id=cf_dist_id, domain_name=cf_domain)
                     except Exception as cf_err: logger.warning(f"Failed CF import {cf_dist_id}: {cf_err}")
                 try: s3_deployment.BucketDeployment(self, f"DeployWebsite{logical_id_base}", sources=[s3_deployment.Source.asset(str(local_path))], destination_bucket=self.s3_bucket, distribution=distribution, distribution_paths=deployment_config.get("distribution_paths", ["/*"]) if distribution else None)
                 except Exception as deploy_err: logger.error(f"Failed BucketDeployment for {logical_id_base}: {deploy_err}")
            elif local_path: logger.error(f"Local source path does not exist: {local_path}")


    def _apply_tags(self, logical_id_base: str, is_existing: bool):
        """Applies tags from config, handling defaults and overrides."""
        # ... (Implementation from previous response) ...
        if not hasattr(self.s3_bucket, 'node'): 
             if is_existing: logger.debug(f"Cannot tag imported bucket {logical_id_base}.")
             return
        final_tags = self.config.get("tags", {}) 
        if not isinstance(final_tags, dict): final_tags = {}
        if not any(k.lower() == "name" for k in final_tags.keys()): final_tags["Name"] = f"{Stack.of(self).stack_name}-{logical_id_base}"[:256]
        if final_tags:
            logger.info(f"Applying {len(final_tags)} tags to {logical_id_base}...")
            for key, value in final_tags.items(): Tags.of(self.s3_bucket).add(str(key), str(value)[:256])


    def _setup_outputs(self, logical_id_base: str):
        """Sets up CfnOutputs for the bucket."""
        # ... (Implementation from previous response) ...
        CfnOutput(self, f"BucketNameOutput{logical_id_base}", value=self.s3_bucket.bucket_name)
        CfnOutput(self, f"BucketArnOutput{logical_id_base}", value=self.s3_bucket.bucket_arn)
        if self.config.get("website_index_document"):
            if hasattr(self.s3_bucket, 'bucket_website_url'): CfnOutput(self, f"BucketWebsiteUrlOutput{logical_id_base}", value=self.s3_bucket.bucket_website_url)
            if hasattr(self.s3_bucket, 'bucket_website_domain_name'): CfnOutput(self, f"BucketWebsiteDomainNameOutput{logical_id_base}", value=self.s3_bucket.bucket_website_domain_name)

