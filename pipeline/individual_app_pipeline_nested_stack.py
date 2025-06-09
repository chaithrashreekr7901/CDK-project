import os
import typing
import logging
import aws_cdk as cdk

from aws_cdk import (
    NestedStack,
    Environment,
    aws_codecommit as codecommit,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_codedeploy as codedeploy,
    aws_iam as iam,
    aws_s3 as s3,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    CfnOutput,
    Fn
)

from aws_cdk.aws_codepipeline_actions import CodeStarConnectionsSourceAction

from constructs import Construct

logger = logging.getLogger(__name__)

class IndividualApplicationPipelineNestedStack(NestedStack):
    """
    A nested stack that creates a single AWS CodePipeline for application deployment
    based on a single pipeline definition from deployment_config.py.
    """
    def __init__(self, scope: Construct, id: str, *,
                 pipeline_config: dict,
                 created_vpcs_map: typing.Dict[str, ec2.IVpc],
                 created_ec2_instances_map: typing.Dict[str, typing.Any], # Holds Ec2InstanceNestedStack objects
                 created_asgs_map: typing.Dict[str, typing.Any], # Holds AutoScalingGroupStack objects
                 created_iam_roles_map: typing.Dict[str, str], # Expects ARNs (strings) for this stack
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(scope, id, description=description, **kwargs)

        pipeline_id = pipeline_config["id"]
        logger.info(f"IndividualApplicationPipelineNestedStack '{id}': Initializing for pipeline '{pipeline_id}'")

        cdk.Tags.of(self).add("PipelineId", pipeline_id)
        cdk.Tags.of(self).add("ManagedBy", "CDK-AppPipeline")
        cdk.Tags.of(self).add("AppType", pipeline_config.get("tags", {}).get("Application", "Generic"))

        # --- Source Stage ---
        app_source_output = codepipeline.Artifact(f"{pipeline_id}SourceOutput")
        source_config = pipeline_config["source_config"]
        source_action = None

        if source_config["source_type"] == "GITHUB":
            source_action = CodeStarConnectionsSourceAction(
                connection_arn=source_config["github_connection_arn"],
                owner=source_config["github_repo_owner"],
                repo=source_config["github_repo_name"],
                branch=source_config["github_branch_name"],
                output=app_source_output,
                trigger_on_push=True,
                action_name=f"{pipeline_id}Source"
            )
        elif source_config["source_type"] == "CODECOMMIT":
            repo = codecommit.Repository.from_repository_name(
                self, f"{pipeline_id}CodeCommitRepo", source_config["codecommit_repo_name"]
            )
            source_action = codepipeline_actions.CodeCommitSourceAction(
                id=f"{pipeline_id}Source",
                repository=repo,
                branch=source_config["codecommit_branch_name"],
                output=app_source_output,
                action_name="CodeCommitSource"
            )
        elif source_config["source_type"] == "S3":
            bucket = s3.Bucket.from_bucket_name(
                self, f"{pipeline_id}S3SourceBucket", source_config["s3_bucket_name"]
            )
            source_action = codepipeline_actions.S3SourceAction(
                id=f"{pipeline_id}Source",
                bucket=bucket,
                bucket_key=source_config["s3_object_key"],
                output=app_source_output,
                action_name="S3Source"
            )
        else:
            logger.error(f"Pipeline '{pipeline_id}': Unsupported source_type: {source_config['source_type']}")
            raise ValueError(f"Unsupported source type: {source_config['source_type']}")


        # --- Build Stage (Optional) ---
        app_build_output = codepipeline.Artifact(f"{pipeline_id}BuildOutput")
        build_config = pipeline_config.get("build_config", {})
        build_action = None

        if build_config.get("enabled", False):
            build_commands = (
                build_config.get("commands_pre_build", []) +
                build_config.get("commands_build", []) +
                build_config.get("commands_post_build", [])
            )

            build_spec_content = {
                "version": "0.2",
                "phases": {
                    "install": { "commands": ["echo 'Installing build dependencies...'"] },
                    "build": { "commands": build_commands }
                },
                "artifacts": {
                    "files": build_config.get("artifacts_paths", ["**/*"])
                }
            }

            build_spec = codebuild.BuildSpec.from_source_filename(build_config["github_build_spec_path"]) \
                if build_config.get("github_build_spec_path") else codebuild.BuildSpec.from_object(build_spec_content)

            env_vars = {}
            for key, val_config in build_config.get("environment_variables", {}).items():
                if val_config["type"] == "PLAINTEXT":
                    env_vars[key] = codebuild.BuildEnvironmentVariable(value=val_config["value"], type=codebuild.BuildEnvironmentVariableType.PLAINTEXT)
                elif val_config["type"] == "SECRETS_MANAGER":
                    env_vars[key] = codebuild.BuildEnvironmentVariable(value=val_config["value"], type=codebuild.BuildEnvironmentVariableType.SECRETS_MANAGER)
                elif val_config["type"] == "PARAMETER_STORE":
                    env_vars[key] = codebuild.BuildEnvironmentVariable(value=val_config["value"], type=codebuild.BuildEnvironmentVariableType.PARAMETER_STORE)

            build_project_role = iam.Role(self, f"{pipeline_id}BuildServiceRole",
                assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"),
                    iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"),
                ]
            )
            if any(v.get("type") in ["SECRETS_MANAGER", "PARAMETER_STORE"] for v in build_config.get("environment_variables", {}).values()):
                build_project_role.add_to_policy(iam.PolicyStatement(
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "ssm:GetParameter"
                    ],
                    resources=["*"]
                ))

            build_project = codebuild.PipelineProject(
                self, f"{pipeline_id}BuildProject",
                project_name=build_config.get("build_project_name", f"{pipeline_id}-Build"),
                build_spec=build_spec,
                environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxBuildImage.from_aws_managed_image_id(build_config["build_image"])
                    if "arn:aws:codebuild" in build_config["build_image"] else
                    codebuild.LinuxBuildImage.from_code_build_image_id(build_config["build_image"]),
                    compute_type=codebuild.ComputeType[build_config["build_compute_type"].split('_')[-1]],

                    environment_variables=env_vars
                ),
                role=build_project_role
            )

            build_action = codepipeline_actions.CodeBuildAction(
                project=build_project,
                input=app_source_output,
                outputs=[app_build_output],
                action_name="CodeBuild"
            )

        # --- Deploy Stage ---
        deploy_config = pipeline_config.get("deploy_config", {})
        deploy_action = None

        if deploy_config.get("enabled", False):
            target_resource_type = pipeline_config["target_resource_type"]
            codedeploy_service_role: typing.Optional[iam.IRole] = None

            codedeploy_role_ref_id = deploy_config.get("codedeploy_service_role_ref_id")
            if codedeploy_role_ref_id:
                codedeploy_role_arn = created_iam_roles_map.get(codedeploy_role_ref_id) # This map contains ARNs
                if not codedeploy_role_arn:
                    logger.error(f"Pipeline '{pipeline_id}': CodeDeploy service role ARN with ref_id '{codedeploy_role_ref_id}' not found in created_iam_roles_map.")
                    raise ValueError(f"CodeDeploy service role ARN '{codedeploy_role_ref_id}' not resolved for pipeline '{pipeline_id}'")
                
                codedeploy_service_role = iam.Role.from_role_arn(
                    self,
                    f"{pipeline_id}{codedeploy_role_ref_id}Import",
                    codedeploy_role_arn
                )
                logger.info(f"Using CodeDeploy service role '{codedeploy_role_ref_id}' (ARN: {codedeploy_role_arn}) from created_iam_roles_map.")
            else:
                logger.error(f"Pipeline '{pipeline_id}': 'codedeploy_service_role_ref_id' is missing in deploy_config.")
                raise ValueError(f"'codedeploy_service_role_ref_id' is required for CodeDeploy deployment in pipeline '{pipeline_id}'")

            deployment_config_map = {
                "AllAtOnce": codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
                "HalfAtATime": codedeploy.ServerDeploymentConfig.HALF_AT_A_TIME,
                "OneAtATime": codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
                "CodeDeployDefault.AllAtOnce": codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
                "CodeDeployDefault.HalfAtATime": codedeploy.ServerDeploymentConfig.HALF_AT_A_TIME,
                "CodeDeployDefault.OneAtATime": codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
                "ALL_AT_ONCE": codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
                "HALF_AT_A_TIME": codedeploy.ServerDeploymentConfig.HALF_AT_A_TIME,
                "ONE_AT_A_TIME": codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
            }

            config_name_from_input = deploy_config["codedeploy_deployment_config_name"]
            resolved_deployment_config = deployment_config_map.get(config_name_from_input)

            if not resolved_deployment_config:
                normalized_config_name = config_name_from_input.replace('.', '_').upper()
                try:
                    resolved_deployment_config = getattr(codedeploy.ServerDeploymentConfig, normalized_config_name)
                    logger.warning(f"Pipeline '{pipeline_id}': Resolved deployment config '{config_name_from_input}' via getattr using normalized name '{normalized_config_name}'. Consider updating config to use exact CDK constant names.")
                except AttributeError:
                    logger.error(f"Pipeline '{pipeline_id}': Invalid CodeDeploy deployment config name: '{config_name_from_input}'. Neither direct map nor normalized name found.")
                    raise ValueError(f"Invalid CodeDeploy deployment config name: {config_name_from_input}")


            if deploy_config["deployment_strategy"] == "CODE_DEPLOY_EC2":
                app = codedeploy.ServerApplication(
                    self, f"{pipeline_id}CodeDeployApp",
                    application_name=deploy_config.get("codedeploy_application_name", f"{pipeline_id}-App")
                )

                if target_resource_type == "EC2_INSTANCE":
                    target_instance_id_str: str = ""
                    instance_name_tag_val: str = ""

                    existing_instance_id = pipeline_config.get("existing_target_resource_id")
                    
                    if existing_instance_id:
                        target_instance_id_str = existing_instance_id
                        instance_name_tag_val = pipeline_config.get("existing_target_instance_name_tag", existing_instance_id)
                        logger.info(f"Pipeline '{pipeline_id}': Using existing EC2 instance '{existing_instance_id}' with assumed Name tag '{instance_name_tag_val}' as target.")
                    else:
                        instance_ref_id = pipeline_config.get("target_resource_ref_id")
                        if not instance_ref_id:
                            logger.error(f"Pipeline '{pipeline_id}': Neither 'target_resource_ref_id' nor 'existing_target_resource_id' provided for EC2_INSTANCE.")
                            raise ValueError(f"EC2 instance target not specified for pipeline '{pipeline_id}'")
                        
                        created_instance_obj = created_ec2_instances_map.get(instance_ref_id)
                        if not created_instance_obj:
                            logger.error(f"Pipeline '{pipeline_id}': EC2 instance '{instance_ref_id}' not found in created resources.")
                            raise ValueError(f"EC2 instance target '{instance_ref_id}' not resolved from created resources for pipeline '{pipeline_id}'")
                        
                        if hasattr(created_instance_obj, 'instance_id_token') and created_instance_obj.instance_id_token:
                            target_instance_id_str = created_instance_obj.instance_id_token
                        else:
                            logger.error(f"Pipeline '{pipeline_id}': Ec2InstanceNestedStack object for '{instance_ref_id}' does not have 'instance_id_token'.")
                            raise ValueError(f"Instance ID token not found for created EC2 instance '{instance_ref_id}'")

                        if hasattr(created_instance_obj, 'public_instance_name_tag_value') and created_instance_obj.public_instance_name_tag_value:
                            instance_name_tag_val = created_instance_obj.public_instance_name_tag_value
                        else:
                            instance_name_tag_val = instance_ref_id
                            logger.warning(f"Pipeline '{pipeline_id}': Could not determine actual Name tag for created instance '{instance_ref_id}'. Using '{instance_ref_id}' as Name tag value for CodeDeploy.")

                        logger.info(f"Pipeline '{pipeline_id}': Using created EC2 instance '{instance_ref_id}' (ID: {target_instance_id_str}) with Name tag '{instance_name_tag_val}' as target.")

                    if not target_instance_id_str:
                        logger.error(f"Pipeline '{pipeline_id}': EC2 instance target ID could not be resolved for CodeDeploy.")
                        raise ValueError(f"EC2 instance target ID not resolved for pipeline '{pipeline_id}'")

                    dg = codedeploy.ServerDeploymentGroup(
                        self, f"{pipeline_id}CodeDeployDG",
                        deployment_group_name=deploy_config.get("codedeploy_deployment_group_name", f"{pipeline_id}-DG"),
                        application=app,
                        ec2_instance_tags=codedeploy.InstanceTagSet({"Name": [instance_name_tag_val]}),
                        deployment_config=resolved_deployment_config,
                        install_agent=deploy_config.get("codedeploy_install_agent", False)
                    )

                elif target_resource_type == "AUTOSCALING_GROUP":
                    asg_ref_id = pipeline_config.get("target_resource_ref_id")
                    asg_name = ""
                    target_asg_l2_construct: typing.Optional[autoscaling.AutoScalingGroup] = None 
                    
                    enable_install_agent_in_codedeploy = False 

                    existing_asg_name = pipeline_config.get("existing_target_resource_id")

                    if existing_asg_name:
                        target_asg_l2_construct = autoscaling.AutoScalingGroup.from_auto_scaling_group_name(
                            self, f"{pipeline_id}ExistingASG", existing_asg_name
                        )
                        asg_name = existing_asg_name
                        logger.info(f"Pipeline '{pipeline_id}': Using existing Auto Scaling Group '{existing_asg_name}' as target. CodeDeploy agent installation (install_agent) will be skipped as the ASG is imported or managed externally.")
                    elif asg_ref_id:
                        asg_stack_obj = created_asgs_map.get(asg_ref_id)
                        if asg_stack_obj:
                            if hasattr(asg_stack_obj, 'auto_scaling_group_resource') and asg_stack_obj.auto_scaling_group_resource:
                                target_asg_l2_construct = asg_stack_obj.auto_scaling_group_resource
                                asg_name = target_asg_l2_construct.auto_scaling_group_name
                                logger.info(f"Pipeline '{pipeline_id}': Using created Auto Scaling Group '{asg_ref_id}' (Name: {asg_name}) as target. CodeDeploy agent installation (install_agent) will be set to False, assuming it's handled by Launch Template UserData.")
                            else:
                                logger.error(f"Pipeline '{pipeline_id}': AutoScalingGroupStack object for '{asg_ref_id}' does not have 'auto_scaling_group_resource'.")
                                raise ValueError(f"Auto Scaling Group resource not found for created ASG '{asg_ref_id}'")
                        else:
                            logger.error(f"Pipeline '{pipeline_id}': Auto Scaling Group '{asg_ref_id}' not found in created resources.")
                            raise ValueError(f"ASG target '{asg_ref_id}' not resolved from created resources for pipeline '{pipeline_id}'")
                    else:
                        logger.error(f"Pipeline '{pipeline_id}': Neither 'target_resource_ref_id' nor 'existing_target_resource_id' provided for AUTOSCALING_GROUP.")
                        raise ValueError(f"ASG target not specified for pipeline '{pipeline_id}'")

                    if not target_asg_l2_construct:
                        logger.error(f"Pipeline '{pipeline_id}': Auto Scaling Group target could not be resolved for CodeDeploy.")
                        raise ValueError(f"ASG target not resolved for pipeline '{pipeline_id}'")

                    dg = codedeploy.ServerDeploymentGroup(
                        self, f"{pipeline_id}CodeDeployDG",
                        deployment_group_name=deploy_config.get("codedeploy_deployment_group_name", f"{pipeline_id}-DG"),
                        application=app,
                        auto_scaling_groups=[target_asg_l2_construct],
                        deployment_config=resolved_deployment_config,
                        install_agent=enable_install_agent_in_codedeploy
                    )
                else:
                    logger.error(f"Pipeline '{pipeline_id}': Unsupported target_resource_type for CodeDeploy: {target_resource_type}")
                    return

                deploy_action = codepipeline_actions.CodeDeployServerDeployAction(
                    action_name="CodeDeploy",
                    input=app_build_output if build_action else app_source_output,
                    deployment_group=dg,
                )

            elif deploy_config["deployment_strategy"] == "SSM_RUN_COMMAND":
                if target_resource_type == "EC2_INSTANCE":
                    instance_ref_id = pipeline_config.get("target_resource_ref_id")
                    target_instance_obj = created_ec2_instances_map.get(instance_ref_id)
                    target_ids = []

                    existing_instance_id = pipeline_config.get("existing_target_resource_id")

                    if existing_instance_id:
                        target_ids = [existing_instance_id]
                        logger.info(f"Pipeline '{pipeline_id}': Using existing EC2 instance '{existing_instance_id}' for SSM Run Command.")
                    elif target_instance_obj and hasattr(target_instance_obj, 'instance_id_token') and target_instance_obj.instance_id_token:
                        target_ids = [target_instance_obj.instance_id_token]
                        logger.info(f"Pipeline '{pipeline_id}': Using created EC2 instance '{instance_ref_id}' (ID: {target_instance_obj.instance_id_token}) for SSM Run Command.")
                    else:
                        logger.error(f"Pipeline '{pipeline_id}': EC2 instance target not resolved for SSM Run Command.")
                        raise ValueError(f"EC2 instance target not resolved for SSM Run Command for pipeline '{pipeline_id}'")

                    deploy_action = codepipeline_actions.SsmSendCommandAction(
                        id=f"{pipeline_id}Deploy",
                        action_name="SSMRunCommand",
                        input=app_build_output if build_action else app_source_output,
                        document_name=deploy_config["ssm_document_name"],
                        instance_ids=target_ids,
                        parameters={"commands": deploy_config["ssm_commands"]},
                        timeout=cdk.Duration.seconds(deploy_config.get("ssm_timeout_seconds", 600))
                    )

                elif target_resource_type == "AUTOSCALING_GROUP":
                    asg_ref_id = pipeline_config.get("target_resource_ref_id")
                    asg_name = ""

                    existing_asg_name = pipeline_config.get("existing_target_resource_id")

                    if existing_asg_name:
                        asg_name = existing_asg_name
                        logger.info(f"Pipeline '{pipeline_id}': Using existing ASG '{existing_asg_name}' for SSM Run Command.")
                    elif asg_ref_id:
                        asg_stack_obj = created_asgs_map.get(asg_ref_id)
                        if asg_stack_obj:
                            if hasattr(asg_stack_obj, 'auto_scaling_group_resource') and asg_stack_obj.auto_scaling_group_resource:
                                asg_name = asg_stack_obj.auto_scaling_group_resource.auto_scaling_group_name
                                logger.info(f"Pipeline '{pipeline_id}': Using created ASG '{asg_ref_id}' (Name: {asg_name}) for SSM Run Command.")
                            else:
                                logger.error(f"Pipeline '{pipeline_id}': AutoScalingGroupStack object for '{asg_ref_id}' does not have 'auto_scaling_group_resource'.")
                                raise ValueError(f"Auto Scaling Group resource not found for created ASG '{asg_ref_id}' for SSM Run Command.")
                        else:
                            logger.error(f"Pipeline '{pipeline_id}': ASG name not resolved for SSM Run Command (ref_id: {asg_ref_id}).")
                            raise ValueError(f"ASG target not resolved for SSM Run Command for pipeline '{pipeline_id}'")
                    else:
                        logger.error(f"Pipeline '{pipeline_id}': Neither 'target_resource_ref_id' nor 'existing_target_resource_id' provided for AUTOSCALING_GROUP.")
                        raise ValueError(f"ASG target not specified for pipeline '{pipeline_id}'")

                    if not asg_name:
                        logger.error(f"Pipeline '{pipeline_id}': ASG name could not be determined for SSM Run Command.")
                        raise ValueError(f"ASG target name not resolved for SSM Run Command for pipeline '{pipeline_id}'")

                    deploy_action = codepipeline_actions.SsmSendCommandAction(
                        id=f"{pipeline_id}Deploy",
                        action_name="SSMRunCommand",
                        input=app_build_output if build_action else app_source_output,
                        document_name=deploy_config["ssm_document_name"],
                        instance_tag_filters=[{"Key": "aws:autoscaling:groupName", "Values": [asg_name]}],
                        parameters={"commands": deploy_config["ssm_commands"]},
                        timeout=cdk.Duration.seconds(deploy_config.get("ssm_timeout_seconds", 600))
                    )
                else:
                    logger.error(f"Pipeline '{pipeline_id}': Unsupported target_resource_type for SSM Run Command: {target_resource_type}")
                    return

            elif deploy_config["deployment_strategy"] == "S3_SYNC":
                target_bucket_id = deploy_config["s3_target_bucket_id"]
                target_bucket = s3.Bucket.from_bucket_name(
                    self, f"{pipeline_id}TargetS3Bucket",
                    bucket_name=target_bucket_id
                )

                deploy_action = codepipeline_actions.S3DeployAction(
                    id=f"{pipeline_id}Deploy",
                    action_name="S3Deploy",
                    input=app_build_output if build_action else app_source_output,
                    bucket=target_bucket,
                    object_key=deploy_config.get("s3_target_bucket_prefix"),
                )
            else:
                logger.error(f"Pipeline '{pipeline_id}': Unsupported deployment_strategy: {deploy_config['deployment_strategy']}")
                raise ValueError(f"Unsupported deployment strategy: {deploy_config['deployment_strategy']}")

        # --- Retrieve CodePipeline Service Role ---
        codepipeline_role_ref_id = pipeline_config.get("codepipeline_service_role_ref_id")
        codepipeline_service_role: typing.Optional[iam.IRole] = None

        if codepipeline_role_ref_id:
            codepipeline_role_arn = created_iam_roles_map.get(codepipeline_role_ref_id)
            if not codepipeline_role_arn:
                logger.error(f"Pipeline '{pipeline_id}': CodePipeline service role ARN with ref_id '{codepipeline_role_ref_id}' not found in created_iam_roles_map.")
                raise ValueError(f"CodePipeline service role ARN '{codepipeline_role_ref_id}' not resolved for pipeline '{pipeline_id}'")
            
            codepipeline_service_role = iam.Role.from_role_arn(
                self,
                f"{pipeline_id}{codepipeline_role_ref_id}Import",
                codepipeline_role_arn
            )
            logger.info(f"Using CodePipeline service role '{codepipeline_role_ref_id}' (ARN: {codepipeline_role_arn}) from created_iam_roles_map.")
        else:
            logger.warning(f"Pipeline '{pipeline_id}': 'codepipeline_service_role_ref_id' is missing. CDK will create a default CodePipeline service role.")

        # Create the application pipeline
        application_pipeline = codepipeline.Pipeline(
            self, f"{pipeline_id}AppPipeline",
            pipeline_name=f"{pipeline_id}-AppPipeline",
            role=codepipeline_service_role,
            restart_execution_on_update=True,
        )

        application_pipeline.add_stage(
            stage_name="Source",
            actions=[source_action]
        )

        if build_action:
            application_pipeline.add_stage(
                stage_name="Build",
                actions=[build_action]
            )

        if deploy_action:
            application_pipeline.add_stage(
                stage_name="Deploy",
                actions=[deploy_action]
            )

        CfnOutput(self, f"{pipeline_id}PipelineNameOutput",
            value=application_pipeline.pipeline_name,
            description=f"Name of the {pipeline_id} application deployment pipeline."
        )

        logger.info(f"Application pipeline '{pipeline_id}' created.")