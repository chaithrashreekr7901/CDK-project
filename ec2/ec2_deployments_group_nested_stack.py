# CRMP-PROJECT/cdk_project/ec2/ec2_deployments_group_nested_stack.py
import logging
import copy
import typing
from aws_cdk import (
    NestedStack,
    Tags,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_autoscaling as autoscaling,
    aws_iam as iam
)
from constructs import Construct

from .ec2_instance_nested_stack import Ec2InstanceNestedStack
from .launch_template_stack import LaunchTemplateStack
from .ebs_volume_stack import EbsVolumeStack
from .application_load_balancer_stack import ApplicationLoadBalancerStack
from .network_load_balancer_stack import NetworkLoadBalancerStack
from .target_group_stack import TargetGroupStack
from .auto_scaling_group_stack import AutoScalingGroupStack
from .security_group_stack import SecurityGroupStack, construct_id_to_cdk_id_part # Import SecurityGroupStack and helper

def merge_dicts(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = merge_dicts(result[key], value)
        elif isinstance(value, list) and key in result and isinstance(result[key], list):
            result[key] = copy.deepcopy(value)
        else:
            result[key] = value
    return result

logger = logging.getLogger(__name__)

class Ec2DeploymentsGroupNestedStack(NestedStack):
    # These type hints are for clarity but don't define the constructor behavior
    created_ec2_instances_map: typing.Dict[str, ec2.Instance]
    created_asgs_map: typing.Dict[str, autoscaling.AutoScalingGroup]
    created_iam_roles_map: typing.Dict[str, iam.IRole] # Add this type hint

    def __init__(self, scope: Construct, id: str, *,
                 ec2_deployments_config: typing.Dict,
                 created_vpcs_map: typing.Dict[str, ec2.IVpc],
                 # ADDED: Explicitly declare the maps passed from the parent stack
                 created_ec2_instances_map: typing.Dict[str, ec2.Instance],
                 created_asgs_map: typing.Dict[str, autoscaling.AutoScalingGroup],
                 created_iam_roles_map: typing.Dict[str, iam.IRole],
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:

        # Filter kwargs to only pass what NestedStack's __init__ expects.
        # 'description' is explicitly handled. Other common ones are 'env', 'stack_name', etc.
        # Assuming 'kwargs' might contain 'env' or 'stack_name' if passed from app.py/MainOrchestratorStack.
        nested_stack_valid_kwargs = {}
        for key, value in kwargs.items():
            if key in ['env', 'stack_name', 'synthesizer', 'termination_protection']: # Add other valid NestedStack kwargs if used
                nested_stack_valid_kwargs[key] = value

        super().__init__(scope, id, description=description, **nested_stack_valid_kwargs)

        # Store the received maps as instance attributes for use within this stack
        self.created_vpcs_map = created_vpcs_map
        self.created_ec2_instances_map = created_ec2_instances_map
        self.created_asgs_map = created_asgs_map
        self.created_iam_roles_map = created_iam_roles_map

        group_description = ec2_deployments_config.get("description", "EC2, Networking & Storage Resources Group")
        logger.info(f"Ec2DeploymentsGroupNestedStack '{id}': Initializing for: {group_description}")
        Tags.of(self).add("CDKResourceGroup", "EC2NetworkingAndStorage")

        self.deployed_instance_stacks: dict[str, Ec2InstanceNestedStack] = {}
        self.deployed_lt_stacks: dict[str, LaunchTemplateStack] = {}
        self.deployed_ebs_volume_stacks: dict[str, EbsVolumeStack] = {}
        self.deployed_alb_stacks: dict[str, ApplicationLoadBalancerStack] = {}
        self.deployed_nlb_stacks: dict[str, NetworkLoadBalancerStack] = {}
        self.deployed_target_group_stacks: dict[str, TargetGroupStack] = {}
        self.deployed_asg_stacks: dict[str, AutoScalingGroupStack] = {}
        self.deployed_security_group_stacks: dict[str, SecurityGroupStack] = {}
        self.resolved_sgs_map: typing.Dict[str, ec2.ISecurityGroup] = {}

        defaults_config = ec2_deployments_config.get("defaults", {})
        global_defaults = {
            k: v for k, v in defaults_config.items()
            if k not in ["instance_defaults", "launch_template_defaults",
                          "ebs_volume_defaults", "alb_defaults", "nlb_defaults",
                          "target_group_defaults", "asg_defaults", "security_group_defaults"]
        }
        instance_specific_defaults = defaults_config.get("instance_defaults", {})
        lt_specific_defaults = defaults_config.get("launch_template_defaults", {})
        ebs_specific_defaults = defaults_config.get("ebs_volume_defaults", {})
        alb_specific_defaults = defaults_config.get("alb_defaults", {})
        nlb_specific_defaults = defaults_config.get("nlb_defaults", {})
        target_group_specific_defaults = defaults_config.get("target_group_defaults", {})
        asg_specific_defaults = defaults_config.get("asg_defaults", {})
        sg_specific_defaults = defaults_config.get("security_group_defaults", {})

        # --- Revised Processing Order ---
        # 1. Custom Security Groups
        # 2. EC2 Instances (standalone)
        # 3. Launch Templates
        # 4. Application Load Balancers (can now reference instance IDs and custom SGs)
        # 5. Network Load Balancers
        # 6. Standalone Target Groups (can reference instance IDs)
        # 7. Auto Scaling Groups
        # 8. EBS Volumes

        # --- 1. Process Custom Security Group Deployments ---
        sg_configurations_list = ec2_deployments_config.get("security_groups", [])
        if not sg_configurations_list:
            logger.info(f"{id}: No custom Security Group configurations found.")
        else:
            logger.info(f"{id}: Processing {len(sg_configurations_list)} Security Group definition(s).")
            for i, sg_cfg_entry_original in enumerate(sg_configurations_list):
                if not isinstance(sg_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid SG config entry at index {i}: not a dictionary.")
                    continue

                sg_cfg_entry = copy.deepcopy(sg_cfg_entry_original)
                sg_id_val = sg_cfg_entry.get("id")
                if not sg_id_val:
                    logger.error(f"Skipping Security Group at index {i} due to missing 'id'.")
                    continue

                is_sg_enabled = sg_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_sg_enabled:
                    logger.info(f"Skipping Security Group config '{sg_id_val}' (disabled).")
                    continue

                if "config" not in sg_cfg_entry: sg_cfg_entry["config"] = {}
                temp_sg_config_block_1 = merge_dicts(global_defaults, sg_specific_defaults)
                temp_sg_config_block_2 = merge_dicts(temp_sg_config_block_1, sg_cfg_entry["config"])
                sg_cfg_entry["config"] = temp_sg_config_block_2

                vpc_id_for_sg = sg_cfg_entry["config"].get("vpc_id")
                if not vpc_id_for_sg:
                    logger.error(f"VPC ID missing for SG '{sg_id_val}'. Skipping SG creation.")
                    continue
                try:
                    vpc_lookup_sg_id = f"{sg_id_val}VpcCtxForSG"
                    vpc_obj_for_sg = ec2.Vpc.from_lookup(self, vpc_lookup_sg_id, vpc_id=vpc_id_for_sg)
                except Exception as e:
                    logger.error(f"Failed to lookup VPC '{vpc_id_for_sg}' for SG '{sg_id_val}': {e}. Skipping SG.", exc_info=True)
                    continue

                sg_name_for_desc = sg_cfg_entry["config"].get("security_group_name", sg_id_val)
                sanitized_sg_cdk_id = construct_id_to_cdk_id_part(sg_id_val)
                sg_nested_stack_id = f"{sanitized_sg_cdk_id}SGStack"

                logger.info(f"Defining SecurityGroupStack for {sg_id_val} (Name: {sg_name_for_desc}) -> CDK ID {sg_nested_stack_id}.")
                try:
                    sg_stack = SecurityGroupStack(
                        self, sg_nested_stack_id,
                        sg_config_entry=sg_cfg_entry,
                        vpc=vpc_obj_for_sg,
                        peer_sgs_map=self.resolved_sgs_map, # Pass current map (might be empty or have other LBs SGs if order changes)
                        description=f"Nested Stack for Security Group: {sg_name_for_desc}"
                    )
                    if sg_stack.security_group:
                        self.deployed_security_group_stacks[sg_id_val] = sg_stack
                        self.resolved_sgs_map[sg_id_val] = sg_stack.security_group # Add newly created SG to the map
                        Tags.of(sg_stack).add("ResourceType", "SecurityGroup")
                        Tags.of(sg_stack).add("ConfigID", sg_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate SecurityGroupStack '{sg_nested_stack_id}': {e}", exc_info=True)

        # --- 2. Process Direct EC2 Instance Deployments ---
        instance_configurations_list = ec2_deployments_config.get("instances", [])
        if not instance_configurations_list:
            logger.info(f"{id}: No direct EC2 instance configurations found.")
        else:
            logger.info(f"{id}: Processing {len(instance_configurations_list)} direct EC2 instance definition(s).")
            for i, ec2_cfg_entry_original in enumerate(instance_configurations_list):
                if not isinstance(ec2_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid EC2 instance config entry at index {i}: not a dictionary.")
                    continue

                ec2_cfg_entry = copy.deepcopy(ec2_cfg_entry_original)
                is_enabled = ec2_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_enabled:
                    logger.info(f"Skipping EC2 instance config '{ec2_cfg_entry.get('id', f'UnknownIndex{i}')}' (disabled).")
                    continue

                config_id_base = ec2_cfg_entry.get("id")
                if not config_id_base:
                    logger.error(f"Skipping EC2 instance at index {i} due to missing 'id'.")
                    continue

                # Ensure 'config' and 'network_config' exist for modification
                if "config" not in ec2_cfg_entry: ec2_cfg_entry["config"] = {}
                if "network_config" not in ec2_cfg_entry["config"]: ec2_cfg_entry["config"]["network_config"] = {}

                network_config_block = ec2_cfg_entry["config"]["network_config"]
                sg_refs = network_config_block.get("security_group_refs")

                if sg_refs and isinstance(sg_refs, list):
                    resolved_sg_ids_for_instance = []
                    for ref_id in sg_refs:
                        if ref_id in self.resolved_sgs_map:
                            resolved_sg_ids_for_instance.append(self.resolved_sgs_map[ref_id].security_group_id)
                        else:
                            logger.warning(f"Instance '{config_id_base}': Could not resolve SG ref_id '{ref_id}'. Assuming it's a physical ID or defined in instance's own SG def.")
                            resolved_sg_ids_for_instance.append(ref_id) # Keep original if not found

                    network_config_block["security_group_ids"] = resolved_sg_ids_for_instance
                    network_config_block.pop("security_group_refs", None)
                    logger.info(f"Instance '{config_id_base}': Updated network_config with resolved security_group_ids: {resolved_sg_ids_for_instance}")

                count = ec2_cfg_entry.get("count", 1)
                try: count = int(count);
                except ValueError: count = 1
                if count < 1: count = 1

                instance_config_from_file = ec2_cfg_entry.get("config", {})

                for item_index in range(count):
                    temp_merged_config = merge_dicts(global_defaults, instance_specific_defaults)
                    merged_ec2_specific_config = merge_dicts(temp_merged_config, copy.deepcopy(instance_config_from_file))

                    suffix = f"-{item_index + 1}" if count > 1 else ""
                    original_instance_name = merged_ec2_specific_config.get("instance_name", config_id_base)
                    current_instance_name = f"{original_instance_name}{suffix}"
                    merged_ec2_specific_config["instance_name"] = current_instance_name
                    if merged_ec2_specific_config.get("iam_instance_profile", {}).get("create_new", False):
                        iam_conf = merged_ec2_specific_config.setdefault("iam_instance_profile", {})
                        orig_role = iam_conf.get("role_name", f"{original_instance_name}-Role")
                        iam_conf["role_name"] = f"{orig_role}{suffix}"
                        orig_prof = iam_conf.get("profile_name", f"{original_instance_name}-Profile")
                        iam_conf["profile_name"] = f"{orig_prof}{suffix}"

                    if merged_ec2_specific_config.get("security_group_definition", {}).get("enabled", False) and \
                        not merged_ec2_specific_config.get("network_config", {}).get("security_group_ids"):
                        sg_def = merged_ec2_specific_config.setdefault("security_group_definition", {})
                        orig_sg_name = sg_def.get("name", f"{original_instance_name}-sg")
                        sg_def["name"] = f"{orig_sg_name}{suffix}"

                    sanitized_id = ''.join(filter(str.isalnum, config_id_base)) or f"Ec2InstDef{i}"
                    cdk_id_item_suffix = f"Inst{item_index + 1}" if count > 1 else ""
                    nested_stack_id = f"{sanitized_id}{cdk_id_item_suffix}InstanceStack"

                    logger.info(f"Defining Ec2InstanceNestedStack for {config_id_base} (instance name: '{current_instance_name}') -> CDK ID '{nested_stack_id}' with resolved SGs.")
                    try:
                        ec2_inst_stack = Ec2InstanceNestedStack(
                            self, nested_stack_id,
                            ec2_specific_config=merged_ec2_specific_config
                        )
                        storage_key = f"{config_id_base}{suffix}" # Use this key to store the stack
                        self.deployed_instance_stacks[storage_key] = ec2_inst_stack
                        Tags.of(ec2_inst_stack).add("Ec2ResourceType", "Instance")
                        Tags.of(ec2_inst_stack).add("ConfigID", config_id_base)
                        if count > 1: Tags.of(ec2_inst_stack).add("InstanceIndex", str(item_index + 1))
                    except Exception as e:
                        logger.error(f"FAILED to instantiate Ec2InstanceNestedStack '{nested_stack_id}': {e}", exc_info=True)

        # --- 3. Process EC2 Launch Template Deployments ---
        lt_configurations_list = ec2_deployments_config.get("launch_templates", [])
        if not lt_configurations_list:
            logger.info(f"{id}: No Launch Template configurations found.")
        else:
            logger.info(f"{id}: Processing {len(lt_configurations_list)} Launch Template definition(s).")
            for i, lt_cfg_entry_original in enumerate(lt_configurations_list):
                if not isinstance(lt_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid Launch Template config entry at index {i}: not a dictionary.")
                    continue
                lt_cfg_entry = copy.deepcopy(lt_cfg_entry_original)
                is_lt_enabled = lt_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_lt_enabled:
                    logger.info(f"Skipping Launch Template config '{lt_cfg_entry.get('id', f'UnknownLTIndex{i}')}' (disabled).")
                    continue

                lt_config_id = lt_cfg_entry.get("id")
                if not lt_config_id:
                    logger.error(f"Skipping Launch Template at index {i} due to missing 'id'.")
                    continue

                temp_lt_merged_entry = merge_dicts(global_defaults, lt_specific_defaults)
                merged_lt_full_entry = merge_dicts(temp_lt_merged_entry, lt_cfg_entry)

                if not merged_lt_full_entry.get("launch_template_name"):
                    logger.error(f"Skipping Launch Template '{lt_config_id}': 'launch_template_name' is missing after merge.")
                    continue

                vpc_for_lt: typing.Optional[ec2.IVpc] = None
                lt_vpc_id_from_config = merged_lt_full_entry.get("config", {}).get("vpc_id") or \
                                         merged_lt_full_entry.get("vpc_id")
                if not lt_vpc_id_from_config:
                    for asg_c in ec2_deployments_config.get("auto_scaling_groups", []):
                        if asg_c.get("config",{}).get("launch_template",{}).get("launch_template_ref_id") == lt_config_id:
                            lt_vpc_id_from_config = asg_c.get("config",{}).get("vpc_id")
                            if lt_vpc_id_from_config: logger.info(f"LT '{lt_config_id}': Inferring VPC ID '{lt_vpc_id_from_config}' from ASG '{asg_c.get('id')}'."); break
                if not lt_vpc_id_from_config:
                    lt_vpc_id_from_config = global_defaults.get("vpc_id")
                    if lt_vpc_id_from_config: logger.info(f"LT '{lt_config_id}': Using global default VPC ID '{lt_vpc_id_from_config}'.")

                if lt_vpc_id_from_config:
                    try:
                        vpc_for_lt = ec2.Vpc.from_lookup(self, f"{lt_config_id}VpcCtxForLT", vpc_id=lt_vpc_id_from_config)
                        logger.info(f"LT '{lt_config_id}': Successfully looked up VPC ID '{lt_vpc_id_from_config}'.")
                    except Exception as e:
                        logger.error(f"Failed to lookup VPC '{lt_vpc_id_from_config}' for LT '{lt_config_id}': {e}. LT Stack receives VPC as None.", exc_info=True)
                        vpc_for_lt = None
                else:
                    logger.warning(f"LT '{lt_config_id}': No 'vpc_id' determined. LT Stack receives VPC as None.")
                    vpc_for_lt = None

                if "template_data" in merged_lt_full_entry and "network_interfaces" in merged_lt_full_entry["template_data"]:
                    ni_list_or_dict = merged_lt_full_entry["template_data"]["network_interfaces"]
                    interfaces_to_process = []

                    if isinstance(ni_list_or_dict, dict) and ni_list_or_dict.get("enabled", True):
                        interfaces_to_process = ni_list_or_dict.get("interfaces", [])
                    elif isinstance(ni_list_or_dict, list):
                        interfaces_to_process = ni_list_or_dict

                    for ni_conf in interfaces_to_process:
                        if isinstance(ni_conf, dict) and "groups_ref_ids" in ni_conf:
                            resolved_group_ids = []
                            for ref_id in ni_conf.get("groups_ref_ids", []):
                                if ref_id in self.resolved_sgs_map:
                                    resolved_group_ids.append(self.resolved_sgs_map[ref_id].security_group_id)
                                else:
                                    logger.warning(f"LT '{lt_config_id}': Could not resolve SG ref_id '{ref_id}' for network interface. Assuming it's a physical ID or will be handled by LT default SG.")
                                    resolved_group_ids.append(ref_id)
                            ni_conf["groups"] = resolved_group_ids
                            ni_conf.pop("groups_ref_ids", None)
                        elif isinstance(ni_conf, dict) and "groups" in ni_conf:
                            ni_conf["groups"] = [str(sg_id) for sg_id in ni_conf["groups"]]


                sanitized_lt_id = ''.join(filter(str.isalnum, lt_config_id)) or f"LaunchTemplateDef{i}"
                lt_nested_stack_id = f"{sanitized_lt_id}LTStack"
                lt_name_for_desc = merged_lt_full_entry.get("launch_template_name")
                logger.info(f"Defining LaunchTemplateStack for {lt_config_id} (Name: {lt_name_for_desc}) -> CDK ID {lt_nested_stack_id}.")
                try:
                    lt_stack = LaunchTemplateStack(
                        self, lt_nested_stack_id,
                        lt_config=merged_lt_full_entry,
                        vpc=vpc_for_lt,
                        description=f"Nested Stack for EC2 Launch Template: {lt_name_for_desc}"
                    )
                    self.deployed_lt_stacks[lt_config_id] = lt_stack
                    Tags.of(lt_stack).add("Ec2ResourceType", "LaunchTemplate")
                    Tags.of(lt_stack).add("ConfigID", lt_config_id)
                except Exception as e:
                    logger.error(f"FAILED to instantiate LaunchTemplateStack '{lt_nested_stack_id}': {e}", exc_info=True)

        # --- 4. Process Application Load Balancers (ALBs) ---
        # ALB stacks are instantiated here, after Instances and SGs, so they can resolve references.
        alb_configurations_list = ec2_deployments_config.get("application_load_balancers", [])
        if not alb_configurations_list:
            logger.info(f"{id}: No Application Load Balancer configurations found.")
        else:
            logger.info(f"{id}: Processing {len(alb_configurations_list)} Application Load Balancer definition(s).")
            for i, alb_cfg_entry_original in enumerate(alb_configurations_list):
                if not isinstance(alb_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid ALB config entry at index {i}: not a dictionary.")
                    continue
                alb_cfg_entry = copy.deepcopy(alb_cfg_entry_original)
                is_alb_enabled = alb_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_alb_enabled:
                    logger.info(f"Skipping Application Load Balancer config '{alb_cfg_entry.get('id', f'UnknownALBIndex{i}')}' (disabled).")
                    continue
                alb_id_val = alb_cfg_entry.get("id")
                if not alb_id_val:
                    logger.error(f"Skipping Application Load Balancer at index {i} due to missing 'id'.")
                    continue

                if "config" not in alb_cfg_entry: alb_cfg_entry["config"] = {}
                temp_alb_config_block = merge_dicts(global_defaults, alb_specific_defaults)
                merged_alb_config_block = merge_dicts(temp_alb_config_block, alb_cfg_entry["config"])

                # Resolve security_group_refs for the ALB itself
                sg_refs_for_alb = merged_alb_config_block.get("security_group_refs")
                if sg_refs_for_alb and isinstance(sg_refs_for_alb, list):
                    resolved_sg_ids_for_alb = []
                    for ref_id in sg_refs_for_alb:
                        if ref_id in self.resolved_sgs_map:
                            resolved_sg_ids_for_alb.append(self.resolved_sgs_map[ref_id].security_group_id)
                        else:
                            logger.warning(f"ALB '{alb_id_val}': Could not resolve SG ref_id '{ref_id}'. Assuming it's a physical ID.")
                            resolved_sg_ids_for_alb.append(ref_id)
                    merged_alb_config_block["security_group_ids"] = resolved_sg_ids_for_alb # Set physical IDs
                    merged_alb_config_block.pop("security_group_refs", None)
                    logger.info(f"ALB '{alb_id_val}': Updated config with resolved security_group_ids: {resolved_sg_ids_for_alb}")

                # Resolve instance_id_refs within target_groups for this ALB's config
                if "target_groups" in merged_alb_config_block:
                    for tg_def in merged_alb_config_block.get("target_groups", []):
                        if "config" in tg_def and "targets" in tg_def["config"]:
                            for target_item in tg_def["config"].get("targets", []):
                                if isinstance(target_item, dict) and "instance_id_ref" in target_item:
                                    instance_ref_id = target_item["instance_id_ref"]
                                    if instance_ref_id in self.deployed_instance_stacks:
                                        instance_stack_obj = self.deployed_instance_stacks[instance_ref_id]
                                        if hasattr(instance_stack_obj, 'instance_id_token') and instance_stack_obj.instance_id_token:
                                            target_item["instance_id"] = instance_stack_obj.instance_id_token
                                            logger.info(f"ALB '{alb_id_val}', TG '{tg_def.get('id')}': Resolved target instance_id_ref '{instance_ref_id}' to '{instance_stack_obj.instance_id_token}'.")
                                            target_item.pop("instance_id_ref", None)
                                        else:
                                            logger.warning(f"ALB '{alb_id_val}', TG '{tg_def.get('id')}': Instance stack for '{instance_ref_id}' found but no 'instance_id_token'.")
                                    else:
                                        logger.warning(f"ALB '{alb_id_val}', TG '{tg_def.get('id')}': Could not resolve target instance_id_ref '{instance_ref_id}'. Instance stack not found.")

                alb_cfg_entry["config"] = merged_alb_config_block # Update the entry with resolved targets & SGs

                alb_name_for_desc = merged_alb_config_block.get("load_balancer_name", alb_id_val)
                sanitized_alb_id = ''.join(filter(str.isalnum, alb_id_val)) or f"ALBDef{i}"
                alb_nested_stack_id = f"{sanitized_alb_id}ALBStack"
                logger.info(f"Defining ApplicationLoadBalancerStack for {alb_id_val} (Name: {alb_name_for_desc}) -> CDK ID {alb_nested_stack_id}.")
                try:
                    alb_stack = ApplicationLoadBalancerStack(
                        self, alb_nested_stack_id,
                        alb_config_entry=alb_cfg_entry,
                        description=f"Nested Stack for Application Load Balancer: {alb_name_for_desc}"
                    )
                    self.deployed_alb_stacks[alb_id_val] = alb_stack
                    # Store ALB's primary security group in the resolved_sgs_map if it was created by the ALB stack
                    if alb_stack.alb and hasattr(alb_stack.alb, 'connections') and alb_stack.alb.connections.security_groups and \
                       not merged_alb_config_block.get("security_group_ids") and not merged_alb_config_block.get("security_group_refs"): # Only if SG was created by ALB stack
                        alb_primary_sg = alb_stack.alb.connections.security_groups[0]
                        self.resolved_sgs_map[alb_id_val] = alb_primary_sg
                        logger.info(f"Stored ALB '{alb_id_val}' (self-created) security group '{alb_primary_sg.security_group_id}' in resolved_sgs_map.")

                    Tags.of(alb_stack).add("ResourceType", "ApplicationLoadBalancer")
                    Tags.of(alb_stack).add("ConfigID", alb_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate ApplicationLoadBalancerStack '{alb_nested_stack_id}': {e}", exc_info=True)


        # --- 5. Process Network Load Balancers ---
        # (Similar logic for NLBs if they need to reference SGs or instances)
        nlb_configurations_list = ec2_deployments_config.get("network_load_balancers", [])
        if not nlb_configurations_list:
            logger.info(f"{id}: No Network Load Balancer configurations found.")
        else:
            # ... (NLB processing logic, including resolving any instance_id_refs for its TGs) ...
            logger.info(f"{id}: Processing {len(nlb_configurations_list)} Network Load Balancer definition(s).")
            for i, nlb_cfg_entry_original in enumerate(nlb_configurations_list):
                if not isinstance(nlb_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid NLB config entry at index {i}: not a dictionary.")
                    continue
                nlb_cfg_entry = copy.deepcopy(nlb_cfg_entry_original)
                is_nlb_enabled = nlb_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_nlb_enabled:
                    logger.info(f"Skipping Network Load Balancer config '{nlb_cfg_entry.get('id', f'UnknownNLBIndex{i}')}' (disabled).")
                    continue
                nlb_id_val = nlb_cfg_entry.get("id")
                if not nlb_id_val:
                    logger.error(f"Skipping Network Load Balancer at index {i} due to missing 'id'.")
                    continue
                if "config" not in nlb_cfg_entry: nlb_cfg_entry["config"] = {}
                temp_nlb_config_block = merge_dicts(global_defaults, nlb_specific_defaults)
                merged_nlb_config_block = merge_dicts(temp_nlb_config_block, nlb_cfg_entry["config"])

                # Resolve instance_id_refs for target groups within this NLB's config
                if "target_groups" in merged_nlb_config_block:
                    for tg_def in merged_nlb_config_block.get("target_groups", []):
                        if "config" in tg_def and "targets" in tg_def["config"]:
                            for target_item in tg_def["config"].get("targets", []):
                                if isinstance(target_item, dict) and "instance_id_ref" in target_item:
                                    instance_ref_id = target_item["instance_id_ref"]
                                    if instance_ref_id in self.deployed_instance_stacks:
                                        instance_stack_obj = self.deployed_instance_stacks[instance_ref_id]
                                        if hasattr(instance_stack_obj, 'instance_id_token') and instance_stack_obj.instance_id_token:
                                            target_item["instance_id"] = instance_stack_obj.instance_id_token
                                            logger.info(f"NLB '{nlb_id_val}', TG '{tg_def.get('id')}': Resolved target instance_id_ref '{instance_ref_id}' to '{instance_stack_obj.instance_id_token}'.")
                                            target_item.pop("instance_id_ref", None)
                                        else:
                                            logger.warning(f"NLB '{nlb_id_val}', TG '{tg_def.get('id')}': Instance stack for '{instance_ref_id}' found but no 'instance_id_token'.")
                                    else:
                                        logger.warning(f"NLB '{nlb_id_val}', TG '{tg_def.get('id')}': Could not resolve target instance_id_ref '{instance_ref_id}'. Instance stack not found.")
                nlb_cfg_entry["config"] = merged_nlb_config_block

                nlb_name_for_desc = nlb_cfg_entry.get("config", {}).get("load_balancer_name", nlb_id_val)
                sanitized_nlb_id = ''.join(filter(str.isalnum, nlb_id_val)) or f"NLBDef{i}"
                nlb_nested_stack_id = f"{sanitized_nlb_id}NLBStack"
                logger.info(f"Defining NetworkLoadBalancerStack for {nlb_id_val} (Name: {nlb_name_for_desc}) -> CDK ID {nlb_nested_stack_id}.")
                try:
                    nlb_stack = NetworkLoadBalancerStack(
                        self, nlb_nested_stack_id,
                        nlb_config_entry=nlb_cfg_entry,
                        description=f"Nested Stack for Network Load Balancer: {nlb_name_for_desc}"
                    )
                    self.deployed_nlb_stacks[nlb_id_val] = nlb_stack
                    Tags.of(nlb_stack).add("ResourceType", "NetworkLoadBalancer")
                    Tags.of(nlb_stack).add("ConfigID", nlb_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate NetworkLoadBalancerStack '{nlb_nested_stack_id}': {e}", exc_info=True)

        # --- 6. Process Standalone Target Group Deployments ---
        target_group_configurations_list = ec2_deployments_config.get("target_groups", [])
        if not target_group_configurations_list:
            logger.info(f"{id}: No standalone Target Group configurations found.")
        else:
            logger.info(f"{id}: Processing {len(target_group_configurations_list)} standalone Target Group definition(s).")
            for i, tg_cfg_entry_original in enumerate(target_group_configurations_list):
                # ... (Full TG processing logic, including resolving instance_id_ref from self.deployed_instance_stacks) ...
                if not isinstance(tg_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid Target Group config entry at index {i}: not a dictionary.")
                    continue
                tg_cfg_entry = copy.deepcopy(tg_cfg_entry_original)
                is_tg_enabled = tg_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_tg_enabled:
                    logger.info(f"Skipping Target Group config '{tg_cfg_entry.get('id', f'UnknownTGIndex{i}')}' (disabled).")
                    continue
                tg_id_val = tg_cfg_entry.get("id")
                if not tg_id_val:
                    logger.error(f"Skipping Target Group at index {i} due to missing 'id'.")
                    continue
                if "config" not in tg_cfg_entry: tg_cfg_entry["config"] = {}
                temp_tg_config_block = merge_dicts(global_defaults, target_group_specific_defaults)
                merged_tg_config_block = merge_dicts(temp_tg_config_block, tg_cfg_entry["config"])

                if "targets" in merged_tg_config_block:
                    for target_def in merged_tg_config_block.get("targets", []):
                        if "instance_id_ref" in target_def:
                            instance_ref_id = target_def["instance_id_ref"]
                            if instance_ref_id in self.deployed_instance_stacks:
                                instance_stack = self.deployed_instance_stacks[instance_ref_id]
                                if hasattr(instance_stack, 'instance_id_token') and instance_stack.instance_id_token:
                                    target_def["instance_id"] = instance_stack.instance_id_token
                                    logger.info(f"Standalone TG '{tg_id_val}': Resolved target instance_id_ref '{instance_ref_id}'.")
                                    target_def.pop("instance_id_ref", None)
                                else:
                                    logger.warning(f"Standalone TG '{tg_id_val}': Instance stack for '{instance_ref_id}' found but no 'instance_id_token'.")
                            else:
                                logger.warning(f"Standalone TG '{tg_id_val}': Could not resolve target instance_id_ref '{instance_ref_id}'. Instance stack not found.")
                tg_cfg_entry["config"] = merged_tg_config_block

                tg_name_for_desc = merged_tg_config_block.get("target_group_name", tg_id_val)
                sanitized_tg_id = ''.join(filter(str.isalnum, tg_id_val)) or f"TGDef{i}"
                tg_nested_stack_id = f"{sanitized_tg_id}TargetGroupStack"
                logger.info(f"Defining TargetGroupStack for {tg_id_val} (Name: {tg_name_for_desc}) -> CDK ID {tg_nested_stack_id}.")
                try:
                    tg_stack = TargetGroupStack(
                        self, tg_nested_stack_id,
                        target_group_entry=tg_cfg_entry,
                        description=f"Nested Stack for Target Group: {tg_name_for_desc}"
                    )
                    self.deployed_target_group_stacks[tg_id_val] = tg_stack
                    Tags.of(tg_stack).add("ResourceType", "TargetGroup")
                    Tags.of(tg_stack).add("ConfigID", tg_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate TargetGroupStack '{tg_nested_stack_id}': {e}", exc_info=True)


        # --- 7. Process Auto Scaling Group Deployments ---
        asg_configurations_list = ec2_deployments_config.get("auto_scaling_groups", [])
        if not asg_configurations_list:
            logger.info(f"{id}: No Auto Scaling Group configurations found.")
        else:
            logger.info(f"{id}: Processing {len(asg_configurations_list)} Auto Scaling Group definition(s).")
            for i, asg_cfg_entry_original in enumerate(asg_configurations_list):
                # ... (Full ASG processing logic, ensuring it uses resolved_sgs_map, deployed_lt_stacks,
                #       deployed_alb_stacks, deployed_target_group_stacks for lookups) ...
                if not isinstance(asg_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid ASG config entry at index {i}: not a dictionary.")
                    continue
                asg_cfg_entry = copy.deepcopy(asg_cfg_entry_original)
                is_asg_enabled = asg_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_asg_enabled:
                    logger.info(f"Skipping Auto Scaling Group config '{asg_cfg_entry.get('id', f'UnknownASGIndex{i}')}' (disabled).")
                    continue
                asg_id_val = asg_cfg_entry.get("id")
                if not asg_id_val:
                    logger.error(f"Skipping Auto Scaling Group at index {i} due to missing 'id'.")
                    continue
                if "config" not in asg_cfg_entry: asg_cfg_entry["config"] = {}
                temp_asg_config_block = merge_dicts(global_defaults, asg_specific_defaults)
                merged_asg_config_block = merge_dicts(temp_asg_config_block, asg_cfg_entry["config"])
                asg_cfg_entry["config"] = merged_asg_config_block

                vpc_id_for_asg = merged_asg_config_block.get("vpc_id")
                if not vpc_id_for_asg:
                    logger.error(f"ASG '{asg_id_val}' requires 'vpc_id' in its config. Skipping.")
                    continue
                try:
                    vpc_lookup_id = f"{asg_id_val}VpcContextForASG"
                    vpc_for_asg = ec2.Vpc.from_lookup(self, vpc_lookup_id, vpc_id=vpc_id_for_asg)
                except Exception as e:
                    logger.error(f"Failed to lookup VPC '{vpc_id_for_asg}' for ASG '{asg_id_val}': {e}. Skipping ASG.", exc_info=True)
                    continue

                launch_template_config = merged_asg_config_block.get("launch_template", {})
                lt_ref_id = launch_template_config.get("launch_template_ref_id")
                launch_template_object: typing.Optional[ec2.ILaunchTemplate] = None
                instance_security_group_for_asg: typing.Optional[ec2.ISecurityGroup] = None

                if lt_ref_id and lt_ref_id in self.deployed_lt_stacks:
                    lt_stack_instance = self.deployed_lt_stacks[lt_ref_id]
                    if hasattr(lt_stack_instance, 'launch_template') and lt_stack_instance.launch_template:
                        launch_template_object = lt_stack_instance.launch_template
                        if hasattr(lt_stack_instance, 'managed_security_group') and lt_stack_instance.managed_security_group:
                            instance_security_group_for_asg = lt_stack_instance.managed_security_group
                            logger.info(f"ASG '{asg_id_val}': Using managed SG '{instance_security_group_for_asg.security_group_id}' from LT Stack '{lt_ref_id}'.")
                        else:
                            lt_full_config_for_sg_check = lt_stack_instance.config
                            lt_template_data_cfg = lt_full_config_for_sg_check.get("template_data", {})
                            lt_network_interfaces_cfg_val = lt_template_data_cfg.get("network_interfaces")
                            actual_ni_list_for_asg_sg_check = []
                            if isinstance(lt_network_interfaces_cfg_val, dict):
                                actual_ni_list_for_asg_sg_check = lt_network_interfaces_cfg_val.get("interfaces", [])
                            elif isinstance(lt_network_interfaces_cfg_val, list):
                                actual_ni_list_for_asg_sg_check = lt_network_interfaces_cfg_val
                            sg_id_from_lt_data_for_asg = None
                            if actual_ni_list_for_asg_sg_check and \
                                isinstance(actual_ni_list_for_asg_sg_check[0], dict) and \
                                actual_ni_list_for_asg_sg_check[0].get("groups") and \
                                isinstance(actual_ni_list_for_asg_sg_check[0]["groups"], list) and \
                                actual_ni_list_for_asg_sg_check[0]["groups"]:
                                sg_id_from_lt_data_for_asg = actual_ni_list_for_asg_sg_check[0]["groups"][0]
                            if sg_id_from_lt_data_for_asg:
                                found_sg_obj_for_asg = None
                                for logical_id_map, sg_obj_in_map in self.resolved_sgs_map.items():
                                    if hasattr(sg_obj_in_map, 'security_group_id') and sg_obj_in_map.security_group_id == sg_id_from_lt_data_for_asg:
                                        found_sg_obj_for_asg = sg_obj_in_map
                                        break
                                    elif logical_id_map == sg_id_from_lt_data_for_asg: # If sg_id_from_lt_data was a logical ref
                                        found_sg_obj_for_asg = sg_obj_in_map
                                        break
                                if found_sg_obj_for_asg:
                                    instance_security_group_for_asg = found_sg_obj_for_asg
                                    logger.info(f"ASG '{asg_id_val}': Using SG '{instance_security_group_for_asg.security_group_id}' based on LT's NI config for '{lt_ref_id}'.")
                                else:
                                    try:
                                        instance_security_group_for_asg = ec2.SecurityGroup.from_security_group_id(self, f"{asg_id_val}LtDataSgImportASG", sg_id_from_lt_data_for_asg)
                                        logger.info(f"ASG '{asg_id_val}': Imported SG '{sg_id_from_lt_data_for_asg}' directly from LT data for '{lt_ref_id}'.")
                                    except Exception as e_sg_import:
                                        logger.error(f"ASG '{asg_id_val}': Failed to import SG '{sg_id_from_lt_data_for_asg}' from LT data: {e_sg_import}")
                            if not instance_security_group_for_asg:
                                logger.warning(f"ASG '{asg_id_val}': LTStack for '{lt_ref_id}' has no 'managed_security_group' and could not determine one from LT data. This might lead to connectivity issues.")
                    else:
                        logger.error(f"LaunchTemplateStack for '{lt_ref_id}' does not have a 'launch_template' attribute or it's None. Cannot create ASG '{asg_id_val}'.")
                        continue
                else:
                    logger.error(f"Launch template reference ID '{lt_ref_id}' for ASG '{asg_id_val}' not found in deployed LTs or ref_id is missing. Skipping ASG.")
                    continue

                if not launch_template_object:
                    logger.error(f"Launch template object for ASG '{asg_id_val}' could not be resolved. Skipping.")
                    continue

                if not instance_security_group_for_asg:
                    logger.error(f"ASG '{asg_id_val}': Critical - Instance security group could not be determined from Launch Template '{lt_ref_id}'. "
                                 f"The ASG will likely fail to attach to target groups or be connectable. Skipping ASG.")
                    continue

                resolved_tgs_with_lb_sgs_and_ports: list[typing.Tuple[elbv2.ITargetGroup, typing.Optional[ec2.ISecurityGroup], typing.Optional[int]]] = []

                tg_attachment_config = merged_asg_config_block.get("target_group_attachment_config", {})
                tg_refs = tg_attachment_config.get("target_group_refs", [])

                logger.debug(f"ASG '{asg_id_val}': Attempting to resolve TGs. Deployed ALBs: {list(self.deployed_alb_stacks.keys())}")
                for tg_ref in tg_refs:
                    tg_ref_id = tg_ref.get("id")
                    if not tg_ref_id:
                        logger.warning(f"ASG '{asg_id_val}': Found a target_group_ref without an 'id'. Skipping this ref.")
                        continue

                    found_tg_object:typing.Optional[elbv2.ITargetGroup] = None
                    lb_sg_for_this_tg: typing.Optional[ec2.ISecurityGroup] = None
                    tg_port_from_config: typing.Optional[int] = None
                    original_tg_config_block = None

                    for alb_stack_key, alb_stack_instance in self.deployed_alb_stacks.items():
                        logger.debug(f"ASG '{asg_id_val}': Checking ALB '{alb_stack_key}' for TG '{tg_ref_id}'. ALB TGs: {list(alb_stack_instance.target_groups.keys()) if hasattr(alb_stack_instance, 'target_groups') else 'N/A'}")
                        if alb_stack_instance.alb and hasattr(alb_stack_instance, 'target_groups') and tg_ref_id in alb_stack_instance.target_groups:
                            found_tg_object = alb_stack_instance.target_groups[tg_ref_id]
                            if alb_stack_key in self.resolved_sgs_map:
                                lb_sg_for_this_tg = self.resolved_sgs_map[alb_stack_key]

                            for tg_entry_in_lb_cfg in alb_stack_instance.config.get("target_groups", []):
                                if tg_entry_in_lb_cfg.get("id") == tg_ref_id:
                                    original_tg_config_block = tg_entry_in_lb_cfg.get("config", {})
                                    break
                            if original_tg_config_block:
                                tg_port_from_config = original_tg_config_block.get("port")
                            logger.info(f"ASG '{asg_id_val}': Resolved TG '{tg_ref_id}' (Port: {tg_port_from_config}) from ALB '{alb_stack_key}'. LB SG: {lb_sg_for_this_tg.security_group_id if lb_sg_for_this_tg else 'N/A'}")
                            break
                    if found_tg_object:
                        resolved_tgs_with_lb_sgs_and_ports.append((found_tg_object, lb_sg_for_this_tg, tg_port_from_config))
                        continue

                    logger.warning(f"ASG '{asg_id_val}': Target Group reference ID '{tg_ref_id}' could not be resolved from any deployed ALB, NLB, or standalone TG stacks.")

                asg_name_for_desc = merged_asg_config_block.get("auto_scaling_group_name", asg_id_val)
                sanitized_asg_id = ''.join(filter(str.isalnum, asg_id_val)) or f"ASGDef{i}"
                asg_nested_stack_id = f"{sanitized_asg_id}ASGStack"
                logger.info(f"Defining AutoScalingGroupStack for {asg_id_val} (Name: {asg_name_for_desc}) -> CDK ID {asg_nested_stack_id}.")
                try:
                    asg_stack = AutoScalingGroupStack(
                        self, asg_nested_stack_id,
                        vpc=vpc_for_asg,
                        launch_template=launch_template_object,
                        instance_security_group=instance_security_group_for_asg,
                        resolved_target_groups_with_lb_sgs_and_ports=resolved_tgs_with_lb_sgs_and_ports,
                        asg_config_entry=asg_cfg_entry,
                        description=f"Nested Stack for Auto Scaling Group: {asg_name_for_desc}"
                    )
                    self.deployed_asg_stacks[asg_id_val] = asg_stack
                    Tags.of(asg_stack).add("ResourceType", "AutoScalingGroup")
                    Tags.of(asg_stack).add("ConfigID", asg_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate AutoScalingGroupStack '{asg_nested_stack_id}': {e}", exc_info=True)

        # --- 7. Process EBS Volume Deployments ---
        ebs_configurations_list = ec2_deployments_config.get("ebs_volumes", [])
        if not ebs_configurations_list:
            logger.info(f"{id}: No EBS Volume configurations found (final pass).")
        else:
            logger.info(f"{id}: Processing {len(ebs_configurations_list)} EBS Volume definition(s) (final pass).")
            for i, ebs_cfg_entry_original in enumerate(ebs_configurations_list):
                if not isinstance(ebs_cfg_entry_original, dict):
                    logger.error(f"Skipping invalid EBS config at index {i}."); continue
                ebs_cfg_entry = copy.deepcopy(ebs_cfg_entry_original)
                is_ebs_enabled = ebs_cfg_entry.get("enabled", global_defaults.get("enabled", True))
                if not is_ebs_enabled:
                    logger.info(f"Skipping EBS config '{ebs_cfg_entry.get('id', '')}' (disabled)."); continue
                ebs_id_val = ebs_cfg_entry.get("id")
                if not ebs_id_val :
                    logger.error(f"Invalid EBS config: {ebs_id_val}. Missing 'id'. Skipping."); continue
                if "config" not in ebs_cfg_entry: ebs_cfg_entry["config"] = {}
                temp_ebs_config_block = merge_dicts(global_defaults, ebs_specific_defaults)
                merged_ebs_volume_config_block = merge_dicts(temp_ebs_config_block, ebs_cfg_entry["config"])
                ebs_cfg_entry["config"] = merged_ebs_volume_config_block
                if not merged_ebs_volume_config_block.get("availability_zone") or \
                   (not merged_ebs_volume_config_block.get("size_gb") and not merged_ebs_volume_config_block.get("snapshot_id")):
                    logger.error(f"Skipping EBS '{ebs_id_val}': missing AZ, or size_gb/snapshot_id in merged config."); continue
                s_ebsid = ''.join(filter(str.isalnum, ebs_id_val)) or f"EBSDef{i}"
                ebs_nsid = f"{s_ebsid}EBSStack"
                ebs_name_desc = merged_ebs_volume_config_block.get("volume_name_tag", ebs_id_val)
                logger.info(f"Defining EbsVolumeStack for {ebs_id_val} (Name: {ebs_name_desc}) -> CDK ID '{ebs_nsid}'.")
                try:
                    ebs_stack = EbsVolumeStack(
                        self, ebs_nsid,
                        volume_config=merged_ebs_volume_config_block,
                        description=f"Nested Stack for EBS Volume: {ebs_name_desc}"
                    )
                    self.deployed_ebs_volume_stacks[ebs_id_val] = ebs_stack
                    Tags.of(ebs_stack).add("Ec2ResourceType", "EBSVolume")
                    Tags.of(ebs_stack).add("ConfigID", ebs_id_val)
                except Exception as e:
                    logger.error(f"FAILED to instantiate EbsVolumeStack '{ebs_nsid}': {e}", exc_info=True)
