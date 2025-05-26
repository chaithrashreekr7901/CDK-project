# CRMP-PROJECT/cdk_project/ec2/security_group_stack.py
import logging
import typing
import aws_cdk as cdk 
from aws_cdk import (
    NestedStack,
    Tags,
    CfnOutput,
    aws_ec2 as ec2
)
from constructs import Construct

logger = logging.getLogger(__name__)

def construct_id_to_cdk_id_part(original_id: str, max_len: int = 30) -> str:
    """Helper to create a more CDK-friendly ID part from a user-defined logical ID."""
    sanitized = ''.join(filter(str.isalnum, original_id))
    return sanitized[:max_len].title() if sanitized else "Default"

class SecurityGroupStack(NestedStack):
    security_group: ec2.SecurityGroup
    security_group_id_output: CfnOutput
    # security_group_arn_output: CfnOutput # ARN is less commonly used for SGs directly

    def __init__(self, scope: Construct, construct_id: str,
                 sg_config_entry: dict, 
                 vpc: ec2.IVpc,
                 peer_sgs_map: typing.Optional[typing.Dict[str, ec2.ISecurityGroup]] = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not sg_config_entry or not sg_config_entry.get("enabled", False):
            logger.info(f"Security Group config for ID '{sg_config_entry.get('id')}' is not provided or disabled. Skipping.")
            self.security_group = None # type: ignore
            self.security_group_id_output = None # type: ignore
            cdk.Annotations.of(self).add_warning(f"SecurityGroupStack for {sg_config_entry.get('id')} created but SG is disabled in config.")
            return


        self.sg_logical_id = sg_config_entry["id"]
        self.config = sg_config_entry["config"] # This is the merged config for this SG
        self.peer_sgs_map = peer_sgs_map if peer_sgs_map else {}

        sg_name = self.config.get("security_group_name", f"{self.sg_logical_id}-sg")
        sg_description = self.config.get("description", f"Security Group for {self.sg_logical_id}")
        allow_all_outbound = self.config.get("allow_all_outbound", True)

        self.security_group = ec2.SecurityGroup(
            self, "Resource",
            vpc=vpc,
            security_group_name=sg_name,
            description=sg_description,
            allow_all_outbound=allow_all_outbound
        )
        Tags.of(self.security_group).add("Name", sg_name)
        for key, value in self.config.get("tags", {}).items():
            Tags.of(self.security_group).add(key, value)

        logger.info(f"Created Security Group '{sg_name}' (Logical ID: {self.sg_logical_id}) with ID {self.security_group.security_group_id}")

        self._apply_rules()


        self.security_group_id_output = CfnOutput(self, "SecurityGroupId", value=self.security_group.security_group_id)


    def _parse_peer(self, rule_config: dict) -> typing.Optional[ec2.IPeer]:
        peer_type = rule_config.get("peer_type", "").upper()
        peer_value = rule_config.get("peer_value") 
        peer_value_ref_id = rule_config.get("peer_value_ref_id")

        logger.debug(f"Parsing peer for rule in SG '{self.sg_logical_id}'. Rule config: {rule_config}")

        if peer_type == "ANY_IPV4":
            return ec2.Peer.any_ipv4()
        elif peer_type == "ANY_IPV6":
            return ec2.Peer.any_ipv6()
        elif peer_type == "CIDR_IPV4" and peer_value:
            return ec2.Peer.ipv4(str(peer_value))
        elif peer_type == "CIDR_IPV6" and peer_value:
            return ec2.Peer.ipv6(str(peer_value))
        elif peer_type == "PREFIX_LIST_ID" and peer_value:
            return ec2.Peer.prefix_list(str(peer_value))
        elif peer_type == "SECURITY_GROUP_ID": 
            if peer_value:
                return ec2.Peer.security_group_id(str(peer_value))
            else:
                logger.error(f"SG Rule for '{self.sg_logical_id}': peer_type SECURITY_GROUP_ID requires 'peer_value' (physical SG ID).")
                return None
        elif peer_type == "SECURITY_GROUP_ID_REF": 
            ref_id_to_lookup = peer_value_ref_id if peer_value_ref_id is not None else peer_value
            
            logger.debug(f"SG Rule for '{self.sg_logical_id}': Attempting SECURITY_GROUP_ID_REF. "
                         f"Raw peer_value_ref_id='{peer_value_ref_id}', raw peer_value='{peer_value}', "
                         f"Effective ref_id_to_lookup='{ref_id_to_lookup}'. "
                         f"Available keys in peer_sgs_map: {list(self.peer_sgs_map.keys())}")

            if ref_id_to_lookup and ref_id_to_lookup in self.peer_sgs_map:
                logger.info(f"SG Rule for '{self.sg_logical_id}': Resolved SECURITY_GROUP_ID_REF '{ref_id_to_lookup}' to SG {self.peer_sgs_map[ref_id_to_lookup].security_group_id}")
                return self.peer_sgs_map[ref_id_to_lookup]
            else:
                logger.error(f"SG Rule for '{self.sg_logical_id}': Cannot resolve SECURITY_GROUP_ID_REF '{ref_id_to_lookup}'. Peer SG not found in provided map.")
                return None
        elif peer_type == "SELF":
            return ec2.Peer.myself()
        else:
            logger.warning(f"SG Rule for '{self.sg_logical_id}': Unsupported peer_type '{peer_type}' or missing value. Rule: {rule_config}")
            return None

    def _parse_port(self, rule_config: dict) -> typing.Optional[ec2.Port]:
        protocol_str = rule_config.get("protocol", "").upper()
        port = rule_config.get("port")
        from_port = rule_config.get("from_port")
        to_port = rule_config.get("to_port")
        icmp_type = rule_config.get("icmp_type")
        icmp_code = rule_config.get("icmp_code")


        if not protocol_str: 
            logger.info(f"No protocol specified for SG rule in '{self.sg_logical_id}', defaulting to all_traffic.")
            return ec2.Port.all_traffic() 
            
        proto = protocol_str.strip().upper()
        try:
            p_int = int(port) if port is not None and str(port).isdigit() else None
            fp_int = int(from_port) if from_port is not None and str(from_port).isdigit() else None
            tp_int = int(to_port) if to_port is not None and str(to_port).isdigit() else None
            icmp_type_int = int(icmp_type) if icmp_type is not None and str(icmp_type).isdigit() else None
            icmp_code_int = int(icmp_code) if icmp_code is not None and str(icmp_code).lstrip('-').isdigit() else None

            if proto == "TCP":
                if p_int is not None: return ec2.Port.tcp(p_int)
                if fp_int is not None and tp_int is not None: return ec2.Port.tcp_range(fp_int, tp_int)
                logger.warning(f"TCP protocol specified for '{self.sg_logical_id}' but port/range is invalid: p={port}, fp={from_port}, tp={to_port}")
            elif proto == "UDP":
                if p_int is not None: return ec2.Port.udp(p_int)
                if fp_int is not None and tp_int is not None: return ec2.Port.udp_range(fp_int, tp_int)
                logger.warning(f"UDP protocol specified for '{self.sg_logical_id}' but port/range is invalid: p={port}, fp={from_port}, tp={to_port}")
            elif proto == "ICMP":
                if icmp_type_int is not None and icmp_code_int is not None: return ec2.Port.icmp_type_and_code(icmp_type_int, icmp_code_int)
                if icmp_type_int is not None : return ec2.Port.icmp_type(icmp_type_int)
                return ec2.Port.all_icmp() 
            elif proto == "-1" or proto == "ALL": 
                return ec2.Port.all_traffic()
            else: 
                if proto.isdigit():
                    protocol_number_str = proto
                    if p_int is None and fp_int is None and tp_int is None:
                        return ec2.Port(protocol=protocol_number_str, string_representation=f"protocol {protocol_number_str} (all ports)", from_port=-1, to_port=-1)
                    if p_int is not None:
                         return ec2.Port(protocol=protocol_number_str, string_representation=f"protocol {protocol_number_str} port {p_int}", from_port=p_int, to_port=p_int)
                    if fp_int is not None and tp_int is not None:
                         return ec2.Port(protocol=protocol_number_str, string_representation=f"protocol {protocol_number_str} ports {fp_int}-{tp_int}", from_port=fp_int, to_port=tp_int)
                else:
                    logger.warning(f"Unrecognized protocol: {protocol_str} for '{self.sg_logical_id}'. Rule: {rule_config}")
        except ValueError as ve: 
            logger.error(f"Could not parse SG connection for '{self.sg_logical_id}' from {rule_config}: {ve}", exc_info=True)
            return None
        logger.warning(f"Could not form a valid SG connection for '{self.sg_logical_id}' from: {rule_config}")
        return None 

    def _apply_rules(self) -> None:
        for i, rule_conf in enumerate(self.config.get("ingress_rules", [])):
            peer = self._parse_peer(rule_conf)
            port_connection = self._parse_port(rule_conf)
            description = rule_conf.get("description", f"Ingress rule {i+1} for {self.sg_logical_id}")
            if peer and port_connection:
                self.security_group.add_ingress_rule(peer, port_connection, description)
                logger.info(f"Added ingress rule to '{self.sg_logical_id}': {description}")
            else:
                logger.error(f"Skipping invalid ingress rule for '{self.sg_logical_id}': {rule_conf}")

        if not self.config.get("allow_all_outbound", True):
            for i, rule_conf in enumerate(self.config.get("egress_rules", [])):
                peer = self._parse_peer(rule_conf)
                port_connection = self._parse_port(rule_conf)
                description = rule_conf.get("description", f"Egress rule {i+1} for {self.sg_logical_id}")
                if peer and port_connection:
                    self.security_group.add_egress_rule(peer, port_connection, description)
                    logger.info(f"Added egress rule to '{self.sg_logical_id}': {description}")
                else:
                    logger.error(f"Skipping invalid egress rule for '{self.sg_logical_id}': {rule_conf}")

