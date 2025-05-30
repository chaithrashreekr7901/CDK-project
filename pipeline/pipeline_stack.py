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
        cdk_infra_stack_name: str,
        env: typing.Optional[Environment] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        # Artifact bucket for pipeline
        artifact_bucket = s3.Bucket(
            self,
            "PipelineArtifactsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Template deploy bucket for CDK templates (cdk.out)
        template_deploy_bucket = s3.Bucket(
            self,
            "CdkTemplateDeployBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Grant CloudFormation access to the template deploy bucket
        template_deploy_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:GetObjectVersion"],
                principals=[iam.ServicePrincipal("cloudformation.amazonaws.com")],
                resources=[f"{template_deploy_bucket.bucket_arn}/*"],
            )
        )

        # Grant CloudFormation access to the artifact bucket
        artifact_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("cloudformation.amazonaws.com")],
                actions=[
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:GetBucketVersioning",
                    "s3:ListBucket",
                ],
                resources=[
                    artifact_bucket.bucket_arn,
                    f"{artifact_bucket.bucket_arn}/*",
                ],
            )
        )

        # CodeBuild role
        codebuild_role = iam.Role(
            self,
            "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
        )
        codebuild_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"))
        codebuild_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AWSCloudFormationFullAccess"))
        codebuild_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ReadOnlyAccess"))
        codebuild_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMReadOnlyAccess"))

        # CodePipeline role
        pipeline_role = iam.Role(
            self,
            "CodePipelineRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
        )
        pipeline_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"))
        pipeline_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "codebuild:*",
                    "cloudformation:*",
                    "iam:PassRole",
                    "codepipeline:*",
                ],
                resources=["*"],
            )
        )

        # CodeBuild project
        build_project = codebuild.PipelineProject(
            self,
            "CdkSynthAndBundleProject",
            project_name=f"{self.stack_name}-SynthAndBundle",
            role=codebuild_role,
            environment_variables={
                "CDK_TEMPLATE_BUCKET_NAME": codebuild.BuildEnvironmentVariable(value=template_deploy_bucket.bucket_name)
            },
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_cdk_synth_bundle.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
        )

        # Artifacts
        source_output = codepipeline.Artifact("SourceCode")
        cdk_output = codepipeline.Artifact("CdkTemplatesOutput")
        app_bundle_output = codepipeline.Artifact("AppBundleOutput")

        # Define the pipeline
        pipeline = codepipeline.Pipeline(
            self,
            "CloudFormationDeploymentPipeline",
            pipeline_name=f"{self.stack_name}-Pipeline",
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
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Build",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth_And_App_Bundle",
                            project=build_project,
                            input=source_output,
                            outputs=[cdk_output, app_bundle_output],
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Infrastructure",
                    actions=[
                        codepipeline_actions.CloudFormationCreateUpdateStackAction(
                            action_name="Deploy_CF_Template",
                            stack_name=cdk_infra_stack_name,
                            template_path=cdk_output.at_path("MyMainInfrastructureStack.template.json"),
                            admin_permissions=True,
                        )
                    ],
                ),
            ],
        )

        # Outputs
        cdk.CfnOutput(self, "PipelineName", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "ArtifactBucket", value=artifact_bucket.bucket_name)
        cdk.CfnOutput(self, "TemplateDeployBucket", value=template_deploy_bucket.bucket_name)
