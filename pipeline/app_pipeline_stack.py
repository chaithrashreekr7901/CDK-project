import typing
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Environment,
    RemovalPolicy,
    aws_s3 as s3,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_codedeploy as codedeploy,
)
from constructs import Construct


class AppDeploymentPipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        github_connection_arn: str,
        github_repo_owner: str,
        github_repo_name: str,
        github_branch: str,
        codedeploy_application_name: str,
        codedeploy_deployment_group_name: str,
        env: typing.Optional[Environment] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, **kwargs)

        # S3 bucket for artifacts
        artifact_bucket = s3.Bucket(
            self, "AppPipelineArtifactsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True
        )

        # IAM Roles
        codebuild_role = iam.Role(
            self, "AppCodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ]
        )
        pipeline_role = iam.Role(
            self, "AppCodePipelineRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ]
        )

        # CodeBuild project for building app artifacts
        build_project = codebuild.PipelineProject(
            self,
            "AppBuildProject",
            project_name=f"{self.stack_name}-AppBuild",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_app_deploy.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
        )

        # Artifacts
        source_output = codepipeline.Artifact("AppSource")
        build_output = codepipeline.Artifact("AppBuildOutput")

        # Reference existing CodeDeploy application and deployment group
        codedeploy_app = codedeploy.ServerApplication.from_server_application_name(
            self, "ImportedApp", codedeploy_application_name
        )

        deployment_group = codedeploy.ServerDeploymentGroup.from_server_deployment_group_attributes(
            self,
            "ImportedDG",
            application=codedeploy_app,
            deployment_group_name=codedeploy_deployment_group_name
        )

        # Define pipeline
        pipeline = codepipeline.Pipeline(
            self,
            "AppDeploymentPipeline",
            pipeline_name=f"{self.stack_name}-Pipeline",
            artifact_bucket=artifact_bucket,
            role=pipeline_role,
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.CodeStarConnectionsSourceAction(
                            action_name="GitHub_App_Source",
                            owner=github_repo_owner,
                            repo=github_repo_name,
                            branch=github_branch,
                            connection_arn=github_connection_arn,
                            output=source_output
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Build",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="Build_App",
                            project=build_project,
                            input=source_output,
                            outputs=[build_output]
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy",
                    actions=[
                        codepipeline_actions.CodeDeployServerDeployAction(
                            action_name="DeployToEC2",
                            input=build_output,
                            deployment_group=deployment_group
                        )
                    ]
                )
            ]
        )

        # Outputs
        cdk.CfnOutput(self, "AppPipelineName", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "AppArtifactBucket", value=artifact_bucket.bucket_name)
