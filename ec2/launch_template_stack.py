# CRMP-PROJECT/cdk_project/ec2/launch_template_stack.py
import logging
import base64
from pathlib import Path
import typing

from aws_cdk import (
    NestedStack,
    Tags,
    CfnTag,
    CfnOutput,
    Aws,
    Fn,
    aws_ec2 as ec2,
)
from constructs import Construct

logger = logging.getLogger(__name__)

def _to_pascal_case_dict(snake_case_dict: typing.Optional[dict]) -> typing.Optional[dict]:
    if not snake_case_dict:
        return None
    pascal_case_dict = {}
    for key, value in snake_case_dict.items():
        if value is not None: 
            parts = key.split('_')
            pascal_key = ''.join(word.capitalize() for word in parts)
            pascal_case_dict[pascal_key] = value
    return pascal_case_dict if pascal_case_dict else None


class LaunchTemplateStack(NestedStack):
    launch_template: ec2.ILaunchTemplate 
    cfn_launch_template: ec2.CfnLaunchTemplate
    managed_security_group: typing.Optional[ec2.ISecurityGroup] = None 

    def __init__(self, scope: Construct, construct_id: str,
                 lt_config: dict, 
                 vpc: typing.Optional[ec2.IVpc] = None, 
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = lt_config
        self.vpc = vpc 
        launch_template_name = self.config.get("launch_template_name")
        if not launch_template_name:
            raise ValueError(f"Launch Template config for '{construct_id}' must have 'launch_template_name'.")

        logger.info(f"LaunchTemplateStack '{construct_id}': Initializing for LT '{launch_template_name}'.")

        template_data_config = self.config.get("template_data", {})
        if not template_data_config:
            logger.warning(f"No 'template_data' provided for LT '{launch_template_name}'.")

        self.managed_security_group = None 
        network_interfaces_data_from_config = template_data_config.get("network_interfaces")
        
        parsed_network_interfaces_cfn, _ = self._parse_network_interfaces_and_get_sg_ids(
            network_interfaces_data_from_config 
        )

        if not self.managed_security_group and parsed_network_interfaces_cfn:
            for ni_prop in parsed_network_interfaces_cfn:
                if hasattr(ni_prop, 'groups') and ni_prop.groups and isinstance(ni_prop.groups, list) and len(ni_prop.groups) > 0:
                    try:
                        sg_import_id = f"{launch_template_name.replace('-', '')[:15]}ImportedSG{ni_prop.groups[0][-8:]}"
                        self.managed_security_group = ec2.SecurityGroup.from_security_group_id(
                            self, sg_import_id, ni_prop.groups[0]
                        )
                        logger.info(f"LT '{launch_template_name}': Set managed_security_group by importing existing SG ID '{ni_prop.groups[0]}' from parsed NI.")
                        break 
                    except Exception as e:
                        logger.warning(f"LT '{launch_template_name}': Failed to import SG '{ni_prop.groups[0]}' for managed_security_group attribute: {e}")
        
        ami_id_str = self._resolve_ami_id(
            template_data_config.get("ami_config"),
            template_data_config.get("image_id")
        )
        # Pass the entire "user_data" block from config to _resolve_user_data
        user_data_b64_str = self._resolve_user_data(template_data_config.get("user_data"))

        iam_instance_profile_payload = self._parse_iam_instance_profile(template_data_config.get("iam_instance_profile", {}))
        block_device_mappings_cfn = self._parse_block_device_mappings(template_data_config.get("block_device_mappings", {}))
        metadata_options_payload = self._parse_metadata_options(template_data_config.get("metadata_options", {}))
        cpu_options_payload = self._parse_cpu_options(template_data_config.get("cpu_options", {}))
        credit_spec_payload = self._parse_credit_specification(template_data_config.get("credit_specification", {}))
        placement_payload = self._parse_placement(template_data_config.get("placement", {}))
        capacity_reservation_payload = self._parse_capacity_reservation(template_data_config.get("capacity_reservation_specification", {}))
        hibernation_options_payload = self._parse_hibernation_options(template_data_config.get("hibernation_options", {}))
        license_specifications_payload = self._parse_license_specifications(template_data_config.get("license_specifications", {}))
        elastic_gpu_payload = self._parse_elastic_gpu(template_data_config.get("elastic_gpu_specifications", {}))
        elastic_inference_payload = self._parse_elastic_inference(template_data_config.get("elastic_inference_accelerators", {}))
        enclave_options_payload = self._parse_enclave_options(template_data_config.get("enclave_options", {}))
        resource_launch_tag_specs = self._parse_tag_specifications_for_template_data(template_data_config.get("tag_specifications", []))
        monitoring_payload = self._parse_monitoring(template_data_config.get("monitoring_detailed"))

        lt_data_args = {
            "image_id": ami_id_str,
            "instance_type": template_data_config.get("instance_type"),
            "key_name": template_data_config.get("key_name"),
            "user_data": user_data_b64_str,
            "network_interfaces": parsed_network_interfaces_cfn if parsed_network_interfaces_cfn else None,
            "iam_instance_profile": iam_instance_profile_payload, 
            "block_device_mappings": block_device_mappings_cfn if block_device_mappings_cfn else None,
            "monitoring": monitoring_payload,
            "disable_api_termination": template_data_config.get("disable_api_termination"),
            "instance_initiated_shutdown_behavior": template_data_config.get("instance_initiated_shutdown_behavior"),
            "ebs_optimized": template_data_config.get("ebs_optimized"),
            "metadata_options": metadata_options_payload, 
            "cpu_options": cpu_options_payload,
            "credit_specification": credit_spec_payload,
            "placement": placement_payload,
            "capacity_reservation_specification": capacity_reservation_payload,
            "hibernation_options": hibernation_options_payload,
            "license_specifications": license_specifications_payload if license_specifications_payload else None,
            "elastic_gpu_specifications": elastic_gpu_payload if elastic_gpu_payload else None,
            "elastic_inference_accelerators": elastic_inference_payload if elastic_inference_payload else None,
            "enclave_options": enclave_options_payload,
            "tag_specifications": resource_launch_tag_specs if resource_launch_tag_specs else None,
        }
        
        if parsed_network_interfaces_cfn and "security_group_ids" in template_data_config:
            logger.info(f"LT '{launch_template_name}': 'security_group_ids' at top level of template_data ignored because 'network_interfaces' are defined.")
        elif not parsed_network_interfaces_cfn and template_data_config.get("security_group_ids"):
             lt_data_args["security_group_ids"] = template_data_config.get("security_group_ids")

        final_lt_data_props = {k: v for k, v in lt_data_args.items() if v is not None}
        launch_template_data_object = ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(**final_lt_data_props) if final_lt_data_props else ec2.CfnLaunchTemplate.LaunchTemplateDataProperty()

        lt_resource_tags_config = self.config.get("tags", {})
        cfn_lt_resource_tags_list = [CfnTag(key=str(k), value=str(v)) for k,v in lt_resource_tags_config.items()]
        
        launch_template_tag_specifications = None
        if cfn_lt_resource_tags_list:
            launch_template_tag_specifications = [
                ec2.CfnLaunchTemplate.LaunchTemplateTagSpecificationProperty( 
                    resource_type="launch-template",
                    tags=cfn_lt_resource_tags_list
                )
            ]
        
        self.cfn_launch_template = ec2.CfnLaunchTemplate(
            self, "CfnResourceLaunchTemplate",
            launch_template_name=launch_template_name,
            version_description=self.config.get("version_description"),
            launch_template_data=launch_template_data_object,
            tag_specifications=launch_template_tag_specifications
        )
        
        self.launch_template = ec2.LaunchTemplate.from_launch_template_attributes(
            self, "L2LaunchTemplateWrapper",
            launch_template_id=self.cfn_launch_template.attr_launch_template_id,
            version_number=self.cfn_launch_template.attr_latest_version_number 
        )

        CfnOutput(self, "LaunchTemplateIdOutput", value=self.cfn_launch_template.attr_launch_template_id)
        CfnOutput(self, "LaunchTemplateLatestVersionOutput", value=self.cfn_launch_template.attr_latest_version_number)

    def _parse_network_interfaces_and_get_sg_ids(self, ni_config_from_template_data: typing.Optional[typing.Union[dict, list]]) -> \
                                             typing.Tuple[typing.Optional[typing.List[ec2.CfnLaunchTemplate.NetworkInterfaceProperty]], typing.Optional[typing.List[str]]]:
        lt_name = self.config.get('launch_template_name', 'UnknownLT')
        determined_sg_ids: typing.List[str] = []
        ni_list_to_process: typing.Optional[list] = None
        is_section_enabled = True 

        if isinstance(ni_config_from_template_data, dict):
            is_section_enabled = ni_config_from_template_data.get("enabled", True)
            if is_section_enabled:
                ni_list_to_process = ni_config_from_template_data.get("interfaces", [])
            else:
                logger.info(f"Network interfaces section is explicitly disabled in config for LT '{lt_name}'.")
        elif isinstance(ni_config_from_template_data, list):
            ni_list_to_process = ni_config_from_template_data
        else: 
            is_section_enabled = False 

        if not is_section_enabled or not ni_list_to_process:
            logger.info(f"Network interfaces section is disabled or no interfaces provided for LT '{lt_name}'.")
            if not self.vpc:
                logger.warning(f"Cannot create default Network Interface with Security Group for LT '{lt_name}' because no VPC context was provided. LT might not be connectable.")
                return None, None
            
            logger.info(f"Creating a default network interface with a new security group for LT '{lt_name}' to ensure connectability.")
            sanitized_lt_name_for_sg_id = ''.join(filter(str.isalnum, lt_name))[:20]
            default_sg_id_construct = f"{sanitized_lt_name_for_sg_id}DefSg"
            
            self.managed_security_group = ec2.SecurityGroup(self, default_sg_id_construct,
                                         vpc=self.vpc, 
                                         description=f"Default SG for LT {lt_name}")
            Tags.of(self.managed_security_group).add("Name", f"{lt_name}-default-lt-sg")
            determined_sg_ids.append(self.managed_security_group.security_group_id)
            return [
                ec2.CfnLaunchTemplate.NetworkInterfaceProperty(
                    device_index=0,
                    groups=[self.managed_security_group.security_group_id], 
                    delete_on_termination=True 
                )
            ], determined_sg_ids

        cfn_interfaces = []
        has_sg_defined_in_config = False
        for conf_idx, conf in enumerate(ni_list_to_process): 
            if not isinstance(conf, dict): continue
            
            current_interface_sgs = conf.get("groups")
            if current_interface_sgs and isinstance(current_interface_sgs, list): 
                has_sg_defined_in_config = True
                determined_sg_ids.extend(sg_id for sg_id in current_interface_sgs if isinstance(sg_id, str))

            private_ip_adds = None
            if conf.get("private_ip_addresses"):
                private_ip_adds = [
                    ec2.CfnLaunchTemplate.PrivateIpAddProperty(primary=ip.get("primary"), private_ip_address=ip.get("private_ip_address"))
                    for ip in conf.get("private_ip_addresses", []) if isinstance(ip, dict) and ip.get("private_ip_address") is not None
                ]
            ipv6_adds = None
            if conf.get("ipv6_addresses"):
                ipv6_adds = [
                    ec2.CfnLaunchTemplate.Ipv6AddProperty(ipv6_address=ip.get("ipv6_address"))
                    for ip in conf.get("ipv6_addresses",[]) if isinstance(ip, dict) and ip.get("ipv6_address") is not None
                ]
            props = {
                "device_index": conf.get("device_index"), "subnet_id": conf.get("subnet_id"),
                "associate_public_ip_address": conf.get("associate_public_ip_address"), "groups": current_interface_sgs, 
                "delete_on_termination": conf.get("delete_on_termination"), "description": conf.get("description"),
                "private_ip_address": conf.get("private_ip_address"),
                "private_ip_addresses": private_ip_adds if private_ip_adds else None,
                "secondary_private_ip_address_count": conf.get("secondary_private_ip_address_count"),
                "ipv6_address_count": conf.get("ipv6_address_count"),
                "ipv6_addresses": ipv6_adds if ipv6_adds else None,
                "network_interface_id": conf.get("network_interface_id"), "network_card_index": conf.get("network_card_index"),
                "interface_type": conf.get("interface_type")
            }
            final_props = {k:v for k,v in props.items() if v is not None}
            
            if final_props.get("device_index") is None and len(ni_list_to_process) == 1 : 
                logger.info(f"Assuming device_index 0 for single network interface in LT {lt_name}")
                final_props["device_index"] = 0

            if "device_index" in final_props : 
                cfn_interfaces.append(ec2.CfnLaunchTemplate.NetworkInterfaceProperty(**final_props))
            else: logger.warning(f"Skipping network interface at index {conf_idx} due to missing device_index: {conf}")
        
        if not cfn_interfaces or not has_sg_defined_in_config:
            logger.warning(f"LT '{lt_name}': Processed interfaces are empty or no security groups were defined in config.")
            if not self.vpc:
                logger.error(f"Cannot ensure default SG for LT '{lt_name}' as no VPC context was provided and config lacks SGs. LT might not be connectable.")
                return cfn_interfaces if cfn_interfaces else None, list(set(determined_sg_ids)) if determined_sg_ids else None
            
            logger.info(f"Ensuring a default NI with SG for LT '{lt_name}' due to missing SGs in provided config.")
            sanitized_lt_name_for_sg_id_ensured = ''.join(filter(str.isalnum, lt_name))[:20]
            default_sg_ensured_id_construct = f"{sanitized_lt_name_for_sg_id_ensured}EnsuredSg"
            self.managed_security_group = ec2.SecurityGroup(self, default_sg_ensured_id_construct,
                                         vpc=self.vpc,
                                         description=f"Ensured SG for LT {lt_name}")
            Tags.of(self.managed_security_group).add("Name", f"{lt_name}-ensured-lt-sg")
            determined_sg_ids = [self.managed_security_group.security_group_id] 
            cfn_interfaces = [ec2.CfnLaunchTemplate.NetworkInterfaceProperty(device_index=0, groups=determined_sg_ids, delete_on_termination=True)]

        unique_determined_sg_ids = list(set(determined_sg_ids)) if determined_sg_ids else []
        
        if unique_determined_sg_ids and not self.managed_security_group:
            try:
                sg_wrapper_id = f"{lt_name.replace('-', '')[:15]}WrapSG" 
                self.managed_security_group = ec2.SecurityGroup.from_security_group_id(
                    self, sg_wrapper_id, unique_determined_sg_ids[0]
                )
                logger.info(f"LT {lt_name}: Set self.managed_security_group by importing SG ID {unique_determined_sg_ids[0]} from config.")
            except Exception as e:
                 logger.warning(f"Could not import SG '{unique_determined_sg_ids[0]}' for LT wrapper's managed_security_group attribute: {e}")

        return cfn_interfaces if cfn_interfaces else None, unique_determined_sg_ids

    def _parse_monitoring(self, monitoring_detailed_flag: typing.Optional[bool]) -> typing.Optional[ec2.CfnLaunchTemplate.MonitoringProperty]:
        if monitoring_detailed_flag is None: return None
        return ec2.CfnLaunchTemplate.MonitoringProperty(enabled=bool(monitoring_detailed_flag))

    def _resolve_ami_id(self, ami_config: typing.Optional[dict], image_id_direct: typing.Optional[str]) -> typing.Optional[str]:
        if image_id_direct: return image_id_direct
        if not ami_config or not ami_config.get("source"): return None
        source = ami_config.get("source", "").upper(); architecture = ami_config.get("architecture", "X86_64").upper()
        
        if architecture == "ARM_64":
            ami_cpu_type = ec2.AmazonLinuxCpuType.ARM_64
        elif architecture == "X86_64":
            ami_cpu_type = ec2.AmazonLinuxCpuType.X86_64
        else: 
            logger.warning(f"Unknown architecture '{architecture}', defaulting to X86_64 for AMI lookup.")
            ami_cpu_type = ec2.AmazonLinuxCpuType.X86_64

        if source == "ID" and ami_config.get("id"): return ami_config.get("id")
        
        img_resolver: typing.Optional[ec2.IMachineImage] = None
        if source == "LATEST_AMAZON_LINUX_2023": img_resolver = ec2.MachineImage.latest_amazon_linux2023(cpu_type=ami_cpu_type)
        elif source == "LATEST_AMAZON_LINUX_2": img_resolver = ec2.MachineImage.latest_amazon_linux(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2, cpu_type=ami_cpu_type)
        elif source == "LATEST_WINDOWS_CORE": img_resolver = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2022_ENGLISH_CORE_BASE)
        elif source == "LATEST_WINDOWS_FULL": img_resolver = ec2.MachineImage.latest_windows(ec2.WindowsVersion.WINDOWS_SERVER_2022_ENGLISH_FULL_BASE)
        elif source == "LOOKUP":
            lookup_filters = ami_config.get("lookup_filters", {}); 
            if not lookup_filters.get("name"): 
                logger.warning("AMI LOOKUP specified but 'name' filter is missing."); return None
            img_resolver = ec2.MachineImage.lookup(
                name=lookup_filters.get("name"), 
                owners=lookup_filters.get("owners"), 
                filters=lookup_filters.get("filters")
            )
        
        if img_resolver: 
            try: 
                return img_resolver.get_image(self).image_id
            except Exception as e: 
                logger.error(f"AMI resolution failed for source '{source}': {e}", exc_info=True)
        logger.warning(f"Could not resolve AMI from config: {ami_config}"); return None

    def _resolve_user_data(self, user_data_config_block: typing.Optional[dict]) -> typing.Optional[str]:
        # Expects user_data_config_block to be the dict under "user_data" key from template_data
        if not user_data_config_block or not isinstance(user_data_config_block, dict) or \
           not user_data_config_block.get("enabled", False):
            logger.info(f"User data is not enabled or not configured for LT '{self.config.get('launch_template_name')}'.")
            return None
        
        script_path_str = user_data_config_block.get("user_data_script_path")
        user_data_code_str = user_data_config_block.get("user_data_code") # New option
        b64_content = user_data_config_block.get("user_data_b64")
        script_type = user_data_config_block.get("type", "SHELL_SCRIPT").upper() # Default to SHELL_SCRIPT

        content_to_encode = None

        if script_path_str:
            script_file = Path(script_path_str)
            if not script_file.is_file(): 
                 logger.error(f"UserData script_path not found or is not a file: {script_path_str}")
                 return None
            try:
                logger.info(f"Reading user data from script path: {script_path_str}")
                content_to_encode = script_file.read_text()
            except Exception as e: 
                logger.error(f"Failed to read/process user_data_script_path '{script_path_str}': {e}", exc_info=True)
                return None
        elif user_data_code_str:
            logger.info("Using direct user_data_code from config.")
            content_to_encode = user_data_code_str
        elif b64_content: 
            logger.info("Using pre-encoded user_data_b64 from config.")
            return b64_content # Already encoded
        
        if content_to_encode:
            # Add shebang if it's a shell script and doesn't have one
            if script_type == "SHELL_SCRIPT" and not content_to_encode.lstrip().startswith("#!"):
                content_to_encode = "#!/bin/bash\n" + content_to_encode
            # Add other script type handling here if necessary (e.g., PowerShell, cloud-boothook)
            return Fn.base64(content_to_encode)
        
        logger.info("No user data content provided in enabled user_data config.")
        return None

    def _parse_iam_instance_profile(self, iam_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.IamInstanceProfileProperty]:
        if not iam_conf or not iam_conf.get("enabled", False): 
            logger.info(f"IAM Instance Profile is disabled in LT config for {self.config.get('launch_template_name')}")
            return None
        arn = iam_conf.get("iam_instance_profile_arn"); name = iam_conf.get("iam_instance_profile_name") 
        if arn:
            logger.info(f"Using existing IAM Instance Profile ARN '{arn}' for LT '{self.config.get('launch_template_name')}'.")
            return ec2.CfnLaunchTemplate.IamInstanceProfileProperty(arn=arn)
        if name: 
            logger.info(f"Using existing IAM Instance Profile Name '{name}' for LT '{self.config.get('launch_template_name')}'.")
            return ec2.CfnLaunchTemplate.IamInstanceProfileProperty(name=name)
        logger.warning(f"IAM instance profile enabled for LT '{self.config.get('launch_template_name')}' but no ARN or Name provided."); return None

    def _parse_block_device_mappings(self, bdm_conf_wrapper: typing.Optional[dict]) -> typing.Optional[typing.List[ec2.CfnLaunchTemplate.BlockDeviceMappingProperty]]:
        if not bdm_conf_wrapper or not isinstance(bdm_conf_wrapper, dict) or not bdm_conf_wrapper.get("enabled", False):
            logger.info(f"Block device mappings section is disabled or not provided correctly for LT '{self.config.get('launch_template_name')}'.")
            return None
        
        bdm_configs_list = bdm_conf_wrapper.get("mappings", [])
        if not bdm_configs_list or not isinstance(bdm_configs_list, list):
            logger.info(f"No 'mappings' list provided in enabled block_device_mappings for LT '{self.config.get('launch_template_name')}'.")
            return None
            
        cfn_bdms = []
        for conf in bdm_configs_list: 
            if not isinstance(conf, dict): continue
            ebs_conf = conf.get("ebs"); ebs_prop = None
            if ebs_conf and isinstance(ebs_conf, dict):
                ebs_payload = { 
                    "volume_size": ebs_conf.get("volume_size"), "volume_type": ebs_conf.get("volume_type"),
                    "iops": ebs_conf.get("iops"), "throughput": ebs_conf.get("throughput"), 
                    "encrypted": ebs_conf.get("encrypted"), "delete_on_termination": ebs_conf.get("delete_on_termination"),
                    "snapshot_id": ebs_conf.get("snapshot_id"), "kms_key_id": ebs_conf.get("kms_key_id")
                }
                final_ebs_payload = {k:v for k,v in ebs_payload.items() if v is not None}
                if final_ebs_payload: ebs_prop = ec2.CfnLaunchTemplate.EbsProperty(**final_ebs_payload)
            
            no_device_val = conf.get("no_device") 
            mapping_props: typing.Dict[str, typing.Any] = {"device_name": conf.get("device_name"), "ebs": ebs_prop, "virtual_name": conf.get("virtual_name")}
            
            if no_device_val is True: 
                mapping_props["no_device"] = "" 
                mapping_props.pop("ebs", None); mapping_props.pop("virtual_name", None)
            elif isinstance(no_device_val, str):
                 mapping_props["no_device"] = no_device_val

            final_mapping_props = {k:v for k,v in mapping_props.items() if v is not None}
            if "device_name" in final_mapping_props: 
                cfn_bdms.append(ec2.CfnLaunchTemplate.BlockDeviceMappingProperty(**final_mapping_props))
            else: 
                logger.warning(f"Skipping BDM due to missing device_name: {conf}")
        return cfn_bdms if cfn_bdms else None

    def _parse_metadata_options(self, md_opts_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.MetadataOptionsProperty]:
        if not md_opts_conf or not md_opts_conf.get("enabled", False): return None
        hop_limit = md_opts_conf.get("http_put_response_hop_limit")
        try:
            hop_limit_int = int(hop_limit) if hop_limit is not None else None
        except ValueError:
            logger.warning(f"Invalid http_put_response_hop_limit '{hop_limit}', using None.")
            hop_limit_int = None

        props = { 
            "http_tokens": md_opts_conf.get("http_tokens"), 
            "http_endpoint": md_opts_conf.get("http_endpoint"),
            "http_put_response_hop_limit": hop_limit_int,
            "instance_metadata_tags": md_opts_conf.get("instance_metadata_tags")
        }
        final_props = {k:v for k,v in props.items() if v is not None}
        return ec2.CfnLaunchTemplate.MetadataOptionsProperty(**final_props) if final_props else None

    def _parse_cpu_options(self, cpu_opts_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.CpuOptionsProperty]:
        if not cpu_opts_conf or not cpu_opts_conf.get("enabled", False): return None
        props = {"core_count": cpu_opts_conf.get("core_count"), "threads_per_core": cpu_opts_conf.get("threads_per_core")}
        final_props = {k:v for k,v in props.items() if v is not None}
        if "core_count" in final_props or "threads_per_core" in final_props:
            return ec2.CfnLaunchTemplate.CpuOptionsProperty(**final_props)
        logger.warning("CpuOptions enabled but no core_count or threads_per_core specified.")
        return None
        
    def _parse_credit_specification(self, credit_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.CreditSpecificationProperty]:
        if not credit_conf or not credit_conf.get("enabled", False) or not credit_conf.get("cpu_credits"): return None
        cpu_credits_val = str(credit_conf["cpu_credits"]).lower()
        if cpu_credits_val not in ["standard", "unlimited"]:
            logger.warning(f"Invalid cpu_credits value '{cpu_credits_val}'. Must be 'standard' or 'unlimited'. Skipping credit spec.")
            return None
        return ec2.CfnLaunchTemplate.CreditSpecificationProperty(cpu_credits=cpu_credits_val)

    def _parse_placement(self, placement_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.PlacementProperty]:
        if not placement_conf or not placement_conf.get("enabled", False): return None
        props = { 
            "availability_zone": placement_conf.get("availability_zone"), "affinity": placement_conf.get("affinity"),
            "group_name": placement_conf.get("group_name"), "host_id": placement_conf.get("host_id"),
            "host_resource_group_arn": placement_conf.get("host_resource_group_arn"),
            "partition_number": placement_conf.get("partition_number"), "spread_domain": placement_conf.get("spread_domain"),
            "tenancy": placement_conf.get("tenancy"), "group_id": placement_conf.get("group_id")
        }
        final_props = {k:v for k,v in props.items() if v is not None}
        return ec2.CfnLaunchTemplate.PlacementProperty(**final_props) if final_props else None

    def _parse_capacity_reservation(self, cap_res_spec_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.CapacityReservationSpecificationProperty]:
        if not cap_res_spec_conf or not cap_res_spec_conf.get("enabled", False): return None
        target_payload_obj = None; target_conf = cap_res_spec_conf.get("capacity_reservation_target", {})
        if target_conf and isinstance(target_conf, dict): 
            target_props_dict = { 
                "capacity_reservation_id": target_conf.get("capacity_reservation_id"),
                "capacity_reservation_resource_group_arn": target_conf.get("capacity_reservation_resource_group_arn")
            }
            final_target_props = {k:v for k,v in target_props_dict.items() if v is not None}
            if final_target_props: target_payload_obj = ec2.CfnLaunchTemplate.CapacityReservationTargetProperty(**final_target_props)
        
        spec_props_dict: typing.Dict[str, typing.Any] = {}
        if target_payload_obj: spec_props_dict["capacity_reservation_target"] = target_payload_obj
        pref = cap_res_spec_conf.get("capacity_reservation_preference")
        if pref is not None: 
            spec_props_dict["capacity_reservation_preference"] = str(pref).lower()
        
        return ec2.CfnLaunchTemplate.CapacityReservationSpecificationProperty(**spec_props_dict) if spec_props_dict else None

    def _parse_hibernation_options(self, hib_opts_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.HibernationOptionsProperty]:
        if not hib_opts_conf or not hib_opts_conf.get("enabled", False): return None
        return ec2.CfnLaunchTemplate.HibernationOptionsProperty(configured=bool(hib_opts_conf.get("configured", True)))

    def _parse_license_specifications(self, lic_spec_conf: typing.Optional[dict]) -> typing.Optional[typing.List[ec2.CfnLaunchTemplate.LicenseSpecificationProperty]]:
        if not lic_spec_conf or not lic_spec_conf.get("enabled", False) or not lic_spec_conf.get("specifications"): return None
        cfn_specs = []
        for spec in lic_spec_conf.get("specifications", []):
            if isinstance(spec, dict) and spec.get("license_configuration_arn"):
                cfn_specs.append(ec2.CfnLaunchTemplate.LicenseSpecificationProperty(license_configuration_arn=spec["license_configuration_arn"]))
        return cfn_specs if cfn_specs else None

    def _parse_elastic_gpu(self, elastic_gpu_conf: typing.Optional[dict]) -> typing.Optional[typing.List[ec2.CfnLaunchTemplate.ElasticGpuSpecificationProperty]]:
        if not elastic_gpu_conf or not elastic_gpu_conf.get("enabled", False) or not elastic_gpu_conf.get("specifications"): return None
        cfn_specs = []
        for spec in elastic_gpu_conf.get("specifications", []):
            if isinstance(spec, dict) and spec.get("type"):
                cfn_specs.append(ec2.CfnLaunchTemplate.ElasticGpuSpecificationProperty(type=spec["type"]))
        return cfn_specs if cfn_specs else None
        
    def _parse_elastic_inference(self, elastic_inf_conf: typing.Optional[dict]) -> typing.Optional[typing.List[ec2.CfnLaunchTemplate.LaunchTemplateElasticInferenceAcceleratorProperty]]:
        if not elastic_inf_conf or not elastic_inf_conf.get("enabled", False) or not elastic_inf_conf.get("accelerators"): return None
        cfn_accels = []
        for acc_conf in elastic_inf_conf.get("accelerators", []):
            if not isinstance(acc_conf, dict): continue
            props = {"type": acc_conf.get("type"), "count": acc_conf.get("count")} 
            final_props = {k:v for k,v in props.items() if v is not None}
            if "type" in final_props: 
                cfn_accels.append(ec2.CfnLaunchTemplate.LaunchTemplateElasticInferenceAcceleratorProperty(**final_props))
        return cfn_accels if cfn_accels else None

    def _parse_enclave_options(self, enclave_opts_conf: typing.Optional[dict]) -> typing.Optional[ec2.CfnLaunchTemplate.EnclaveOptionsProperty]:
        if not enclave_opts_conf or not enclave_opts_conf.get("enabled", False): return None
        return ec2.CfnLaunchTemplate.EnclaveOptionsProperty(enabled=True) 

    def _parse_tag_specifications_for_template_data(self, tag_spec_configs: typing.Optional[list]) -> typing.Optional[typing.List[ec2.CfnLaunchTemplate.TagSpecificationProperty]]:
        if not tag_spec_configs: return None
        cfn_tag_specs = []
        for ts_conf in tag_spec_configs:
            if not isinstance(ts_conf, dict): continue
            resource_type = ts_conf.get("resource_type"); tags_dict = ts_conf.get("tags", {})
            if resource_type and tags_dict and isinstance(tags_dict, dict):
                cfn_tags_list = [CfnTag(key=str(k), value=str(v)) for k,v in tags_dict.items()]
                if cfn_tags_list: 
                    cfn_tag_specs.append(ec2.CfnLaunchTemplate.TagSpecificationProperty(resource_type=resource_type, tags=cfn_tags_list))
        return cfn_tag_specs if cfn_tag_specs else None

