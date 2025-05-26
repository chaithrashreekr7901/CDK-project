# CRMP-PROJECT/cdk_project/ec2/target_group_stack.py
import logging
import typing
from aws_cdk import (
    NestedStack, Stack, Duration, Tags, CfnOutput, RemovalPolicy, Aws,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as elbv2_targets,
    aws_lambda as lambda_ # For Lambda targets
)
from constructs import Construct

logger = logging.getLogger(__name__)

def parse_duration_optional(seconds: typing.Optional[typing.Any], default_if_none: bool = False, default_value_seconds: int = 30) -> typing.Optional[Duration]:
    if seconds is not None:
        try:
            return Duration.seconds(int(seconds))
        except ValueError:
            logger.warning(f"Invalid duration value '{seconds}', cannot convert to int. Returning None or default.")
    if default_if_none:
        return Duration.seconds(default_value_seconds)
    return None

class TargetGroupStack(NestedStack): # Ensure this class name is correct
    target_group: elbv2.ITargetGroup 

    def __init__(self, scope: Construct, construct_id: str,
                 target_group_entry: dict, 
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not target_group_entry.get("enabled", False): 
            logger.info(f"Target Group configuration '{target_group_entry.get('id')}' is disabled. Skipping.")
            self.target_group = None # type: ignore
            return

        config_id = target_group_entry.get("id")
        config = target_group_entry.get("config", {})

        if not config_id:
            raise ValueError("Target Group entry must have an 'id'.")
        if not config:
            raise ValueError(f"Target Group '{config_id}' has no 'config' block.")

        logger.info(f"TargetGroupStack '{construct_id}': Initializing for Target Group ID '{config_id}' from JSON.")

        vpc_id = config.get("vpc_id")
        if not vpc_id:
            raise ValueError(f"Target Group '{config_id}': 'vpc_id' is required.")
        vpc = ec2.Vpc.from_lookup(self, f"{config_id}VpcLookup", vpc_id=vpc_id)

        target_group_name = config.get("target_group_name") 
        port = config.get("port")
        if port is None: 
            raise ValueError(f"Target Group '{config_id}': 'port' is required.")
        
        protocol_str = config.get("protocol", "HTTP").upper() 
        target_type_str = config.get("target_type", "INSTANCE").upper()
        
        target_type_map = {
            "INSTANCE": elbv2.TargetType.INSTANCE,
            "IP": elbv2.TargetType.IP,
            "LAMBDA": elbv2.TargetType.LAMBDA, 
            "ALB": elbv2.TargetType.ALB        
        }
        cdk_target_type = target_type_map.get(target_type_str)
        if not cdk_target_type:
            raise ValueError(f"Target Group '{config_id}': Invalid 'target_type': {target_type_str}")

        deregistration_delay = parse_duration_optional(config.get("deregistration_delay_seconds"))

        hc_props = None
        hc_config = config.get("health_check", {})
        if hc_config.get("enabled", True): 
            hc_protocol_str = hc_config.get("protocol", "HTTP" if config.get("load_balancer_type") == "APPLICATION" else "TCP").upper()
            
            hc_protocol_map = {
                "HTTP": elbv2.Protocol.HTTP,
                "HTTPS": elbv2.Protocol.HTTPS,
                "TCP": elbv2.Protocol.TCP
            }
            cdk_hc_protocol = hc_protocol_map.get(hc_protocol_str)
            if not cdk_hc_protocol:
                 logger.warning(f"TG '{config_id}': Invalid health check protocol '{hc_protocol_str}'. Defaulting appropriately.")
                 cdk_hc_protocol = elbv2.Protocol.HTTP if config.get("load_balancer_type") == "APPLICATION" else elbv2.Protocol.TCP

            hc_props = elbv2.HealthCheck(
                enabled=True,
                protocol=cdk_hc_protocol,
                port=hc_config.get("port", hc_config.get("health_check_port_override", "traffic-port")), 
                path=hc_config.get("path") if cdk_hc_protocol in [elbv2.Protocol.HTTP, elbv2.Protocol.HTTPS] else None,
                interval=parse_duration_optional(hc_config.get("interval_seconds")), 
                timeout=parse_duration_optional(hc_config.get("timeout_seconds")),     
                healthy_threshold_count=hc_config.get("healthy_threshold_count"),       
                unhealthy_threshold_count=hc_config.get("unhealthy_threshold_count"),   
                healthy_http_codes=hc_config.get("matcher_http_codes") if cdk_hc_protocol in [elbv2.Protocol.HTTP, elbv2.Protocol.HTTPS] else None
            )

        lb_type = config.get("load_balancer_type", "").upper()
        
        if lb_type == "APPLICATION":
            alb_protocol_map = {"HTTP": elbv2.ApplicationProtocol.HTTP, "HTTPS": elbv2.ApplicationProtocol.HTTPS}
            cdk_alb_protocol = alb_protocol_map.get(protocol_str)
            if not cdk_alb_protocol:
                raise ValueError(f"Target Group '{config_id}': Invalid 'protocol' ('{protocol_str}') for APPLICATION load_balancer_type.")

            alb_proto_ver_str = config.get("protocol_version", "HTTP1").upper()
            alb_proto_ver_map = {"HTTP1": elbv2.ApplicationProtocolVersion.HTTP1, "GRPC": elbv2.ApplicationProtocolVersion.GRPC}
            cdk_alb_proto_ver = alb_proto_ver_map.get(alb_proto_ver_str)

            app_target_group = elbv2.ApplicationTargetGroup(self, "Resource",
                vpc=vpc,
                target_group_name=target_group_name,
                port=int(port),
                protocol=cdk_alb_protocol,
                protocol_version=cdk_alb_proto_ver,
                target_type=cdk_target_type,
                deregistration_delay=deregistration_delay,
                health_check=hc_props,
            )
            self.target_group = app_target_group

            stickiness_conf = config.get("stickiness", {})
            if stickiness_conf.get("enabled", False) and stickiness_conf.get("type", "").upper() == "APPLICATION_COOKIE":
                duration = parse_duration_optional(stickiness_conf.get("cookie_duration_seconds"))
                cookie_name = stickiness_conf.get("cookie_name")
                if duration and cookie_name:
                    app_target_group.enable_cookie_stickiness(duration, cookie_name)
            
            lb_algo = config.get("load_balancing_algorithm_type")
            if lb_algo: 
                # app_target_group.set_attribute("load_balancing.algorithm.type", lb_algo.lower().replace("_","-"))
                app_target_group.set_attribute("load_balancing.algorithm.type", lb_algo.lower())

        elif lb_type == "NETWORK":
            nlb_protocol_map = {
                "TCP": elbv2.Protocol.TCP, "UDP": elbv2.Protocol.UDP,
                "TLS": elbv2.Protocol.TLS, "TCP_UDP": elbv2.Protocol.TCP_UDP
            }
            cdk_nlb_protocol = nlb_protocol_map.get(protocol_str)
            if not cdk_nlb_protocol:
                raise ValueError(f"Target Group '{config_id}': Invalid 'protocol' ('{protocol_str}') for NETWORK load_balancer_type.")

            net_target_group = elbv2.NetworkTargetGroup(self, "Resource",
                vpc=vpc,
                target_group_name=target_group_name,
                port=int(port),
                protocol=cdk_nlb_protocol,
                target_type=cdk_target_type,
                deregistration_delay=deregistration_delay,
                health_check=hc_props,
                preserve_client_ip=config.get("preserve_client_ip") if cdk_nlb_protocol in [elbv2.Protocol.TCP, elbv2.Protocol.TLS] else None
            )
            self.target_group = net_target_group
        else:
            raise ValueError(f"Target Group '{config_id}': Invalid or missing 'load_balancer_type'. Must be 'APPLICATION' or 'NETWORK'.")

        for attr in config.get("attributes", []):
            if "key" in attr and "value" in attr:
                self.target_group.set_attribute(str(attr["key"]), str(attr["value"]))
        
        targets_list = config.get("targets", [])
        for i, target_spec in enumerate(targets_list):
            try:
                if cdk_target_type == elbv2.TargetType.INSTANCE:
                    instance_id = target_spec.get("instance_id")
                    if not instance_id: 
                        logger.warning(f"TG '{config_id}': Skipping instance target, missing 'instance_id': {target_spec}")
                        continue
                    
                    self.target_group.add_target(elbv2_targets.InstanceIdTarget(instance_id))
                    # # For Application Load Balancer Target Groups
                    # if lb_type == "APPLICATION":
                    #     self.target_group.add_target(elbv2_targets.InstanceIdTarget(instance_id))
                    # # For Network Load Balancer Target Groups (Requires CDK Upgrade for from_instance_id)
                    # elif lb_type == "NETWORK":
                    #     instance_lookup_id = f"{config_id}TargetInstanceImport{i}"
                    #     # This line requires a CDK version that supports from_instance_id()
                    #     instance_obj = ec2.Instance.from_instance_id(self, instance_lookup_id, instance_id)
                    #     self.target_group.add_target(elbv2_targets.InstanceIdTarget(instance_id))
                        
                elif cdk_target_type == elbv2.TargetType.IP:
                    ip_address = target_spec.get("ip_address")
                    if not ip_address:
                        logger.warning(f"TG '{config_id}': Skipping IP target, missing 'ip_address': {target_spec}")
                        continue
                    self.target_group.add_target(elbv2_targets.IpTarget(
                        ip_address=ip_address,
                        port=target_spec.get("port"), 
                        availability_zone=target_spec.get("availability_zone") 
                    ))
                elif cdk_target_type == elbv2.TargetType.LAMBDA and lb_type == "APPLICATION":
                    lambda_arn = target_spec.get("lambda_function_arn")
                    if not lambda_arn:
                        logger.warning(f"TG '{config_id}': Skipping Lambda target, missing 'lambda_function_arn': {target_spec}")
                        continue
                    lambda_import_id = f"{config_id}TargetLambdaImport{i}"
                    fn = lambda_.Function.from_function_arn(self, lambda_import_id, lambda_arn)
                    self.target_group.add_target(elbv2_targets.LambdaTarget(fn))
                elif cdk_target_type == elbv2.TargetType.ALB and lb_type == "NETWORK":
                    alb_arn = target_spec.get("alb_arn")
                    if not alb_arn:
                        logger.warning(f"TG '{config_id}': Skipping ALB target, missing 'alb_arn': {target_spec}")
                        continue
                    alb_import_id = f"{config_id}TargetAlbImport{i}"
                    alb_sg_id = target_spec.get("security_group_id_of_alb_target") 
                    if not alb_sg_id:
                         raise ValueError(f"TG '{config_id}': 'security_group_id_of_alb_target' is required when targeting an ALB for an NLB Target Group.")

                    imported_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
                        self, alb_import_id,
                        load_balancer_arn=alb_arn,
                        security_group_id=alb_sg_id 
                    )
                    self.target_group.add_target(elbv2_targets.AlbTarget(imported_alb, target_spec.get("port",80)))
                else:
                    logger.warning(f"TG '{config_id}': Unsupported target specification or combination for LB type '{lb_type}' and TG type '{target_type_str}': {target_spec}")
            except Exception as e:
                logger.error(f"Error adding target to TG '{config_id}': {target_spec}. Error: {e}", exc_info=True)

        CfnOutput(self, "TargetGroupArnOutput", value=self.target_group.target_group_arn)
        CfnOutput(self, "TargetGroupNameOutput", value=self.target_group.target_group_name)
        if target_group_name: 
             CfnOutput(self, "TargetGroupPhysicalNameOutput", value=target_group_name)
