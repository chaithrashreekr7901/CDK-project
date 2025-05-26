# CRMP-PROJECT/cdk_project/ec2/network_load_balancer_stack.py
import logging
import typing
from aws_cdk import (
    NestedStack, Stack, Duration, Tags, CfnOutput, RemovalPolicy, Aws,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as elbv2_targets, 
    aws_s3 as s3,
    aws_certificatemanager as acm
    # aws_route53 as route53 
    # aws_route53_targets as route53_targets
)
from constructs import Construct

logger = logging.getLogger(__name__)

def parse_duration_optional(seconds: typing.Optional[typing.Any], default_if_none: bool = False, default_value_seconds: int = 30) -> typing.Optional[Duration]:
    """Helper to parse seconds into CDK Duration, or return None."""
    if seconds is not None:
        try:
            return Duration.seconds(int(seconds))
        except ValueError:
            logger.warning(f"Invalid duration value '{seconds}', cannot convert to int. Returning None or default.")
    if default_if_none:
        return Duration.seconds(default_value_seconds)
    return None

class NetworkLoadBalancerStack(NestedStack):
    nlb: elbv2.NetworkLoadBalancer
    listeners: typing.Dict[str, elbv2.NetworkListener]
    target_groups: typing.Dict[str, elbv2.INetworkTargetGroup] 

    def __init__(self, scope: Construct, construct_id: str,
                 nlb_config_entry: dict, 
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not nlb_config_entry.get("enabled", False):
            logger.info(f"NLB configuration '{nlb_config_entry.get('id')}' is disabled. Skipping.")
            self.nlb = None # type: ignore
            self.listeners = {}
            self.target_groups = {}
            return

        self.config = nlb_config_entry["config"]
        self.nlb_logical_id = nlb_config_entry["id"]
        self.listeners = {}
        self.target_groups = {}
        
        vpc_id = self.config.get("vpc_id")
        if not vpc_id:
            raise ValueError(f"NLB '{self.nlb_logical_id}': 'vpc_id' is required in the configuration.")
        
        vpc = ec2.Vpc.from_lookup(self, f"{self.nlb_logical_id}VpcLookup", vpc_id=vpc_id)

        nlb_subnet_mappings: typing.Optional[typing.List[elbv2.SubnetMapping]] = None
        subnet_mappings_config = self.config.get("subnet_mappings", [])
        
        if subnet_mappings_config:
            nlb_subnet_mappings = []
            for mapping_conf in subnet_mappings_config:
                subnet_id_map = mapping_conf.get("subnet_id")
                if not subnet_id_map: 
                    logger.warning(f"NLB '{self.nlb_logical_id}': Subnet mapping entry missing 'subnet_id' or is empty. Skipping this mapping: {mapping_conf}")
                    continue
                
                mapping_props: typing.Dict[str, typing.Any] = {"subnet_id": subnet_id_map} 
                if mapping_conf.get("allocation_id"): 
                    mapping_props["allocation_id"] = mapping_conf["allocation_id"]
                elif mapping_conf.get("private_ipv4_address"): 
                    mapping_props["private_ipv4_address"] = mapping_conf["private_ipv4_address"]
                
                nlb_subnet_mappings.append(elbv2.SubnetMapping(**mapping_props))
            if not nlb_subnet_mappings: 
                nlb_subnet_mappings = None 
        
        nlb_vpc_subnets_selection: typing.Optional[ec2.SubnetSelection] = None
        if not nlb_subnet_mappings: 
            provided_subnet_ids = self.config.get("subnet_ids")
            if provided_subnet_ids and isinstance(provided_subnet_ids, list):
                logger.info(f"NLB '{self.nlb_logical_id}': Looking up explicitly provided subnet_ids.")
                selected_subnets_for_nlb: typing.List[ec2.ISubnet] = []
                for i, sub_id_str in enumerate(provided_subnet_ids):
                    if not sub_id_str: 
                        logger.warning(f"NLB '{self.nlb_logical_id}': Empty subnet_id found in subnet_ids list at index {i}. Skipping.")
                        continue
                    try:
                        subnet_lookup_id = f"{self.nlb_logical_id}SelSubnet{i}"
                        selected_subnets_for_nlb.append(ec2.Subnet.from_subnet_id(self, subnet_lookup_id, sub_id_str))
                    except Exception as e:
                         logger.error(f"NLB '{self.nlb_logical_id}': Failed to look up subnet_id '{sub_id_str}'. Error: {e}. Skipping this subnet.")
                if not selected_subnets_for_nlb:
                    raise ValueError(f"NLB '{self.nlb_logical_id}': No valid subnets could be looked up from provided subnet_ids: {provided_subnet_ids}")
                nlb_vpc_subnets_selection = ec2.SubnetSelection(subnets=selected_subnets_for_nlb)
            elif self.config.get("subnet_selection"):
                sel_conf = self.config["subnet_selection"]
                subnet_type_str = sel_conf.get("subnet_type", "PUBLIC" if self.config.get("internet_facing") else "PRIVATE_WITH_EGRESS").upper()
                subnet_type_map = {
                    "PUBLIC": ec2.SubnetType.PUBLIC,
                    "PRIVATE_WITH_EGRESS": ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    "PRIVATE_ISOLATED": ec2.SubnetType.PRIVATE_ISOLATED,
                }
                nlb_vpc_subnets_selection = ec2.SubnetSelection(
                    subnet_type=subnet_type_map.get(subnet_type_str),
                    subnet_group_name=sel_conf.get("subnet_group_name")
                )
            else: 
                default_subnet_type = ec2.SubnetType.PUBLIC if self.config.get("internet_facing") else ec2.SubnetType.PRIVATE_WITH_EGRESS
                nlb_vpc_subnets_selection = ec2.SubnetSelection(subnet_type=default_subnet_type)

        self.nlb = elbv2.NetworkLoadBalancer(self, f"{self.nlb_logical_id}Resource",
            vpc=vpc,
            load_balancer_name=self.config.get("load_balancer_name"),
            internet_facing=self.config.get("internet_facing", False),
            subnet_mappings=nlb_subnet_mappings if nlb_subnet_mappings else None,
            vpc_subnets=nlb_vpc_subnets_selection if not nlb_subnet_mappings else None,
            cross_zone_enabled=self.config.get("cross_zone_load_balancing"),
            deletion_protection=self.config.get("deletion_protection", False)
        )

        if self.config.get("client_ip_preservation") is not None:
             logger.warning("NLB 'client_ip_preservation' is primarily a Target Group attribute. Ensure it's set there.")
        
        if self.config.get("dns_record_type"): 
            logger.info(f"NLB DNS record type hint: {self.config.get('dns_record_type')}. Actual DNS behavior depends on NLB setup.")

        custom_attributes = self.config.get("attributes", [])
        for attr in custom_attributes:
            if "key" in attr and "value" in attr:
                try:
                    self.nlb.set_attribute(attr["key"], attr["value"])
                except Exception as e:
                    logger.error(f"Failed to set NLB attribute {attr['key']}: {e}")

        access_logs_config = self.config.get("access_logs", {})
        if access_logs_config.get("enabled", False):
            bucket_name = access_logs_config.get("s3_bucket_name")
            if bucket_name:
                try:
                    logger.info(f"NLB '{self.nlb_logical_id}': Access logs configured to S3 bucket '{bucket_name}'. (Manual L1/Cfn override might be needed for NLB TLS access logs).")
                except Exception as e:
                    logger.error(f"Failed to configure access logs for NLB '{self.nlb_logical_id}': {e}")
            else:
                logger.warning(f"NLB '{self.nlb_logical_id}': Access logs enabled but 's3_bucket_name' not provided.")

        for tg_entry in self.config.get("target_groups", []):
            tg_id = tg_entry.get("id")
            if not tg_id or not tg_entry.get("enabled", True):
                logger.info(f"Skipping NLB target group: ID missing or disabled: {tg_id}")
                continue
            
            tg_config = tg_entry["config"]
            tg_vpc_id = tg_config.get("vpc_id", vpc_id) 
            tg_vpc = vpc if tg_vpc_id == vpc_id else ec2.Vpc.from_lookup(self, f"{self.nlb_logical_id}{tg_id}VpcCtx", vpc_id=tg_vpc_id)

            protocol_str = tg_config.get("protocol", "TCP").upper()
            tg_protocol_map = {
                "TCP": elbv2.Protocol.TCP, "UDP": elbv2.Protocol.UDP,
                "TLS": elbv2.Protocol.TLS, "TCP_UDP": elbv2.Protocol.TCP_UDP
            }
            tg_protocol = tg_protocol_map.get(protocol_str)
            if not tg_protocol:
                 logger.error(f"Invalid protocol '{protocol_str}' for NLB Target Group '{tg_id}'. Skipping."); continue
            
            target_type_str = tg_config.get("target_type", "INSTANCE").upper()
            tg_target_type_map = {"INSTANCE": elbv2.TargetType.INSTANCE, "IP": elbv2.TargetType.IP, "ALB": elbv2.TargetType.ALB}
            tg_target_type = tg_target_type_map.get(target_type_str)
            if not tg_target_type:
                logger.error(f"Invalid target_type '{target_type_str}' for NLB TG '{tg_id}'. Skipping."); continue

            health_check_props = None
            hc_config = tg_config.get("health_check", {})
            if hc_config.get("enabled", True):
                hc_protocol_str = hc_config.get("protocol", "TCP").upper()
                hc_protocol_map = {
                    "TCP": elbv2.Protocol.TCP,       
                    "HTTP": elbv2.Protocol.HTTP,     
                    "HTTPS": elbv2.Protocol.HTTPS    
                }
                hc_protocol = hc_protocol_map.get(hc_protocol_str)
                if not hc_protocol:
                    logger.warning(f"Invalid health check protocol '{hc_protocol_str}' for NLB TG '{tg_id}'. Defaulting to TCP.")
                    hc_protocol = elbv2.Protocol.TCP 

                health_check_props = elbv2.HealthCheck(
                    enabled=True,
                    protocol=hc_protocol, 
                    port=hc_config.get("port", hc_config.get("health_check_port_override", "traffic-port")),
                    path=hc_config.get("path") if hc_protocol in [elbv2.Protocol.HTTP, elbv2.Protocol.HTTPS] else None,
                    interval=parse_duration_optional(hc_config.get("interval_seconds"), default_value_seconds=30),
                    healthy_threshold_count=hc_config.get("healthy_threshold_count", 3),
                    unhealthy_threshold_count=hc_config.get("unhealthy_threshold_count", 3),
                    timeout=parse_duration_optional(hc_config.get("timeout_seconds")) if hc_protocol in [elbv2.Protocol.HTTP, elbv2.Protocol.HTTPS] else None,
                    healthy_http_codes=hc_config.get("matcher_http_codes") if hc_protocol in [elbv2.Protocol.HTTP, elbv2.Protocol.HTTPS] else None
                )

            preserve_client_ip = tg_config.get("preserve_client_ip") 

            target_group = elbv2.NetworkTargetGroup(self, f"{self.nlb_logical_id}{tg_id}TgResource",
                vpc=tg_vpc,
                target_group_name=tg_config.get("target_group_name"),
                port=int(tg_config.get("port", 80)),
                protocol=tg_protocol,
                target_type=tg_target_type,
                deregistration_delay=parse_duration_optional(tg_config.get("deregistration_delay_seconds")),
                health_check=health_check_props,
                preserve_client_ip=preserve_client_ip if tg_protocol in [elbv2.Protocol.TCP, elbv2.Protocol.TLS] else None
            )
            self.target_groups[tg_id] = target_group
            
            for attr in tg_config.get("attributes", []):
                if "key" in attr and "value" in attr: target_group.set_attribute(attr["key"], attr["value"])

            # --- Target Registration using ec2.Instance.from_instance_id() (Requires newer CDK) ---
            for i, target_spec in enumerate(tg_config.get("targets", [])): 
                try:
                    if tg_target_type == elbv2.TargetType.INSTANCE and target_spec.get("instance_id"):
                        instance_id_str = target_spec["instance_id"]
                        # instance_lookup_id = f"{self.nlb_logical_id}{tg_id}InstanceImport{i}"
                        
                        # This is the modern way, requires a newer CDK version that supports from_instance_id()
                        # instance_obj = ec2.Instance.from_instance_id(self, instance_lookup_id, instance_id_str)
                        target_group.add_target(elbv2_targets.InstanceIdTarget(instance_id_str))
                        logger.info(f"NLB TG '{tg_id}': Added instance target with ID: {instance_id_str} using InstanceIdTarget.")

                    elif tg_target_type == elbv2.TargetType.IP and target_spec.get("ip_address"):
                        target_group.add_target(elbv2_targets.IpTarget(
                            ip_address=target_spec["ip_address"],
                            port=target_spec.get("port"), 
                            availability_zone=target_spec.get("availability_zone")
                        ))
                    elif tg_target_type == elbv2.TargetType.ALB and target_spec.get("alb_arn"):
                        alb_target_id = f"{self.nlb_logical_id}{tg_id}AlbTargetImport{i}" 
                        alb_sg_id = target_spec.get("security_group_id_of_alb_target", "sg-00000000000000000") 
                        if alb_sg_id == "sg-00000000000000000":
                            logger.warning(f"NLB TG '{tg_id}': ALB target specified but 'security_group_id_of_alb_target' is missing or placeholder.")

                        imported_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
                            self, alb_target_id,
                            load_balancer_arn=target_spec["alb_arn"],
                            security_group_id=alb_sg_id 
                        )
                        target_group.add_target(elbv2_targets.AlbTarget(imported_alb, target_spec.get("port", 80)))
                    else:
                        logger.warning(f"NLB TG '{tg_id}': Skipping invalid target spec or mismatched target_type: {target_spec}")
                except Exception as e:
                    logger.error(f"Error adding target to NLB TG '{tg_id}': {target_spec}, Error: {e}", exc_info=True)
            # --- End Target Registration ---


        for listener_entry in self.config.get("listeners", []):
            listener_id = listener_entry.get("id")
            if not listener_id or not listener_entry.get("enabled", True):
                logger.info(f"Skipping NLB listener: ID missing or disabled: {listener_id}")
                continue
            
            listener_config = listener_entry 

            protocol_str = listener_config.get("protocol", "TCP").upper()
            listener_protocol_map = {
                "TCP": elbv2.Protocol.TCP, "UDP": elbv2.Protocol.UDP,
                "TLS": elbv2.Protocol.TLS, "TCP_UDP": elbv2.Protocol.TCP_UDP
            }
            listener_protocol = listener_protocol_map.get(protocol_str)
            if not listener_protocol:
                logger.error(f"Invalid protocol '{protocol_str}' for NLB Listener '{listener_id}'. Skipping."); continue

            certs = []
            alpn_policy_cdk: typing.Optional[typing.List[elbv2.AlpnPolicy]] = None 
            ssl_policy_obj = None 
            if listener_protocol == elbv2.Protocol.TLS:
                cert_configs = listener_config.get("certificates", [])
                if not cert_configs:
                    raise ValueError(f"TLS Listener '{listener_id}' for NLB requires at least one certificate.")
                for cert_conf in cert_configs:
                    if cert_conf.get("certificate_arn"):
                        certs.append(elbv2.ListenerCertificate.from_arn(cert_conf["certificate_arn"]))
                
                alpn_policy_list_str = listener_config.get("alpn_policy") 
                if alpn_policy_list_str and isinstance(alpn_policy_list_str, list):
                    alpn_map = {
                        "HTTP1ONLY": elbv2.AlpnPolicy.HTTP1_ONLY, "HTTP2ONLY": elbv2.AlpnPolicy.HTTP2_ONLY,
                        "HTTP2OPTIONAL": elbv2.AlpnPolicy.HTTP2_OPTIONAL, "HTTP2PREFERRED": elbv2.AlpnPolicy.HTTP2_PREFERRED,
                        "NONE": elbv2.AlpnPolicy.NONE
                    }
                    alpn_policy_cdk = [alpn_map[p.upper()] for p in alpn_policy_list_str if p.upper() in alpn_map]
                    if not alpn_policy_cdk : alpn_policy_cdk = None 

                ssl_policy_name = listener_config.get("ssl_policy")
                if ssl_policy_name:
                    ssl_policy_obj = ssl_policy_name 

            action_config = listener_config.get("default_action", {})
            action_type = action_config.get("type", "FORWARD").upper()
            default_listener_action = None

            if action_type == "FORWARD":
                tg_id_ref = action_config.get("target_group_id")
                existing_tg_arn = action_config.get("existing_target_group_arn")
                
                target_group_to_use: elbv2.INetworkTargetGroup = None # type: ignore
                if tg_id_ref and tg_id_ref in self.target_groups:
                    target_group_to_use = self.target_groups[tg_id_ref]
                elif existing_tg_arn:
                    target_group_to_use = elbv2.NetworkTargetGroup.from_target_group_attributes( 
                        self, f"{self.nlb_logical_id}{listener_id}ImportedTg",
                        target_group_arn=existing_tg_arn
                    )
                if target_group_to_use:
                    default_listener_action = elbv2.NetworkListenerAction.forward([target_group_to_use])
                else: 
                    logger.error(f"NLB Listener '{listener_id}': FORWARD action - target_group_id or existing_target_group_arn is invalid. Skipping listener."); continue
            else:
                logger.error(f"NLB Listener '{listener_id}': Invalid default_action type '{action_type}'. Only FORWARD is typically used. Skipping listener."); continue

            listener = self.nlb.add_listener(f"{self.nlb_logical_id}{listener_id}ListenerResource",
                port=int(listener_config.get("port", 80)),
                protocol=listener_protocol,
                certificates=certs if certs else None,
                alpn_policy=alpn_policy_cdk if listener_protocol == elbv2.Protocol.TLS else None,
                ssl_policy= ssl_policy_obj if listener_protocol == elbv2.Protocol.TLS else None, 
                default_action=default_listener_action
            )
            self.listeners[listener_id] = listener

        CfnOutput(self, f"{self.nlb_logical_id}DnsName", value=self.nlb.load_balancer_dns_name)
        CfnOutput(self, f"{self.nlb_logical_id}Arn", value=self.nlb.load_balancer_arn)
        
    def _parse_sg_peer(self, peer_type_str: typing.Optional[str], peer_value: typing.Optional[str]) -> typing.Optional[ec2.IPeer]:
        if not peer_type_str: return None 
        pt = peer_type_str.upper()
        if pt == "ANY_IPV4": return ec2.Peer.any_ipv4()
        logger.warning(f"SG peer parsing requested in NLB stack but not directly applicable to NLB: {peer_type_str}")
        return None

    def _parse_sg_connection(self, protocol_str: typing.Optional[str], port: typing.Optional[typing.Any]=None, 
                             from_port: typing.Optional[typing.Any]=None, to_port: typing.Optional[typing.Any]=None) -> typing.Optional[ec2.Port]:
        if not protocol_str: return None
        logger.warning(f"SG connection parsing requested in NLB stack but not directly applicable to NLB: {protocol_str}")
        return None
