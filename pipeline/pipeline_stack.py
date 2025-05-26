# cdk_project/pipeline/pipeline_stack.py
import aws_cdk as cdk # Make sure cdk is imported if CfnCapabilities is used directly
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_iam as iam,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    # SecretValue # Not currently used in this version, can be removed if not planned
)
from constructs import Construct

class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, 
                 # Using generic source parameters
                 source_connection_arn: str, 
                 source_repo_owner: str,     
                 source_repo_name: str,      
                 source_branch_name: str,    
                 cdk_app_stack_name: str,    
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Artifact Bucket for CodePipeline
        artifact_bucket = s3.Bucket(
            self, "PipelineArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY, 
            auto_delete_objects=True, # For non-prod, ensure bucket is empty for destroy
            encryption=s3.BucketEncryption.S3_MANAGED # Good practice
        )

        # 2. IAM Role for CodeBuild
        codebuild_role = iam.Role(
            self, "CodeBuildRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            # It's better to create specific policies rather than full access in production
            # For now, using managed policies for simplicity.
            managed_policies=[
                # iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"), # If only reading from S3
                # iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchLogsFullAccess"), # For logs
            ]
        )
        # Grant CodeBuild role permissions to write to the artifact bucket
        artifact_bucket.grant_read_write(codebuild_role)
        
        # Grant permissions for CloudWatch Logs
        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=["arn:aws:logs:*:*:*"] # Scope down in production
        ))

        # If your CDK synth needs to perform lookups or access other AWS resources:
        codebuild_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ec2:Describe*", # Example for VPC lookups
                "iam:PassRole", # Often needed if CodeBuild creates roles or passes them
                "sts:AssumeRole", # For CDK to assume roles for lookups/deployment
                # Add other permissions as required by your 'cdk synth' process
                "cloudformation:DescribeStacks" # For CDK to determine existing resources
            ],
            resources=["*"] # Scope down in production
        ))


        # 3. CodeBuild Project
        cdk_build_project = codebuild.PipelineProject(
            self, "CdkBuildProject",
            project_name=f"{Stack.of(self).stack_name}-CdkBuild",
            role=codebuild_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"), 
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0, 
                privileged=True # Needed if building Docker images, otherwise can be false
            ),
            # Pass account and region to CDK synth if needed (often picked up from execution environment)
            # environment_variables={
            #     "CDK_DEFAULT_ACCOUNT": codebuild.BuildEnvironmentVariable(value=self.account),
            #     "CDK_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(value=self.region),
            # }
        )

        # 4. CodePipeline
        source_output = codepipeline.Artifact("SourceOutput")
        cdk_build_output = codepipeline.Artifact("CdkBuildOutput")

        # IAM Role for CodePipeline service
        # This role is assumed by CodePipeline to interact with other services like S3, CodeBuild, CloudFormation
        pipeline_role = iam.Role(
            self, "CodePipelineServiceRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSCodePipeline_FullAccess") # Scope down for production
            ]
        )
        # Grant pipeline role permissions to the artifact bucket
        artifact_bucket.grant_read_write(pipeline_role)


        pipeline = codepipeline.Pipeline(
            self, "CdkCiCdPipeline",
            pipeline_name=f"{Stack.of(self).stack_name}-Pipeline",
            artifact_bucket=artifact_bucket,
            role=pipeline_role, # Assign the explicit role to the pipeline
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        codepipeline_actions.CodeStarConnectionsSourceAction(
                            action_name="GitHub_Source", # Updated for clarity
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
                            outputs=[cdk_build_output],
                            role=codebuild_role # Explicitly assign role to action if needed, though project has it
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
                            admin_permissions=True, # This gives CloudFormation broad permissions.
                                                   # For production, create a specific CloudFormation execution role
                                                   # and pass its ARN to the 'role' property of this action.
                            capabilities=[cdk.CfnCapabilities.NAMED_IAM, cdk.CfnCapabilities.AUTO_EXPAND],
                            # deployment_role=cloudformation_deploy_role, # Example of using a specific CFN execution role
                        )
                    ]
                )
            ]
        )
