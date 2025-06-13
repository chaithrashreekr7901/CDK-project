# CRMP-PROJECT/cdk_project/s3/s3_deployments_group_nested_stack.py
import logging
import copy
from aws_cdk import (
    NestedStack,
    Tags
)
from constructs import Construct
from .s3_bucket_instance_stack import S3BucketInstanceStack

logger = logging.getLogger(__name__)

def merge_dicts(base, E):
    result = copy.deepcopy(base)
    for k, v in E.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = merge_dicts(result[k], v)
        elif isinstance(v, list) and k in result and isinstance(result[k], list):
            result[k] = v
        else:
            result[k] = v
    return result

class S3DeploymentsGroupNestedStack(NestedStack):
    def __init__(self, scope: Construct, id: str, s3_deployments_config: dict, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        group_description = s3_deployments_config.get("description", "S3 Bucket Deployments Group")
        logger.info(f"S3DeploymentsGroupNestedStack: Initializing for: {group_description}")
        Tags.of(self).add("CDKResourceGroup", "S3Buckets")

        self.deployed_bucket_stacks = {}

        default_bucket_config = s3_deployments_config.get("defaults", {})
        if not isinstance(default_bucket_config, dict):
            logger.warning("Top-level 'defaults' in S3 config is not a dictionary. Defaults will not be applied.")
            default_bucket_config = {}

        bucket_configurations_list = s3_deployments_config.get("buckets", [])

        if not bucket_configurations_list:
            logger.warning("S3DeploymentsGroupNestedStack: No S3 bucket configurations found in 's3_deployments_config.buckets'.")
            return

        instance_counter = 0

        for i, bucket_cfg_entry in enumerate(bucket_configurations_list):
            if not isinstance(bucket_cfg_entry, dict):
                logger.error(f"Skipping invalid bucket config entry at index {i}: not a dictionary.")
                continue
            
            if not bucket_cfg_entry.get("enabled", False):
                skipped_bucket_id = bucket_cfg_entry.get('id', f'UnknownID_Index{i}_Disabled')
                logger.info(f"Skipping S3 bucket config '{skipped_bucket_id}' (enabled: false).")
                continue

            config_id_base = bucket_cfg_entry.get("id")
            bucket_specific_config = bucket_cfg_entry.get("config", {})
            count = bucket_cfg_entry.get("count", 1)

            if not config_id_base:
                logger.error(f"Skipping S3 deployment at index {i} due to missing 'id'.")
                continue
            if not isinstance(bucket_specific_config, dict):
                logger.error(f"Skipping S3 bucket '{config_id_base}': 'config' block is not a dictionary.")
                continue
            if not isinstance(count, int) or count < 1:
                logger.warning(f"Invalid 'count' for bucket '{config_id_base}'. Defaulting to 1.")
                count = 1

            for item_index in range(count):
                instance_counter += 1
                
                merged_config = merge_dicts(default_bucket_config, bucket_specific_config)

                # --- FIX IS HERE ---
                # Force the ID used for the nested stack to be all lowercase.
                # This prevents the uppercase characters from ever reaching the child stack.
                sanitized_config_id_base = ''.join(filter(str.isalnum, config_id_base)).lower()
                # --- END OF FIX ---

                if not sanitized_config_id_base: sanitized_config_id_base = f"bucket{i}"
                
                instance_suffix = f"{item_index + 1}" if count > 1 else ""
                final_logical_id_base = f"{sanitized_config_id_base}{instance_suffix}"
                nested_stack_cdk_id = f"{final_logical_id_base}S3BucketInstanceStack"
                
                merged_config['_instance_index_'] = item_index

                bucket_name_for_desc = merged_config.get("bucket_name", f"{config_id_base}{instance_suffix}")

                logger.info(f"Defining S3BucketInstanceStack for ConfigID '{config_id_base}' (Instance {item_index+1}/{count}) -> CDK_ID '{nested_stack_cdk_id}'.")
                
                try:
                    bucket_stack = S3BucketInstanceStack(
                        self,
                        nested_stack_cdk_id,
                        s3_merged_config=merged_config,
                        description=f"Nested Stack for S3 Bucket: {bucket_name_for_desc}"
                    )
                    storage_key = f"{config_id_base}_{item_index}" if count > 1 else config_id_base
                    self.deployed_bucket_stacks[storage_key] = bucket_stack
                    Tags.of(bucket_stack).add("S3ConfigID", config_id_base)
                    if count > 1: Tags.of(bucket_stack).add("S3InstanceIndex", str(item_index))

                except Exception as e:
                    logger.error(f"FAILED to instantiate S3BucketInstanceStack for '{nested_stack_cdk_id}': {e}", exc_info=True)