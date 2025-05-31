import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    Environment,
)
from constructs import Construct
import typing

class PipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        source_connection_arn: str,
        source_repo_owner: str,
        source_repo_name: str,
        source_branch_name: str,
        env: typing.Optional[Environment] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Artifact Bucket for CodePipeline
        artifact_bucket = s3.Bucket(
            self, "PipelineArtifactsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True
        )

        # IAM Roles
        codebuild_role = iam.Role(
            self, "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ]
        )

        pipeline_role = iam.Role(
            self, "CodePipelineRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
            ]
        )

        # Source and Artifacts
        source_output = codepipeline.Artifact("SourceCode")
        synth_output = codepipeline.Artifact("CdkSynthOutput")

        # CodeBuild Project for CDK Synth
        synth_project = codebuild.PipelineProject(
            self,
            "CdkSynthProject",
            project_name=f"{self.stack_name}-CDKSynth",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec/buildspec_cdk_synth.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
        )

        # CodeBuild Project for CDK Deploy
        deploy_project = codebuild.PipelineProject(
            self,
            "CdkDeployProject",
            project_name=f"{self.stack_name}-CDKDeploy",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("CRMP-cdk/buildspec/buildspec_cdk_deploy.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
        )

        # Pipeline Definition
        pipeline = codepipeline.Pipeline(
            self,
            "CloudResourcePipeline",
            pipeline_name=f"{self.stack_name}-Pipeline",
            artifact_bucket=artifact_bucket,
            role=pipeline_role,
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.CodeStarConnectionsSourceAction(
                            action_name="GitSource",
                            owner=source_repo_owner,
                            repo=source_repo_name,
                            branch=source_branch_name,
                            connection_arn=source_connection_arn,
                            output=source_output,
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Synth",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth",
                            project=synth_project,
                            input=source_output,
                            outputs=[synth_output],
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Deploy",
                            project=deploy_project,
                            input=synth_output,
                        )
                    ],
                ),
            ]
        )

        # Outputs
        cdk.CfnOutput(self, "PipelineName", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "ArtifactBucket", value=artifact_bucket.bucket_name)
