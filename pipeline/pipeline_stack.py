import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    aws_codedeploy as codedeploy,
    aws_cloudformation as cloudformation,
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
        codedeploy_application_name: str,
        codedeploy_deployment_group_name: str,
        env: typing.Optional[Environment] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, **kwargs)

        # Artifact S3 bucket
        artifact_bucket = s3.Bucket(
            self, "PipelineArtifactsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True
        )

        # Adding S3 Bucket Policy for CodePipeline, CodeBuild, and CloudFormation to access the artifact bucket
        artifact_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                resources=[
                    artifact_bucket.bucket_arn,
                    f"{artifact_bucket.bucket_arn}/*",
                ],
                effect=iam.Effect.ALLOW,
                principals=[iam.ArnPrincipal("*")],
            )
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

        # Build Project: Synth + Bundle
        build_project = codebuild.PipelineProject(
            self,
            "CdkSynthAndBundleProject",
            project_name=f"{self.stack_name}-SynthAndBundle",
            role=codebuild_role,
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

        # CodeDeploy app and deployment group
        codedeploy_app = codedeploy.ServerApplication.from_server_application_name(
            self, "CDApp", server_application_name=codedeploy_application_name
        )
        codedeploy_group = codedeploy.ServerDeploymentGroup.from_server_deployment_group_attributes(
            self, "CDGroup",
            application=codedeploy_app,
            deployment_group_name=codedeploy_deployment_group_name
        )

        # Pipeline definition
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
                codepipeline.StageProps(
                    stage_name="Deploy_Application",
                    actions=[
                        codepipeline_actions.CodeDeployServerDeployAction(
                            action_name="CodeDeployAppToEC2",
                            deployment_group=codedeploy_group,
                            input=app_bundle_output
                        )
                    ]
                )
            ]
        )

        # Outputs
        cdk.CfnOutput(self, "PipelineName", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "ArtifactBucket", value=artifact_bucket.bucket_name)
