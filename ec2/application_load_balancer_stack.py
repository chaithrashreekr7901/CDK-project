# CRMP-PROJECT/cdk_project/ec2/application_load_balancer_stack.py
import logging
import typing
from aws_cdk import (
    NestedStack, Stack, Duration, Tags, CfnOutput, RemovalPolicy, Aws,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as elbv2_targets, # For ALB target types
    aws_s3 as s3,
    aws_certificatemanager as acm,
    aws_lambda as lambda_ # For Lambda targets
)
from constructs import Construct

logger = logging.getLogger(__name__)

def parse_duration_optional(seconds: typing.Optional[typing.Any], default_if_none: bool = False, default_value_seconds: int = 60) -> typing.Optional[Duration]:
    if seconds is not None:
        try:
            return Duration.seconds(int(seconds))
        except ValueError:
            logger.warning(f"Invalid duration value '{seconds}', cannot convert to int. Returning None or default.")
    if default_if_none:
        return Duration.seconds(default_value_seconds)
    return None

class ApplicationLoadBalancerStack(NestedStack): # Ensure this class name is exactly "ApplicationLoadBalancerStack"
    alb: elbv2.ApplicationLoadBalancer
    listeners: typing.Dict[str, elbv2.ApplicationListener]
    target_groups: typing.Dict[str, elbv2.IApplicationTargetGroup]

    def __init__(self, scope: Construct, construct_id: str,
                 alb_config_entry: dict, # Expects one item from the "application_load_balancers" list
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not alb_config_entry.get("enabled", False):
            logger.info(f"ALB configuration '{alb_config_entry.get('id')}' is disabled. Skipping.")
            # Initialize to prevent potential attribute errors if accessed later when disabled
            self.alb = None # type: ignore 
            self.listeners = {}
            self.target_groups = {}
            return

        self.config = alb_config_entry["config"]
        self.alb_logical_id = alb_config_entry["id"]
        self.listeners = {}
        self.target_groups = {}
        
        vpc_id = self.config.get("vpc_id")
        if not vpc_id:
            raise ValueError(f"ALB '{self.alb_logical_id}': 'vpc_id' is required.")
        
        vpc = ec2.Vpc.from_lookup(self, f"{self.alb_logical_id}VpcContext", vpc_id=vpc_id)

        alb_security_group = None
        existing_sg_ids = self.config.get("security_group_ids", [])
        custom_sg_config = self.config.get("custom_security_group", {})

        if existing_sg_ids:
            if len(existing_sg_ids) > 1:
                 logger.warning(f"ALB '{self.alb_logical_id}': Multiple existing_sg_ids provided. Using the first one: {existing_sg_ids[0]}.")
            try:
                alb_security_group = ec2.SecurityGroup.from_security_group_id(self, f"{self.alb_logical_id}ImportedSg", existing_sg_ids[0])
                logger.info(f"ALB '{self.alb_logical_id}': Using existing Security Group ID {existing_sg_ids[0]}.")
            except Exception as e:
                logger.error(f"Failed to import SG {existing_sg_ids[0]} for ALB '{self.alb_logical_id}': {e}. CDK will create a default SG if none is defined in custom_security_group.")
                alb_security_group = None 

        if not alb_security_group and custom_sg_config.get("enabled", True): 
            sg_name = custom_sg_config.get("name", f"{self.alb_logical_id}-sg")
            alb_security_group = ec2.SecurityGroup(self, f"{self.alb_logical_id}AlbSg",
                vpc=vpc,
                description=custom_sg_config.get("description", f"SG for ALB {self.alb_logical_id}"),
                security_group_name=sg_name,
                allow_all_outbound=custom_sg_config.get("allow_all_outbound", True)
            )
            Tags.of(alb_security_group).add("Name", sg_name)
            for rule_conf in custom_sg_config.get("ingress_rules", []):
                try:
                    peer = self._parse_sg_peer(rule_conf.get("peer_type"), rule_conf.get("peer_value"))
                    connection = self._parse_sg_connection(
                        rule_conf.get("protocol"), rule_conf.get("port"), 
                        rule_conf.get("from_port"), rule_conf.get("to_port")
                    )
                    if peer and connection:
                        alb_security_group.add_ingress_rule(peer, connection, rule_conf.get("description"))
                except Exception as e:
                    logger.error(f"Failed to add SG rule {rule_conf} to ALB SG '{sg_name}': {e}")

        subnet_selection: ec2.SubnetSelection
        provided_subnet_ids = self.config.get("subnet_ids")
        if provided_subnet_ids and isinstance(provided_subnet_ids, list):
            logger.info(f"ALB '{self.alb_logical_id}': Looking up explicitly provided subnet_ids.")
            selected_subnets: typing.List[ec2.ISubnet] = []
            for i, subnet_id_str in enumerate(provided_subnet_ids):
                try:
                    subnet_lookup_id = f"{self.alb_logical_id}Subnet{i}" # Unique ID for each subnet lookup
                    selected_subnets.append(ec2.Subnet.from_subnet_id(self, subnet_lookup_id, subnet_id_str))
                except Exception as e:
                    logger.error(f"ALB '{self.alb_logical_id}': Failed to look up subnet_id '{subnet_id_str}'. Error: {e}. Skipping this subnet.")
            
            if not selected_subnets: # If all lookups failed or list was empty after filtering
                raise ValueError(f"ALB '{self.alb_logical_id}': No valid subnets could be looked up from provided subnet_ids: {provided_subnet_ids}")
            
            subnet_selection = ec2.SubnetSelection(subnets=selected_subnets) # Use the list of ISubnet objects

        elif self.config.get("subnet_selection"):
            sel_conf = self.config["subnet_selection"]
            subnet_type_str = sel_conf.get("subnet_type", "PUBLIC").upper()
            subnet_type_map = {
                "PUBLIC": ec2.SubnetType.PUBLIC,
                "PRIVATE_WITH_EGRESS": ec2.SubnetType.PRIVATE_WITH_EGRESS,
                "PRIVATE_ISOLATED": ec2.SubnetType.PRIVATE_ISOLATED,
            }
            cdk_subnet_type = subnet_type_map.get(subnet_type_str, ec2.SubnetType.PUBLIC)
            logger.info(f"ALB '{self.alb_logical_id}': Selecting subnets by type '{subnet_type_str}'.")
            subnet_selection = ec2.SubnetSelection(
                subnet_type=cdk_subnet_type,
                subnet_group_name=sel_conf.get("subnet_group_name")
            )
        else: 
            default_type = ec2.SubnetType.PUBLIC if self.config.get("internet_facing", False) else ec2.SubnetType.PRIVATE_WITH_EGRESS
            logger.info(f"ALB '{self.alb_logical_id}': Using default subnet selection (type: '{default_type.value}').")
            subnet_selection = ec2.SubnetSelection(subnet_type=default_type)
        
        if not subnet_selection: # Should be caught by earlier logic if subnet_ids was provided but failed
            raise ValueError(f"ALB '{self.alb_logical_id}': Subnet configuration is missing or invalid, resulting in no subnet selection.")

        ip_address_type_str = self.config.get("ip_address_type", "IPV4").upper()
        ip_type = elbv2.IpAddressType.DUAL_STACK if ip_address_type_str == "DUALSTACK" else elbv2.IpAddressType.IPV4

        self.alb = elbv2.ApplicationLoadBalancer(self, f"{self.alb_logical_id}Resource",
            vpc=vpc,
            load_balancer_name=self.config.get("load_balancer_name"),
            internet_facing=self.config.get("internet_facing", False),
            ip_address_type=ip_type,
            security_group=alb_security_group, 
            vpc_subnets=subnet_selection, 
            deletion_protection=self.config.get("deletion_protection", False),
            http2_enabled=self.config.get("http2_enabled", True),
            idle_timeout=parse_duration_optional(self.config.get("idle_timeout_seconds")),
            desync_mitigation_mode=elbv2.DesyncMitigationMode(self.config.get("desync_mitigation_mode", "DEFENSIVE").upper()) \
                if self.config.get("desync_mitigation_mode") else None,
            drop_invalid_header_fields=self.config.get("drop_invalid_header_fields")
        )

        if self.config.get("preserve_host_header") is not None:
            self.alb.set_attribute("routing.http.preserve_host_header.enabled", str(self.config["preserve_host_header"]).lower())
        if self.config.get("xff_header_processing_mode"):
             self.alb.set_attribute("routing.http.xff_header_processing.mode", self.config["xff_header_processing_mode"].lower())
        
        custom_attributes = self.config.get("attributes", [])
        for attr in custom_attributes:
            if "key" in attr and "value" in attr:
                self.alb.set_attribute(attr["key"], attr["value"])

        access_logs_config = self.config.get("access_logs", {})
        if access_logs_config.get("enabled", False) and access_logs_config.get("s3_bucket_name"):
            try:
                access_log_bucket = s3.Bucket.from_bucket_name(self, f"{self.alb_logical_id}AccessLogBucket", access_logs_config["s3_bucket_name"])
                self.alb.log_access_logs(access_log_bucket, access_logs_config.get("s3_prefix"))
            except Exception as e:
                logger.error(f"Failed to configure access logs for ALB '{self.alb_logical_id}': {e}")
        
        waf_acl_arn = self.config.get("waf_acl_arn")
        if waf_acl_arn:
            elbv2.CfnWebACLAssociation(self, f"{self.alb_logical_id}WafAssociation",
                resource_arn=self.alb.load_balancer_arn,
                web_acl_arn=waf_acl_arn
            )
            logger.info(f"ALB '{self.alb_logical_id}': Associated with WAF ACL '{waf_acl_arn}'.")

        for tg_entry in self.config.get("target_groups", []):
            tg_id = tg_entry.get("id")
            if not tg_id or not tg_entry.get("enabled", True):
                logger.info(f"Skipping target group: ID missing or disabled: {tg_id}")
                continue
            
            tg_config = tg_entry.get("config", {}) # Ensure 'config' key is accessed
            tg_vpc_id = tg_config.get("vpc_id", vpc_id)
            tg_vpc = vpc if tg_vpc_id == vpc_id else ec2.Vpc.from_lookup(self, f"{self.alb_logical_id}{tg_id}VpcCtx", vpc_id=tg_vpc_id)

            protocol_str = tg_config.get("protocol", "HTTP").upper()
            tg_protocol = elbv2.ApplicationProtocol.HTTP if protocol_str == "HTTP" else elbv2.ApplicationProtocol.HTTPS
            
            protocol_version_str = tg_config.get("protocol_version", "HTTP1").upper()
            tg_proto_ver = elbv2.ApplicationProtocolVersion.HTTP1 if protocol_version_str=="HTTP1" else elbv2.ApplicationProtocolVersion.GRPC if protocol_version_str=="GRPC" else None

            target_type_str = tg_config.get("target_type", "INSTANCE").upper()
            target_type_map = {"INSTANCE": elbv2.TargetType.INSTANCE, "IP": elbv2.TargetType.IP, "LAMBDA": elbv2.TargetType.LAMBDA, "ALB": elbv2.TargetType.ALB}
            tg_target_type = target_type_map.get(target_type_str)
            if not tg_target_type:
                 logger.error(f"Invalid target_type '{target_type_str}' for TG '{tg_id}'. Skipping."); continue

            hc_props = None
            hc_conf = tg_config.get("health_check", {})
            if hc_conf.get("enabled", True):
                hc_protocol_map = {"HTTP": elbv2.Protocol.HTTP, "HTTPS": elbv2.Protocol.HTTPS} # ALB HC protocols
                hc_props = elbv2.HealthCheck(
                    enabled=True,
                    protocol=hc_protocol_map.get(hc_conf.get("protocol", "HTTP").upper()),
                    port=hc_conf.get("port", "traffic-port"),
                    path=hc_conf.get("path", "/" if tg_protocol in [elbv2.ApplicationProtocol.HTTP, elbv2.ApplicationProtocol.HTTPS] else None),
                    interval=parse_duration_optional(hc_conf.get("interval_seconds"), default_value_seconds=30),
                    timeout=parse_duration_optional(hc_conf.get("timeout_seconds"), default_value_seconds=5),
                    healthy_threshold_count=hc_conf.get("healthy_threshold_count", 3),
                    unhealthy_threshold_count=hc_conf.get("unhealthy_threshold_count", 3),
                    healthy_http_codes=hc_conf.get("matcher_http_codes", "200")
                )

            target_group = elbv2.ApplicationTargetGroup(self, f"{self.alb_logical_id}{tg_id}TgResource",
                vpc=tg_vpc,
                target_group_name=tg_config.get("target_group_name"),
                port=int(tg_config.get("port", 80)),
                protocol=tg_protocol,
                protocol_version=tg_proto_ver,
                target_type=tg_target_type,
                deregistration_delay=parse_duration_optional(tg_config.get("deregistration_delay_seconds")),
                health_check=hc_props
            )
            self.target_groups[tg_id] = target_group
            
            stickiness_conf = tg_config.get("stickiness", {})
            if stickiness_conf.get("enabled", False) and stickiness_conf.get("type", "").upper() == "APPLICATION_COOKIE":
                duration = parse_duration_optional(stickiness_conf.get("cookie_duration_seconds"))
                cookie_name = stickiness_conf.get("cookie_name")
                if duration and cookie_name: 
                     target_group.enable_cookie_stickiness(duration, cookie_name)

            for attr in tg_config.get("attributes", []):
                if "key" in attr and "value" in attr: target_group.set_attribute(attr["key"], attr["value"])

            for target_spec in tg_config.get("targets", []):
                if tg_target_type == elbv2.TargetType.INSTANCE and target_spec.get("instance_id"):
                    target_group.add_target(elbv2_targets.InstanceIdTarget(target_spec["instance_id"])) 
                elif tg_target_type == elbv2.TargetType.IP and target_spec.get("ip_address"):
                    target_group.add_target(elbv2_targets.IpTarget( 
                        ip_address=target_spec["ip_address"],
                        port=target_spec.get("port"), 
                        availability_zone=target_spec.get("availability_zone")
                    ))
                elif tg_target_type == elbv2.TargetType.LAMBDA and target_spec.get("lambda_function_arn"):
                    lambda_target_id = f"{self.alb_logical_id}{tg_id}{target_spec['lambda_function_arn'].split(':')[-1].replace('_','')}TargetFn"
                    fn = lambda_.Function.from_function_arn(self, lambda_target_id, target_spec["lambda_function_arn"])
                    target_group.add_target(elbv2_targets.LambdaTarget(fn)) 
                else:
                    logger.warning(f"TG '{tg_id}': Skipping invalid target spec or mismatched target_type: {target_spec}")

        for listener_entry in self.config.get("listeners", []):
            listener_id = listener_entry.get("id")
            if not listener_id or not listener_entry.get("enabled", True):
                logger.info(f"Skipping listener: ID missing or disabled: {listener_id}")
                continue
            
            # listener_config is the listener_entry itself as per your JSON structure
            listener_config = listener_entry 

            listener_protocol_str = listener_config.get("protocol", "HTTP").upper()
            listener_protocol = elbv2.ApplicationProtocol.HTTP if listener_protocol_str == "HTTP" else elbv2.ApplicationProtocol.HTTPS
            
            certs = []
            ssl_policy = None
            if listener_protocol == elbv2.ApplicationProtocol.HTTPS:
                certificates_config = listener_config.get("certificates") # Get the list
                if not certificates_config or not isinstance(certificates_config, list): # Check if it's a list
                    raise ValueError(f"HTTPS Listener '{listener_id}' requires a 'certificates' list.")
                
                for cert_info in certificates_config: # Iterate over the list
                    if isinstance(cert_info, dict) and cert_info.get("certificate_arn"): 
                        certs.append(elbv2.ListenerCertificate.from_arn(cert_info["certificate_arn"]))
                    else:
                        logger.warning(f"Certificate entry for listener '{listener_id}' is invalid or missing 'certificate_arn'. Skipping this certificate entry: {cert_info}")
                
                if not certs: # If after processing, no valid certs were added
                     raise ValueError(f"HTTPS Listener '{listener_id}' has no valid certificate ARNs provided. At least one is required.")

                ssl_policy_name = listener_config.get("ssl_policy")
                if ssl_policy_name:
                    # Ensure SslPolicy attributes are uppercase for getattr
                    ssl_policy_attr_name = ssl_policy_name.upper()
                    if hasattr(elbv2.SslPolicy, ssl_policy_attr_name):
                        ssl_policy = getattr(elbv2.SslPolicy, ssl_policy_attr_name)
                    else: 
                        logger.warning(f"SSL Policy '{ssl_policy_name}' (tried as '{ssl_policy_attr_name}') for Listener '{listener_id}' not found. Using default.")


            action_config = listener_config.get("default_action", {})
            action_type = action_config.get("type", "").upper()
            default_listener_action = None

            if action_type == "FORWARD":
                tg_id_ref = action_config.get("target_group_id") 
                existing_tg_arn = action_config.get("existing_target_group_arn")
                
                target_group_to_use: elbv2.IApplicationTargetGroup = None # type: ignore
                if tg_id_ref and tg_id_ref in self.target_groups:
                    target_group_to_use = self.target_groups[tg_id_ref]
                elif existing_tg_arn:
                    target_group_to_use = elbv2.ApplicationTargetGroup.from_target_group_attributes(
                        self, f"{self.alb_logical_id}{listener_id}ImportedTg",
                        target_group_arn=existing_tg_arn
                    )
                if target_group_to_use:
                    default_listener_action = elbv2.ListenerAction.forward([target_group_to_use])
                else: 
                    logger.error(f"Listener '{listener_id}': FORWARD action - target_group_id '{tg_id_ref}' or existing_target_group_arn '{existing_tg_arn}' is invalid or TG not found. Skipping listener.")
                    continue # Skip this listener if default action cannot be resolved
            
            elif action_type == "FIXED_RESPONSE":
                fr_conf = action_config.get("fixed_response_config", {})
                status_code_val = fr_conf.get("status_code", "200")
                try:
                    status_code_int = int(status_code_val)
                except ValueError:
                    logger.error(f"Listener '{listener_id}': Invalid status_code '{status_code_val}' for FIXED_RESPONSE. Must be an integer. Skipping.")
                    continue
                default_listener_action = elbv2.ListenerAction.fixed_response(
                    status_code=status_code_int, 
                    content_type=fr_conf.get("content_type"), message_body=fr_conf.get("message_body"))
            
            elif action_type == "REDIRECT":
                r_conf = action_config.get("redirect_config", {})
                default_listener_action = elbv2.ListenerAction.redirect(
                    protocol=r_conf.get("protocol", "#{protocol}").upper(), host=r_conf.get("host", "#{host}"),
                    port=r_conf.get("port", "#{port}"), path=r_conf.get("path", "/#{path}"), query=r_conf.get("query", "#{query}"),
                    permanent=str(r_conf.get("status_code", "HTTP_301")).upper() == "HTTP_301")
            else: 
                logger.error(f"Listener '{listener_id}': Invalid default_action type '{action_type}'. Skipping listener.")
                continue

            listener = self.alb.add_listener(f"{self.alb_logical_id}{listener_id}ListenerResource",
                port=int(listener_config.get("port", 80)),
                protocol=listener_protocol,
                certificates=certs if certs else None,
                ssl_policy=ssl_policy,
                default_action=default_listener_action,
                open=True 
            )
            self.listeners[listener_id] = listener

        CfnOutput(self, f"{self.alb_logical_id}DnsName", value=self.alb.load_balancer_dns_name)
        CfnOutput(self, f"{self.alb_logical_id}Arn", value=self.alb.load_balancer_arn)


    def _parse_sg_peer(self, peer_type_str: typing.Optional[str], peer_value: typing.Optional[str]) -> typing.Optional[ec2.IPeer]:
        if not peer_type_str: return ec2.Peer.any_ipv4()
        pt = peer_type_str.upper()
        if pt == "ANY_IPV4": return ec2.Peer.any_ipv4()
        if pt == "ANY_IPV6": return ec2.Peer.any_ipv6()
        if pt == "CIDR_IP" and peer_value: return ec2.Peer.ipv4(str(peer_value))
        if pt == "SECURITY_GROUP" and peer_value: return ec2.Peer.security_group_id(str(peer_value))
        logger.warning(f"Unsupported SG peer_type: {peer_type_str} in ALB SG config")
        return None

    def _parse_sg_connection(self, protocol_str: typing.Optional[str], port: typing.Optional[typing.Any]=None, 
                             from_port: typing.Optional[typing.Any]=None, to_port: typing.Optional[typing.Any]=None) -> typing.Optional[ec2.Port]:
        if not protocol_str: return ec2.Port.tcp(80)
        proto = protocol_str.strip().upper()
        try:
            p_int = int(port) if port is not None else None
            fp_int = int(from_port) if from_port is not None else None
            tp_int = int(to_port) if to_port is not None else None

            if proto == "TCP":
                if p_int is not None: return ec2.Port.tcp(p_int)
                if fp_int is not None and tp_int is not None: return ec2.Port.tcp_range(fp_int, tp_int)
            elif proto == "ALL": return ec2.Port.all_traffic()
        except ValueError:
             logger.warning(f"Invalid port value for SG connection: {port}, {from_port}, {to_port}")
             return None
        logger.warning(f"Unsupported SG connection: protocol={protocol_str}, port={port} in ALB SG config")
        return None
