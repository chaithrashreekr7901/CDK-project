import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    Environment # Ensure Environment is imported
)
from constructs import Construct
import logging
import typing

logger = logging.getLogger(__name__)

class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 source_connection_arn: str,
                 source_repo_owner: str,
                 source_repo_name: str,
                 source_branch_name: str,
                 cdk_infra_stack_name: str,
                 app_bundle_s3_bucket_name: str,
                 env: typing.Optional[Environment] = None,
                 description: typing.Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, env=env, description=description, **kwargs)

        logger.info(f"Initializing PipelineStack '{construct_id}' for deploying infra: {cdk_infra_stack_name} "
                    f"and app bundle to S3 bucket: {app_bundle_s3_bucket_name}")

        # 1. Artifact bucket for CodePipeline itself
        pipeline_artifact_bucket = s3.Bucket( # Renamed variable for clarity
            self, "PipelineStageArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True
        )
        logger.info(f"Pipeline stage artifact bucket created: {pipeline_artifact_bucket.bucket_name}")

        # 2. IAM Role for CodeBuild projects
        codebuild_execution_role = iam.Role( # Renamed variable for clarity
            self, "CodeBuildExecutionRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for CodeBuild projects for CDK synth, bundle, and infra deploy"
        )
        # Grant CodeBuild role permissions to read/write to the pipeline's own artifact bucket
        pipeline_artifact_bucket.grant_read_write(codebuild_execution_role)

        # Grant permission to the CodeBuild role to write to the designated app_bundle_s3_bucket
        app_bundle_target_s3_bucket = s3.Bucket.from_bucket_name( # Renamed variable
            self, "AppBundleUploadTargetS3Bucket", # Changed ID for clarity
            app_bundle_s3_bucket_name
        )
        app_bundle_target_s3_bucket.grant_put(
            codebuild_execution_role,
            objects_key_pattern="app-revisions/*" # Corrected: objects_key_pattern
        )
        logger.info(f"Granted CodeBuild role PutObject access to S3 bucket '{app_bundle_s3_bucket_name}' with pattern 'app-revisions/*'.")

        # Basic logging permissions for CodeBuild
        codebuild_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{self.stack_name}-*:*",
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{self.stack_name}-SynthBundle:*", # Specific for synth project
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{self.stack_name}-InfraDeployTrigger:*" # Specific for deploy project
            ] # Made log group names more specific to project names if possible
        ))

        # Permissions needed by CDK CLI during synth and deploy, and for CodeDeploy resource management via CFN
        codebuild_execution_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "sts:AssumeRole",
                "iam:PassRole",
                "cloudformation:*",
                "ec2:Describe*",
                "s3:*", # For CDK assets, context lookups. App bundle upload is separate.
                "codedeploy:*", # For CDK to manage CodeDeploy resources via CloudFormation
                # Add other service permissions your MainOrchestratorStack might need
            ],
            resources=["*"] # Scope down for production
        ))
        logger.info(f"CodeBuild execution role created: {codebuild_execution_role.role_name} with necessary permissions.")

        # 3. CodeBuild Projects
        synth_bundle_project = codebuild.PipelineProject(
            self, "CdkSynthAndBundleProject",
            project_name=f"{self.stack_name}-SynthBundle", # Use self.stack_name for uniqueness
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_synth_bundle.yml"),
            environment_variables={
                "APP_BUNDLE_S3_BUCKET": codebuild.BuildEnvironmentVariable(value=app_bundle_s3_bucket_name),
                "AWS_REGION": codebuild.BuildEnvironmentVariable(value=self.region)
            },
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True
            ),
            description="CodeBuild project to synthesize CDK app, bundle application, and upload bundle to S3."
        )
        logger.info(f"CDK Synth & App Bundle project created: {synth_bundle_project.project_name}")

        infra_deploy_project = codebuild.PipelineProject(
            self, "CdkInfraDeployAndTriggerProject",
            project_name=f"{self.stack_name}-InfraDeployTrigger", # Use self.stack_name
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_infra_deploy.yml"),
            environment_variables={
                "CDK_INFRA_STACK_NAME": codebuild.BuildEnvironmentVariable(value=cdk_infra_stack_name)
            },
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True
            ),
            description=f"CodeBuild project to deploy CDK infra stack ({cdk_infra_stack_name}) and trigger CodeDeploy via CloudFormation."
        )
        logger.info(f"CDK Infrastructure Deploy & Trigger project created: {infra_deploy_project.project_name}")

        # 4. Pipeline Artifacts
        source_output_artifact = codepipeline.Artifact("SourceCodeOutput")
        cdk_synth_output_with_location_artifact = codepipeline.Artifact("CdkSynthOutputWithBundleLocation")

        # 5. CodePipeline Role
        pipeline_execution_role = iam.Role( # Renamed variable
            self, "CodePipelineExecutionRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description="Role for CodePipeline to orchestrate stages and actions"
        )
        pipeline_artifact_bucket.grant_read_write(pipeline_execution_role)
        codebuild_execution_role.grant_pass_role(pipeline_execution_role) # Allow pipeline to start CodeBuild with its role

        pipeline_execution_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild",
                "codestar-connections:UseConnection",
                "iam:PassRole", # For passing the CodeBuild role
                "s3:Get*", "s3:List*", "s3:PutObject", "s3:DeleteObject" # For managing artifacts in its own bucket
            ],
            resources=[
                pipeline_artifact_bucket.bucket_arn,
                f"{pipeline_artifact_bucket.bucket_arn}/*",
                source_connection_arn, # For CodeStar connection
                codebuild_execution_role.role_arn, # For iam:PassRole
                synth_bundle_project.project_arn, # For codebuild actions
                infra_deploy_project.project_arn, # For codebuild actions
                # "*" can be too broad for some actions, but needed for others like UseConnection if not scoped
            ]
        ))
        # Allow assuming roles for CodePipeline actions (CDK creates these per action)
        pipeline_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-pipeline-*" ], # Pattern for CDK-generated action roles
        ))
        logger.info(f"CodePipeline execution role created: {pipeline_execution_role.role_name}")

        # 6. CodePipeline Definition
        pipeline = codepipeline.Pipeline(
            self, "CdkAppTriggeringCodeDeployPipeline",
            pipeline_name=f"{self.stack_name}-InfraAppCfTrigger", # Using self.stack_name
            artifact_bucket=pipeline_artifact_bucket,
            role=pipeline_execution_role,
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.CodeStarConnectionsSourceAction(
                            action_name="GitHub_Source",
                            owner=source_repo_owner, repo=source_repo_name, branch=source_branch_name,
                            connection_arn=source_connection_arn, output=source_output_artifact,
                            trigger_on_push=True
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Synth_Bundle_Upload",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth_Bundle_Upload_To_S3",
                            project=synth_bundle_project,
                            input=source_output_artifact,
                            outputs=[cdk_synth_output_with_location_artifact]
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Infrastructure_And_Trigger_App_Deploy",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name=f"Deploy_Infra_And_Trigger_{cdk_infra_stack_name.replace('-', '_')}",
                            project=infra_deploy_project,
                            input=cdk_synth_output_with_location_artifact,
                        )
                    ]
                )
            ]
        )
        logger.info(f"CodePipeline '{pipeline.pipeline_name}' defined.")

        cdk.CfnOutput(self, "PipelineNameOutput", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "PipelineArnOutput", value=pipeline.pipeline_arn)
        cdk.CfnOutput(self, "AppBundleS3BucketOutput", value=app_bundle_s3_bucket_name)
        cdk.CfnOutput(self, "PipelineArtifactS3BucketOutput", value=pipeline_artifact_bucket.bucket_name)

