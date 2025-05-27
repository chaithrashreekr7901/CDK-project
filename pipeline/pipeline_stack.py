import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
)
from constructs import Construct
import logging

logger = logging.getLogger(__name__)

class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 source_connection_arn: str,
                 source_repo_owner: str,
                 source_repo_name: str,
                 source_branch_name: str,
                 cdk_app_stack_name: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Artifact bucket
        artifact_bucket = s3.Bucket(
            self, "PipelineArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL
        )

        # 2. IAM Role for CodeBuild
        codebuild_role = iam.Role(
            self, "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for all CodeBuild projects in pipeline"
        )
        artifact_bucket.grant_read_write(codebuild_role)

        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=["*"]
        ))

        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "sts:AssumeRole",
                "iam:PassRole",
                "cloudformation:*",
                "ec2:Describe*",
                "s3:*"
            ],
            resources=["*"]
        ))

        # 3. CodeBuild Projects
        bootstrap_project = codebuild.PipelineProject(
            self, "CdkBootstrapProject",
            project_name=f"{Stack.of(self).stack_name}-Bootstrap",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec-bootstrap.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0
            )
        )

        synth_project = codebuild.PipelineProject(
            self, "CdkSynthProject",
            project_name=f"{Stack.of(self).stack_name}-Synth",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec-synth.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0
            )
        )

        deploy_project = codebuild.PipelineProject(
            self, "CdkDeployProject",
            project_name=f"{Stack.of(self).stack_name}-Deploy",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec-deploy.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0
            )
        )

        # 4. Artifacts
        source_output = codepipeline.Artifact("SourceOutput")
        bootstrap_output = codepipeline.Artifact("BootstrapOutput")
        synth_output = codepipeline.Artifact("SynthOutput")
        deploy_output = codepipeline.Artifact("DeployOutput")

        # 5. CodePipeline Role
        pipeline_role = iam.Role(
            self, "CodePipelineRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description="Role for CodePipeline"
        )

        artifact_bucket.grant_read_write(pipeline_role)
        codebuild_role.grant_pass_role(pipeline_role)

        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild",
                "codestar-connections:UseConnection",
                "cloudformation:*", "iam:PassRole", "s3:GetObject", "s3:ListBucket"
            ],
            resources=["*"]
        ))

        # 6. CodePipeline Definition
        pipeline = codepipeline.Pipeline(
            self, "CdkPipeline",
            pipeline_name=f"{Stack.of(self).stack_name}-Pipeline",
            artifact_bucket=artifact_bucket,
            role=pipeline_role,
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
                            output=source_output,
                            trigger_on_push=True
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Bootstrap",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Bootstrap",
                            project=bootstrap_project,
                            input=source_output,
                            outputs=[bootstrap_output]
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Synth",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth",
                            project=synth_project,
                            input=source_output,
                            outputs=[synth_output]
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Deploy",
                            project=deploy_project,
                            input=source_output,
                            outputs=[deploy_output]
                        )
                    ]
                )
            ]
        )

        logger.info(f"Pipeline created: {pipeline.pipeline_name}")
