# cdk_project/ec2/ec2_instance_nested_stack.py
import logging
from pathlib import Path
import base64
import typing

from aws_cdk import (
    NestedStack, Tags, CfnTag, CfnOutput, Aws, Fn, CfnCondition,
    aws_ec2 as ec2,
    aws_iam as iam
)
from constructs import Construct

logger = logging.getLogger(__name__)

def construct_id_to_cdk_id_part(original_id: str, max_len: int = 30) -> str:
    """Converts a user-defined ID into a CloudFormation-compatible ID part."""
    return ''.join(filter(str.isalnum, original_id))[:max_len].title() if original_id else "Default"


class Ec2InstanceNestedStack(NestedStack):
    public_instance: typing.Optional[ec2.CfnInstance] = None # Changed to CfnInstance as you are creating L1
    instance_id_token: typing.Optional[str] = None
    public_instance_name_tag_value: typing.Optional[str] = None
    created_security_group_object: typing.Optional[ec2.SecurityGroup] = None
    created_instance_role: typing.Optional[iam.Role] = None # To store the created IAM Role L2 object


    def __init__(self, scope: Construct, construct_id: str,
                 ec2_specific_config: dict,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = ec2_specific_config
        self.cfn_ec2_instance = None # Initialize attribute
        self.created_security_group_object = None # Initialize attribute
        self.instance_id_token = None # Initialize attribute

        instance_name_tag_value = self.config.get("instance_name", construct_id)
        self.public_instance_name_tag_value = instance_name_tag_value # Ensure this is set for outputs

        logger.info(f"Ec2InstanceNestedStack '{construct_id}': Initializing for EC2 Instance '{instance_name_tag_value}'.")

        network_cfg = self.config.get("network_config", {})
        vpc_id_from_config = network_cfg.get("vpc_id")
        subnet_id_from_config = network_cfg.get("subnet_id")

        if not subnet_id_from_config:
            raise ValueError(f"'network_config.subnet_id' is required for EC2 instance '{instance_name_tag_value}'")

        sg_definition_conf = self.config.get("security_group_definition", {})
        final_security_group_ids = self._resolve_or_create_security_groups(vpc_id_from_config, network_cfg, sg_definition_conf)

        ami_id_str = self._resolve_ami_id_string()
        if not ami_id_str: raise ValueError(f"Could not resolve AMI ID for '{instance_name_tag_value}'")

        instance_type_str = self.config.get("instance_type", "t3.micro")
        key_name_str = self.config.get("key_name")

        # Call the updated method to get the instance profile name/ARN
        iam_instance_profile_name_or_arn = self._resolve_iam_instance_profile_name(instance_name_tag_value)

        user_data_b64_str = self._resolve_user_data_with_mounts()

        block_device_mappings_for_instance = self._configure_block_devices_for_cfn_instance()

        # --- Network Interface Handling (same as your latest version) ---
        network_interfaces = [
            ec2.CfnInstance.NetworkInterfaceProperty(
                device_index="0",
                subnet_id=subnet_id_from_config,
                associate_public_ip_address=network_cfg.get("associate_public_ip_address"),
                group_set=final_security_group_ids if final_security_group_ids else None,
                delete_on_termination=True,
                private_ip_address=network_cfg.get("private_ip_address")
            )
        ]
        advanced_network_interfaces_conf = network_cfg.get("network_interfaces")
        if advanced_network_interfaces_conf and isinstance(advanced_network_interfaces_conf, list):
            logger.info(f"Using advanced network_interfaces configuration for {instance_name_tag_value}.")
            network_interfaces = []
            for nic_conf in advanced_network_interfaces_conf:
                if isinstance(nic_conf, dict) and nic_conf.get("device_index") is not None:
                    network_interfaces.append(ec2.CfnInstance.NetworkInterfaceProperty(
                        device_index=str(nic_conf["device_index"]),
                        subnet_id=nic_conf.get("subnet_id"),
                        associate_public_ip_address=nic_conf.get("associate_public_ip_address"),
                        groups=nic_conf.get("groups"),
                        description=nic_conf.get("description"),
                        private_ip_address=nic_conf.get("private_ip_address"),
                        secondary_private_ip_address_count=nic_conf.get("secondary_private_ip_address_count"),
                        delete_on_termination=nic_conf.get("delete_on_termination", True)
                    ))
            if not network_interfaces:
                raise ValueError(f"Advanced network_interfaces was specified but resulted in no valid interfaces for {instance_name_tag_value}")
        # --- End Network Interface Handling ---


        cfn_instance_tags = [CfnTag(key="Name", value=instance_name_tag_value)]
        additional_tags_from_config = self.config.get("tags", {})
        if isinstance(additional_tags_from_config, dict):
            for k, v in additional_tags_from_config.items():
                if k.lower() != "name": cfn_instance_tags.append(CfnTag(key=str(k), value=str(v)))

        metadata_options_dict_for_override = self._prepare_metadata_options_for_override()

        cfn_instance_props: dict = {
            "image_id": ami_id_str,
            "instance_type": instance_type_str,
            "network_interfaces": network_interfaces,
            "key_name": key_name_str,
            "iam_instance_profile": iam_instance_profile_name_or_arn, # This will now be the string name/ARN
            "user_data": user_data_b64_str,
            "block_device_mappings": block_device_mappings_for_instance,
            "disable_api_termination": self.config.get("disable_api_termination"),
            "instance_initiated_shutdown_behavior": self.config.get("instance_initiated_shutdown_behavior"),
            "monitoring": self.config.get("monitoring_detailed"),
            "tags": cfn_instance_tags,
            "source_dest_check": network_cfg.get("source_dest_check") if not advanced_network_interfaces_conf else None,
        }

        self._add_other_specific_cfn_properties(cfn_instance_props)

        final_cfn_instance_props = {k: v for k, v in cfn_instance_props.items() if v is not None}
        self.cfn_ec2_instance = ec2.CfnInstance(self, "CfnInstanceResource", **final_cfn_instance_props)

        if metadata_options_dict_for_override:
            self.cfn_ec2_instance.add_property_override("MetadataOptions", metadata_options_dict_for_override)

        self.instance_id_token = self.cfn_ec2_instance.ref
        logger.info(f"Defined L1 CfnInstance '{instance_name_tag_value}' with ID Token '{self.instance_id_token}'.")

        # For L2 compatibility, if needed elsewhere, you can expose an L2 instance
        # self.public_instance = ec2.Instance.from_instance_attributes(
        #     self, "L2Instance",
        #     instance_id=self.instance_id_token,
        #     vpc=vpc # You'd need to pass vpc to the constructor or resolve it here
        # )

        # Ensure public_instance_name_tag_value is correctly exposed
        self.public_instance_name_tag_value = instance_name_tag_value

        # Setup outputs using the L1 CfnInstance attributes
        self._setup_outputs_for_cfn(instance_name_tag_value, key_name_str, iam_instance_profile_name_or_arn)


    def _generate_mount_script_block(self, attachment_device_name: str, mount_config: dict) -> str:
        """
        Generates a shell script block for a single volume mount configuration,
        discovering the OS device path from the attachment_device_name.
        """
        mount_point = mount_config.get("mount_point")
        fs_type = mount_config.get("filesystem_type", "ext4")
        owner_user = mount_config.get("owner_user")
        owner_group = mount_config.get("owner_group")
        permissions = mount_config.get("permissions")

        if not attachment_device_name or not mount_point:
            logger.warning(f"Skipping mount config due to missing attachment_device_name ('{attachment_device_name}') or mount_point ('{mount_point}')")
            return ""

        if not attachment_device_name.startswith("/dev/"):
            logger.warning(f"Attachment device path '{attachment_device_name}' does not look like an absolute device path. Proceeding, but verify.")

        if not mount_point.startswith("/"):
            logger.error(f"Mount point '{mount_point}' must be an absolute path. Skipping this mount for {attachment_device_name}.")
            return f"echo \"Error: Mount point '{mount_point}' for attachment '{attachment_device_name}' is not absolute. Skipping.\" >&2"

        script_lines = [
            f"echo \"--- Processing mount for attachment {attachment_device_name} to {mount_point} ---\"",
            f"ATTACHMENT_DEVICE_NAME=\"{attachment_device_name}\"",
            f"MOUNT_TARGET=\"{mount_point}\"",
            f"FS_TO_USE=\"{fs_type}\"",
            "OS_DEVICE_PATH=\"\"",

            "# Step 1: Wait for the attachment device name (e.g., /dev/sdf) to appear",
            "for i in {1..24}; do # Increased retries to 2 minutes (24 * 5s)",
            "  if [ -b \"$ATTACHMENT_DEVICE_NAME\" ]; then",
            "    echo \"Attachment device $ATTACHMENT_DEVICE_NAME found after $i attempts.\"",
            "    break",
            "  fi",
            "  if [ $i -eq 24 ]; then",
            "    echo \"Error: Attachment device $ATTACHMENT_DEVICE_NAME did not become available after 120 seconds. Cannot proceed with this mount.\" >&2",
            "    return 1",
            "  fi",
            "  echo \"Attempt $i: Attachment device $ATTACHMENT_DEVICE_NAME not found yet. Sleeping 5s...\"",
            "  sleep 5",
            "done",
            "",
            "# Step 2: Discover the actual OS device path (e.g., /dev/nvme1n1 or /dev/xvdf)",
            "echo \"Attempting to resolve actual OS device path for $ATTACHMENT_DEVICE_NAME...\"",
            "if OS_DEVICE_PATH_TEMP=$(realpath \"$ATTACHMENT_DEVICE_NAME\"); then",
            "  if [ -b \"$OS_DEVICE_PATH_TEMP\" ]; then",
            "    OS_DEVICE_PATH=\"$OS_DEVICE_PATH_TEMP\"",
            "    echo \"Resolved $ATTACHMENT_DEVICE_NAME to OS device path: $OS_DEVICE_PATH\"",
            "  else",
            "    echo \"Error: realpath resolved $ATTACHMENT_DEVICE_NAME to $OS_DEVICE_PATH_TEMP, but it's not a block device. Trying original name.\" >&2",
            "    OS_DEVICE_PATH=\"$ATTACHMENT_DEVICE_NAME\" # Fallback to using attachment name directly if realpath fails to yield a block device",
            "  fi",
            "else",
            "  echo \"Error: realpath command failed for $ATTACHMENT_DEVICE_NAME. Trying original name.\" >&2",
            "  OS_DEVICE_PATH=\"$ATTACHMENT_DEVICE_NAME\" # Fallback",
            "fi",
            "",
            "if [ -z \"$OS_DEVICE_PATH\" ]; then",
            "  echo \"Critical Error: OS_DEVICE_PATH is empty for $ATTACHMENT_DEVICE_NAME. Cannot mount.\" >&2",
            "  return 1",
            "fi",
            "echo \"Using device path $OS_DEVICE_PATH for mount operations.\"",
            "",
            "# Step 3: Check filesystem on $OS_DEVICE_PATH and format if needed",
            "echo \"Checking filesystem on $OS_DEVICE_PATH...\"",
            "EXISTING_FS_TYPE=$(sudo blkid -p \"$OS_DEVICE_PATH\" -s TYPE -o value || echo \"\")",
            "if [ -z \"$EXISTING_FS_TYPE\" ]; then",
            f"  echo \"Device $OS_DEVICE_PATH is not formatted or blkid failed. Attempting to format as {fs_type}...\"",
            f"  if sudo mkfs -t \"${{FS_TO_USE}}\" \"${{OS_DEVICE_PATH}}\"; then",
            "    echo \"Formatting $OS_DEVICE_PATH complete.\"",
            "  else",
            "    echo \"Error: Formatting $OS_DEVICE_PATH as $FS_TO_USE failed.\" >&2",
            "    return 1",
            "  fi",
            "else",
            "  echo \"Device $OS_DEVICE_PATH already has a filesystem: $EXISTING_FS_TYPE.\"",
            "  if [ \"$EXISTING_FS_TYPE\" != \"$FS_TO_USE\" ]; then",
            f"    echo \"Warning: Existing filesystem $EXISTING_FS_TYPE on $OS_DEVICE_PATH differs from configured {fs_type}. Using existing.\" >&2",
            "  fi",
            "fi",
            "",
            "# Step 4: Create mount point and mount",
            "if [ ! -d \"$MOUNT_TARGET\" ]; then",
            "  echo \"Creating mount point $MOUNT_TARGET...\"",
            "  if ! sudo mkdir -p \"$MOUNT_TARGET\"; then",
            "    echo \"Error: Could not create mount point $MOUNT_TARGET\" >&2",
            "    return 1",
            "  fi",
            "fi",
            "",
            "if ! findmnt -rno TARGET \"$OS_DEVICE_PATH\" | grep -q \"^$MOUNT_TARGET$\"; then",
            "  echo \"Mounting $OS_DEVICE_PATH to $MOUNT_TARGET...\"",
            "  if ! sudo mount \"$OS_DEVICE_PATH\" \"$MOUNT_TARGET\"; then",
            "    echo \"Error: Failed to mount $OS_DEVICE_PATH to $MOUNT_TARGET.\" >&2",
            "    return 1",
            "  fi",
            "",
            "  UUID=$(sudo blkid -s UUID -o value \"$OS_DEVICE_PATH\" || echo \"\")",
            "  if [ -n \"$UUID\" ]; then",
            f"    FSTAB_ENTRY=\"UUID=$UUID $MOUNT_TARGET $FS_TO_USE defaults,nofail,discard 0 2\"",
            "    if ! grep -qs \"^UUID=$UUID\" /etc/fstab && ! grep -qs \" $MOUNT_TARGET \" /etc/fstab; then",
            "      echo \"Adding to /etc/fstab: $FSTAB_ENTRY\"",
            "      echo \"$FSTAB_ENTRY\" | sudo tee -a /etc/fstab",
            "    else",
            "      echo \"fstab entry for UUID=$UUID or mount point $MOUNT_TARGET already exists.\"",
            "    fi",
            "  else",
            "    echo \"Warning: Could not get UUID for $OS_DEVICE_PATH. fstab entry not added automatically.\" >&2",
            "  fi",
            "else",
            "  echo \"$OS_DEVICE_PATH is already mounted at $MOUNT_TARGET or findmnt check failed.\"",
            "fi",
        ]

        if owner_user and owner_group:
            script_lines.append(f"sudo chown {owner_user}:{owner_group} \"$MOUNT_TARGET\" || echo \"Warning: chown {owner_user}:{owner_group} on $MOUNT_TARGET failed.\" >&2")
        elif owner_user:
            script_lines.append(f"sudo chown {owner_user} \"$MOUNT_TARGET\" || echo \"Warning: chown {owner_user} on $MOUNT_TARGET failed.\" >&2")

        if permissions:
            script_lines.append(f"sudo chmod {permissions} \"$MOUNT_TARGET\" || echo \"Warning: chmod {permissions} on $MOUNT_TARGET failed.\" >&2")

        script_lines.append(f"echo \"--- Finished processing mount for attachment {attachment_device_name} (OS path $OS_DEVICE_PATH) to {mount_point} ---\"")

        function_name = "mount_volume_" + "".join(filter(str.isalnum, attachment_device_name.replace("/dev/", "")))
        full_script = [f"{function_name}() {{"]
        full_script.extend([f"  {line}" for line in script_lines])
        full_script.append("  return 0 # Explicit success")
        full_script.append("}")
        full_script.append(f"{function_name} || echo \"Function {function_name} reported an error.\" >&2")

        return "\n".join(full_script)

    def _resolve_user_data_with_mounts(self) -> typing.Optional[str]:
        ud_conf = self.config.get("user_data", {})
        base_commands_list = []

        user_data_header = [
            "#!/bin/bash",
            "exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1",
            "set -x",
            "echo \"Starting UserData script execution... $(date)\"",
        ]

        if ud_conf.get("enabled", False):
            ud_type = ud_conf.get("type", "SHELL_SCRIPT").upper()
            raw_script_content = ""
            if ud_type == "RAW_TEXT":
                raw_script_content = ud_conf.get("raw_commands", "")
            elif ud_type == "SHELL_SCRIPT":
                if ud_conf.get("script_path"):
                    try:
                        raw_script_content = Path(ud_conf["script_path"]).read_text()
                    except Exception as e:
                        logger.error(f"Error reading UserData script {ud_conf['script_path']}: {e}")
                        raw_script_content = f"echo \"Error reading base user data script from {ud_conf['script_path']}\" >&2\n"
                elif ud_conf.get("raw_commands"):
                    raw_script_content = ud_conf.get("raw_commands", "")

            if raw_script_content:
                lines = raw_script_content.splitlines()
                if lines and lines[0].startswith("#!"):
                    base_commands_list.extend(lines[1:])
                else:
                    base_commands_list.extend(lines)

        mount_script_blocks_list = []

        storage_conf = self.config.get("storage_config", {})
        configured_ebs_block_devices = []
        if isinstance(storage_conf, dict) and storage_conf.get("enabled", True):
            additional_ebs_list = storage_conf.get("ebs_block_devices", [])
            if isinstance(additional_ebs_list, list):
                configured_ebs_block_devices = [
                    dev for dev in additional_ebs_list
                    if isinstance(dev, dict) and dev.get("enabled", True) and dev.get("device_name")
                ]

        configured_mount_configs = []
        mount_configurations_list = self.config.get("mount_configurations", [])
        if isinstance(mount_configurations_list, list):
            configured_mount_configs = [
                conf for conf in mount_configurations_list
                if isinstance(conf, dict) and conf.get("enabled", False) and conf.get("mount_point")
            ]

        if configured_ebs_block_devices and configured_mount_configs:
            logger.info(f"Instance '{self.config.get('instance_name')}': Found {len(configured_ebs_block_devices)} enabled additional EBS devices and {len(configured_mount_configs)} enabled mount configurations.")

            mount_script_blocks_list.append("\n# --- EBS Volume Mounts (Generated by CDK with Auto-Discovery) ---")

            num_volumes_to_mount = min(len(configured_ebs_block_devices), len(configured_mount_configs))
            if len(configured_ebs_block_devices) != len(configured_mount_configs):
                logger.warning(
                    f"Mismatch between enabled additional EBS devices ({len(configured_ebs_block_devices)}) "
                    f"and enabled mount configurations ({len(configured_mount_configs)}). "
                    f"Will attempt to mount first {num_volumes_to_mount} based on order."
                )
                mount_script_blocks_list.append(
                    f"# WARNING: Mismatch in count of EBS devices and mount configs. Processing {num_volumes_to_mount} pairs."
                )

            for i in range(num_volumes_to_mount):
                ebs_device_info = configured_ebs_block_devices[i]
                mount_conf_info = configured_mount_configs[i]

                attachment_name = ebs_device_info.get("device_name")
                if not attachment_name:
                    logger.error(f"Skipping mount for index {i}: EBS device info missing 'device_name'. EBS: {ebs_device_info}")
                    mount_script_blocks_list.append(f"# ERROR: No attachment_name for EBS device at index {i}")
                    continue

                logger.info(f"Generating mount script for attachment '{attachment_name}' with mount config: {mount_conf_info.get('mount_point')}")
                script_block = self._generate_mount_script_block(attachment_name, mount_conf_info)
                if script_block:
                    mount_script_blocks_list.append(script_block)

        final_script_lines = list(user_data_header)
        if base_commands_list:
            final_script_lines.append("\n# --- Base User Data Commands ---")
            final_script_lines.extend(base_commands_list)

        if mount_script_blocks_list:
            final_script_lines.extend(mount_script_blocks_list)

        final_script_lines.append("echo \"Finished UserData script execution. $(date)\"")

        if len(final_script_lines) > len(user_data_header) + 2:
            final_user_data_script = "\n".join(final_script_lines)
            logger.info(f"Final UserData for '{self.config.get('instance_name')}':\n{final_user_data_script[:1500]}...")
            return Fn.base64(final_user_data_script)

        logger.info(f"No substantive UserData commands or mount configurations for '{self.config.get('instance_name')}'. Not setting UserData.")
        return None


    def _resolve_or_create_security_groups(self, vpc_id_for_sg: typing.Optional[str], network_cfg: dict, sg_definition_conf: dict) -> typing.Optional[typing.List[str]]:
        existing_sg_ids = network_cfg.get("security_group_ids")
        if existing_sg_ids and isinstance(existing_sg_ids, list) and len(existing_sg_ids) > 0:
            return existing_sg_ids
        
        if sg_definition_conf.get("enabled", False):
            if not vpc_id_for_sg:
                raise ValueError("VPC ID is required to create a new security group.")

            # --- THE FIX ---
            availability_zones = network_cfg.get("availability_zones")
            if not availability_zones or not isinstance(availability_zones, list):
                raise ValueError("network_config.availability_zones (a list of strings) is required to create a security group.")

            vpc_for_l2_sg = ec2.Vpc.from_vpc_attributes(self, "VpcContextForSG",
                vpc_id=vpc_id_for_sg,
                availability_zones=availability_zones
            )
            # --- END OF FIX ---

            return self._create_new_security_group(vpc_for_l2_sg, sg_definition_conf)
        
        return None

    def _create_new_security_group(self, vpc: ec2.IVpc, sg_def: dict) -> typing.Optional[typing.List[str]]:
        instance_name = self.config.get('instance_name', self.node.id); sg_name = sg_def.get("name", f"{instance_name}-sg")
        sg_cdk_id = construct_id_to_cdk_id_part(f"SgFor{instance_name}"); sg_description = sg_def.get("description", f"Security group for {instance_name}")
        allow_all_outbound = sg_def.get("allow_all_outbound", True)
        try:
            self.created_security_group_object = ec2.SecurityGroup(self, sg_cdk_id, vpc=vpc, description=sg_description, security_group_name=sg_name, allow_all_outbound=allow_all_outbound)
            Tags.of(self.created_security_group_object).add("Name", sg_name); sg_tags = sg_def.get("tags", self.config.get("tags", {}));
            if isinstance(sg_tags, dict):
                for k,v in sg_tags.items():
                    if k.lower() != "name": Tags.of(self.created_security_group_object).add(k,v)
            logger.info(f"Created new Security Group '{sg_name}' (ID Token: {self.created_security_group_object.security_group_id})")
            for rule_conf in sg_def.get("ingress_rules", []): self._add_sg_rule(self.created_security_group_object, sg_name, rule_conf, is_ingress=True)
            if not allow_all_outbound:
                for rule_conf in sg_def.get("egress_rules", []): self._add_sg_rule(self.created_security_group_object, sg_name, rule_conf, is_ingress=False)
            return [self.created_security_group_object.security_group_id]
        except Exception as e: logger.error(f"Failed to create new SG '{sg_name}': {e}", exc_info=True); return None

    def _add_sg_rule(self, sg: ec2.SecurityGroup, sg_physical_name_for_log: str, rule_conf: dict, is_ingress: bool):
        try:
            peer = self._parse_sg_peer(rule_conf.get("peer_type"), rule_conf.get("peer_value"))
            connection = self._parse_sg_connection(rule_conf.get("protocol"), rule_conf.get("port"), rule_conf.get("from_port"), rule_conf.get("to_port"))
            description = rule_conf.get("description")
            if peer and connection:
                if is_ingress: sg.add_ingress_rule(peer, connection, description)
                else: sg.add_egress_rule(peer, connection, description)
                logger.info(f"Added {'ingress' if is_ingress else 'egress'} rule to '{sg_physical_name_for_log}'. Peer: {peer}, Conn: {connection}")
            else: logger.warning(f"Could not parse peer/connection for SG rule for '{sg_physical_name_for_log}': {rule_conf}")
        except Exception as e: logger.error(f"Failed to add SG rule {rule_conf} to SG '{sg_physical_name_for_log}': {e}", exc_info=True)

    def _parse_sg_peer(self, peer_type_str: typing.Optional[str], peer_value: typing.Optional[str]) -> typing.Optional[ec2.IPeer]:
        if not peer_type_str: logger.warning(f"SG rule peer_type missing. Value: '{peer_value}'"); return None
        pt = peer_type_str.upper()
        if pt in ["CIDR_IP", "CIDR_IPV6", "SECURITY_GROUP", "PREFIX_LIST"] and not peer_value:
            logger.warning(f"SG rule type '{pt}' needs peer_value."); return None
        try:
            if pt == "CIDR_IP": return ec2.Peer.ipv4(str(peer_value))
            if pt == "CIDR_IPV6": return ec2.Peer.ipv6(str(peer_value))
            if pt == "SECURITY_GROUP": return ec2.Peer.security_group_id(str(peer_value))
            if pt == "PREFIX_LIST": return ec2.Peer.prefix_list_id(str(peer_value))
            if pt == "ANY_IPV4": return ec2.Peer.any_ipv4()
            if pt == "ANY_IPV6": return ec2.Peer.any_ipv6()
            if pt == "SELF": return ec2.Peer.myself()
        except Exception as e:
            logger.error(f"Error creating peer for type {pt} with value {peer_value}: {e}")
            return None
        logger.warning(f"Unsupported SG peer_type: {peer_type_str}"); return None

    def _parse_sg_connection(self, protocol_str: typing.Optional[str], port: typing.Optional[typing.Any]=None, from_port: typing.Optional[typing.Any]=None, to_port: typing.Optional[typing.Any]=None) -> typing.Optional[ec2.Port]:
        if not protocol_str:
            logger.info("No protocol specified for SG rule, defaulting to all_traffic.")
            return ec2.Port.all_traffic()

        proto = protocol_str.strip().upper()
        try:
            p_int = int(port) if port is not None and str(port).isdigit() else None
            fp_int = int(from_port) if from_port is not None and str(from_port).isdigit() else None
            tp_int = int(to_port) if to_port is not None and str(to_port).isdigit() else None

            if proto == "TCP":
                if p_int is not None: return ec2.Port.tcp(p_int)
                if fp_int is not None and tp_int is not None: return ec2.Port.tcp_range(fp_int, tp_int)
                logger.warning(f"TCP protocol specified but port/range is invalid: p={port}, fp={from_port}, tp={to_port}")
            elif proto == "UDP":
                if p_int is not None: return ec2.Port.udp(p_int)
                if fp_int is not None and tp_int is not None: return ec2.Port.udp_range(fp_int, tp_int)
                logger.warning(f"UDP protocol specified but port/range is invalid: p={port}, fp={from_port}, tp={to_port}")
            elif proto == "ICMP":
                if fp_int is not None and tp_int is not None: return ec2.Port.icmp_type_and_code(fp_int, tp_int)
                return ec2.Port.all_icmp()
            elif proto == "-1" or proto == "ALL":
                return ec2.Port.all_traffic()
            else:
                if proto.isdigit():
                    protocol_number_str = proto
                    if p_int is None and fp_int is None and tp_int is None:
                            return ec2.Port(
                                protocol=protocol_number_str,
                                string_representation=f"protocol {protocol_number_str}",
                                from_port=0 if (p_int is None and fp_int is None) else (p_int if p_int is not None else fp_int),
                                to_port=65535 if (p_int is None and tp_int is None) else (p_int if p_int is not None else tp_int)
                            )
                    logger.warning(f"Specific ports for arbitrary protocol number {protocol_number_str} with ec2.Port is complex. Review rule: {protocol_str, port, from_port, to_port}")
                    return None
                else:
                    logger.warning(f"Unrecognized protocol: {protocol_str}. Rule: {protocol_str, port, from_port, to_port}")
        except ValueError as ve:
            logger.error(f"Could not parse SG connection protocol={protocol_str}, p={port}, fp={from_port}, tp={to_port}: {ve}", exc_info=True)
            return None
        logger.warning(f"Could not form a valid SG connection from: protocol={protocol_str}, p={port}, fp={from_port}, tp={to_port}")
        return None

    def _add_other_specific_cfn_properties(self, cfn_props: dict):
        tenancy_val = self.config.get("tenancy");
        if tenancy_val and isinstance(tenancy_val, str) and tenancy_val.lower() != "default": cfn_props["tenancy"] = tenancy_val.lower()

        cpu_opts_conf = self.config.get("cpu_options", {});
        if isinstance(cpu_opts_conf, dict) and cpu_opts_conf.get("enabled", False):
            payload = {k:v for k,v in {"core_count":cpu_opts_conf.get("core_count"), "threads_per_core":cpu_opts_conf.get("threads_per_core")}.items() if v is not None}
            if payload: cfn_props["cpu_options"] = ec2.CfnInstance.CpuOptionsProperty(**payload)

        credit_spec_conf = self.config.get("credit_specification", {});
        if isinstance(credit_spec_conf, dict) and credit_spec_conf.get("enabled", False) and credit_spec_conf.get("cpu_credits"):
            cfn_props["credit_specification"] = ec2.CfnInstance.CreditSpecificationProperty(cpu_credits=str(credit_spec_conf["cpu_credits"]).lower())

        hibernation_opts_conf = self.config.get("hibernation_options", {})
        if isinstance(hibernation_opts_conf, dict) and hibernation_opts_conf.get("enabled", False):
            cfn_props["hibernation_options"] = ec2.CfnInstance.HibernationOptionsProperty(configured=True)

        enclave_opts_conf = self.config.get("enclave_options", {})
        if isinstance(enclave_opts_conf, dict) and enclave_opts_conf.get("enabled", False):
            cfn_props["enclave_options"] = ec2.CfnInstance.EnclaveOptionsProperty(enabled=True)

        placement_conf = self.config.get("placement_config", {})
        if isinstance(placement_conf, dict) and placement_conf.get("enabled", False) and placement_conf.get("group_name"):
            cfn_props["placement_group_name"] = placement_conf["group_name"]

        cap_res_conf = self.config.get("capacity_reservation_target", {});
        if isinstance(cap_res_conf, dict) and cap_res_conf.get("enabled", False):
            target_payload_dict = {}
            if cap_res_conf.get("capacity_reservation_id"):
                target_payload_dict["capacity_reservation_id"] = cap_res_conf.get("capacity_reservation_id")
            if cap_res_conf.get("capacity_reservation_resource_group_arn"):
                target_payload_dict["capacity_reservation_resource_group_arn"] = cap_res_conf.get("capacity_reservation_resource_group_arn")

            if target_payload_dict:
                spec_payload_dict:typing.Dict[str,typing.Any] = {
                    "capacity_reservation_target": ec2.CfnInstance.CapacityReservationTargetProperty(**target_payload_dict)
                }
                pref = cap_res_conf.get("preference");
                if pref and isinstance(pref, str) and pref.lower() in ["open", "none"]:
                    spec_payload_dict["capacity_reservation_preference"] = pref.lower()

                cfn_props["capacity_reservation_specification"] = ec2.CfnInstance.CapacityReservationSpecificationProperty(**spec_payload_dict)


    def _prepare_metadata_options_for_override(self) -> typing.Optional[dict]:
        md_opts_conf = self.config.get("metadata_options", {})
        if not isinstance(md_opts_conf, dict) or not md_opts_conf.get("enabled", True if md_opts_conf else False):
            return None

        cfn_pascal_case_props: typing.Dict[str, typing.Any] = {}

        http_tokens = md_opts_conf.get("http_tokens")
        if http_tokens is not None:
            cfn_pascal_case_props["HttpTokens"] = str(http_tokens)

        http_endpoint = md_opts_conf.get("http_endpoint")
        if http_endpoint is not None:
            cfn_pascal_case_props["HttpEndpoint"] = "enabled" if http_endpoint else "disabled"

        http_put_hop_limit = md_opts_conf.get("http_put_response_hop_limit")
        if http_put_hop_limit is not None:
            try:
                cfn_pascal_case_props["HttpPutResponseHopLimit"] = int(http_put_hop_limit)
            except ValueError:
                logger.warning(f"Invalid http_put_response_hop_limit '{http_put_hop_limit}', skipping.")

        instance_metadata_tags = md_opts_conf.get("instance_metadata_tags")
        if instance_metadata_tags is not None:
            cfn_pascal_case_props["InstanceMetadataTags"] = "enabled" if instance_metadata_tags else "disabled"

        if not cfn_pascal_case_props:
            return None
        return cfn_pascal_case_props

    def _resolve_ami_id_string(self) -> str | None:
        ami_conf = self.config.get("ami_config", {});
        source = str(ami_conf.get("source", "LATEST_AMAZON_LINUX_2023")).upper()
        arch_str = str(ami_conf.get("architecture", "X86_64")).upper()

        ami_arch_linux = ec2.AmazonLinuxCpuType.X86_64
        if arch_str == "ARM_64":
            ami_arch_linux = ec2.AmazonLinuxCpuType.ARM_64
        elif arch_str != "X86_64":
            logger.warning(f"Unsupported architecture '{arch_str}' in ami_config. Defaulting to X86_64 for AMI resolution.")

        if source == "ID" and ami_conf.get("id"): return str(ami_conf["id"])

        img_resolver: ec2.IMachineImage|None=None
        if source == "LATEST_AMAZON_LINUX_2":
            img_resolver = ec2.MachineImage.latest_amazon_linux2(cpu_type=ami_arch_linux)
        elif source == "LATEST_AMAZON_LINUX_2023":
            img_resolver = ec2.MachineImage.latest_amazon_linux2023(cpu_type=ami_arch_linux)
        elif source == "LATEST_WINDOWS_CORE":
            img_resolver = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2022_ENGLISH_CORE_BASE)
        elif source == "LATEST_WINDOWS_FULL":
            img_resolver = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2022_ENGLISH_FULL_BASE)
        elif source == "LOOKUP" and ami_conf.get("lookup_filters"):
            filters_data = ami_conf.get("lookup_filters",{});
            img_resolver = ec2.MachineImage.lookup(
                name=filters_data.get("name"),
                owners=filters_data.get("owners"),
                filters=filters_data.get("filters")
            )
        else:
            logger.warning(f"AMI source '{source}' unhandled or misconfigured. Defaulting to LATEST_AMAZON_LINUX_2023 ({arch_str}).");
            img_resolver = ec2.MachineImage.latest_amazon_linux2023(cpu_type=ami_arch_linux)

        if img_resolver:
            try:
                return img_resolver.get_image(self).image_id;
            except Exception as e:
                logger.error(f"Failed AMI resolve for source '{source}', architecture '{arch_str}': {e}", exc_info=True);
                return None
        return None

    def _resolve_iam_instance_profile_name(self, instance_name_tag_value:str) -> str | None:
        profile_conf = self.config.get("iam_instance_profile", {});
        if not isinstance(profile_conf, dict) or not profile_conf.get("enabled", True if profile_conf else False):
            logger.info(f"IAM instance profile disabled for '{instance_name_tag_value}'.")
            return None

        if profile_conf.get("existing_profile_name"):
            logger.info(f"Using existing IAM profile name: {profile_conf['existing_profile_name']}")
            return str(profile_conf["existing_profile_name"])
        if profile_conf.get("existing_profile_arn"):
            logger.info(f"Using existing IAM profile ARN: {profile_conf['existing_profile_arn']}")
            # Extract name from ARN if it's an ARN, as CfnInstance expects name or ARN
            arn_parts = str(profile_conf["existing_profile_arn"]).split(':')
            if len(arn_parts) > 5 and arn_parts[5].startswith('instance-profile/'):
                return arn_parts[5].split('/')[-1]
            return str(profile_conf["existing_profile_arn"])


        if profile_conf.get("create_new", True):
            role_name_cfg = profile_conf.get("role_name", f"{instance_name_tag_value}-Role");
            profile_name_cfg = profile_conf.get("profile_name", f"{instance_name_tag_value}-Profile")
            role_cdk_id = construct_id_to_cdk_id_part(f"RoleFor{instance_name_tag_value}");
            profile_cdk_id = construct_id_to_cdk_id_part(f"ProfileFor{instance_name_tag_value}")

            self.created_instance_role = iam.Role(self, role_cdk_id, # Store the L2 role object
                                                  assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                                                  role_name=role_name_cfg,
                                                  description=f"IAM Role for {instance_name_tag_value}")

            if profile_conf.get("ssm_session_permissions", False):
                logger.info(f"Adding AmazonSSMManagedInstanceCore to role '{role_name_cfg}' due to ssm_session_permissions=True.")
                self.created_instance_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))

            # NEW: Add CodeDeploy agent permissions to the EC2 instance's role
            if profile_conf.get("enable_codedeploy_agent_permissions", False):
                logger.info(f"Adding AmazonEC2RoleforAWSCodeDeploy to role '{role_name_cfg}' for CodeDeploy agent.")
                self.created_instance_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2RoleforAWSCodeDeploy"))

            for i, arn in enumerate(profile_conf.get("managed_policy_arns", [])):
                if not arn or not isinstance(arn, str):
                    logger.warning(f"Skipping invalid managed policy ARN: {arn}")
                    continue
                if "AmazonSSMManagedInstanceCore" in arn and profile_conf.get("ssm_session_permissions", False):
                    logger.info(f"Skipping managed policy '{arn}' as it's already added by ssm_session_permissions.")
                    continue

                mp_name_part = arn.split('/')[-1].replace('-', '').replace('_', '').capitalize()[:20];
                unique_mp_id = f"ManagedPol{mp_name_part}{i}";
                self.created_instance_role.add_managed_policy(iam.ManagedPolicy.from_managed_policy_arn(self, unique_mp_id, arn))

            custom_policies = profile_conf.get("custom_policy_statements", [])
            if custom_policies and isinstance(custom_policies, list):
                policy_doc = iam.PolicyDocument();
                for i, stmt_dict in enumerate(custom_policies):
                    if not isinstance(stmt_dict, dict):
                        logger.warning(f"Skipping invalid custom policy statement (not a dict): {stmt_dict}"); continue
                    effect = iam.Effect.ALLOW if str(stmt_dict.get("effect", "Allow")).upper() == "ALLOW" else iam.Effect.DENY;
                    ps = iam.PolicyStatement(sid=stmt_dict.get("sid", f"CustomStmt{i}"),
                                             effect=effect,
                                             actions=stmt_dict.get("actions"),
                                             resources=stmt_dict.get("resources"),
                                             conditions=stmt_dict.get("conditions"));
                    policy_doc.add_statements(ps)
                if not policy_doc.is_empty:
                    self.created_instance_role.attach_inline_policy(iam.Policy(self, f"{role_cdk_id}CustomPolicy", document=policy_doc))

            # Create the CfnInstanceProfile and return its name
            cfn_profile = iam.CfnInstanceProfile(self, profile_cdk_id,
                                                 roles=[self.created_instance_role.role_name], # Use the role name
                                                 instance_profile_name=profile_name_cfg)
            logger.info(f"Created new IAM Role '{self.created_instance_role.role_name}' and Instance Profile '{cfn_profile.instance_profile_name}'. Profile Ref: {cfn_profile.ref}")
            return cfn_profile.instance_profile_name # Return the name of the instance profile

        logger.warning(f"IAM profile configuration for '{instance_name_tag_value}' is ambiguous. Not creating or assigning profile.")
        return None

    def _configure_block_devices_for_cfn_instance(self) -> typing.Optional[typing.List[ec2.CfnInstance.BlockDeviceMappingProperty]]:
        storage_conf = self.config.get("storage_config", {});
        if not isinstance(storage_conf, dict) or not storage_conf.get("enabled", True if storage_conf else False):
            logger.info("Storage configuration is disabled. No block devices will be explicitly configured beyond AMI defaults.")
            return None

        cfn_devices: typing.List[ec2.CfnInstance.BlockDeviceMappingProperty] = [];
        root_vol_conf = storage_conf.get("root_volume",{})

        if isinstance(root_vol_conf, dict) and root_vol_conf:
            ebs_props=self._parse_ebs_props_for_cfn_instance(root_vol_conf,True)
            if ebs_props:
                root_dev_name=root_vol_conf.get("device_name_override", self._get_default_root_device_name());
                cfn_devices.append(ec2.CfnInstance.BlockDeviceMappingProperty(device_name=root_dev_name,ebs=ebs_props))
                logger.info(f"Configured root volume '{root_dev_name}' with props: {ebs_props}")

        additional_ebs_devices = storage_conf.get("ebs_block_devices", [])
        if isinstance(additional_ebs_devices, list):
            for i, bd_conf in enumerate(additional_ebs_devices):
                if not isinstance(bd_conf, dict) or not bd_conf.get("enabled",True):
                    logger.info(f"Skipping EBS block device at index {i} as it's disabled or not a dict.")
                    continue

                dev_name=bd_conf.get("device_name");
                if not dev_name or not isinstance(dev_name, str):
                    logger.warning(f"Skipping EBS block device at index {i}: missing or invalid 'device_name'. Config: {bd_conf}");
                    continue

                ebs_props=self._parse_ebs_props_for_cfn_instance(bd_conf,False)
                if ebs_props:
                    cfn_devices.append(ec2.CfnInstance.BlockDeviceMappingProperty(device_name=dev_name,ebs=ebs_props))
                    logger.info(f"Configured additional EBS volume '{dev_name}' with props: {ebs_props}")
                else:
                    logger.warning(f"Could not parse EBS properties for device '{dev_name}' at index {i}. Config: {bd_conf}")

        return cfn_devices if cfn_devices else None

    def _get_default_root_device_name(self) -> str:
        ami_conf = self.config.get("ami_config", {});
        source_lower = str(ami_conf.get("source", "")).lower()
        os_hint_upper = str(ami_conf.get("os_type_hint", "")).upper()

        if "windows" in source_lower or "WINDOWS" in os_hint_upper:
            return "/dev/sda1"
        return "/dev/xvda"

    def _parse_ebs_props_for_cfn_instance(self, vol_config: dict, is_root:bool) -> typing.Optional[ec2.CfnInstance.EbsProperty]:
        props: typing.Dict[str, typing.Any] = {};

        size_gb = vol_config.get("size_gb")
        if size_gb is not None:
            try: props["volume_size"] = int(size_gb)
            except ValueError: logger.warning(f"Invalid size_gb '{size_gb}', skipping size for volume. Config: {vol_config}");

        vol_type_str_cfg = vol_config.get("type")
        props["volume_type"] = "gp3"
        valid_ebs_types = ["standard", "gp2", "gp3", "io1", "io2", "sc1", "st1"]
        if vol_type_str_cfg and isinstance(vol_type_str_cfg, str) and vol_type_str_cfg.lower() in valid_ebs_types:
            props["volume_type"] = vol_type_str_cfg.lower()
        elif vol_type_str_cfg:
            logger.warning(f"Invalid EBS type '{vol_type_str_cfg}', using default 'gp3'. Config: {vol_config}")

        iops_val = vol_config.get("iops")
        if iops_val is not None:
            try: props["iops"] = int(iops_val)
            except ValueError: logger.warning(f"Invalid iops '{iops_val}', skipping iops. Config: {vol_config}")

        if props["volume_type"] in ["io1", "io2"] and "iops" not in props:
            logger.error(f"EBS type {props['volume_type']} requires 'iops'. Volume config: {vol_config}"); return None

        throughput_mbps_val = vol_config.get("throughput_mbps")
        if props["volume_type"] == "gp3" and throughput_mbps_val is not None:
            try: props["throughput"] = int(throughput_mbps_val)
            except ValueError: logger.warning(f"Invalid throughput_mbps '{throughput_mbps_val}', skipping throughput. Config: {vol_config}")
        elif props["volume_type"] != "gp3" and throughput_mbps_val is not None:
            logger.warning(f"Throughput is only applicable for gp3 volumes, but current type is {props['volume_type']}. Ignoring throughput. Config: {vol_config}")

        props["encrypted"] = vol_config.get("encrypted", True if is_root else True)

        kms_key_id = vol_config.get("kms_key_id")
        if kms_key_id and isinstance(kms_key_id, str):
            if props["encrypted"]:
                props["kms_key_id"] = kms_key_id
            else:
                logger.warning(f"kms_key_id '{kms_key_id}' provided but encrypted is False. Ignoring KMS key. Config: {vol_config}")

        props["delete_on_termination"] = vol_config.get("delete_on_termination", True if is_root else False)

        snapshot_id_val = vol_config.get("snapshot_id")
        if snapshot_id_val and isinstance(snapshot_id_val, str):
            props["snapshot_id"] = snapshot_id_val
            if "volume_size" in props:
                logger.info(f"Using snapshot_id '{snapshot_id_val}'. Specified size_gb '{props.get('volume_size')}' will be used if larger than snapshot.")

        if "volume_size" not in props and "snapshot_id" not in props:
            logger.error(f"EBS configuration requires 'size_gb' or 'snapshot_id'. Config: {vol_config}"); return None

        final_ebs_props = {k:v for k,v in props.items() if v is not None}
        return ec2.CfnInstance.EbsProperty(**final_ebs_props) if final_ebs_props else None

    def _setup_outputs_for_cfn(self, instance_name_tag: str, key_name_str: str | None, iam_profile_ref: str | None):
        if self.instance_id_token:
            CfnOutput(self, f"InstanceIdOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=self.instance_id_token, description=f"Instance ID for {instance_name_tag}")

        if self.cfn_ec2_instance:
            primary_nic_config = None
            network_cfg = self.config.get("network_config", {})
            adv_nics = network_cfg.get("network_interfaces")
            if adv_nics and isinstance(adv_nics, list):
                for nic in adv_nics:
                    if isinstance(nic, dict) and str(nic.get("device_index")) == "0":
                        primary_nic_config = nic
                        break
            else:
                primary_nic_config = network_cfg

            if primary_nic_config and primary_nic_config.get("associate_public_ip_address"):
                public_ip_attr_token = self.cfn_ec2_instance.attr_public_ip
                CfnOutput(self, f"InstancePublicIpOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=public_ip_attr_token, description=f"Public IP for {instance_name_tag} (if available)")

            CfnOutput(self, f"InstancePrivateIpOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=self.cfn_ec2_instance.attr_private_ip, description=f"Private IP for {instance_name_tag}")

        if key_name_str:
            CfnOutput(self, f"InstanceKeyNameOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=key_name_str, description=f"Key pair name for {instance_name_tag}")

        if iam_profile_ref:
            CfnOutput(self, f"InstanceProfileRefOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=iam_profile_ref, description=f"IAM Instance Profile Name/Ref for {instance_name_tag}")

        if self.created_security_group_object:
            CfnOutput(self, f"CreatedSecurityGroupIdOutput{construct_id_to_cdk_id_part(instance_name_tag)}", value=self.created_security_group_object.security_group_id, description=f"ID of newly created Security Group for {instance_name_tag}")

