# CRMP-Project/cdk_project/peering/vpc_peering_instance_stack.py
import logging
from aws_cdk import (
    NestedStack,
    CfnTag,
    aws_ec2 as ec2,
    Environment, 
    CfnOutput    
)
from constructs import Construct

logger = logging.getLogger(__name__)

class VpcPeeringInstanceStack(NestedStack):
    def __init__(self, scope: Construct, construct_id: str, peering_config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.peering_config = peering_config
        self.peering_connection_id_output: CfnOutput | None = None

        peering_name_cfg = self.peering_config.get('peering_connection_name', construct_id)
        logger.info(f"VpcPeeringInstanceStack '{construct_id}': Initializing for peering '{peering_name_cfg}'.")

        vpc_a_config = self.peering_config.get('vpc_a')
        vpc_b_config = self.peering_config.get('vpc_b')

        if not vpc_a_config or not vpc_b_config:
            logger.error("CRITICAL: VPC A or VPC B configuration is missing in peering_config. Peering stack will be empty.")
            raise ValueError("VPC A or VPC B configuration is missing in peering_config.")

        required_keys = ['id', 'account_id', 'region', 'cidr']
        if not all(vpc_a_config.get(key) for key in required_keys):
            logger.error("CRITICAL: Missing required configuration for VPC A (id, account_id, region, or cidr). Peering stack will be empty.")
            raise ValueError("Missing required configuration for VPC A in peering_config.")
        if not all(vpc_b_config.get(key) for key in required_keys):
            logger.error("CRITICAL: Missing required configuration for VPC B (id, account_id, region, or cidr). Peering stack will be empty.")
            raise ValueError("Missing required configuration for VPC B in peering_config.")

        # --- Reference VPC A (Requester) ---
        # Since the stack and VPC A are in the same account and region (us-east-1),
        # we only need to provide the vpc_id.
        try:
            logger.info(f"Attempting to look up VPC A with ID '{vpc_a_config['id']}' using stack's environment (Account: {self.account}, Region: {self.region}).")
            vpc_a_object = ec2.Vpc.from_lookup(
                self,
                "LookupVpcAForPeering", 
                vpc_id=vpc_a_config['id'] # Only vpc_id is needed if VPC is in stack's account/region
            )
            vpc_a_id_to_use = vpc_a_object.vpc_id 
            logger.info(f"Successfully looked up VPC A: {vpc_a_id_to_use} (Configured CIDR for routes: {vpc_a_config['cidr']})")
        except Exception as e:
            logger.error(f"CRITICAL: Error looking up VPC A ('{vpc_a_config['id']}' in stack's region '{self.region}'): {e}.")
            logger.error("ACTION: Please ensure the VPC ID in deployment_config.py is correct, the VPC exists in account '{self.account}' and region '{self.region}', and the CDK has permissions for lookup (check bootstrap and roles).")
            raise RuntimeError(f"VPC A lookup failed for {vpc_a_config['id']} in stack's region {self.region}") from e

        # --- Create VPC Peering Connection ---
        peering_connection_name_tag = self.peering_config.get('peering_connection_name', f"{vpc_a_config['id']}-to-{vpc_b_config['id']}-peer")
        
        cfn_tags_for_peering = [CfnTag(key="Name", value=peering_connection_name_tag)]
        for key, value in self.peering_config.get('tags', {}).items():
            cfn_tags_for_peering.append(CfnTag(key=str(key), value=str(value)))

        peering_connection = ec2.CfnVPCPeeringConnection(
            self,
            "CfnPeeringConnectionResource",
            vpc_id=vpc_a_id_to_use,
            peer_vpc_id=vpc_b_config['id'],
            peer_owner_id=vpc_b_config['account_id'],
            peer_region=vpc_b_config['region'], # This should be us-east-1 as per your config
            tags=cfn_tags_for_peering
        )
        logger.info(f"VPC Peering Connection '{peering_connection.ref}' requested from {vpc_a_id_to_use} to {vpc_b_config['id']}.")
        logger.info("Reminder: If cross-account, the peering connection needs to be accepted in the peer account unless a peer_role_arn is configured for auto-acceptance.")

        self.peering_connection_id_output = CfnOutput(
            self, "PeeringConnectionId",
            value=peering_connection.ref,
            description=f"VPC Peering Connection ID for {peering_connection_name_tag}"
        )

        # --- Add Routes in VPC A's Route Tables (pointing to VPC B's CIDR) ---
        self._add_routes_to_local_vpc(
            vpc_target_cidr=vpc_b_config['cidr'],
            route_table_configs_for_local_vpc=vpc_a_config.get('route_tables', {}),
            peering_connection_id_ref=peering_connection.ref,
            local_vpc_label="VPC A",
            logical_id_prefix="VpcARouteToVpcB"
        )

        # --- Add Routes in VPC B's Route Tables (pointing to VPC A's CIDR) ---
        # This will work because both VPCs and the stack are in us-east-1 in the same account.
        if self.account == vpc_b_config['account_id'] and self.region == vpc_b_config['region']:
            logger.info(f"VPC B ({vpc_b_config['id']}) is in the same account/region as this stack. Attempting to add routes in VPC B.")
            self._add_routes_to_local_vpc(
                vpc_target_cidr=vpc_a_config['cidr'],
                route_table_configs_for_local_vpc=vpc_b_config.get('route_tables', {}),
                peering_connection_id_ref=peering_connection.ref,
                local_vpc_label="VPC B",
                logical_id_prefix="VpcBRouteToVpcA"
            )
        else:
            # This else block should ideally not be hit if your config is correct and both VPCs are in us-east-1
            logger.warning(f"VPC B ({vpc_b_config['id']}) is in account '{vpc_b_config['account_id']}' region '{vpc_b_config['region']}', "
                           f"while this stack is in account '{self.account}' region '{self.region}'. This is unexpected if both are in us-east-1.")
            logger.warning(f"Routes to VPC A ({vpc_a_config['cidr']}) in VPC B's route tables MUST be created MANUALLY or by a SEPARATE STACK/PROCESS "
                           f"in VPC B's account/region, using Peering Connection ID: {peering_connection.ref}.")


    def _add_routes_to_local_vpc(self, vpc_target_cidr: str, route_table_configs_for_local_vpc: dict, peering_connection_id_ref: str, local_vpc_label: str, logical_id_prefix: str):
        logger.info(f"Configuring routes in {local_vpc_label} towards CIDR {vpc_target_cidr} via peering {peering_connection_id_ref}.")
        for rt_type, rt_config_list in route_table_configs_for_local_vpc.items():
            if rt_config_list.get('enabled', False):
                route_table_ids = rt_config_list.get('ids', [])
                if route_table_ids:
                    logger.debug(f"Processing {len(route_table_ids)} {rt_type} route table(s) for {local_vpc_label}.")
                    for i, rt_id_str in enumerate(route_table_ids):
                        route_logical_id = f"{logical_id_prefix}{rt_type.capitalize()}Route{i}"
                        try:
                            ec2.CfnRoute(
                                self,
                                route_logical_id,
                                route_table_id=rt_id_str,
                                destination_cidr_block=vpc_target_cidr,
                                vpc_peering_connection_id=peering_connection_id_ref
                            )
                            logger.info(f"Route defined in {local_vpc_label} (RT ID: {rt_id_str}, Type: {rt_type}) to {vpc_target_cidr} via peering.")
                        except Exception as e:
                            logger.error(f"Failed to define route for {local_vpc_label} (RT ID: {rt_id_str}, Type: {rt_type}): {e}")
                else:
                    logger.warning(f"Route addition for {local_vpc_label} {rt_type} subnets enabled, but no route table IDs provided in config.")
            else:
                logger.debug(f"Route addition for {local_vpc_label} {rt_type} subnets is disabled in config.")
