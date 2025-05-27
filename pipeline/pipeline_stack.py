import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    Environment # Ensure Environment is imported if used for type hinting
)
from constructs import Construct
import logging
import typing # Added for type hinting if not already present

logger = logging.getLogger(__name__)

class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 source_connection_arn: str,
                 source_repo_owner: str,
                 source_repo_name: str,
                 source_branch_name: str,
                 cdk_infra_stack_name: str,
                 app_bundle_s3_bucket_name: str,
                 env: typing.Optional[Environment] = None, # Added env parameter
                 description: typing.Optional[str] = None, # Added description
                 **kwargs) -> None:
        # Pass all standard Stack props to the base class
        super().__init__(scope, construct_id, env=env, description=description, **kwargs)

        logger.info(f"Initializing PipelineStack '{construct_id}' for deploying infra: {cdk_infra_stack_name} "
                    f"and app bundle to S3 bucket: {app_bundle_s3_bucket_name}")

        # 1. Artifact bucket for CodePipeline itself
        artifact_bucket = s3.Bucket(
            self, "PipelineStageArtifactBucket", # Renamed for clarity vs app_bundle_s3_bucket
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True # Good practice for pipeline artifacts
        )
        logger.info(f"Pipeline stage artifact bucket created: {artifact_bucket.bucket_name}")

        # 2. IAM Role for CodeBuild projects
        codebuild_role = iam.Role(
            self, "CodeBuildExecutionRole", # Renamed for clarity
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for CodeBuild projects for CDK synth, bundle, and infra deploy"
        )
        # Grant CodeBuild role permissions to read/write to the pipeline's own artifact bucket
        artifact_bucket.grant_read_write(codebuild_role)

        # Grant permission to the CodeBuild role to write to the designated app_bundle_s3_bucket
        # This bucket is where the 'buildspec_synth_bundle.yml' will upload the application zip.
        app_bundle_target_bucket = s3.Bucket.from_bucket_name(
            self, "AppBundleUploadTargetBucket", # Changed ID for clarity
            app_bundle_s3_bucket_name
        )
        # Corrected line: objects_key_prefix changed to objects_key_pattern
        app_bundle_target_bucket.grant_put(
            codebuild_role,
            objects_key_pattern="app-revisions/*" # Use objects_key_pattern
        )
        # Optional: Grant read if CodeBuild needs to verify or read back for some reason
        # app_bundle_target_bucket.grant_read(codebuild_role, objects_key_pattern="app-revisions/*")
        logger.info(f"Granted CodeBuild role PutObject access to S3 bucket '{app_bundle_s3_bucket_name}' with pattern 'app-revisions/*'.")


        # Basic logging permissions for CodeBuild
        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{Stack.of(self).stack_name}-*:*"]
        ))

        # Permissions needed by CDK CLI during synth and deploy, and for CodeDeploy resource management via CFN
        # These are broad; scope them down if possible based on your CDK app's needs.
        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "sts:AssumeRole",       # For CDK to assume deployment roles
                "iam:PassRole",         # To pass roles to CloudFormation & other services
                "cloudformation:*",     # Full CloudFormation access for cdk deploy
                "ec2:Describe*",        # Often needed by CDK for context lookups or VPCs
                "s3:*",                 # For CDK assets, context lookups, etc. (NOT for the app bundle upload, that's specific)
                "codedeploy:*",         # For CDK to manage CodeDeploy Application/DeploymentGroup/Deployment resources via CloudFormation
                # Add other service-specific permissions your CDK app (MainOrchestratorStack) might need during deploy
                # e.g., "rds:*", "lambda:*", "apigateway:*"
            ],
            resources=["*"] # Scope down if possible, especially for production
        ))
        logger.info(f"CodeBuild role created: {codebuild_role.role_name} with necessary CDK and deployment permissions.")

        # 3. CodeBuild Projects
        # CDK Synth & App Bundle Project
        synth_bundle_project = codebuild.PipelineProject(
            self, "CdkSynthAndBundleProject",
            project_name=f"{Stack.of(self).stack_name}-SynthBundle",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_synth_bundle.yml"),
            environment_variables={
                "APP_BUNDLE_S3_BUCKET": codebuild.BuildEnvironmentVariable(value=app_bundle_s3_bucket_name),
                "AWS_REGION": codebuild.BuildEnvironmentVariable(value=self.region) # Pass region for AWS CLI commands
            },
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0, # Consider newer versions if available/needed
                privileged=True # Often needed if CDK builds Docker assets
            ),
            description="CodeBuild project to synthesize CDK app, bundle application, and upload bundle to S3."
        )
        logger.info(f"CDK Synth & App Bundle project created: {synth_bundle_project.project_name}")

        # CDK Infrastructure Deploy Project (which also triggers CodeDeploy via CloudFormation)
        infra_deploy_project = codebuild.PipelineProject(
            self, "CdkInfraDeployAndTriggerProject", # Renamed for clarity
            project_name=f"{Stack.of(self).stack_name}-InfraDeployTrigger",
            role=codebuild_role,
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
        source_output_artifact = codepipeline.Artifact("SourceCodeOutput") # Renamed for clarity
        # This artifact from the synth_bundle_project now contains cdk.out/ AND cdk.out/bundle_location.json
        cdk_synth_output_with_location_artifact = codepipeline.Artifact("CdkSynthOutputWithBundleLocation")


        # 5. CodePipeline Role
        pipeline_exec_role = iam.Role( # Renamed for clarity
            self, "CodePipelineExecutionRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description="Role for CodePipeline to orchestrate stages and actions"
        )
        # Grant Pipeline role permissions for its own artifact bucket
        artifact_bucket.grant_read_write(pipeline_exec_role)
        # Allow Pipeline role to pass the CodeBuild role to CodeBuild service
        codebuild_role.grant_pass_role(pipeline_exec_role)

        pipeline_exec_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "codebuild:StartBuild",
                "codebuild:BatchGetBuilds", # To monitor builds
                "codebuild:StopBuild",      # To stop builds if needed
                "codestar-connections:UseConnection", # For GitHub source action
                "iam:PassRole",             # Already covered by grant_pass_role, but explicit can be fine
                # S3 permissions for CodePipeline to manage artifacts in its bucket
                "s3:Get*",
                "s3:List*",
                "s3:PutObject",
                "s3:DeleteObject" # If pipeline needs to clean up artifacts
            ],
            # Scope S3 permissions to the pipeline's artifact bucket if possible,
            # and other necessary buckets (like CodeBuild's S3 bucket if it's different and managed by pipeline role)
            resources=[
                artifact_bucket.bucket_arn,
                f"{artifact_bucket.bucket_arn}/*",
                # Add other specific resource ARNs if needed, otherwise "*" is broad
                "*" # Keep broad for actions like UseConnection, PassRole, CodeBuild start
            ]
        ))
        logger.info(f"CodePipeline execution role created: {pipeline_exec_role.role_name}")

        # 6. CodePipeline Definition
        pipeline = codepipeline.Pipeline(
            self, "CdkAppTriggeringCodeDeployPipeline",
            pipeline_name=f"{Stack.of(self).stack_name}-InfraAppCfTriggerPipeline",
            artifact_bucket=artifact_bucket, # Pipeline's own artifact bucket
            role=pipeline_exec_role,
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
                            trigger_on_push=True
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Synth_Bundle_Upload",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth_Bundle_Upload_To_S3", # More descriptive
                            project=synth_bundle_project,
                            input=source_output_artifact,
                            outputs=[cdk_synth_output_with_location_artifact] # Single output artifact from this stage
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Infrastructure_And_Trigger_App_Deploy",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name=f"Deploy_Infra_And_Trigger_{cdk_infra_stack_name.replace('-', '_')}",
                            project=infra_deploy_project,
                            input=cdk_synth_output_with_location_artifact, # Contains cdk.out/ and bundle_location.json
                            # No primary output artifact expected from this cdk deploy action usually
                        )
                    ]
                )
                # The explicit CodeDeploy stage was removed as CodeDeploy is triggered by CloudFormation.
            ]
        )
        logger.info(f"CodePipeline '{pipeline.pipeline_name}' created with Source, Synth_Bundle_Upload, and Deploy_Infrastructure_And_Trigger_App_Deploy stages.")

        # Outputs
        cdk.CfnOutput(self, "PipelineNameOutput", value=pipeline.pipeline_name, description="Name of the deployed CodePipeline")
        cdk.CfnOutput(self, "PipelineArnOutput", value=pipeline.pipeline_arn, description="ARN of the deployed CodePipeline")
        cdk.CfnOutput(self, "AppBundleS3BucketOutput", value=app_bundle_s3_bucket_name, description="S3 Bucket where application bundles are uploaded")
        cdk.CfnOutput(self, "PipelineArtifactS3BucketOutput", value=artifact_bucket.bucket_name, description="S3 Bucket for CodePipeline's own stage artifacts")

