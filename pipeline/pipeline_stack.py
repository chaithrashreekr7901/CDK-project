# cdk_project/pipeline/pipeline_stack.py
import aws_cdk as cdk 
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    # SecretValue # Not currently used in this version
)
from constructs import Construct
import logging # It's good practice to have logging available

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

        logger.info(f"PipelineStack '{construct_id}': Initializing CI/CD pipeline for {source_repo_owner}/{source_repo_name} branch {source_branch_name}")

        # 1. Artifact Bucket for CodePipeline
        artifact_bucket = s3.Bucket(
            self, "PipelineArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY, 
            auto_delete_objects=True, 
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL # Good security practice
        )
        logger.info(f"Pipeline artifact bucket: {artifact_bucket.bucket_name}")

        # 2. IAM Role for CodeBuild
        codebuild_role = iam.Role(
            self, "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description=f"Role for CodeBuild project in pipeline {Stack.of(self).stack_name}"
        )
        
        # Grant CodeBuild role permissions to S3 artifact bucket
        artifact_bucket.grant_read_write(codebuild_role)
        
        # Grant permissions for CloudWatch Logs
        codebuild_role.add_to_policy(iam.PolicyStatement(
            sid="CodeBuildCloudWatchLogs",
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{Stack.of(self).stack_name}-CdkBuild:*",
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{Stack.of(self).stack_name}-CdkBuild"
            ]
        ))

        # Permissions for CDK synth (lookups, etc.) and bootstrap (if buildspec tries it)
        codebuild_role.add_to_policy(iam.PolicyStatement(
            sid="CodeBuildCdkToolkitPermissions",
            actions=[
                "sts:AssumeRole", # To assume roles like the CDK lookup role
                "iam:PassRole",   # If CDK synth creates roles that CodeBuild needs to pass
                "cloudformation:DescribeStacks",
                "ec2:Describe*", # For VPC lookups, etc. Scope down if possible.
                # "ssm:GetParameter", # If you use SSM parameters in CDK
                # "kms:Decrypt", # If you use encrypted SSM parameters
                # "s3:ListAllMyBuckets", # Sometimes needed for asset publishing checks
                # "s3:GetBucketLocation",
                # "s3:GetBucketPolicy",
                # "s3:GetObject", # For assets
                # "s3:PutObject"  # For assets
            ],
            resources=["*"] # Scope down these resources in production
        ))
        # Add permissions to allow CodeBuild to interact with the CDK bootstrap stack's resources if needed
        # This is often covered by sts:AssumeRole if CodeBuild assumes the CDK's execution roles.
        # If you encounter "cdk-hnb659fds-*" role access issues during build, this might need adjustment.

        # 3. CodeBuild Project
        cdk_build_project_name = f"{Stack.of(self).stack_name}-CdkBuild"
        cdk_build_project = codebuild.PipelineProject(
            self, "CdkBuildProject",
            project_name=cdk_build_project_name,
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"), 
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0, 
                privileged=False # Usually not needed unless building Docker images
            ),
            timeout=cdk.Duration.minutes(30) # Optional: set a timeout
        )
        logger.info(f"CodeBuild project created: {cdk_build_project.project_name}")

        # 4. CodePipeline
        source_output = codepipeline.Artifact("SourceOutput")
        cdk_build_output = codepipeline.Artifact("CdkBuildOutput")

        pipeline_role = iam.Role(
            self, "CodePipelineServiceRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description=f"Role for CodePipeline service {Stack.of(self).stack_name}"
        )
        # Grant necessary permissions to pipeline role
        artifact_bucket.grant_read_write(pipeline_role)
        codebuild_role.grant_pass_role(pipeline_role) # Allow pipeline to pass CodeBuild role
        pipeline_role.add_to_policy(iam.PolicyStatement( # Allow pipeline to start CodeBuild
            actions=["codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild"],
            resources=[cdk_build_project.project_arn]
        ))
        pipeline_role.add_to_policy(iam.PolicyStatement( # Allow pipeline to use CodeStar Connection
            actions=["codestar-connections:UseConnection"],
            resources=[source_connection_arn]
        ))
        # Permissions for CloudFormation actions
        pipeline_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "cloudformation:*", # Scope down for production
                "iam:PassRole",     # To pass the CloudFormation execution role
                "s3:GetObject",     # To get templates from artifact bucket
                "s3:ListBucket"
            ],
            resources=["*"] # Scope down for production
        ))


        pipeline = codepipeline.Pipeline(
            self, "CdkCiCdPipeline",
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
                    stage_name="Build",
                    actions=[
                        codepipeline_actions.CodeBuildAction(
                            action_name="CDK_Synth",
                            project=cdk_build_project,
                            input=source_output,
                            outputs=[cdk_build_output]
                            # role=codebuild_role # Role is defined at project level
                        )
                    ]
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_CFN",
                    actions=[
                        codepipeline_actions.CloudFormationCreateUpdateStackAction(
                            action_name=f"Deploy_{cdk_app_stack_name}",
                            stack_name=cdk_app_stack_name,
                            template_path=cdk_build_output.at_path(f"{cdk_app_stack_name}.template.json"),
                            admin_permissions=True, # For simplicity. In prod, use a specific CFN execution role.
                            cfn_capabilities=[ # <<< CORRECTED PARAMETER NAME
                                cdk.CfnCapabilities.NAMED_IAM, 
                                cdk.CfnCapabilities.AUTO_EXPAND
                            ],
                            # deployment_role=cloudformation_deploy_role, # Example of using a specific CFN execution role
                        )
                    ]
                )
            ]
        )
        logger.info(f"CodePipeline created: {pipeline.pipeline_name}")

