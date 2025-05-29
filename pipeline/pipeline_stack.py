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
        description: typing.Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=env, description=description, **kwargs)
        # Artifact S3 bucket
        pipeline_artifact_bucket = s3.Bucket(
            self, "PipelineStageArtifactBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True
        )
        logger.info(f"Pipeline stage artifact bucket created: {pipeline_artifact_bucket.bucket_name}")


        # IAM Roles
        # --- Role for CodeBuild Projects ---
        codebuild_execution_role = iam.Role(
            self, "CodeBuildExecutionRole",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description="Role for CodeBuild projects (Synth & Bundle)"
        )
        pipeline_artifact_bucket.grant_read_write(codebuild_execution_role)
        codebuild_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/codebuild/{self.stack_name}-Build:*"] # More specific
        ))
        # Permissions for CDK Synth
        codebuild_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"], # For CDK to assume lookup roles if context lookups are needed
            resources=["*"] # Scoped down if specific lookup roles are known
        ))
        codebuild_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["ec2:DescribeAvailabilityZones", "ec2:DescribeRegions"], # Common CDK context lookups
            resources=["*"]
        ))
        logger.info(f"CodeBuild execution role created: {codebuild_execution_role.role_name}")


        # Build Project: Synth + Bundle
        build_project = codebuild.PipelineProject(
            self,
            "CdkBuildProject",
            project_name=f"{self.stack_name}-BuildAndBundle",
            role=codebuild_execution_role,
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec_cdk_synth_bundle.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True,
            ),
            description="CodeBuild project to synthesize CDK app and bundle application."
        )
        logger.info(f"CDK Build project created: {build_project.project_name}")
        
        
        cfn_stack_deployment_role = iam.Role(
            self, "MainStackCfnDeploymentRole",
            assumed_by=iam.ServicePrincipal("cloudformation.amazonaws.com"),
            description=f"Role assumed by CloudFormation to deploy {cdk_infra_stack_name}"
        )
        pipeline_artifact_bucket.grant_read(cfn_stack_deployment_role)
        
        cdk_bootstrap_qualifier = "hnb659fds" # As seen in your error message
        cdk_bootstrap_assets_bucket_name = f"cdk-{cdk_bootstrap_qualifier}-assets-{self.account}-{self.region}"
        cdk_bootstrap_assets_bucket = s3.Bucket.from_bucket_name(self, "CdkBootstrapAssetsBucket", cdk_bootstrap_assets_bucket_name)
        cdk_bootstrap_assets_bucket.grant_read(cfn_stack_deployment_role)
        logger.info(f"Granted CFN deployment role read access to CDK assets bucket: {cdk_bootstrap_assets_bucket_name}")
        
        # Add SSM GetParameter(s) permission for CDK Bootstrap version check
        cfn_stack_deployment_role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameters", "ssm:GetParameter"], # Include both for robustness
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/cdk-bootstrap/{cdk_bootstrap_qualifier}/version"
            ]
        ))
        logger.info(f"Granted CFN deployment role ssm:GetParameters access for bootstrap version.")

        
        
        cfn_stack_deployment_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ec2:*", "vpc:*", # Assuming MainStack creates VPCs and EC2 resources
                "iam:PassRole",  # If MainStack defines IAM roles for EC2, Lambda, CodeDeploy service role, etc.
                "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", # If MainStack creates roles
                "autoscaling:*",
                "elasticloadbalancing:*",
                "codedeploy:*",  # To create/manage CodeDeploy Application, DeploymentGroup
                "rds:*",         # If deploying RDS
                "s3:*",          # If deploying other S3 buckets (beyond assets)
                "logs:*",        # For CloudWatch Logs (e.g., VPC Flow Logs)
                "cloudwatch:*"   # For Alarms, Dashboards etc.
                # Add any other specific service permissions your MainOrchestratorStack needs
            ],
            resources=["*"]  # Best practice is to scope these down in production
        ))
        logger.info(f"CloudFormation deployment role created: {cfn_stack_deployment_role.role_name} with broad permissions for stack resources.")

        
        # --- Pipeline Artifacts ---
        source_output_artifact = codepipeline.Artifact("SourceCodeOutput")
        cdk_templates_artifact = codepipeline.Artifact("CdkTemplatesOutput")
        application_bundle_artifact = codepipeline.Artifact("AppBundleOutput")
        
        # --- CodePipeline Role ---
        pipeline_execution_role = iam.Role(
            self, "CodePipelineExecutionRole",
            assumed_by=iam.ServicePrincipal("codepipeline.amazonaws.com"),
            description="Role for CodePipeline to orchestrate stages and actions"
        )
        pipeline_artifact_bucket.grant_read_write(pipeline_execution_role)
        codebuild_execution_role.grant_pass_role(pipeline_execution_role) # Allow pipeline to start CodeBuild
        cfn_stack_deployment_role.grant_pass_role(pipeline_execution_role) # Allow pipeline to pass the CFN deployment role

        
        # Permissions for CodePipeline actions
        pipeline_execution_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "codebuild:StartBuild", "codebuild:BatchGetBuilds",
                "codestar-connections:UseConnection",
                "s3:Get*", "s3:List*", "s3:PutObject", # For its own artifact bucket
                "cloudformation:DescribeStacks", "cloudformation:CreateChangeSet", "cloudformation:DescribeChangeSet",
                "cloudformation:ExecuteChangeSet", "cloudformation:DeleteChangeSet", "cloudformation:DescribeStackEvents",
                "codedeploy:CreateDeployment", "codedeploy:GetApplication", "codedeploy:GetDeployment",
                "codedeploy:GetDeploymentConfig", "codedeploy:GetDeploymentGroup", "codedeploy:RegisterApplicationRevision",
                "iam:PassRole" # Already granted above, but good to have if policies are separate
            ],
            resources=["*"] # Scope down in production
        ))
        logger.info(f"CodePipeline execution role created: {pipeline_execution_role.role_name}")

        # --- Reference existing CodeDeploy Application and Deployment Group ---
        # These are created by MainOrchestratorStack. The pipeline needs to know their names.
        cd_application = codedeploy.ServerApplication.from_server_application_name(
            self, "ImportedCodeDeployApplication",
            server_application_name=codedeploy_application_name
        )
        cd_deployment_group = codedeploy.ServerDeploymentGroup.from_server_deployment_group_attributes(
            self, "ImportedCodeDeployDeploymentGroup",
            application=cd_application,
            deployment_group_name=codedeploy_deployment_group_name
            # deployment_config_name= # Optional: if you use a specific one
        )
        logger.info(f"Referencing CodeDeploy App: {cd_application.application_name}, Group: {cd_deployment_group.deployment_group_name}")


        #Pipeline definition
        pipeline = codepipeline.Pipeline(
            self,
            "CloudFormationDeploymentPipeline",
            pipeline_name=f"{self.stack_name}-Pipeline",
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
                            outputs=[cdk_templates_artifact, application_bundle_artifact]
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Infrastructure",
                    actions=[
                        codepipeline_actions.CloudFormationCreateUpdateStackAction(
                            action_name=f"Deploy_CFN_{cdk_infra_stack_name.replace('-', '_')}",
                            stack_name=cdk_infra_stack_name,
                            template_path=cdk_templates_artifact.at_path(f"{cdk_infra_stack_name}.template.json"),
                            admin_permissions=False,
                            deployment_role=cfn_stack_deployment_role,
                            # Corrected parameter name:
                            cfn_capabilities=[ # Changed from 'capabilities'
                                cdk.CfnCapabilities.NAMED_IAM,
                                cdk.CfnCapabilities.AUTO_EXPAND
                            ]
                        )
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="Deploy_Application",
                    actions=[
                        codepipeline_actions.CodeDeployServerDeployAction(
                            action_name="Deploy_App_To_EC2_Via_CodeDeploy",
                            deployment_group=cd_deployment_group,
                            input=application_bundle_artifact
                            
                        )
                    ]
                )
            ]
        )
        
        logger.info(f"CodePipeline '{pipeline.pipeline_name}' defined with direct CodeDeploy action.")

        cdk.CfnOutput(self, "PipelineNameOutput", value=pipeline.pipeline_name)
        cdk.CfnOutput(self, "PipelineArnOutput", value=pipeline.pipeline_arn)
        cdk.CfnOutput(self, "PipelineArtifactS3BucketOutput", value=pipeline_artifact_bucket.bucket_name)
        cdk.CfnOutput(self, "CfnDeploymentRoleArn", value=cfn_stack_deployment_role.role_arn)