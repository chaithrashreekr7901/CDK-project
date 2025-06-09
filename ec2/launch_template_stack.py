import logging
import typing
import re
from aws_cdk import (
    NestedStack,
    Duration,
    aws_ec2 as ec2,
    aws_iam as iam,
    Tags,
    CfnOutput
)
from constructs import Construct

# Import TYPE_CHECKING for type hinting only
from typing import TYPE_CHECKING

# Conditional import for type checking purposes only
if TYPE_CHECKING:
    from aws_cdk import aws_iam as iam_type_checking # Use an alias to prevent runtime issues with isinstance(..., iam.IRole)


logger = logging.getLogger(__name__)

# Utility function
def convert_to_l2_instance_type(instance_type_str: str) -> ec2.InstanceType:
    """Converts a string instance type (e.g., 't3.micro') to an ec2.InstanceType object."""
    try:
        # The L2 InstanceType constructor can often parse standard strings directly
        return ec2.InstanceType(instance_type_str)
    except Exception as e:
        raise ValueError(f"Failed to parse instance type '{instance_type_str}': {e}")

class LaunchTemplateStack(NestedStack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 lt_config: typing.Dict,
                 vpc: typing.Optional[ec2.IVpc] = None,
                 created_security_groups_map: typing.Dict[str, ec2.ISecurityGroup],
                 created_iam_roles_map: typing.Dict[str, 'iam_type_checking.IRole'], # Use the alias for type hint
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:

        # Filter kwargs to only pass those valid for NestedStack
        nested_stack_valid_kwargs = {k: v for k, v in kwargs.items() if k in ['env', 'stack_name', 'synthesizer', 'termination_protection']}
        super().__init__(scope, construct_id, description=description, **nested_stack_valid_kwargs)

        self.config = lt_config # Store the full config for internal use
        self.vpc = vpc
        self.created_security_groups_map = created_security_groups_map
        # IMPORTANT: When passing created_iam_roles_map, ensure it truly contains iam.Role objects, not just ARNs.
        # This self.created_iam_roles_map is still typed as Dict[str, iam.IRole], which is good for type checkers.
        self.created_iam_roles_map = created_iam_roles_map 
        
        lt_name = self.config.get("launch_template_name", construct_id) # This ensures lt_name is always a string
        logger.info(f"LaunchTemplateStack '{construct_id}': Initializing for LT '{lt_name}'.")

        template_data = self.config.get("template_data", {})
        
        # --- AMI Resolution ---
        machine_image = None
        ami_config = template_data.get("ami_config")
        if ami_config and ami_config.get("source"):
            source = ami_config["source"]
            if source == "ID":
                ami_id = ami_config.get("id")
                if not ami_id:
                    raise ValueError(f"AMI ID missing for Launch Template '{lt_name}' when source is 'ID'.")
                machine_image = ec2.MachineImage.generic_linux({"us-east-1": ami_id})
                logger.info(f"LT '{lt_name}': Using specific AMI ID '{ami_id}'.")
            elif source == "LATEST_AMAZON_LINUX_2023":
                arch = ec2.InstanceArchitecture.ARM_64 if ami_config.get("architecture") == "arm_64" else ec2.InstanceArchitecture.X86_64
                machine_image = ec2.MachineImage.latest_amazon_linux2023(cpu_type=arch)
                logger.info(f"LT '{lt_name}': Using latest Amazon Linux 2023 ({arch.name}).")
            elif source == "LATEST_AMAZON_LINUX_2": # Assuming this maps to AL2
                arch = ec2.InstanceArchitecture.ARM_64 if ami_config.get("architecture") == "arm_64" else ec2.InstanceArchitecture.X86_64
                machine_image = ec2.MachineImage.latest_amazon_linux2(cpu_type=arch)
                logger.info(f"LT '{lt_name}': Using latest Amazon Linux 2 ({arch.name}).")
            elif source == "LOOKUP" and ami_config.get("lookup_filters"):
                if not self.vpc:
                    raise ValueError(f"VPC must be provided to Launch Template '{lt_name}' for AMI 'LOOKUP' source.")
                lookup_filters = ami_config["lookup_filters"]
                machine_image = ec2.MachineImage.lookup(
                    self, f"{construct_id}AmiLookup",
                    name=lookup_filters.get("name"),
                    owners=lookup_filters.get("owners"),
                    filters=lookup_filters.get("filters")
                )
                logger.info(f"LT '{lt_name}': Looking up AMI with filters: {lookup_filters.get('name')}.")
            else:
                logger.warning(f"LT '{lt_name}': Unsupported AMI source '{source}'. Machine image will not be set.")
        elif template_data.get("image_id"):
            machine_image = ec2.MachineImage.generic_linux({"us-east-1": template_data["image_id"]})
            logger.info(f"LT '{lt_name}': Using direct image_id '{template_data['image_id']}'.")
        else:
            logger.warning(f"LT '{lt_name}': No AMI configuration or image_id provided. Launch Template will require AMI at launch.")
            machine_image = None

        # --- Instance Type ---
        instance_type_obj = None
        instance_type_str = template_data.get("instance_type")
        if instance_type_str:
            try:
                instance_type_obj = convert_to_l2_instance_type(instance_type_str)
            except ValueError as e:
                logger.error(f"LT '{lt_name}': Invalid instance type '{instance_type_str}': {e}. Instance type will not be set.")
        else:
            logger.info(f"LT '{lt_name}': No instance_type provided. Launch Template will require instance type at launch.")

        # --- User Data ---
        user_data_obj = None
        user_data_config = template_data.get("user_data", {})
        if user_data_config.get("enabled", False):
            user_data_type = user_data_config.get("type", "SHELL_SCRIPT")
            if user_data_type == "SHELL_SCRIPT":
                if user_data_config.get("user_data_script_path"):
                    with open(user_data_config["user_data_script_path"], 'r') as f:
                        script_content = f.read()
                    user_data_obj = ec2.UserData.for_linux()
                    user_data_obj.add_commands(script_content)
                    logger.info(f"LT '{lt_name}': Using UserData from script path '{user_data_config['user_data_script_path']}'.")
                elif user_data_config.get("user_data_code"):
                    user_data_obj = ec2.UserData.for_linux()
                    user_data_obj.add_commands(user_data_config["user_data_code"])
                    logger.info(f"LT '{lt_name}': Using inline UserData (shell script).")
                else:
                    logger.warning(f"LT '{lt_name}': UserData enabled but no script_path or code provided for SHELL_SCRIPT type.")
            elif user_data_type == "RAW_TEXT" and user_data_config.get("user_data_code"):
                user_data_obj = ec2.UserData.custom(user_data_config["user_data_code"])
                logger.info(f"LT '{lt_name}': Using raw UserData (raw text).")
            elif user_data_type == "BASE64_ENCODED" and user_data_config.get("user_data_b64"):
                user_data_obj = ec2.UserData.custom(user_data_config["user_data_b64"])
                logger.info(f"LT '{lt_name}': Using base64 encoded UserData.")
            else:
                logger.warning(f"LT '{lt_name}': Unsupported UserData type '{user_data_type}' or missing content.")
        else:
            logger.info(f"LT '{lt_name}': UserData is disabled or not configured.")

        # --- IAM Instance Profile ---
        role_for_instance_profile: typing.Optional[iam.IRole] = None
        instance_profile_config = template_data.get("iam_instance_profile", {})
        if instance_profile_config.get("enabled", False):
            if instance_profile_config.get("launchtemplate_service_role_ref_id"):
                role_ref_id = instance_profile_config["launchtemplate_service_role_ref_id"]
                resolved_role_from_map = self.created_iam_roles_map.get(role_ref_id)
                
                # Check for the concrete `iam.Role` type or ensure it's an L2 construct that implements IRole.
                # Avoid `isinstance(..., iam.IRole)` directly due to runtime_checkable limitation.
                if resolved_role_from_map:
                    # In CDK, when you get an object from a map that was populated by `iam.Role(self, ...)`
                    # it will be an instance of `iam.Role`, which is a concrete class.
                    # We can check for `iam.Role` (the concrete class) directly.
                    if isinstance(resolved_role_from_map, iam.Role): 
                        role_for_instance_profile = resolved_role_from_map
                        logger.info(f"LT '{lt_name}': Using directly resolved IAM role object '{role_ref_id}'.")
                    else:
                        # Fallback for cases where it might be a token but not an actual iam.Role object
                        # (e.g., if created_iam_roles_map somehow gets populated with just ARNs).
                        # This should ideally not happen if IamRolesGroupNestedStack always puts iam.Role objects.
                        # If it's a string, it means it's a token and `from_role_arn` would be attempted,
                        # which is what caused the original circular dependency.
                        # Logging a warning and not setting the role will reveal if the map is not correctly populated.
                        logger.warning(
                            f"LT '{lt_name}': IAM role ref ID '{role_ref_id}' in created_iam_roles_map "
                            f"is not a concrete iam.Role object. Instance profile will not be attached via role reference. "
                            f"Actual type: {type(resolved_role_from_map)}. Expected `aws_iam.Role`."
                        )
                else:
                    logger.warning(f"LT '{lt_name}': IAM role ref ID '{role_ref_id}' not found in created_iam_roles_map. Instance profile will not be attached via role reference.")
            elif instance_profile_config.get("iam_instance_profile_arn"):
                try:
                    # This path is for explicitly provided ARNs (external to the system, or already resolved tokens).
                    # It's less likely to cause a cycle if the ARN is truly external or a resolved string.
                    role_for_instance_profile = iam.Role.from_role_arn(
                        self,
                        f"{construct_id}DirectProfileRoleImport",
                        instance_profile_config["iam_instance_profile_arn"],
                        mutable=False
                    )
                    logger.info(f"LT '{lt_name}': Using direct IAM instance profile ARN (as role) '{instance_profile_config['iam_instance_profile_arn']}'.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Failed to import direct IAM instance profile ARN '{instance_profile_config['iam_instance_profile_arn']}': {e}", exc_info=True)
                    raise ValueError(f"Failed to import direct IAM instance profile ARN for LT '{lt_name}'")
            elif instance_profile_config.get("iam_instance_profile_name"):
                 logger.warning(f"LT '{lt_name}': Direct IAM instance profile name lookup not supported for L2 construct directly. Use ARN or ref_id. Instance profile will not be attached.")
            else:
                logger.warning(f"LT '{lt_name}': IAM instance profile enabled but no ARN, name, or ref_id provided. Instance profile will not be attached.")
        else:
            logger.info(f"LT '{lt_name}': IAM instance profile is disabled or not configured.")

        # --- Network Interfaces / Security Groups ---
        network_interfaces_props: typing.Optional[list[ec2.CfnLaunchTemplate.NetworkInterfaceProperty]] = None
        self.managed_security_group: typing.Optional[ec2.ISecurityGroup] = None
        
        # Collect Security Group IDs for the Launch Template (top-level association via L1 override)
        security_group_ids_for_lt_l1: typing.List[str] = []

        if template_data.get("network_interfaces"):
            ni_list_or_dict = template_data["network_interfaces"]
            interfaces_to_process = []
            if isinstance(ni_list_or_dict, dict) and ni_list_or_dict.get("enabled", True):
                interfaces_to_process = ni_list_or_dict.get("interfaces", [])
            elif isinstance(ni_list_or_dict, list):
                interfaces_to_process = ni_list_or_dict

            if interfaces_to_process:
                network_interfaces_props = []
                for i, ni_conf in enumerate(interfaces_to_process):
                    resolved_sg_ids_for_current_ni = []
                    
                    if ni_conf.get("groups") and isinstance(ni_conf["groups"], list):
                         resolved_sg_ids_for_current_ni = [str(sg_id) for sg_id in ni_conf["groups"]]
                         logger.info(f"LT {lt_name}: NI {ni_conf.get('device_index', i)} using direct SG IDs: {resolved_sg_ids_for_current_ni}")
                    elif ni_conf.get("groups_ref_ids") and isinstance(ni_conf["groups_ref_ids"], list):
                        for ref_id in ni_conf["groups_ref_ids"]:
                            if ref_id in self.created_security_groups_map:
                                resolved_sg_ids_for_current_ni.append(self.created_security_groups_map[ref_id].security_group_id)
                            else:
                                logger.warning(f"LT '{lt_name}': NI {ni_conf.get('device_index', i)} could not resolve SG ref_id '{ref_id}'. Assuming it's a physical ID or will be handled by default.")
                                resolved_sg_ids_for_current_ni.append(ref_id)
                        logger.info(f"LT {lt_name}: NI {ni_conf.get('device_index', i)} using resolved SG IDs from refs: {resolved_sg_ids_for_current_ni}")
                    else:
                        logger.warning(f"LT {lt_name}: No security groups defined for NI {ni_conf.get('device_index', i)}. Might use default VPC SG or no SG.")

                    # Populate security_group_ids_for_lt_l1 for the top-level LaunchTemplateData.SecurityGroupIds
                    if resolved_sg_ids_for_current_ni:
                        for sg_id_str in resolved_sg_ids_for_current_ni:
                            if sg_id_str not in security_group_ids_for_lt_l1:
                                security_group_ids_for_lt_l1.append(sg_id_str)

                            if i == 0: # For the primary interface, try to set managed_security_group
                                found_sg_obj_in_map = None
                                for sg_logical_id_in_map, sg_obj_in_map in self.created_security_groups_map.items():
                                    if sg_obj_in_map.security_group_id == sg_id_str:
                                        found_sg_obj_in_map = sg_obj_in_map
                                        break
                                if found_sg_obj_in_map:
                                    self.managed_security_group = found_sg_obj_in_map
                                    logger.info(f"LT {lt_name}: Set self.managed_security_group to '{self.managed_security_group.security_group_id}'.")
                                elif not self.managed_security_group:
                                    try:
                                        self.managed_security_group = ec2.SecurityGroup.from_security_group_id(self, f"{construct_id}PrimarySgImport{sg_id_str[:8]}", sg_id_str)
                                        logger.info(f"LT {lt_name}: Imported primary security group '{sg_id_str}' for managed_security_group.")
                                    except Exception as e:
                                        logger.warning(f"LT {lt_name}: Failed to import primary SG '{sg_id_str}' for managed_security_group: {e}")
                    
                    # Create CfnLaunchTemplate.NetworkInterfaceProperty for L1 override
                    network_interfaces_props.append(ec2.CfnLaunchTemplate.NetworkInterfaceProperty(
                        description=ni_conf.get("description"),
                        private_ip_address=ni_conf.get("private_ip_address"),
                        private_ip_addresses=[ec2.CfnLaunchTemplate.PrivateIpAddressSpecificationProperty(
                            private_ip_address=ip_spec.get("private_ip_address"),
                            primary=ip_spec.get("primary")
                        ) for ip_spec in ni_conf.get("private_ip_addresses", [])] if ni_conf.get("private_ip_addresses") else None,
                        secondary_private_ip_address_count=ni_conf.get("secondary_private_ip_address_count"),
                        ipv6_address_count=ni_conf.get("ipv6_address_count"),
                        ipv6_addresses=[ec2.CfnLaunchTemplate.Ipv6AddressProperty(ipv6_address=ipv6_spec.get("ipv6_address"))
                                        for ipv6_spec in ni_conf.get("ipv6_addresses", [])] if ni_conf.get("ipv6_addresses") else None,
                        network_interface_id=ni_conf.get("network_interface_id"),
                        network_card_index=ni_conf.get("network_card_index")
                    ))
            else:
                logger.info(f"LT '{lt_name}': No network interfaces defined in config.")
        else:
            logger.info(f"LT '{lt_name}': Network interfaces section is not provided. Default VPC Security Group(s) will apply if not specified elsewhere.")


        # --- Block Device Mappings ---
        block_device_mappings_props: typing.Optional[list[ec2.CfnLaunchTemplate.BlockDeviceMappingProperty]] = None
        block_device_mappings_config = template_data.get("block_device_mappings")
        if block_device_mappings_config and block_device_mappings_config.get("enabled", False):
            mappings = block_device_mappings_config.get("mappings", [])
            if mappings:
                block_device_mappings_props = []
                for mapping in mappings:
                    ebs_config = mapping.get("ebs", {})
                    if ebs_config:
                        block_device_mappings_props.append(ec2.CfnLaunchTemplate.BlockDeviceMappingProperty(
                            device_name=mapping.get("device_name"),
                            virtual_name=mapping.get("virtual_name"),
                            ebs=ec2.CfnLaunchTemplate.EbsProperty(
                                volume_size=ebs_config.get("volume_size"),
                                volume_type=ebs_config.get("volume_type"),
                                encrypted=ebs_config.get("encrypted"),
                                delete_on_termination=ebs_config.get("delete_on_termination"),
                                iops=ebs_config.get("iops"),
                                throughput=ebs_config.get("throughput"),
                                snapshot_id=ebs_config.get("snapshot_id"),
                                kms_key_id=ebs_config.get("kms_key_id")
                            )
                        ))
            else:
                logger.warning(f"LT '{lt_name}': Block device mappings enabled but no mappings defined.")
        else:
            logger.info(f"LT '{lt_name}': Block device mappings section is disabled or not provided correctly.")

        # --- Resolve KeyPair ---
        key_pair_obj: typing.Optional[ec2.IKeyPair] = None
        key_name_from_config = template_data.get("key_name")
        if key_name_from_config:
            try:
                key_pair_obj = ec2.KeyPair.from_key_pair_name(self, f"{construct_id}KeyPair", key_name_from_config)
                logger.info(f"LT '{lt_name}': Using existing KeyPair '{key_name_from_config}'.")
            except Exception as e:
                logger.error(f"LT '{lt_name}': Failed to import KeyPair '{key_name_from_config}': {e}. Key pair will not be associated.", exc_info=True)
                key_pair_obj = None
        else:
            logger.info(f"LT '{lt_name}': No key_name provided in config. Key pair will not be associated with Launch Template.")


        # --- Build the Launch Template ---
        self.launch_template = ec2.LaunchTemplate(
            self,
            f"{construct_id}LaunchTemplate",
            launch_template_name=lt_name,
            version_description=self.config.get("version_description", "Managed by CDK Orchestrator"),
            machine_image=machine_image,
            instance_type=instance_type_obj,
            key_pair=key_pair_obj, # Use the KeyPair object
            user_data=user_data_obj,
            role=role_for_instance_profile,
            block_devices=block_device_mappings_props,
            # The 'security_groups' property is intentionally omitted here as it's passed as an L1 override below.
        )

        cfn_lt = self.launch_template.node.default_child # Get the underlying CfnLaunchTemplate
        if isinstance(cfn_lt, ec2.CfnLaunchTemplate):
            # Apply NetworkInterfaces as L1 override only if configured
            if network_interfaces_props:
                cfn_lt.add_property_override("LaunchTemplateData.NetworkInterfaces", network_interfaces_props)
                logger.info(f"LT '{lt_name}': Applied NetworkInterfaces property override via L1 CfnLaunchTemplate.")

            # Apply SecurityGroupIds (from security_group_ids_for_lt_l1) via L1 override
            if security_group_ids_for_lt_l1:
                cfn_lt.add_property_override("LaunchTemplateData.SecurityGroupIds", security_group_ids_for_lt_l1)
                logger.info(f"LT '{lt_name}': Applied SecurityGroupIds property override via L1 CfnLaunchTemplate: {security_group_ids_for_lt_l1}.")
            else:
                logger.info(f"LT '{lt_name}': No security group IDs collected for top-level LaunchTemplateData.SecurityGroupIds.")


            # Handle cpu_options via L1 override
            cpu_options_config = template_data.get("cpu_options", {})
            if cpu_options_config.get("enabled", False):
                try:
                    cfn_lt.add_property_override("LaunchTemplateData.CpuOptions", {
                        "CoreCount": cpu_options_config.get("core_count"),
                        "ThreadsPerCore": cpu_options_config.get("threads_per_core")
                    })
                    logger.info(f"LT '{lt_name}': Applied CpuOptions property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying CpuOptions override: {e}", exc_info=True)

            # Handle credit_specification via L1 override
            credit_spec_config = template_data.get("credit_specification", {})
            if credit_spec_config.get("enabled", False) and credit_spec_config.get("cpu_credits"):
                try:
                    cfn_lt.add_property_override("LaunchTemplateData.CreditSpecification", {
                        "CpuCredits": credit_spec_config["cpu_credits"]
                    })
                    logger.info(f"LT '{lt_name}': Applied CreditSpecification property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying CreditSpecification override: {e}", exc_info=True)
            
            # Handle hibernation_options via L1 override
            hibernation_options_config = template_data.get("hibernation_options", {})
            if hibernation_options_config.get("enabled", False):
                try:
                    # Note: Cfn property is typically 'Configured', not 'Enabled'
                    cfn_lt.add_property_override("LaunchTemplateData.HibernationOptions", {
                        "Configured": True
                    })
                    logger.info(f"LT '{lt_name}': Applied HibernationOptions property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying HibernationOptions override: {e}", exc_info=True)

            # Handle ebs_optimized via L1 override
            if template_data.get("ebs_optimized") is not None:
                try:
                    cfn_lt.add_property_override("LaunchTemplateData.EbsOptimized", template_data["ebs_optimized"])
                    logger.info(f"LT '{lt_name}': Applied EbsOptimized property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying EbsOptimized override: {e}", exc_info=True)

            # Handle instance_initiated_shutdown_behavior via L1 override
            if template_data.get("instance_initiated_shutdown_behavior") is not None:
                try:
                    cfn_lt.add_property_override("LaunchTemplateData.InstanceInitiatedShutdownBehavior", template_data["instance_initiated_shutdown_behavior"])
                    logger.info(f"LT '{lt_name}': Applied InstanceInitiatedShutdownBehavior property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying InstanceInitiatedShutdownBehavior override: {e}", exc_info=True)

            # Handle metadata_options via L1 override
            metadata_options_config = template_data.get("metadata_options", {})
            if metadata_options_config.get("enabled", False):
                try:
                    cfn_lt.add_property_override("LaunchTemplateData.MetadataOptions", {
                        "HttpTokens": metadata_options_config.get("http_tokens"),
                        "HttpEndpoint": metadata_options_config.get("http_endpoint"),
                        "HttpPutResponseHopLimit": metadata_options_config.get("http_put_response_hop_limit"),
                        "InstanceMetadataTags": metadata_options_config.get("instance_metadata_tags")
                    })
                    logger.info(f"LT '{lt_name}': Applied MetadataOptions property override via L1 CfnLaunchTemplate.")
                except Exception as e:
                    logger.error(f"LT '{lt_name}': Error applying MetadataOptions override: {e}", exc_info=True)
        else:
            logger.error(f"LT '{lt_name}': Could not get CfnLaunchTemplate to apply L1 property overrides.")


        if self.config.get("tag_specifications"):
            for tag_spec in self.config["tag_specifications"]:
                resource_type = tag_spec.get("resource_type")
                tags_to_apply = tag_spec.get("tags", {})
                propagate = tag_spec.get("propagate_at_launch", True)
                
                for key, value in tags_to_apply.items():
                    Tags.of(self.launch_template).add(key, value)
                    logger.info(f"LT '{lt_name}': Added tag '{key}':'{value}' to Launch Template resource.")

        top_level_tags = self.config.get("tags", {})
        for key, value in top_level_tags.items():
            Tags.of(self).add(key, value)
            logger.info(f"LT '{lt_name}': Added stack tag '{key}':'{value}'.")

        CfnOutput(self, f"{construct_id}NameOutput", value=lt_name)
        CfnOutput(self, f"{construct_id}IdOutput", value=self.launch_template.launch_template_id)