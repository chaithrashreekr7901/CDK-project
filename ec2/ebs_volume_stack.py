import logging
import typing
from aws_cdk import (
    NestedStack,
    Tags,
    CfnTag, # Retained for explicit CfnTag usage if ever needed, though Tags.of() is preferred for L2
    CfnOutput,
    RemovalPolicy,
    Size,     # Still needed for Size.gibibytes()
    # Duration, Stack, aws_iam, custom_resources are no longer needed for this simplified version
    aws_ec2 as ec2,
    aws_kms as kms
)
from constructs import Construct

logger = logging.getLogger(__name__)

class EbsVolumeStack(NestedStack):
    volume: ec2.Volume
    volume_attachment: ec2.CfnVolumeAttachment | None = None
    # Removed: generated_mount_script_output
    # Removed: ssm_run_command_cr

    # Removed: _generate_standalone_mount_script method

    def __init__(self, scope: Construct, construct_id: str,
                 volume_config: dict,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = volume_config
        self.volume_attachment = None # Initialize

        volume_name_tag_value = self.config.get("volume_name_tag", construct_id)
        logger.info(f"EbsVolumeStack '{construct_id}': Initializing for EBS Volume '{volume_name_tag_value}'.")

        availability_zone = self.config.get("availability_zone")
        if not availability_zone:
            raise ValueError(f"EBS Volume '{volume_name_tag_value}': 'availability_zone' is required.")

        size_gb_config = self.config.get("size_gb")
        snapshot_id_config = self.config.get("snapshot_id")

        if not snapshot_id_config and not size_gb_config:
            raise ValueError(f"EBS Volume '{volume_name_tag_value}': 'size_gb' must be provided if 'snapshot_id' is not set.")
        
        volume_size_property = Size.gibibytes(int(size_gb_config)) if size_gb_config is not None else None
        
        volume_type_str = self.config.get("volume_type", "gp3").lower()
        ebs_volume_type_map = {
            "standard": ec2.EbsDeviceVolumeType.STANDARD, "gp2": ec2.EbsDeviceVolumeType.GP2,
            "gp3": ec2.EbsDeviceVolumeType.GP3, "io1": ec2.EbsDeviceVolumeType.IO1,
            "io2": ec2.EbsDeviceVolumeType.IO2, "sc1": ec2.EbsDeviceVolumeType.SC1,
            "st1": ec2.EbsDeviceVolumeType.ST1
        }
        cdk_ebs_type = ebs_volume_type_map.get(volume_type_str)
        if not cdk_ebs_type:
            logger.warning(f"Invalid EBS volume_type '{volume_type_str}' for '{volume_name_tag_value}'. Defaulting to GP3.")
            cdk_ebs_type = ec2.EbsDeviceVolumeType.GP3

        iops_config = self.config.get("iops")
        if iops_config is not None:
            try: iops_config = int(iops_config)
            except ValueError:
                logger.error(f"Invalid 'iops' value '{iops_config}' for {volume_name_tag_value}. Must be an integer.")
                iops_config = None # Or raise, depending on desired strictness
        
        throughput_config = self.config.get("throughput")
        if throughput_config is not None:
            try: throughput_config = int(throughput_config)
            except ValueError:
                logger.error(f"Invalid 'throughput' value '{throughput_config}' for {volume_name_tag_value}. Must be an integer.")
                throughput_config = None

        if cdk_ebs_type in [ec2.EbsDeviceVolumeType.IO1, ec2.EbsDeviceVolumeType.IO2] and not iops_config:
             logger.warning(f"EBS Volume '{volume_name_tag_value}': Type '{volume_type_str}' typically requires 'iops'. Ensure config or defaults are correct.")

        if cdk_ebs_type != ec2.EbsDeviceVolumeType.GP3 and throughput_config is not None:
            logger.warning(f"EBS Volume '{volume_name_tag_value}': 'throughput' is only applicable for gp3 volumes. It will be ignored.")
            throughput_config = None 

        encrypted_config = self.config.get("encrypted", True)
        kms_key_id_config = self.config.get("kms_key_id")
        encryption_key_object = None
        if encrypted_config and kms_key_id_config:
            try:
                encryption_key_object = kms.Key.from_key_arn(self, "VolumeKmsKeyImport", kms_key_id_config)
                logger.info(f"Using custom KMS key ARN: {kms_key_id_config} for volume '{volume_name_tag_value}'.")
            except Exception as e:
                logger.error(f"Failed to import KMS key '{kms_key_id_config}': {e}. Using AWS-managed key.")
                encryption_key_object = None

        removal_policy_str = self.config.get("removal_policy", "RETAIN").upper()
        removal_policy_cdk = RemovalPolicy.DESTROY if removal_policy_str == "DESTROY" else RemovalPolicy.RETAIN

        # Debug logging (still useful)
        logger.info(f"DEBUG EbsVolumeStack ({volume_name_tag_value}): Values before ec2.Volume call:")
        logger.info(f"  Raw size_gb_config from self.config: {self.config.get('size_gb')} (type: {type(self.config.get('size_gb'))})")
        logger.info(f"  Raw iops_config from self.config: {self.config.get('iops')} (type: {type(self.config.get('iops'))})")
        logger.info(f"  Resolved volume_size_property for CDK: {volume_size_property}")
        logger.info(f"  Resolved cdk_ebs_type for CDK: {cdk_ebs_type}")
        effective_iops = iops_config if cdk_ebs_type in [ec2.EbsDeviceVolumeType.IO1, ec2.EbsDeviceVolumeType.IO2, ec2.EbsDeviceVolumeType.GP3] else None
        logger.info(f"  Effective IOPS to be passed: {effective_iops}")
        effective_throughput = throughput_config if cdk_ebs_type == ec2.EbsDeviceVolumeType.GP3 else None
        logger.info(f"  Effective Throughput to be passed: {effective_throughput}")

        self.volume = ec2.Volume(self, "EbsVolumeResource",
            availability_zone=availability_zone,
            size=volume_size_property,
            volume_type=cdk_ebs_type,
            iops=effective_iops,
            throughput=effective_throughput,
            encrypted=encrypted_config,
            encryption_key=encryption_key_object,
            snapshot_id=snapshot_id_config,
            # multi_attach_enabled=self.config.get("multi_attach_enabled"), # Kept commented for older CDK compatibility
            removal_policy=removal_policy_cdk,
            volume_name=volume_name_tag_value
        )
        
        configured_tags = self.config.get("tags", {})
        if isinstance(configured_tags, dict):
            for k, v_tag in configured_tags.items():
                if k.lower() != "name": 
                    Tags.of(self.volume).add(str(k), str(v_tag))
        
        logger.info(f"EBS Volume '{volume_name_tag_value}' (Tokenized ID: {self.volume.volume_id}) defined in AZ '{availability_zone}'.")

        # --- Attachment Logic (remains the same) ---
        attachment_conf = self.config.get("attachment_config", {})
        if attachment_conf.get("enabled", False):
            instance_id_to_attach = attachment_conf.get("instance_id")
            device_name_for_attachment = attachment_conf.get("device_name") 

            if not instance_id_to_attach or not device_name_for_attachment:
                logger.error(f"EBS Volume '{volume_name_tag_value}': Attachment enabled but 'instance_id' or 'device_name' is missing.")
            else:
                self.volume_attachment = ec2.CfnVolumeAttachment(self, "VolumeAttachmentResource",
                    instance_id=instance_id_to_attach,
                    volume_id=self.volume.volume_id,
                    device=device_name_for_attachment
                )
                if self.volume.node.default_child: # Ensure dependency on L2 Volume's underlying CfnVolume
                    self.volume_attachment.node.add_dependency(self.volume.node.default_child)
                
                logger.info(f"Volume (Tokenized ID: {self.volume.volume_id}) defined for attachment to instance '{instance_id_to_attach}' as '{device_name_for_attachment}'.")
        
        # --- Removed SSM Run Command and GeneratedMountScriptOutput logic ---

        # --- Standard Outputs (remain the same) ---
        CfnOutput(self, "VolumeIdOutput",
                  value=self.volume.volume_id,
                  description=f"ID of the created EBS Volume: {volume_name_tag_value}")
        if self.volume_attachment:
            CfnOutput(self, "AttachedToInstanceIdOutput",
                      value=self.volume_attachment.instance_id,
                      description=f"Instance ID volume '{volume_name_tag_value}' is attached to.")
            CfnOutput(self, "AttachedAsDeviceNameOutput",
                      value=self.volume_attachment.device,
                      description=f"Device name for volume '{volume_name_tag_value}' on the instance.")