# cdk_project/vpc/vpc_instance_nested_stack.py

import logging
import ipaddress
from aws_cdk import (
    NestedStack,
    Tags, # Keep this for L2 constructs if used elsewhere, but not for CfnVPC.tags.add()
    CfnTag, # This is correct for creating L1 tags
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_iam as iam,
    CfnOutput,
    Fn  
)
from constructs import Construct

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s: %(message)s')

class VpcInstanceNestedStack(NestedStack):
    public_vpc: ec2.IVpc | None = None

    def __init__(self, scope: Construct, construct_id: str, vpc_specific_config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc_params = vpc_specific_config.get("config", {}) 
        self.vpc_core_conf = vpc_params 
        self.subnets_conf = vpc_params.get("subnets_config", {})
        self.gateways_conf = vpc_params.get("gateways_config", {})
        self.route_tables_conf = vpc_params.get("route_tables_config", {})
        self.network_acls_conf = vpc_params.get("network_acls_config", {})
        self.security_groups_conf = vpc_params.get("security_groups_config", {})
        self.dhcp_options_conf = vpc_params.get("dhcp_options_config", {})
        self.vpc_endpoints_conf = vpc_params.get("vpc_endpoints_config", {})
        self.vpc_flow_logs_conf = vpc_params.get("vpc_flow_logs_config", {})

        self.vpc_id: str | None = None
        self.vpc_cidr_block: str | None = None
        self.availability_zones_used: list[str] = []  
        self.cfn_vpc_resource: ec2.CfnVPC | None = None
        self.cdk_vpc_construct: ec2.IVpc | None = None

        self.public_subnets_map: dict[str, list[ec2.CfnSubnet]] = {}  
        self.private_subnets_map: dict[str, list[ec2.CfnSubnet]] = {}  
        self.isolated_subnets_map: dict[str, list[ec2.CfnSubnet]] = {}  
        self.all_created_subnets: list[ec2.CfnSubnet] = []

        self.internet_gateway_resource: ec2.CfnInternetGateway | None = None
        self.igw_attachment_resource: ec2.CfnVPCGatewayAttachment | None = None

        self.public_shared_rt: ec2.CfnRouteTable | None = None
        self.isolated_shared_rt: ec2.CfnRouteTable | None = None
        self.private_per_az_rts: dict[str, ec2.CfnRouteTable] = {}  
        self.private_shared_rt: ec2.CfnRouteTable | None = None

        self.nat_gateways_by_az: dict[str, ec2.CfnNatGateway] = {}  
        self.nat_gateway_eips_list: list[ec2.CfnEIP] = []  
        self.nat_gateways_list: list[ec2.CfnNatGateway] = []  

        self.security_groups_map: dict[str, ec2.CfnSecurityGroup] = {}
        self.network_acls_map: dict[str, ec2.CfnNetworkAcl] = {}

        instance_name_tag = self.vpc_core_conf.get('name', construct_id) 
        logger.info(f"VpcInstanceNestedStack '{construct_id}': Initializing for VPC '{instance_name_tag}'.")

        if not self._configure_vpc_base_context():
            raise RuntimeError(f"Failed to configure base VPC context for {construct_id}")
        
        if self.vpc_core_conf.get('creation_mode') == 'NEW':
            if not self._create_subnets_low_level():
                raise RuntimeError(f"Subnet creation failed for {construct_id}")
            self._create_gateways_low_level()
            self._create_route_tables_for_vpc()
            self._associate_subnets_with_route_tables()
            self._configure_routes_for_vpc()
            self._create_network_acls_low_level()
            self._create_dhcp_options_low_level()
            self._create_vpc_endpoints_low_level()
            self._create_vpc_flow_logs_low_level()
            self._create_security_groups_low_level() 
        else: # EXISTING VPC
            self._populate_subnet_info_from_existing_vpc() 
            self._create_security_groups_low_level() 
            self._create_vpc_endpoints_low_level() 
            self._create_vpc_flow_logs_low_level() 


        # REMOVE CALL TO _apply_instance_tags() from __init__
        # It's now handled directly within _configure_vpc_base_context for NEW VPCs
        # and not needed for EXISTING VPCs from this stack.
        # self._apply_instance_tags() # <--- COMMENT OUT or REMOVE THIS LINE


        if self.cdk_vpc_construct:
            self.public_vpc = self.cdk_vpc_construct  
            logger.info(f"VpcInstanceNestedStack '{construct_id}': Exposed 'public_vpc' attribute (VPC ID: {self.public_vpc.vpc_id if self.public_vpc else 'N/A'}).")
        else:
            self.public_vpc = None 
            logger.error(f"VpcInstanceNestedStack '{construct_id}': CRITICAL - self.cdk_vpc_construct was NOT set. 'public_vpc' attribute is None.")

        if self.vpc_id:
            CfnOutput(
                self,
                "VpcIdOutput",
                value=self.vpc_id,
                description=f"VPC ID for {instance_name_tag} (Config ID: {vpc_specific_config.get('id')})"
            )
            if self.vpc_cidr_block:
                 CfnOutput(self, "VpcCidrOutput", value=self.vpc_cidr_block,
                           description=f"VPC CIDR for {instance_name_tag}")
            
            # Only output subnet IDs if the respective subnet type has been created
            # Check for non-empty list of CfnSubnet objects before creating Fn.join Output
            public_subnets_refs = [s.ref for s in self._get_all_subnets_of_type('public')]
            if public_subnets_refs:
                CfnOutput(self, "PublicSubnetIdsOutput",
                          value=Fn.join(",", public_subnets_refs),
                          description=f"Public Subnet IDs for {instance_name_tag}")

            private_subnets_refs = [s.ref for s in self._get_all_subnets_of_type('private')]
            if private_subnets_refs:
                CfnOutput(self, "PrivateSubnetIdsOutput",
                          value=Fn.join(",", private_subnets_refs),
                          description=f"Private Subnet IDs for {instance_name_tag}")

            isolated_subnets_refs = [s.ref for s in self._get_all_subnets_of_type('isolated')]
            if isolated_subnets_refs: # <--- CRITICAL FIX: Add this conditional check
                CfnOutput(self, "IsolatedSubnetIdsOutput",
                          value=Fn.join(",", isolated_subnets_refs),
                          description=f"Isolated Subnet IDs for {instance_name_tag}")
            else:
                logger.info(f"Skipping IsolatedSubnetIdsOutput for {instance_name_tag} as no isolated subnets were defined/created.") # Optional: log for clarity

        logger.info(f"VpcInstanceNestedStack '{construct_id}': Initialization complete.")


    def _configure_vpc_base_context(self) -> bool:
        mode = self.vpc_core_conf.get('creation_mode', 'NEW')
        vpc_name_tag = self.vpc_core_conf.get('name', self.stack_name)
        logger.info(f"Configuring VPC context in '{mode}' mode for '{vpc_name_tag}'.")

        if mode == 'EXISTING':
            lookup_conf = self.vpc_core_conf.get('existing_vpc_lookup', {})
            if not lookup_conf.get('enabled', False):
                logger.error("EXISTING mode: existing_vpc_lookup.enabled is False.")
                return False
            try:
                lookup_options = {}
                if lookup_conf.get('by_id'):
                    lookup_options['vpc_id'] = lookup_conf['by_id']
                elif lookup_conf.get('by_tags'):
                    lookup_options['tags'] = lookup_conf['by_tags']
                else:
                    logger.error("EXISTING mode lookup: requires 'by_id' or 'by_tags'.")
                    return False
                self.cdk_vpc_construct = ec2.Vpc.from_lookup(self, "ImportedVpcConstruct", **lookup_options)
                if not self.cdk_vpc_construct:
                    logger.error("VPC lookup failed.")
                    return False
                self.vpc_id = self.cdk_vpc_construct.vpc_id
                self.availability_zones_used = self.cdk_vpc_construct.availability_zones
                self.vpc_cidr_block = self.cdk_vpc_construct.vpc_cidr_block
                logger.info(f"Looked up existing VPC: ID={self.vpc_id}, CIDR={self.vpc_cidr_block}, AZs={self.availability_zones_used}")
                return True
            except Exception as e:
                logger.error(f"Error during existing VPC lookup: {e}")
                return False

        elif mode == 'NEW':
            self.vpc_cidr_block = self.vpc_core_conf.get('cidr')
            if not self.vpc_cidr_block:
                logger.error("NEW mode: 'cidr' is missing.") 
                return False
            
            if not self.subnets_conf.get('enabled', False):
                logger.error("NEW mode: 'subnets_config.enabled' is False, but subnets are required for a new VPC.")
                return False

            az_suffix_configs = self.subnets_conf.get('availability_zones_config', [])
            if not az_suffix_configs:
                logger.error("NEW mode: 'subnets_config.availability_zones_config' is missing.")
                return False
            
            current_region = self.region
            if not current_region or current_region.startswith("TOKEN"):
                logger.error(f"Stack region not resolved ('{current_region}').")
                return False
            
            self.availability_zones_used = [
                f"{current_region}{az_conf.get('az_name_suffix')}"
                for az_conf in az_suffix_configs
                if az_conf.get('az_name_suffix')
            ]
            if not self.availability_zones_used:
                logger.error("No valid AZs determined from config.")
                return False
            logger.info(f"Planned AZs for new VPC '{vpc_name_tag}': {self.availability_zones_used}") # Log using actual VPC name from config
            
            initial_cfn_vpc_tags = [CfnTag(key="Name", value=vpc_name_tag)]
            extra_vpc_tags = self.vpc_core_conf.get('tags', {})
            for key, value in extra_vpc_tags.items():
                if key != "Name":
                    initial_cfn_vpc_tags.append(CfnTag(key=key, value=value))

            self.cfn_vpc_resource = ec2.CfnVPC(
                self,
                "CfnResourceVPC",
                cidr_block=self.vpc_cidr_block,
                enable_dns_hostnames=self.vpc_core_conf.get('dns_options', {}).get('enable_dns_hostnames', True),
                enable_dns_support=self.vpc_core_conf.get('dns_options', {}).get('enable_dns_support', True),
                tags=initial_cfn_vpc_tags 
            )
            
            self.vpc_id = self.cfn_vpc_resource.ref
            logger.info(f"New CfnVPC resource defined for '{vpc_name_tag}' (ID Token: {self.vpc_id})")
            
            try:
                self.cdk_vpc_construct = ec2.Vpc.from_vpc_attributes(
                    self,
                    "ImportedNewVpcAttributes",
                    vpc_id=self.vpc_id,
                    availability_zones=self.availability_zones_used,
                    vpc_cidr_block=self.vpc_cidr_block,
                )
            except Exception as e:
                logger.warning(f"Could not create IVpc representation for new VPC: {e}. Some L2 VPC features may be limited.")
            return True
        else:
            logger.error(f"Invalid 'creation_mode': {mode}.")
            return False

    def _create_subnets_low_level(self) -> bool:
        if not self.subnets_conf.get('enabled', False):
            logger.info("Subnet creation skipped (config).")
            return True
        if not self.cfn_vpc_resource or not self.vpc_id or not self.availability_zones_used:
            logger.error("Cannot create subnets: VPC Cfn resource, ID, or AZs not set (NEW mode).")
            return False
        if not self.vpc_cidr_block:
            logger.error("VPC CIDR not set (NEW mode).")
            return False
        az_configs = self.subnets_conf.get('availability_zones_config', [])
        if not az_configs:
            logger.warning("Subnet creation: 'availability_zones_config' empty.")
            return True
        logger.info(f"Creating subnets for VPC '{self.vpc_id}' using CfnSubnet...")
        try:
            vpc_network = ipaddress.ip_network(self.vpc_cidr_block)
        except ValueError as e:
            logger.error(f"Invalid VPC CIDR '{self.vpc_cidr_block}': {e}")
            return False
        
        current_subnet_index = 0 
        
        region = self.region
        for az_idx, az_master_c in enumerate(az_configs):
            az_sfx = az_master_c.get('az_name_suffix')
            if not az_sfx:
                logger.warning(f"Skipping AZ config (missing 'az_name_suffix'): {az_master_c}")
                continue
            actual_az = f"{region}{az_sfx}"
            if actual_az not in self.availability_zones_used:
                logger.warning(f"AZ '{actual_az}' not in VPC's planned AZs. Skipping.")
                continue
            logger.info(f"Processing subnets for AZ: {actual_az}")

            s_counter = 0 
            
            for st_key in ['public', 'private', 'isolated']:
                st_detail = az_master_c.get(st_key, {})
                if not st_detail.get('enabled') or st_detail.get('count', 0) <= 0:
                    continue
                num_s = st_detail.get('count', 0)
                s_mask = st_detail.get('cidr_mask')
                name_pfx = st_detail.get('name_prefix', st_key.capitalize())
                
                if s_mask is None:
                    logger.error(f"Missing 'cidr_mask' for {st_key} in AZ {actual_az}.")
                    return False
                
                for i in range(num_s):
                    all_possible_subnets_of_mask = list(vpc_network.subnets(new_prefix=s_mask))
                    
                    if current_subnet_index >= len(all_possible_subnets_of_mask):
                        logger.error(f"Ran out of available CIDR blocks for subnets of mask /{s_mask} in VPC {self.vpc_cidr_block}.")
                        return False
                    
                    alloc_cidr = str(all_possible_subnets_of_mask[current_subnet_index])
                    current_subnet_index += 1 
                    
                    s_id = f"{name_pfx.replace('-', '')}{az_sfx.upper()}{i+1}Snet{s_counter}"
                    s_tag = f"{self.vpc_core_conf.get('name', 'VPC')}-{name_pfx}-{az_sfx}-{i+1}"
                    
                    cfn_s = ec2.CfnSubnet(
                        self,
                        s_id,
                        vpc_id=self.vpc_id,
                        cidr_block=alloc_cidr,
                        availability_zone=actual_az,
                        map_public_ip_on_launch=(st_key == 'public'),
                        tags=[CfnTag(key="Name", value=s_tag)]
                    )
                    if self.cfn_vpc_resource:
                        cfn_s.add_dependency(self.cfn_vpc_resource)
                    map_attr = getattr(self, f"{st_key}_subnets_map")
                    map_attr.setdefault(actual_az, []).append(cfn_s)
                    self.all_created_subnets.append(cfn_s)
                    logger.info(f"Defined {st_key} subnet '{s_tag}' CIDR {alloc_cidr} in {actual_az}.")
                    s_counter += 1
        logger.info(f"Finished defining {len(self.all_created_subnets)} subnets.")
        return True

    def _get_subnets_by_type_for_az(self, subnet_type_key: str, az: str) -> list[ec2.CfnSubnet]:
        map_to_use = getattr(self, f"{subnet_type_key.lower()}_subnets_map", {})
        return map_to_use.get(az, [])

    def _get_all_subnets_of_type(self, subnet_type_key: str) -> list[ec2.CfnSubnet]:
        flat_list = []
        map_to_use = getattr(self, f"{subnet_type_key.lower()}_subnets_map", {})
        for az_subnets in map_to_use.values():
            flat_list.extend(az_subnets)
        return flat_list

    def _create_gateways_low_level(self):
        if not self.vpc_id:
            logger.error("Cannot create gateways: VPC ID not set.")
            return
        igw_c = self.gateways_conf.get("internet_gateway", {})
        if igw_c.get("enabled", False):
            logger.info("Creating Internet Gateway...")
            vpc_pfx = self.vpc_core_conf.get('name', 'VPC')
            self.internet_gateway_resource = ec2.CfnInternetGateway(
                self,
                "CFNIGW",
                tags=[CfnTag(key="Name", value=f"{vpc_pfx}-IGW")]
            )
            if self.cfn_vpc_resource:
                self.internet_gateway_resource.add_dependency(self.cfn_vpc_resource)
            self.igw_attachment_resource = ec2.CfnVPCGatewayAttachment(
                self,
                "CFNIGWAttach",
                vpc_id=self.vpc_id,
                internet_gateway_id=self.internet_gateway_resource.ref
            )
            logger.info(f"IGW '{self.internet_gateway_resource.ref}' defined and attached to '{self.vpc_id}'.")

        nat_conf = self.gateways_conf.get("nat_gateways", {})
        if nat_conf.get("enabled", False):
            count_per_az = nat_conf.get("count_per_az", 0)
            total_count = nat_conf.get("total_count", 0)  

            if count_per_az > 0:  
                logger.info(f"Planning {count_per_az} NAT Gateway(s) per AZ with public subnets.")
                nat_created_total = 0
                for az_name in self.availability_zones_used:
                    public_subnets_in_this_az = self._get_subnets_by_type_for_az('public', az_name)
                    if not public_subnets_in_this_az:
                        logger.info(f"No public subnets in AZ '{az_name}' to place NAT Gateways for HA strategy.")
                        continue
                    for i in range(min(count_per_az, len(public_subnets_in_this_az))):
                        target_public_subnet = public_subnets_in_this_az[i]
                        az_short_suffix = az_name.split(self.region)[-1] if self.region and self.region in az_name else az_name
                        eip_id = f"NatEIP{az_short_suffix.upper()}{i+1}"
                        eip_tag = f"{self.vpc_core_conf.get('name', 'VPC')}-NatEIP-{az_short_suffix}{i+1}"
                        eip = ec2.CfnEIP(self, eip_id, domain="vpc", tags=[CfnTag(key="Name", value=eip_tag)])
                        self.nat_gateway_eips_list.append(eip)
                        nat_id = f"NatGW{az_short_suffix.upper()}{i+1}"
                        nat_tag = f"{self.vpc_core_conf.get('name', 'VPC')}-NatGW-{az_short_suffix}{i+1}"
                        nat_gw = ec2.CfnNatGateway(
                            self,
                            nat_id,
                            subnet_id=target_public_subnet.ref,
                            allocation_id=eip.attr_allocation_id,
                            tags=[CfnTag(key="Name", value=nat_tag)]
                        )
                        if self.igw_attachment_resource:
                            nat_gw.add_dependency(self.igw_attachment_resource)
                        self.nat_gateways_by_az[az_name] = nat_gw  
                        logger.info(f"NAT Gateway '{nat_id}' defined in subnet '{target_public_subnet.ref}' (AZ: {az_name}).")
                        nat_created_total += 1
                logger.info(f"Defined {nat_created_total} NAT Gateways (per-AZ strategy).")

            elif total_count > 0:  
                logger.info(f"Planning a total of {total_count} NAT Gateway(s) for the VPC.")
                public_subnets_all = self._get_all_subnets_of_type('public')
                if not public_subnets_all:
                    logger.error(f"Cannot create {total_count} NAT Gateways: No public subnets found in any AZ.")
                    return
                num_to_create = min(total_count, len(public_subnets_all))
                if num_to_create < total_count:
                    logger.warning(f"Requested {total_count} total NATs, but only {num_to_create} public subnets available. Creating {num_to_create} NATs.")
                for i in range(num_to_create):
                    target_public_subnet = public_subnets_all[i]  
                    az_of_subnet = target_public_subnet.availability_zone  
                    eip_id = f"SharedNatEIP{i+1}"
                    eip_tag = f"{self.vpc_core_conf.get('name', 'VPC')}-SharedNatEIP-{i+1}"
                    eip = ec2.CfnEIP(self, eip_id, domain="vpc", tags=[CfnTag(key="Name", value=eip_tag)])
                    self.nat_gateway_eips_list.append(eip)
                    nat_id = f"SharedNatGW{i+1}"
                    nat_tag = f"{self.vpc_core_conf.get('name', 'VPC')}-SharedNatGW-{i+1}"
                    nat_gw = ec2.CfnNatGateway(
                        self,
                        nat_id,
                        subnet_id=target_public_subnet.ref,
                        allocation_id=eip.attr_allocation_id,
                        tags=[CfnTag(key="Name", value=nat_tag)]
                    )
                    if self.igw_attachment_resource:
                        nat_gw.add_dependency(self.igw_attachment_resource)
                    self.nat_gateways_list.append(nat_gw)
                    logger.info(f"Shared NAT Gateway '{nat_id}' defined in subnet '{target_public_subnet.ref}' (AZ: {az_of_subnet}).")
                logger.info(f"Defined {len(self.nat_gateways_list)} shared NAT Gateways (total_count strategy).")
            else:
                logger.info("NAT Gateway creation skipped (neither count_per_az nor total_count > 0).")
        else:
            logger.info("NAT Gateway creation skipped by global 'enabled' flag in gateways_config.")

    def _create_route_tables_for_vpc(self):
        if not self.route_tables_conf.get("enabled", False) or not self.vpc_id:
            logger.info("RT management skipped.")
            return
        logger.info("Creating Route Tables...")
        vpc_pfx = self.vpc_core_conf.get('name', 'VPC')
        rt_cats_conf = self.route_tables_conf.get('categories', {})
        nat_gateways_config = self.gateways_conf.get("nat_gateways", {})
        is_nat_per_az = nat_gateways_config.get("enabled", False) and nat_gateways_config.get("count_per_az", 0) > 0

        # Public Route Table (Shared)
        public_rt_conf = rt_cats_conf.get('public', {})
        if public_rt_conf.get("enabled", False) and self._get_all_subnets_of_type('public'):
            rt_id = "PublicSharedRT"
            rt_tag = f"{vpc_pfx}-public-shared-rt"
            self.public_shared_rt = ec2.CfnRouteTable(
                self,
                rt_id,
                vpc_id=self.vpc_id,
                tags=[CfnTag(key="Name", value=rt_tag)]
            )
            if self.cfn_vpc_resource:
                self.public_shared_rt.add_dependency(self.cfn_vpc_resource)
            logger.info(f"Defined shared public RT '{rt_id}' ({rt_tag}).")

        # Private Route Tables
        private_rt_conf = rt_cats_conf.get('private', {})
        if private_rt_conf.get("enabled", False) and self._get_all_subnets_of_type('private'):
            if is_nat_per_az:  
                logger.info("Creating per-AZ private route tables for HA NAT Gateway strategy.")
                for az_name in self.availability_zones_used:
                    if self._get_subnets_by_type_for_az('private', az_name):  
                        az_suffix = az_name.split(self.region)[-1] if self.region and self.region in az_name else az_name
                        rt_id = f"PrivateRTForAZ{az_suffix.upper()}"
                        rt_tag = f"{vpc_pfx}-private-{az_suffix}-rt"
                        rt = ec2.CfnRouteTable(
                            self,
                            rt_id,
                            vpc_id=self.vpc_id,
                            tags=[CfnTag(key="Name", value=rt_tag)]
                        )
                        if self.cfn_vpc_resource:
                            rt.add_dependency(self.cfn_vpc_resource)
                        self.private_per_az_rts[az_name] = rt
                        logger.info(f"Defined private RT '{rt_id}' for AZ {az_name}.")
            else:  
                rt_id = "PrivateSharedRT"
                rt_tag = f"{vpc_pfx}-private-shared-rt"
                self.private_shared_rt = ec2.CfnRouteTable(
                    self,
                    rt_id,
                    vpc_id=self.vpc_id,
                    tags=[CfnTag(key="Name", value=rt_tag)]
                )
                if self.cfn_vpc_resource:
                    self.private_shared_rt.add_dependency(self.cfn_vpc_resource)
                logger.info(f"Defined shared private RT '{rt_id}' ({rt_tag}).")

        # Isolated Route Table (Shared)
        isolated_rt_conf = rt_cats_conf.get('isolated', {})
        if isolated_rt_conf.get("enabled", False) and self._get_all_subnets_of_type('isolated'):
            rt_id = "IsolatedSharedRT"
            rt_tag = f"{vpc_pfx}-isolated-shared-rt"
            self.isolated_shared_rt = ec2.CfnRouteTable(
                self,
                rt_id,
                vpc_id=self.vpc_id,
                tags=[CfnTag(key="Name", value=rt_tag)]
            )
            if self.cfn_vpc_resource:
                self.isolated_shared_rt.add_dependency(self.cfn_vpc_resource)
            logger.info(f"Defined shared isolated RT '{rt_id}' ({rt_tag}).")

    def _associate_subnets_with_route_tables(self):
        if not self.route_tables_conf.get("enabled", False):
            logger.info("Subnet to RT association skipped.")
            return
        logger.info("Associating subnets with route tables...")
        assoc_counter = 0
        nat_gateways_config = self.gateways_conf.get("nat_gateways", {})
        is_nat_per_az = nat_gateways_config.get("enabled", False) and nat_gateways_config.get("count_per_az", 0) > 0

        # Public subnets to shared public RT
        if self.public_shared_rt:
            public_subnets = self._get_all_subnets_of_type('public')
            for idx, snet_cfn in enumerate(public_subnets):
                ec2.CfnSubnetRouteTableAssociation(
                    self,
                    f"PublicSnetAssoc{idx}{assoc_counter}",
                    subnet_id=snet_cfn.ref,
                    route_table_id=self.public_shared_rt.ref
                )
                assoc_counter += 1
            if public_subnets:
                logger.info(f"Associated {len(public_subnets)} public subnets with {self.public_shared_rt.ref}.")

        # Private subnets
        if is_nat_per_az:  
            for az_name, private_rt_for_az in self.private_per_az_rts.items():
                private_subnets_in_az = self._get_subnets_by_type_for_az('private', az_name)
                for idx, snet_cfn in enumerate(private_subnets_in_az):
                    ec2.CfnSubnetRouteTableAssociation(
                        self,
                        f"PrivateSnet{az_name.replace('-','')}Assoc{idx}{assoc_counter}",
                        subnet_id=snet_cfn.ref,
                        route_table_id=private_rt_for_az.ref
                    )
                    assoc_counter += 1
                if private_subnets_in_az:
                    logger.info(f"Associated {len(private_subnets_in_az)} private subnets in AZ {az_name} with RT {private_rt_for_az.ref}.")
        elif self.private_shared_rt:  
            private_subnets = self._get_all_subnets_of_type('private')
            for idx, snet_cfn in enumerate(private_subnets):
                ec2.CfnSubnetRouteTableAssociation(
                    self,
                    f"PrivateSharedSnetAssoc{idx}{assoc_counter}",
                    subnet_id=snet_cfn.ref,
                    route_table_id=self.private_shared_rt.ref
                )
                assoc_counter += 1
            if private_subnets:
                logger.info(f"Associated {len(private_subnets)} private subnets with shared private RT {self.private_shared_rt.ref}.")

        # Isolated subnets to shared isolated RT
        if self.isolated_shared_rt:
            isolated_subnets = self._get_all_subnets_of_type('isolated')
            for idx, snet_cfn in enumerate(isolated_subnets):
                ec2.CfnSubnetRouteTableAssociation(
                    self,
                    f"IsolatedSnetAssoc{idx}{assoc_counter}",
                    subnet_id=snet_cfn.ref,
                    route_table_id=self.isolated_shared_rt.ref
                )
                assoc_counter += 1
            if isolated_subnets:
                logger.info(f"Associated {len(isolated_subnets)} isolated subnets with {self.isolated_shared_rt.ref}.")
        logger.info(f"Defined {assoc_counter} total subnet RT associations.")

    def _configure_routes_for_vpc(self):
        if not self.route_tables_conf.get("enabled", False):
            logger.info("Route configuration skipped.")
            return
        logger.info("Configuring routes...")
        route_id_counter = 0
        def_routes_c = self.route_tables_conf.get('default_routes', {})
        rt_cats_conf = self.route_tables_conf.get('categories', {})
        nat_gateways_config = self.gateways_conf.get("nat_gateways", {})
        is_nat_per_az = nat_gateways_config.get("enabled", False) and nat_gateways_config.get("count_per_az", 0) > 0

        # Default Public to IGW
        if def_routes_c.get('public_to_igw', False) and self.internet_gateway_resource and self.public_shared_rt:
            logger.info(f"Adding default route (IGW) to public RT '{self.public_shared_rt.ref}'.")
            ec2.CfnRoute(
                self,
                f"PubDefRouteIGW{route_id_counter}",
                route_table_id=self.public_shared_rt.ref,
                destination_cidr_block="0.0.0.0/0",
                gateway_id=self.internet_gateway_resource.ref
            ).add_dependency(self.igw_attachment_resource)
            route_id_counter += 1

        # Default Private to NAT
        if def_routes_c.get('private_to_nat', False):
            if is_nat_per_az:  
                for az_name, private_rt_for_az in self.private_per_az_rts.items():
                    nat_gw_in_az = self.nat_gateways_by_az.get(az_name)
                    if nat_gw_in_az:
                        logger.info(f"Adding default route to NAT GW '{nat_gw_in_az.ref}' in private RT '{private_rt_for_az.ref}' for AZ {az_name}.")
                        ec2.CfnRoute(
                            self,
                            f"PrivDefRouteNATForAZ{az_name.replace('-','')}{route_id_counter}",
                            route_table_id=private_rt_for_az.ref,
                            destination_cidr_block="0.0.0.0/0",
                            nat_gateway_id=nat_gw_in_az.ref
                        )
                        route_id_counter += 1
                    else:
                        logger.warning(f"Config requests private_to_nat for AZ {az_name}, but no NAT GW found in that AZ.")
            elif self.private_shared_rt and self.nat_gateways_list:  
                target_nat_gw = self.nat_gateways_list[0]  
                logger.warning(f"PRIVATE ROUTING (Shared): Shared private RT '{self.private_shared_rt.ref}' routes to FIRST shared NAT GW '{target_nat_gw.ref}'.")
                ec2.CfnRoute(
                    self,
                    f"PrivSharedDefRouteNAT{route_id_counter}",
                    route_table_id=self.private_shared_rt.ref,
                    destination_cidr_block="0.0.0.0/0",
                    nat_gateway_id=target_nat_gw.ref
                )
                route_id_counter += 1
            else:
                logger.warning("Config requests private_to_nat, but conditions for routing (per-AZ or shared NAT/RT) not fully met.")

        # Custom Routes
        for cat_key, cat_details in rt_cats_conf.items():
            target_rts_for_custom = []
            if cat_key == "private" and is_nat_per_az:
                target_rts_for_custom.extend(self.private_per_az_rts.values())
            elif cat_key == "private" and self.private_shared_rt:
                target_rts_for_custom.append(self.private_shared_rt)
            elif cat_key == "public" and self.public_shared_rt:
                target_rts_for_custom.append(self.public_shared_rt)
            elif cat_key == "isolated" and self.isolated_shared_rt:
                target_rts_for_custom.append(self.isolated_shared_rt)

            if not cat_details.get("enabled", False) or not target_rts_for_custom:
                continue

            custom_routes_cfg = cat_details.get('routes', [])
            if custom_routes_cfg:
                for rt_idx, target_rt_obj in enumerate(target_rts_for_custom):
                    logger.info(f"Adding {len(custom_routes_cfg)} custom route(s) to '{cat_key}' RT '{target_rt_obj.ref}' (index {rt_idx})...")
                    for i, r_def in enumerate(custom_routes_cfg):
                        r_props = {'route_table_id': target_rt_obj.ref}
                        has_d = False
                        has_t = False
                        if r_def.get('destination_cidr_block'):
                            r_props['destination_cidr_block'] = r_def['destination_cidr_block']
                            has_d = True
                        tgt_type = r_def.get('target_type', '').lower()
                        tgt_id_cfg = r_def.get('target_id')
                        comment = r_def.get('comment', '')  # For logging
                        if tgt_type == 'internet_gateway' and self.internet_gateway_resource:
                            r_props['gateway_id'] = self.internet_gateway_resource.ref
                            has_t = True
                        elif tgt_type == 'nat_gateway':
                            if is_nat_per_az:
                                current_az_for_rt = next((az for az, rt in self.private_per_az_rts.items() if rt.ref == target_rt_obj.ref), None)
                                nat_target_for_custom = self.nat_gateways_by_az.get(current_az_for_rt) if current_az_for_rt else None
                            else: 
                                nat_target_for_custom = self.nat_gateways_list[0] if self.nat_gateways_list else None
                            
                            if nat_target_for_custom:
                                r_props['nat_gateway_id'] = nat_target_for_custom.ref
                                has_t = True
                                logger.info(f"Custom route in {cat_key} RT {target_rt_obj.ref} to NAT GW {nat_target_for_custom.ref} (simplified target selection).")
                            else:
                                logger.warning(f"Custom route {comment} target 'nat_gateway' specified but no NAT GWs available.")
                                continue
                        elif tgt_type == 'vpc_peering_connection' and tgt_id_cfg:
                            r_props['vpc_peering_connection_id'] = tgt_id_cfg
                            has_t = True
                        if has_d and has_t:
                            ec2.CfnRoute(self, f"CustomRoute{cat_key.capitalize()}{rt_idx}{route_id_counter}", **r_props)
                            route_id_counter += 1
                        else:
                            logger.warning(f"Skipping invalid custom route: {r_def}")
        logger.info(f"Finished routes. Total CfnRoute resources: {route_id_counter}.")

    def _create_security_groups_low_level(self):
        if not self.security_groups_conf.get("enabled", False) or not self.vpc_id:
            logger.info("SG creation skipped.")
            return
        logger.info("Creating SGs...")
        groups_cfg = self.security_groups_conf.get("groups", {})
        for sg_cfg_key, sg_details in groups_cfg.items():
            if not sg_details.get("enabled", False):
                logger.info(f"Skipping SG '{sg_cfg_key}'.")
                continue
            sg_tag = sg_details.get("name", f"{self.vpc_core_conf.get('name','VPC')}-{sg_cfg_key}")
            sg_desc = sg_details.get("description", f"SG {sg_tag}")
            sg_cdk_id = f"SG{sg_cfg_key.replace('-','').capitalize()}"
            cfn_sg = ec2.CfnSecurityGroup(
                self,
                sg_cdk_id,
                group_description=sg_desc,
                vpc_id=self.vpc_id,
                group_name=sg_tag,
                tags=[CfnTag(key="Name", value=sg_tag)]
            )
            if self.cfn_vpc_resource:
                cfn_sg.add_dependency(self.cfn_vpc_resource)
            self.security_groups_map[sg_cfg_key] = cfn_sg
            for i, r_conf in enumerate(sg_details.get("ingress", [])):
                r_props = self._parse_sg_rule_low_level(r_conf, cfn_sg.attr_group_id, is_egress=False)
                if r_props:
                    ec2.CfnSecurityGroupIngress(self, f"{sg_cdk_id}In{i}", **r_props)
            if sg_details.get("allow_all_outbound", True):
                ec2.CfnSecurityGroupEgress(
                    self,
                    f"{sg_cdk_id}EgressAll",
                    group_id=cfn_sg.attr_group_id,
                    ip_protocol="-1",
                    cidr_ip="0.0.0.0/0",
                    description="Allow all outbound"
                )
            else:
                for i, r_conf in enumerate(sg_details.get("egress", [])):
                    r_props = self._parse_sg_rule_low_level(r_conf, cfn_sg.attr_group_id, is_egress=True)
                    if r_props:
                        ec2.CfnSecurityGroupEgress(self, f"{sg_cdk_id}Egress{i}", **r_props)
        logger.info(f"Defined {len(self.security_groups_map)} SGs.")

    def _parse_sg_rule_low_level(self, rule_config: dict, cfn_sg_attr_group_id: str, is_egress: bool) -> dict | None:
        props = {'group_id': cfn_sg_attr_group_id}
        proto_map = {'tcp': '6', 'udp': '17', 'icmp': '1', 'all': '-1', 6: '6', 17: '17', 1: '1', -1: '-1'}
        proto_in = rule_config.get("protocol", "-1")
        props['ip_protocol'] = proto_map.get(str(proto_in).lower(), str(proto_in))
        if props['ip_protocol'] in ['6', '17']:
            p_val = rule_config.get("port")
            fp_val = rule_config.get("from_port", p_val)
            tp_val = rule_config.get("to_port", p_val)
            if fp_val is None or tp_val is None:
                logger.error(f"TCP/UDP rule needs port: {rule_config}")
                return None
            props['from_port'] = int(fp_val)
            props['to_port'] = int(tp_val)
        elif props['ip_protocol'] == '1':
            props['from_port'] = int(rule_config.get("icmp_type", -1))
            props['to_port'] = int(rule_config.get("icmp_code", -1))
        peer_spec = False
        
        # New: Handle SECURITY_GROUP_ID_REF for inter-SG peering within the same VPC instance
        if "peer_type" in rule_config and rule_config["peer_type"] == "SECURITY_GROUP_ID_REF":
            peer_sg_ref_id = rule_config.get("peer_value_ref_id") or rule_config.get("peer_value") # Support old and new keys
            if peer_sg_ref_id and peer_sg_ref_id in self.security_groups_map:
                peer_sg_obj = self.security_groups_map[peer_sg_ref_id]
                if is_egress:
                    props['destination_security_group_id'] = peer_sg_obj.attr_group_id
                else:
                    props['source_security_group_id'] = peer_sg_obj.attr_group_id
                peer_spec = True
                logger.info(f"SG Rule: Resolved SECURITY_GROUP_ID_REF '{peer_sg_ref_id}' to SG {peer_sg_obj.attr_group_id}")
            else:
                logger.error(f"SG Rule: Could not resolve SECURITY_GROUP_ID_REF '{peer_sg_ref_id}' in current VPC instance. Rule: {rule_config}")
                return None
        # Original: Handle other peer types
        elif "peer_cidr" in rule_config: # Old name
            props['cidr_ip'] = rule_config["peer_cidr"]
            peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "CIDR_IPV4": # New name
            props['cidr_ip'] = rule_config["peer_value"]
            peer_spec = True
        elif "peer_cidr_ipv6" in rule_config: # Old name
            props['cidr_ipv6'] = rule_config["peer_cidr_ipv6"]
            peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "CIDR_IPV6": # New name
            props['cidr_ipv6'] = rule_config["peer_value"]
            peer_spec = True
        elif "peer_sg_id_from_config" in rule_config: # Old name for internal SG reference
            peer_sg_key = rule_config["peer_sg_id_from_config"]
            if peer_sg_key in self.security_groups_map:
                peer_sg_obj = self.security_groups_map[peer_sg_key]
                if is_egress:
                    props['destination_security_group_id'] = peer_sg_obj.attr_group_id
                else:
                    props['source_security_group_id'] = peer_sg_obj.attr_group_id
                peer_spec = True
            else:
                logger.error(f"Peer SG key '{peer_sg_key}' not found. Rule: {rule_config}")
                return None
        elif "peer_type" in rule_config and rule_config["peer_type"] == "SECURITY_GROUP_ID": # New name for external SG ID
             # This will be a raw ID, not resolved from self.security_groups_map
             if is_egress:
                 props['destination_security_group_id'] = rule_config["peer_value"]
             else:
                 props['source_security_group_id'] = rule_config["peer_value"]
             peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "ANY_IPV4":
            props['cidr_ip'] = "0.0.0.0/0"
            peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "ANY_IPV6":
            props['cidr_ipv6'] = "::/0"
            peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "SELF":
            if is_egress: # Egress to self is usually not needed as outbound is implicitly allowed.
                props['destination_security_group_id'] = cfn_sg_attr_group_id
            else:
                props['source_security_group_id'] = cfn_sg_attr_group_id
            peer_spec = True
        elif "peer_prefix_list_id" in rule_config:
            if is_egress:
                props['destination_prefix_list_id'] = rule_config["peer_prefix_list_id"]
            else:
                props['source_prefix_list_id'] = rule_config["peer_prefix_list_id"]
            peer_spec = True
        elif "peer_type" in rule_config and rule_config["peer_type"] == "PREFIX_LIST_ID":
            if is_egress:
                props['destination_prefix_list_id'] = rule_config["peer_value"]
            else:
                props['source_prefix_list_id'] = rule_config["peer_value"]
            peer_spec = True
        else:
            logger.error(f"SG rule needs a valid peer type/value: {rule_config}")
            return None
            
        if "description" in rule_config:
            props['description'] = rule_config["description"]
        return props

    def _create_network_acls_low_level(self):
        if not self.network_acls_conf.get("enabled", False) or not self.vpc_id:
            logger.info("NACL creation skipped.")
            return
        logger.info("Configuring NACLs...")
        nacl_rulesets_cfg = self.network_acls_conf.get("rules", {})
        entry_count = 0
        for nacl_cfg_key, nacl_details in nacl_rulesets_cfg.items():
            if not nacl_details.get("enabled", False):
                logger.info(f"Skipping NACL '{nacl_cfg_key}'.")
                continue
            nacl_tag = nacl_details.get("name_prefix", f"{self.vpc_core_conf.get('name','VPC')}-{nacl_cfg_key}Nacl")
            nacl_cdk_id = f"NACL{nacl_cfg_key.replace('-','').capitalize()}"
            cfn_nacl = ec2.CfnNetworkAcl(
                self,
                nacl_cdk_id,
                vpc_id=self.vpc_id,
                tags=[CfnTag(key="Name", value=nacl_tag)]
            )
            if self.cfn_vpc_resource:
                cfn_nacl.add_dependency(self.cfn_vpc_resource)
            self.network_acls_map[nacl_cfg_key] = cfn_nacl
            for r_conf in nacl_details.get("inbound", []):
                r_props = self._parse_nacl_rule_low_level(r_conf, cfn_nacl.ref, is_egress=False)
                if r_props:
                    ec2.CfnNetworkAclEntry(self, f"{nacl_cdk_id}In{entry_count}", **r_props)
                    entry_count += 1
            for r_conf in nacl_details.get("outbound", []):
                r_props = self._parse_nacl_rule_low_level(r_conf, cfn_nacl.ref, is_egress=True)
                if r_props:
                    ec2.CfnNetworkAclEntry(self, f"{nacl_cdk_id}Out{entry_count}", **r_props)
                    entry_count += 1
            
            # Subnet to NACL Association
            # Determine which subnets to associate based on 'nacl_cfg_key' (e.g., 'Public', 'Private', 'Isolated')
            subnets_to_associate = []
            if nacl_cfg_key.lower() == 'public':
                subnets_to_associate = self._get_all_subnets_of_type('public')
            elif nacl_cfg_key.lower() == 'private':
                subnets_to_associate = self._get_all_subnets_of_type('private')
            elif nacl_cfg_key.lower() == 'isolated':
                subnets_to_associate = self._get_all_subnets_of_type('isolated')
            else:
                logger.warning(f"NACL config key '{nacl_cfg_key}' does not map to a known subnet type (public, private, isolated). No subnets will be automatically associated.")

            if subnets_to_associate:
                logger.info(f"Associating NACL '{nacl_tag}' with {len(subnets_to_associate)} '{nacl_cfg_key.lower()}' subnets.")
                for i, snet_cfn_obj in enumerate(subnets_to_associate):
                    ec2.CfnSubnetNetworkAclAssociation(
                        self,
                        f"{nacl_cdk_id}Assoc{i}",
                        subnet_id=snet_cfn_obj.ref,
                        network_acl_id=cfn_nacl.ref
                    )
            else:
                logger.warning(f"NACL '{nacl_tag}' defined, but no '{nacl_cfg_key.lower()}' subnets for association.")
        logger.info(f"Defined {len(self.network_acls_map)} NACLs.")

    def _parse_nacl_rule_low_level(self, rule_config: dict, cfn_nacl_ref: str, is_egress: bool) -> dict | None:
        props = {'network_acl_id': cfn_nacl_ref, 'egress': is_egress}
        proto_map = {'tcp': 6, 'udp': 17, 'icmp': 1, 'all': -1, '6': 6, '17': 17, '1': 1, '-1': -1}
        if "rule" not in rule_config:
            logger.error(f"NACL rule missing 'rule' number: {rule_config}")
            return None
        try:
            props['rule_number'] = int(rule_config['rule'])
        except ValueError:
            logger.error(f"NACL rule_number '{rule_config['rule']}' not int.")
            return None
        if "action" not in rule_config or str(rule_config['action']).lower() not in ['allow', 'deny']:
            logger.error(f"NACL rule missing/invalid 'action': {rule_config}")
            return None
        props['rule_action'] = str(rule_config['action']).upper() 
        if "protocol" not in rule_config:
            logger.error(f"NACL rule missing 'protocol': {rule_config}")
            return None
        proto_in_str = str(rule_config['protocol']).lower()
        if proto_in_str in proto_map:
            props['protocol'] = proto_map[proto_in_str]
        else:
            try:
                props['protocol'] = int(proto_in_str)
            except ValueError:
                logger.error(f"NACL rule invalid 'protocol': {proto_in_str}")
                return None
        if "cidr" in rule_config:
            props['cidr_block'] = rule_config["cidr"]
        elif "cidr_ipv6" in rule_config:
            props['ipv6_cidr_block'] = rule_config["cidr_ipv6"]
        else:
            logger.error(f"NACL rule missing CIDR: {rule_config}")
            return None
        if props['protocol'] in [6, 17]:
            p_val = rule_config.get("port")
            fp_val = rule_config.get("from_port", p_val)
            tp_val = rule_config.get("to_port", p_val)
            if fp_val is not None and tp_val is not None:
                try:
                    props['port_range'] = ec2.CfnNetworkAclEntry.PortRangeProperty(from_=int(fp_val), to=int(tp_val))
                except ValueError:
                    logger.error(f"NACL rule TCP/UDP invalid ports: from='{fp_val}', to='{tp_val}'.")
                    return None
        if props['protocol'] == 1:
            icmp_t_val = rule_config.get("icmp_type")
            icmp_c_val = rule_config.get("icmp_code")
            if icmp_t_val is not None and icmp_c_val is not None:
                try:
                    props['icmp_type_code'] = ec2.CfnNetworkAclEntry.IcmpProperty(type=int(icmp_t_val), code=int(icmp_c_val))
                except ValueError:
                    logger.error(f"NACL rule ICMP invalid type/code: type='{icmp_t_val}', code='{icmp_c_val}'.")
                    return None
        return props

    def _create_dhcp_options_low_level(self):
        if not self.dhcp_options_conf.get("enabled", False) or not self.vpc_id:
            logger.info("DHCP Options skipped.")
            return
        logger.info("Configuring DHCP Options...")
        dhcp_tag = self.dhcp_options_conf.get('name_tag', f"{self.vpc_core_conf.get('name','VPC')}-dhcp")
        dhcp_cfn_p = {}
        for k_cfg, cfn_p_name in {
            "domain_name": "domain_name",
            "domain_name_servers": "domain_name_servers",
            "ntp_servers": "ntp_servers",
            "netbios_name_servers": "netbios_name_servers"
        }.items():
            if k_cfg in self.dhcp_options_conf and self.dhcp_options_conf[k_cfg] is not None:
                if cfn_p_name in ["domain_name_servers", "ntp_servers", "netbios_name_servers"] and not isinstance(self.dhcp_options_conf[k_cfg], list):
                    dhcp_cfn_p[cfn_p_name] = [str(self.dhcp_options_conf[k_cfg])]
                    logger.warning(f"DHCP option '{k_cfg}' expected list, converted single value '{self.dhcp_options_conf[k_cfg]}' to list.")
                else:
                    dhcp_cfn_p[cfn_p_name] = self.dhcp_options_conf[k_cfg]

        if "netbios_node_type" in self.dhcp_options_conf and self.dhcp_options_conf["netbios_node_type"] is not None:
            try:
                dhcp_cfn_p["netbios_node_type"] = int(self.dhcp_options_conf["netbios_node_type"])
            except ValueError:
                logger.error(f"Invalid 'netbios_node_type' in DHCP config: {self.dhcp_options_conf['netbios_node_type']}.")
        
        cfn_tags = [CfnTag(key="Name", value=dhcp_tag)] + [
            CfnTag(key=k, value=v) for k, v in self.dhcp_options_conf.get('tags', {}).items()
        ]
        dhcp_cfn_p["tags"] = cfn_tags
        try:
            cfn_dhcp_set = ec2.CfnDHCPOptions(self, "CfnDHCPOptionsSetRes", **dhcp_cfn_p)
            logger.info(f"DHCP Options Set '{cfn_dhcp_set.ref}' defined.")
            ec2.CfnVPCDHCPOptionsAssociation(
                self,
                "CfnVPCDHCPAssocRes",
                vpc_id=self.vpc_id,
                dhcp_options_id=cfn_dhcp_set.ref
            )
            logger.info(f"Association defined for DHCP Options Set with VPC '{self.vpc_id}'.")
        except Exception as e:
            logger.error(f"Failed to create/associate DHCP Options: {e}")

    def _create_vpc_endpoints_low_level(self):
        if not self.vpc_endpoints_conf.get("enabled", False) or not self.vpc_id:
            logger.info("VPC Endpoint creation skipped.")
            return
        logger.info("Configuring VPC Endpoints...")
        ep_to_create_dict = self.vpc_endpoints_conf.get("services", {})
        ep_cdk_counter = 0
        for ep_cfg_key, ep_details in ep_to_create_dict.items():
            if not ep_details.get("enabled", False):
                logger.info(f"Skipping VPC Endpoint '{ep_cfg_key}'.")
                continue
            s_name_short = ep_details.get('service_name_short')
            ep_type = ep_details.get('type', '').upper()
            if not s_name_short or not ep_type:
                logger.warning(f"Skipping ep '{ep_cfg_key}': Missing service/type.")
                continue
            full_s_name = f"com.amazonaws.{self.region}.{s_name_short}"
            logger.info(f"Defining VPC Endpoint for {full_s_name} ({ep_type}, Config Key: {ep_cfg_key})")
            ep_cdk_id = f"VpcEp{ep_cfg_key.replace('.','').replace('-','').capitalize()}{ep_cdk_counter}"
            
            # Populate route tables for gateway endpoints based on their existence
            # This requires public_shared_rt, private_per_az_rts, private_shared_rt, isolated_shared_rt
            # to be populated by _create_route_tables_for_vpc if they are enabled.
            route_tables_by_category = { 
                'public': [self.public_shared_rt] if self.public_shared_rt else [],
                'private': list(self.private_per_az_rts.values()) if self.private_per_az_rts else ([self.private_shared_rt] if self.private_shared_rt else []),
                'isolated': [self.isolated_shared_rt] if self.isolated_shared_rt else []
            }

            common_cfn_p = {
                'vpc_id': self.vpc_id,
                'service_name': full_s_name,
                'vpc_endpoint_type': ep_type
            }
            if ep_type == 'GATEWAY':
                tgt_rt_ids = []
                for rt_cat_key in ep_details.get('route_table_target_types', []):
                    rt_list_for_cat = route_tables_by_category.get(rt_cat_key.lower())
                    if rt_list_for_cat: 
                        for rt_obj in rt_list_for_cat: 
                            if rt_obj: 
                                tgt_rt_ids.append(rt_obj.ref)
                            else:
                                logger.warning(f"Found None RT object in list for '{rt_cat_key}' for Gateway Ep '{ep_cfg_key}'.")
                    else:
                        logger.warning(f"No route tables found for category '{rt_cat_key}' for Gateway Ep '{ep_cfg_key}'.")
                if not tgt_rt_ids:
                    logger.warning(f"Cannot create Gateway Ep '{ep_cfg_key}': No target RTs.")
                    continue
                common_cfn_p['route_table_ids'] = list(set(tgt_rt_ids))
            elif ep_type == 'INTERFACE':
                tgt_snet_ids = []
                for snet_cat_key in ep_details.get('subnet_target_types', []):
                    tgt_snet_ids.extend([s.ref for s in self._get_all_subnets_of_type(snet_cat_key.lower())])
                if not tgt_snet_ids:
                    logger.warning(f"Cannot create Interface Ep '{ep_cfg_key}': No target subnets.")
                    continue
                common_cfn_p['subnet_ids'] = list(set(tgt_snet_ids))
                common_cfn_p['private_dns_enabled'] = ep_details.get('private_dns_enabled', False)
                tgt_sg_ids = []
                for sg_cfg_key_ep in ep_details.get('security_group_config_ids', []):
                    if sg_cfg_key_ep in self.security_groups_map:
                        tgt_sg_ids.append(self.security_groups_map[sg_cfg_key_ep].attr_group_id)
                    else:
                        logger.warning(f"SG ID '{sg_cfg_key_ep}' for Interface Ep '{ep_cfg_key}' not found.")
                if tgt_sg_ids:
                    common_cfn_p['security_group_ids'] = list(set(tgt_sg_ids))
            else:
                logger.warning(f"Skipping ep '{ep_cfg_key}': Unknown type '{ep_type}'.")
                continue
            try:
                ep_tags = ep_details.get('tags', {})
                endpoint_tags_list = [CfnTag(key="Name", value=f"{s_name_short}-{ep_type}-endpoint")] + \
                                     [CfnTag(key=k, value=v) for k,v in ep_tags.items()]
                common_cfn_p['tags'] = endpoint_tags_list

                cfn_endpoint = ec2.CfnVPCEndpoint(self, ep_cdk_id, **common_cfn_p)
                logger.info(f"VPC Endpoint '{ep_cdk_id}' defined.")
                ep_cdk_counter += 1
            except Exception as e:
                logger.error(f"Failed to define VPC Endpoint '{ep_cdk_id}': {e}")

    def _get_retention_enum(self, days: int | None) -> logs.RetentionDays | None:
        if days is None:
            return None
        mapping = {
            1: logs.RetentionDays.ONE_DAY,
            3: logs.RetentionDays.THREE_DAYS,
            5: logs.RetentionDays.FIVE_DAYS,
            7: logs.RetentionDays.ONE_WEEK,
            14: logs.RetentionDays.TWO_WEEKS,
            30: logs.RetentionDays.ONE_MONTH,
            60: logs.RetentionDays.TWO_MONTHS,
            90: logs.RetentionDays.THREE_MONTHS,
            120: logs.RetentionDays.FOUR_MONTHS,
            150: logs.RetentionDays.FIVE_MONTHS,
            180: logs.RetentionDays.SIX_MONTHS,
            365: logs.RetentionDays.ONE_YEAR,
            400: logs.RetentionDays.THIRTEEN_MONTHS,
            545: logs.RetentionDays.EIGHTEEN_MONTHS,
            731: logs.RetentionDays.TWO_YEARS,
            1827: logs.RetentionDays.FIVE_YEARS,
            3653: logs.RetentionDays.TEN_YEARS
        }
        if days in mapping:
            return mapping[days]
        if 28 <= days <= 31: 
            return logs.RetentionDays.ONE_MONTH
        if 59 <= days <= 62: 
            return logs.RetentionDays.TWO_MONTHS
        
        logger.warning(f"Retention period {days} days not directly mapped to a precise enum value. Using closest standard.")
        if days > 0:
            if days >= 3653: return logs.RetentionDays.TEN_YEARS
            if days >= 1827: return logs.RetentionDays.FIVE_YEARS
            if days >= 731: return logs.RetentionDays.TWO_YEARS
            if days >= 365: return logs.RetentionDays.ONE_YEAR
            if days >= 180: return logs.RetentionDays.SIX_MONTHS
            if days >= 90: return logs.RetentionDays.THREE_MONTHS
            if days >= 60: return logs.RetentionDays.TWO_MONTHS
            if days >= 30: return logs.RetentionDays.ONE_MONTH
            if days >= 14: return logs.RetentionDays.TWO_WEEKS
            if days >= 7: return logs.RetentionDays.ONE_WEEK
            if days >= 5: return logs.RetentionDays.FIVE_DAYS
            if days >= 3: return logs.RetentionDays.THREE_DAYS
            if days >= 1: return logs.RetentionDays.ONE_DAY
        return None 

    def _create_vpc_flow_logs_low_level(self):
        if not self.vpc_flow_logs_conf.get("enabled", False) or not self.vpc_id:
            logger.info("VPC Flow Logs skipped.")
            return
        logger.info("Configuring VPC Flow Logs...")
        fl_tag = self.vpc_flow_logs_conf.get('name_tag', f"{self.vpc_core_conf.get('name','VPC')}-flowlogs")
        tf_type = self.vpc_flow_logs_conf.get('traffic_type', 'ALL').upper()
        dest_type = self.vpc_flow_logs_conf.get('destination_type', 'cloud-watch-logs').lower()
        fl_cfn_p = {
            'resource_id': self.vpc_id,
            'resource_type': 'VPC',
            'traffic_type': tf_type,
            'tags': [CfnTag(key="Name", value=fl_tag)]
        }
        if dest_type == 'cloud-watch-logs':
            fl_cfn_p['log_destination_type'] = 'cloud-watch-logs'
            flow_logs_iam_role = iam.Role(
                self,
                "VpcFlowLogsCWLRole",
                assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com")
            )
            flow_logs_iam_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams"
                    ],
                    resources=["arn:aws:logs:*:*:*"]
                )
            )
            fl_cfn_p['deliver_logs_permission_arn'] = flow_logs_iam_role.role_arn
            lg_name_cfg = self.vpc_flow_logs_conf.get('log_group_name')
            lg_ret_days_int = self.vpc_flow_logs_conf.get('retention_days')
            retention_enum = self._get_retention_enum(lg_ret_days_int)
            if lg_name_cfg:
                fl_cfn_p['log_group_name'] = lg_name_cfg
                logger.info(f"Flow logs to CWL Group: '{lg_name_cfg}'.")
            elif retention_enum is not None:
                cw_lg = logs.LogGroup(self, "VpcFlowLogGroupResource", retention=retention_enum)
                fl_cfn_p['log_group_name'] = cw_lg.log_group_name
                logger.info(f"Flow logs to new CWL Group '{cw_lg.log_group_name}' with retention: {retention_enum.name}.")
            else: 
                cw_lg = logs.LogGroup(self, "VpcFlowLogGroupResource") 
                fl_cfn_p['log_group_name'] = cw_lg.log_group_name
                logger.info(f"Flow logs to new CWL Group '{cw_lg.log_group_name}' (default retention).")

        elif dest_type == 's3':
            s3_arn_cfg = self.vpc_flow_logs_conf.get('s3_destination_arn')
            if not s3_arn_cfg:
                logger.error("S3 Flow Logs: 's3_destination_arn' missing.")
                return
            fl_cfn_p['log_destination_type'] = 's3'
            fl_cfn_p['log_destination'] = s3_arn_cfg
            if 's3_file_format' in self.vpc_flow_logs_conf:
                fl_cfn_p['file_format'] = self.vpc_flow_logs_conf['s3_file_format'].lower()
            logger.info(f"Flow logs to S3: '{s3_arn_cfg}'.")
        else:
            logger.error(f"Unsupported Flow Log destination: {dest_type}.")
            return
        if 'max_aggregation_interval' in self.vpc_flow_logs_conf:
            fl_cfn_p['max_aggregation_interval'] = self.vpc_flow_logs_conf['max_aggregation_interval']
        try:
            ec2.CfnFlowLog(self, "CfnVPCFlowLogRes", **fl_cfn_p)
            logger.info(f"VPC Flow Log '{fl_tag}' definition created for VPC '{self.vpc_id}'.")
        except Exception as e:
            logger.error(f"Failed to define VPC Flow Log: {e}")

    def _apply_instance_tags(self):
        if self.cfn_vpc_resource and self.vpc_core_conf.get('creation_mode') == 'NEW':
            configured_tags = self.vpc_core_conf.get("tags", {})
            if configured_tags:
                logger.info(f"Applying additional tags for CfnVPC '{self.vpc_core_conf.get('name', 'VPC')}' from configuration.")
                # This loop is correct for appending to the Cfn resource's tags list property
                for key, value in configured_tags.items():
                    # Check if tag already exists (e.g., "Name" tag that's part of initial CfnVPC constructor)
                    # The tags list on CfnVPC should be directly accessible as a Python list of CfnTag objects.
                    # No need to use list_tags() or Tags.of() here.
                    tag_exists_in_initial_list = False
                    for existing_tag in self.cfn_vpc_resource.tags: # Access the tags list directly
                        if existing_tag.key == key:
                            tag_exists_in_initial_list = True
                            break
                    
                    if not tag_exists_in_initial_list:
                        # Append new CfnTag objects to the existing tags list property
                        # For L1 constructs, you append to the list, not call .add()
                        self.cfn_vpc_resource.tags.append(CfnTag(key=key, value=value)) # <--- FIX: Use append for list
                    else:
                        logger.debug(f"Tag '{key}' already exists for VPC {self.vpc_core_conf.get('name', 'VPC')}, skipping addition.")
                logger.debug(f"Finished applying additional tags from configuration for CfnVPC.")
            else:
                logger.info(f"No additional tags in 'vpc_core_conf.tags' for CfnVPC '{self.vpc_core_conf.get('name', 'VPC')}'.")
        else:
            logger.info("Tags in config are not applied by this stack to an existing VPC, or no CfnVPC resource to tag.")


    def _populate_subnet_info_from_existing_vpc(self):
        if not self.cdk_vpc_construct or self.vpc_core_conf.get('creation_mode') != 'EXISTING':
            return False 

        logger.info(f"Populating subnet info from existing VPC '{self.vpc_id}' based on tags.")
        
        try:
            self.public_subnets_map = {}
            self.private_subnets_map = {}
            self.isolated_subnets_map = {}
            self.all_created_subnets = []

            for subnet in self.cdk_vpc_construct.public_subnets:
                if isinstance(subnet.node.default_child, ec2.CfnSubnet):
                    self.public_subnets_map.setdefault(subnet.availability_zone, []).append(subnet.node.default_child)
                    self.all_created_subnets.append(subnet.node.default_child)
            for subnet in self.cdk_vpc_construct.private_subnets:
                if isinstance(subnet.node.default_child, ec2.CfnSubnet):
                    self.private_subnets_map.setdefault(subnet.availability_zone, []).append(subnet.node.default_child)
                    self.all_created_subnets.append(subnet.node.default_child)
            for subnet in self.cdk_vpc_construct.isolated_subnets:
                if isinstance(subnet.node.default_child, ec2.CfnSubnet):
                    self.isolated_subnets_map.setdefault(subnet.availability_zone, []).append(subnet.node.default_child)
                    self.all_created_subnets.append(subnet.node.default_child)
            
            logger.info(f"Populated subnet maps for existing VPC via L2 subnet properties. Total: {len(self.all_created_subnets)}.")
            return True
        except Exception as e:
            logger.warning(f"Could not populate subnet info from L2 VPC subnets for existing VPC '{self.vpc_id}': {e}. This might affect routing/NACLs. Consider explicit subnet IDs in config for existing VPCs.")
            return False