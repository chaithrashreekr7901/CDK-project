# cdk_project/pipeline/application_pipelines_group_nested_stack.py

import logging
import typing
import aws_cdk as cdk
from aws_cdk import NestedStack, aws_ec2 as ec2, aws_iam as iam, aws_autoscaling as autoscaling
from constructs import Construct

from .individual_app_pipeline_nested_stack import IndividualApplicationPipelineNestedStack

logger = logging.getLogger(__name__)

class ApplicationPipelinesGroupNestedStack(NestedStack):
    """
    A nested stack that orchestrates the creation of multiple individual application deployment pipelines.
    """
    def __init__(self, scope: Construct, id: str, *,
                 pipeline_deployments_config: typing.List[typing.Dict],
                 created_vpcs_map: typing.Dict[str, ec2.IVpc],
                 created_ec2_instances_map: typing.Dict[str, ec2.Instance],
                 created_asgs_map: typing.Dict[str, autoscaling.AutoScalingGroup],
                 # CHANGED: Expects iam.IRole objects from MainOrchestrator
                 created_iam_roles_map: typing.Dict[str, iam.IRole],
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(scope, id, description=description, **kwargs)

        logger.info(f"ApplicationPipelinesGroupNestedStack '{id}': Initializing.")
        cdk.Tags.of(self).add("PipelineGroup", "ApplicationDeployment")

        if not pipeline_deployments_config:
            logger.warning("ApplicationPipelinesGroupNestedStack: No pipeline definitions found.")
            return

        # Prepare a new map containing only ARNs (strings) to pass to individual pipelines
        # This resolves the TypeError when passing IRole objects to from_role_arn
        iam_roles_map_for_individual_pipelines: typing.Dict[str, str] = {}
        for role_id, role_obj in created_iam_roles_map.items():
            if hasattr(role_obj, 'role_arn'):
                iam_roles_map_for_individual_pipelines[role_id] = role_obj.role_arn
            else:
                logger.warning(f"Role object for '{role_id}' does not have a 'role_arn' attribute. Skipping for individual pipeline map.")


        for pipeline_config in pipeline_deployments_config:
            if pipeline_config.get("enabled", False):
                pipeline_id = pipeline_config["id"]
                logger.info(f"ApplicationPipelinesGroupNestedStack: Creating individual pipeline NestedStack for '{pipeline_id}'.")

                # Pass the maps directly to the individual pipeline stack
                IndividualApplicationPipelineNestedStack(
                    self,
                    f"Pipeline-{pipeline_id}", # Unique ID for the nested stack
                    pipeline_config=pipeline_config,
                    created_vpcs_map=created_vpcs_map,
                    created_ec2_instances_map=created_ec2_instances_map,
                    created_asgs_map=created_asgs_map,
                    # CHANGED: Pass the new map containing ARNs (strings)
                    created_iam_roles_map=iam_roles_map_for_individual_pipelines,
                    description=f"Nested Stack for application pipeline: {pipeline_id}"
                )
            else:
                logger.info(f"ApplicationPipelinesGroupNestedStack: Skipping disabled pipeline: {pipeline_config.get('id', 'Unknown')}.")

        logger.info(f"ApplicationPipelinesGroupNestedStack '{id}': Initialization complete.")
