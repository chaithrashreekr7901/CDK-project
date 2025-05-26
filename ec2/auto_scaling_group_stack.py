import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    Tags,
    NestedStack,
    Duration 
)
from aws_cdk.aws_elasticloadbalancingv2 import (
    IApplicationTargetGroup, 
    INetworkTargetGroup,
    ApplicationProtocol, 
    Protocol as ElbProtocol 
)
from constructs import Construct
import logging
import typing
import re # For string conversion

logger = logging.getLogger(__name__)

def pascal_to_upper_snake(pascal_case_string: str) -> str:
    """Converts a PascalCase string to UPPER_SNAKE_CASE."""
    if not pascal_case_string:
        return ""
    # Add underscore before uppercase letters (except if it's the first char or already preceded by an underscore/uppercase)
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', pascal_case_string)
    # Add underscore between lowercase/digit and uppercase
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.upper()

class AutoScalingGroupStack(NestedStack):
    def __init__(self, scope: Construct, id: str,
                 vpc: ec2.IVpc,
                 launch_template: ec2.ILaunchTemplate, 
                 instance_security_group: typing.Optional[ec2.ISecurityGroup], 
                 resolved_target_groups_with_lb_sgs_and_ports: list[typing.Tuple[elbv2.ITargetGroup, typing.Optional[ec2.ISecurityGroup], typing.Optional[int]]],
                 asg_config_entry: dict, 
                 **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        if not asg_config_entry or not asg_config_entry.get("enabled"):
            logger.info(f"Auto Scaling Group configuration for {id} is not provided or not enabled. Skipping.")
            cdk.Annotations.of(self).add_info(f"ASG {id} is not deployed as per configuration.")
            self.asg = None 
            self.auto_scaling_group_resource = None
            return

        config = asg_config_entry.get("config", {})
        asg_logical_id = asg_config_entry.get("id", "DefaultAsgId") 

        asg_name = config.get("auto_scaling_group_name")

        vpc_subnets_config = config.get("vpc_subnets_config", {})
        subnet_ids_from_config = vpc_subnets_config.get("subnet_ids", [])
        
        selected_subnets_for_asg = []
        if subnet_ids_from_config:
            logger.info(f"ASG {asg_logical_id}: Using specific subnet IDs: {subnet_ids_from_config}")
            for i, subnet_id_val in enumerate(subnet_ids_from_config):
                selected_subnets_for_asg.append(
                    ec2.Subnet.from_subnet_id(self, f"{asg_logical_id}Subnet{i}", subnet_id_val)
                )
            vpc_subnet_selection = ec2.SubnetSelection(subnets=selected_subnets_for_asg)
        elif "subnet_selection" in vpc_subnets_config:
            selection_props = vpc_subnets_config["subnet_selection"]
            subnet_type_str = selection_props.get("subnet_type", "PRIVATE_WITH_EGRESS").upper()
            subnet_type_map = {
                "PUBLIC": ec2.SubnetType.PUBLIC,
                "PRIVATE_WITH_EGRESS": ec2.SubnetType.PRIVATE_WITH_EGRESS,
                "PRIVATE_ISOLATED": ec2.SubnetType.PRIVATE_ISOLATED,
            }
            cdk_subnet_type = subnet_type_map.get(subnet_type_str)
            if not cdk_subnet_type:
                raise ValueError(f"Invalid subnet_type '{subnet_type_str}' for ASG {asg_logical_id}")
            
            logger.info(f"ASG {asg_logical_id}: Selecting subnets by type: {subnet_type_str}")
            vpc_subnet_selection = ec2.SubnetSelection(
                subnet_type=cdk_subnet_type,
                subnet_group_name=selection_props.get("subnet_group_name")
            )
        else:
            logger.info(f"ASG {asg_logical_id}: Defaulting to PRIVATE_WITH_EGRESS subnet selection.")
            vpc_subnet_selection = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        health_check_grace_period_seconds = config.get("health_check_grace_period_seconds", 300)
        health_check_type_str = config.get("health_check_type", "EC2").upper()
        
        if health_check_type_str == "EC2":
            asg_health_check = autoscaling.HealthCheck.ec2(
                grace=Duration.seconds(health_check_grace_period_seconds)
            )
        elif health_check_type_str == "ELB":
            if not resolved_target_groups_with_lb_sgs_and_ports:
                 logger.warning(f"ASG '{asg_logical_id}': Health check type is ELB, but no target groups are resolved. Health check might not function correctly.")
            asg_health_check = autoscaling.HealthCheck.elb(
                grace=Duration.seconds(health_check_grace_period_seconds)
            )
        else:
            logger.warning(f"Invalid health_check_type '{health_check_type_str}' for ASG {asg_logical_id}. Defaulting to EC2.")
            asg_health_check = autoscaling.HealthCheck.ec2(
                grace=Duration.seconds(health_check_grace_period_seconds)
            )

        default_cooldown_seconds_val = config.get("default_cooldown_seconds", 300)

        termination_policies_config = config.get("termination_policies", ["Default"])
        termination_policies = []
        for policy_name_str in termination_policies_config:
            policy_name_upper = policy_name_str.upper()
            if policy_name_upper == "DEFAULT":
                policy_enum_name = "DEFAULT" 
            elif policy_name_upper == "OLDESTLAUNCHTEMPLATE":
                policy_enum_name = "OLDEST_LAUNCH_TEMPLATE"
            elif policy_name_upper == "NEWESTINSTANCE":
                policy_enum_name = "NEWEST_INSTANCE"
            elif policy_name_upper == "OLDESTINSTANCE":
                policy_enum_name = "OLDEST_INSTANCE"
            elif policy_name_upper == "CLOSESTTONEXTINSTANCEHOUR":
                policy_enum_name = "CLOSEST_TO_NEXT_INSTANCE_HOUR"
            elif policy_name_upper == "OLDESTLAUNCHCONFIGURATION": 
                policy_enum_name = "OLDEST_LAUNCH_CONFIGURATION"
            elif policy_name_upper == "ALLOCATIONSTRATEGY": 
                 policy_enum_name = "ALLOCATION_STRATEGY"
            else:
                policy_enum_name = policy_name_upper 

            try:
                policy_enum_member = getattr(autoscaling.TerminationPolicy, policy_enum_name)
                termination_policies.append(policy_enum_member)
            except AttributeError:
                logger.warning(f"Unsupported termination policy: {policy_name_str} (tried as {policy_enum_name}) for ASG {asg_logical_id}. It will be ignored.")
                cdk.Annotations.of(self).add_warning(f"Unsupported termination policy: {policy_name_str} for ASG {asg_logical_id}.")

        if not termination_policies: 
            logger.info(f"No valid termination policies found for ASG {asg_logical_id}, defaulting to DEFAULT.")
            termination_policies.append(autoscaling.TerminationPolicy.DEFAULT)
        
        self.asg = autoscaling.AutoScalingGroup(
            self,
            f"{asg_logical_id}Resource", 
            auto_scaling_group_name=asg_name,
            vpc=vpc,
            launch_template=launch_template,
            min_capacity=config.get("min_capacity"),
            max_capacity=config.get("max_capacity"),
            desired_capacity=config.get("desired_capacity", config.get("min_capacity")),
            vpc_subnets=vpc_subnet_selection,
            health_check=asg_health_check,
            cooldown=Duration.seconds(default_cooldown_seconds_val), 
            new_instances_protected_from_scale_in=config.get("new_instances_protected_from_scale_in", False),
            termination_policies=termination_policies,
            capacity_rebalance=config.get("capacity_rebalancing", False),
        )
        Tags.of(self.asg).add("Name", asg_name if asg_name else f"{cdk.Aws.STACK_NAME}-{asg_logical_id}")

        target_group_arns_for_asg = []
        if resolved_target_groups_with_lb_sgs_and_ports:
            if not instance_security_group:
                logger.error(f"ASG '{asg_logical_id}': Instance security group is missing. Cannot manually configure ingress rules for target groups.")
            else:
                for tg_obj, lb_sg_obj, tg_port_from_config in resolved_target_groups_with_lb_sgs_and_ports:
                    try:
                        target_port_to_open = 8080 
                        if tg_port_from_config is not None:
                            try:
                                target_port_to_open = int(tg_port_from_config)
                            except ValueError:
                                logger.error(f"Invalid port value '{tg_port_from_config}' for TG '{tg_obj.node.id}'. Defaulting to {target_port_to_open}.")
                        else: 
                            logger.warning(f"ASG '{asg_logical_id}': Port for TG '{tg_obj.node.id}' was not provided. Defaulting to port {target_port_to_open} for SG rule. Please verify.")
                        
                        tg_node_id_for_desc_parts = tg_obj.node.id.split('/')
                        description_tg_part = tg_node_id_for_desc_parts[-1][:20] if tg_node_id_for_desc_parts and tg_node_id_for_desc_parts[-1] else "UnknownTG"
                        description_lb_part = "LB"
                        if lb_sg_obj and hasattr(lb_sg_obj, 'node'):
                            lb_sg_node_id_parts = lb_sg_obj.node.id.split('/')
                            description_lb_part = lb_sg_node_id_parts[-1][:20] if lb_sg_node_id_parts and lb_sg_node_id_parts[-1] else "UnknownLB_SG"
                        
                        rule_description = f"Allow from {description_lb_part} to TG {description_tg_part} on {target_port_to_open}"

                        if lb_sg_obj: 
                            logger.info(f"ASG '{asg_logical_id}': Adding ingress from LB SG {lb_sg_obj.security_group_id} to Instance SG {instance_security_group.security_group_id} on port {target_port_to_open}")
                            instance_security_group.add_ingress_rule(
                                peer=lb_sg_obj,
                                connection=ec2.Port.tcp(target_port_to_open),
                                description=rule_description
                            )
                        else: 
                            logger.info(f"ASG '{asg_logical_id}': TG {description_tg_part} has no associated LB SG. Instances must allow traffic from NLB subnets or relevant sources on port {target_port_to_open}.")
                        
                        target_group_arns_for_asg.append(tg_obj.target_group_arn)
                        logger.info(f"ASG {asg_logical_id}': Prepared to attach to TG ARN '{tg_obj.target_group_arn}'.")

                    except Exception as e:
                        logger.error(f"ASG '{asg_logical_id}': Failed to prepare attachment or configure SG for TG '{tg_obj.node.id if hasattr(tg_obj, 'node') else 'UnknownTG'}': {e}", exc_info=True)
        
            if target_group_arns_for_asg:
                cfn_asg = self.asg.node.default_child
                if isinstance(cfn_asg, autoscaling.CfnAutoScalingGroup):
                    cfn_asg.add_property_override("TargetGroupARNs", target_group_arns_for_asg)
                    logger.info(f"ASG '{asg_logical_id}': Set TargetGroupARNs on CfnAutoScalingGroup: {target_group_arns_for_asg}")
                else:
                    logger.error(f"ASG '{asg_logical_id}': Could not get CfnAutoScalingGroup to set TargetGroupARNs.")
        else:
            logger.info(f"No resolved target groups provided for ASG {asg_logical_id}.")


        for policy_conf in config.get("scaling_policies", []):
            policy_id = policy_conf.get("id")
            if not policy_conf.get("enabled", True) or not policy_id:
                logger.info(f"Scaling policy '{policy_id}' for ASG {asg_logical_id} is disabled or has no ID. Skipping.")
                continue
            policy_type = policy_conf.get("policy_type", "").upper()
            if policy_type == "TARGET_TRACKING":
                tt_config = policy_conf.get("target_tracking_config", {})
                target_value = tt_config.get("target_value")
                predefined_metric_config_str = tt_config.get("predefined_metric_type", "") # Original config string
                if target_value is None: 
                    logger.warning(f"Target tracking policy '{policy_id}' for ASG {asg_logical_id} is missing 'target_value'. Skipping.")
                    continue
                
                policy_cooldown_duration = Duration.seconds(tt_config.get("cooldown_seconds", 
                                                                      tt_config.get("scale_out_cooldown_seconds", default_cooldown_seconds_val)))
                disable_scale_in_val = tt_config.get("disable_scale_in", False)

                # Convert PascalCase config string to UPPER_SNAKE_CASE for enum lookup
                predefined_metric_enum_str = pascal_to_upper_snake(predefined_metric_config_str)
                cdk_predefined_metric = None
                if predefined_metric_enum_str:
                    try:
                        cdk_predefined_metric = getattr(autoscaling.PredefinedMetric, predefined_metric_enum_str)
                    except AttributeError:
                        logger.error(f"Invalid predefined_metric_type '{predefined_metric_config_str}' (mapped to '{predefined_metric_enum_str}') for policy '{policy_id}'. Skipping.")
                        continue
                else:
                    logger.error(f"predefined_metric_type is empty for policy '{policy_id}'. Skipping.")
                    continue
                
                # Common properties for TargetTrackingScalingPolicy
                common_policy_props = {
                    "auto_scaling_group": self.asg,
                    "target_value": float(target_value),
                    "cooldown": policy_cooldown_duration,
                    "disable_scale_in": disable_scale_in_val
                }

                if predefined_metric_config_str == "ALBRequestCountPerTarget":
                    alb_tg_for_metric = None
                    resource_label = None
                    for tg, _lb_sg, _port in resolved_target_groups_with_lb_sgs_and_ports:
                        if isinstance(tg, IApplicationTargetGroup): 
                            alb_tg_for_metric = tg
                            # Construct resource_label from TG ARN and LB ARN
                            # Example: app/my-load-balancer/LBFriendlyID/targetgroup/my-target-group/TGFriendlyID
                            if hasattr(tg, 'target_group_load_balancer_arns') and tg.target_group_load_balancer_arns and len(tg.target_group_load_balancer_arns) > 0:
                                # Correctly parse ALB ARN to get the part after 'loadbalancer/'
                                lb_arn_path = tg.target_group_load_balancer_arns[0].split(':loadbalancer/',1)[1]
                                # Correctly parse TG ARN to get the part after 'targetgroup/'
                                tg_arn_path = tg.target_group_arn.split(':targetgroup/',1)[1]
                                resource_label = f"{lb_arn_path}/targetgroup/{tg_arn_path}"
                            else:
                                logger.warning(f"Could not determine full resourceLabel for ALBRequestCountPerTarget for TG {tg.node.id} as target_group_load_balancer_arns is missing or empty.")
                            break 
                    
                    if alb_tg_for_metric and resource_label:
                        autoscaling.TargetTrackingScalingPolicy(self, f"{policy_id}Policy",
                            **common_policy_props,
                            predefined_metric=cdk_predefined_metric, # Should be ALB_REQUEST_COUNT_PER_TARGET
                            resource_label=resource_label
                        )
                        logger.info(f"ASG {asg_logical_id}: Added ALBRequestCountPerTarget tracking policy '{policy_id}' for TG {alb_tg_for_metric.node.id}.")
                    else:
                        logger.warning(f"ALBRequestCountPerTarget policy '{policy_id}' for ASG {asg_logical_id} specified, but no suitable Application Target Group or resource label found. Skipping.")
                elif cdk_predefined_metric: # For other predefined metrics like CPU, Network
                    autoscaling.TargetTrackingScalingPolicy(self, f"{policy_id}Policy",
                        **common_policy_props,
                        predefined_metric=cdk_predefined_metric
                    )
                    logger.info(f"ASG {asg_logical_id}: Added {predefined_metric_config_str} target tracking policy '{policy_id}'.")
                else:
                    # This case should be caught by earlier checks on cdk_predefined_metric
                    logger.warning(f"Could not create target tracking policy for '{predefined_metric_config_str}'.")

            elif policy_type == "STEP_SCALING":
                logger.warning(f"STEP_SCALING policy type for '{policy_id}' in ASG {asg_logical_id} is not fully implemented in this basic stack. Manual setup or enhancements needed.")
            elif policy_type == "SIMPLE_SCALING":
                logger.warning(f"SIMPLE_SCALING policy type for '{policy_id}' in ASG {asg_logical_id} is legacy and not recommended. Skipping.")
            else:
                logger.warning(f"Unknown scaling policy_type '{policy_type}' for '{policy_id}' in ASG {asg_logical_id}.")

        for scheduled_action_conf in config.get("scheduled_actions", []):
            action_id = scheduled_action_conf.get("id")
            if not scheduled_action_conf.get("enabled", True) or not action_id:
                logger.info(f"Scheduled action '{action_id}' for ASG {asg_logical_id} is disabled or has no ID. Skipping.")
                continue
            schedule_expression = scheduled_action_conf.get("schedule")
            if not schedule_expression:
                logger.warning(f"Scheduled action '{action_id}' for ASG {asg_logical_id} is missing 'schedule'. Skipping.")
                continue
            self.asg.scale_on_schedule(
                action_id, 
                schedule=autoscaling.Schedule.expression(schedule_expression),
                time_zone=scheduled_action_conf.get("time_zone"), 
                min_capacity=scheduled_action_conf.get("min_capacity"),
                max_capacity=scheduled_action_conf.get("max_capacity"),
                desired_capacity=scheduled_action_conf.get("desired_capacity")
            )
            logger.info(f"ASG {asg_logical_id}: Added scheduled action '{action_id}'.")

        for hook_conf in config.get("lifecycle_hooks", []):
            hook_id = hook_conf.get("id")
            if not hook_conf.get("enabled", True) or not hook_id: 
                logger.info(f"Lifecycle hook '{hook_id}' for ASG {asg_logical_id} is disabled or has no ID. Skipping.")
                continue
            transition_str = hook_conf.get("lifecycle_transition")
            if not transition_str:
                logger.warning(f"Lifecycle hook '{hook_id}' for ASG {asg_logical_id} is missing 'lifecycle_transition'. Skipping.")
                continue
            lifecycle_transition_map = {
                "autoscaling:EC2_INSTANCE_LAUNCHING": autoscaling.LifecycleTransition.INSTANCE_LAUNCHING,
                "autoscaling:EC2_INSTANCE_TERMINATING": autoscaling.LifecycleTransition.INSTANCE_TERMINATING,
            }
            cdk_transition = lifecycle_transition_map.get(transition_str)
            if not cdk_transition:
                logger.warning(f"Invalid lifecycle_transition '{transition_str}' for hook '{hook_id}' in ASG {asg_logical_id}. Skipping.")
                continue
            notification_target_arn = hook_conf.get("notification_target_arn")
            notification_target_obj = None 
            if notification_target_arn:
                logger.warning(f"Lifecycle hook '{hook_id}' has notification_target_arn. Dynamic import of INotificationTarget from ARN is complex. Pass the object or use L1 for full ARN support.")
            role_arn_for_hook = hook_conf.get("role_arn")
            iam_role_for_hook = None
            if role_arn_for_hook:
                iam_role_for_hook = iam.Role.from_role_arn(self, f"{hook_id}NotificationRoleImport", role_arn=role_arn_for_hook)
            default_result_str = hook_conf.get("default_result", "CONTINUE").upper()
            cdk_default_result = getattr(autoscaling.DefaultResult, default_result_str, autoscaling.DefaultResult.CONTINUE)
            autoscaling.LifecycleHook(
                self,
                hook_id, 
                auto_scaling_group=self.asg,
                lifecycle_hook_name=hook_conf.get("lifecycle_hook_name"),
                lifecycle_transition=cdk_transition,
                default_result=cdk_default_result,
                heartbeat_timeout=Duration.seconds(hook_conf["heartbeat_timeout_seconds"]) if hook_conf.get("heartbeat_timeout_seconds") is not None else None,
                notification_metadata=hook_conf.get("notification_metadata"),
                notification_target=notification_target_obj, 
                role=iam_role_for_hook
            )
            logger.info(f"ASG {asg_logical_id}: Added lifecycle hook '{hook_id}'.")

        for tag_info in config.get("tags", []):
            key = tag_info.get("key")
            value = tag_info.get("value")
            if key and value:
                Tags.of(self.asg).add(key, value) 
                logger.info(f"ASG {asg_logical_id}: Added tag '{key}':'{value}'. Propagation is default for ASG L2 construct tags.")
                if tag_info.get("propagate_at_launch") == False:
                     cdk.Annotations.of(self).add_warning(f"ASG Tag '{key}': Config requests propagate_at_launch=False. "
                                                         f"CDK L2 ASG tags propagate by default. For explicit non-propagation, "
                                                         f"consider using L1 CfnAutoScalingGroup or ensure instance tagging handles this.")
            else:
                logger.warning(f"Invalid tag entry for ASG {asg_logical_id}: {tag_info}")

        cdk.CfnOutput(self, f"{asg_logical_id}NameOutput", value=self.asg.auto_scaling_group_name)
        cdk.CfnOutput(self, f"{asg_logical_id}ArnOutput", value=self.asg.auto_scaling_group_arn)
        
        self.auto_scaling_group_resource = self.asg