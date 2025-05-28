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
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, **kwargs)

        pipeline_artifact_bucket = s3.Bucket(
            self,
            "PipelineStageArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
        )

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
                resources=["*"],
            )
        )
        codebuild_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "sts:AssumeRole",
                    "iam:PassRole",
                    "cloudformation:*",
                    "ec2:Describe*",
                    "s3:*",
                    "codedeploy:GetApplication",
                    "codedeploy:GetDeploymentGroup",
                ],
                resources=["*"],
            )
        )

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
        )

        infra_deploy_project = codebuild.PipelineProject(
            self,
            "CdkInfraDeployProject",
            project_name=f"{self.stack_name}-InfraDeploy",
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "install": {
                        "runtime-versions": {
                            "nodejs": "18",
                            "python": "3.11"
                        },
                        "commands": [
                            "echo Installing AWS CDK...",
                            "npm install -g aws-cdk",
                            "echo Installing Python dependencies...",
                            "pip install -r requirements.txt",
                            "echo Current Git branch:",
                            "git rev-parse --abbrev-ref HEAD || echo Not a git repo"
                        ]
                    },
                    "pre_build": {
                        "commands": [
                            "echo Available files in build context:",
                            "find . -type f",
                            "echo Starting CDK deploy for stack: ${CDK_INFRA_STACK_NAME}"
                        ]
                    },
                    "build": {
                        "commands": [
                            "echo Deploying CDK stack: ${CDK_INFRA_STACK_NAME}...",
                            "cdk deploy ${CDK_INFRA_STACK_NAME} --require-approval never --outputs-file cdk-deploy-outputs.json",
                            "echo CDK deploy completed."
                        ]
                    }
                },
                "artifacts": {
                    "files": [
                        "cdk-deploy-outputs.json"
                    ]
                },
                "cache": {
                    "paths": [
                        "/root/.npm/**/*",
                        "/root/.cache/pip/**/*"
                    ]
                }
            }),
            environment_variables={
                "CDK_INFRA_STACK_NAME": codebuild.BuildEnvironmentVariable(value=cdk_infra_stack_name)
            },
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
        )

        source_output_artifact = codepipeline.Artifact("SourceCodeOutput")
        cdk_templates_artifact = codepipeline.Artifact("CdkTemplatesOutput")
        application_bundle_artifact = codepipeline.Artifact("AppBundleOutput")

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
                resources=["*"],
            )
        )

        cd_application = codedeploy.ServerApplication.from_server_application_name(
            self, "ImportedCodeDeployApplication", server_application_name=codedeploy_application_name
        )
        cd_deployment_group = codedeploy.ServerDeploymentGroup.from_server_deployment_group_attributes(
            self,
            "ImportedCodeDeployDeploymentGroup",
            application=cd_application,
            deployment_group_name=codedeploy_deployment_group_name,
        )

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
                            input=source_output_artifact,  # ✅ Full source so inline buildspec works
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

        cdk.CfnOutput(self, "PipelineNameOutput", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "PipelineArnOutput", value=pipeline.pipeline_arn)
        cdk.CfnOutput(self, "PipelineArtifactS3BucketOutput", value=pipeline_artifact_bucket.bucket_name)
