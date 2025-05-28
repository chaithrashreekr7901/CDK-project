import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    aws_codedeploy as codedeploy,  # For CodeDeploy resources
    Environment,
)
from constructs import Construct
import logging
import typing

logger = logging.getLogger(__name__)

class PipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        source_connection_arn: str,
        source_repo_owner: str,
        source_repo_name: str,
        source_branch_name: str,
        cdk_infra_stack_name: str,
        codedeploy_application_name: str,
        codedeploy_deployment_group_name: str,
        env: typing.Optional[Environment] = None,
        # description: typing.Optional[str] = None,  # Stack does not support 'description' param by default; comment out or remove
        **kwargs,
    ) -> None:
        # Remove description argument or manage separately if needed
        super().__init__(scope, construct_id, env=env, **kwargs)

        logger.info(
            f"Initializing PipelineStack '{construct_id}' for deploying infra: {cdk_infra_stack_name} "
            f"and app via CodeDeploy App: {codedeploy_application_name}, Group: {codedeploy_deployment_group_name}"
        )

        pipeline_artifact_bucket = s3.Bucket(
            self,
            "PipelineStageArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
        )
        logger.info(f"Pipeline artifact bucket created: {pipeline_artifact_bucket.bucket_name}")

        codebuild_execution_role = iam.Role(
            self,
            "CodeBuildExecutionRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for CodeBuild projects",
        )
        pipeline_artifact_bucket.grant_read_write(codebuild_execution_role)
        codebuild_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{self.stack_name}-*:*"],
            )
        )
        codebuild_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "sts:AssumeRole",
                    "iam:PassRole",
                    "cloudformation:*",
                    "ec2:Describe*",
                    "s3:*",  # For CDK assets and context
                    "codedeploy:GetApplication",
                    "codedeploy:GetDeploymentGroup",
                ],
                resources=["*"],  # Scope down in production!
            )
        )
        logger.info(f"CodeBuild execution role created: {codebuild_execution_role.role_name}")

        # CodeBuild Project for Synth and App Bundling
        build_project = codebuild.PipelineProject(
            self,
            "CdkBuildProject",
            project_name=f"{self.stack_name}-Build",
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_synth_bundle.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
            description="CodeBuild project to synthesize CDK app and bundle application.",
        )
        logger.info(f"CDK Build project created: {build_project.project_name}")

        # CodeBuild Project for Infrastructure Deployment
        infra_deploy_project = codebuild.PipelineProject(
            self,
            "CdkInfraDeployProject",
            project_name=f"{self.stack_name}-InfraDeploy",
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_infra_deploy.yml"),
            environment_variables={
                "CDK_INFRA_STACK_NAME": codebuild.BuildEnvironmentVariable(value=cdk_infra_stack_name)
            },
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
            description=f"CodeBuild project to deploy CDK infrastructure stack: {cdk_infra_stack_name}.",
        )
        logger.info(f"CDK Infra Deploy project created: {infra_deploy_project.project_name}")

        # Pipeline Artifacts
        source_output_artifact = codepipeline.Artifact("SourceCodeOutput")
        cdk_templates_artifact = codepipeline.Artifact("CdkTemplatesOutput")
        application_bundle_artifact = codepipeline.Artifact("AppBundleOutput")

        # CodePipeline Role
        pipeline_execution_role = iam.Role(
            self,
            "CodePipelineExecutionRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description="Role for CodePipeline to orchestrate stages and actions",
        )
        pipeline_artifact_bucket.grant_read_write(pipeline_execution_role)
        codebuild_execution_role.grant_pass_role(pipeline_execution_role)
        pipeline_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "codebuild:StartBuild",
                    "codebuild:BatchGetBuilds",
                    "codebuild:StopBuild",
                    "codestar-connections:UseConnection",
                    "iam:PassRole",
                    "s3:Get*",
                    "s3:List*",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "codedeploy:CreateDeployment",
                    "codedeploy:GetApplication",
                    "codedeploy:GetDeployment",
                    "codedeploy:GetDeploymentConfig",
                    "codedeploy:GetDeploymentGroup",
                    "codedeploy:RegisterApplicationRevision",
                ],
                resources=["*"],  # Scope down if possible
            )
        )
        logger.info(f"CodePipeline execution role created: {pipeline_execution_role.role_name}")

        # Reference existing CodeDeploy Application and Deployment Group
        cd_application = codedeploy.ServerApplication.from_server_application_name(
            self, "ImportedCodeDeployApplication", server_application_name=codedeploy_application_name
        )
        cd_deployment_group = codedeploy.ServerDeploymentGroup.from_server_deployment_group_attributes(
            self,
            "ImportedCodeDeployDeploymentGroup",
            application=cd_application,
            deployment_group_name=codedeploy_deployment_group_name,
        )
        logger.info(
            f"Referencing CodeDeploy App: {cd_application.application_name}, Group: {cd_deployment_group.deployment_group_name}"
        )

        # Define Pipeline
        pipeline = codepipeline.Pipeline(
            self,
            "CdkAppWithDirectCodeDeployPipeline",
            pipeline_name=f"{self.stack_name}-DirectCodeDeploy",
            artifact_bucket=pipeline_artifact_bucket,
            role=pipeline_execution_role,
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.CodeStarConnectionsSourceAction(
                            action_name="GitHub_Source",
                            owner=source_repo_owner,
                            repo=source_repo_name,
                            branch=source_branch_name,
                            connection_arn=source_connection_arn,
                            output=source_output_artifact,
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Build",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth_And_App_Bundle",
                            project=build_project,
                            input=source_output_artifact,
                            outputs=[cdk_templates_artifact, application_bundle_artifact],
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Infrastructure",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name=f"Deploy_Infra_{cdk_infra_stack_name.replace('-', '_')}",
                            project=infra_deploy_project,
                            input=cdk_templates_artifact,
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Application",
                    actions=[
                        codepipeline_actions.CodeDeployServerDeployAction(
                            action_name="Deploy_App_To_EC2_Via_CodeDeploy",
                            deployment_group=cd_deployment_group,
                            input=application_bundle_artifact,
                        )
                    ],
                ),
            ],
        )
        logger.info(f"CodePipeline '{pipeline.pipeline_name}' created with direct CodeDeploy action.")

        cdk.CfnOutput(self, "PipelineNameOutput", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "PipelineArnOutput", value=pipeline.pipeline_arn)
        cdk.CfnOutput(self, "PipelineArtifactS3BucketOutput", value=pipeline_artifact_bucket.bucket_name)
