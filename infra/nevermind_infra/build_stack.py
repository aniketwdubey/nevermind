import aws_cdk as cdk
import aws_cdk.aws_codebuild as codebuild
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_logs as logs
from aws_cdk import Stack
from constructs import Construct

from .helpers import add_bucket_policy, configure_stack, disposable_bucket


class BuildStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, env: cdk.Environment, stack_name: str
    ) -> None:
        super().__init__(scope, construct_id, env=env, stack_name=stack_name)
        images = ecr.CfnRepository(
            self,
            "Images",
            image_tag_mutability="IMMUTABLE",
            image_scanning_configuration={"scanOnPush": True},
            encryption_configuration={"encryptionType": "AES256"},
            lifecycle_policy={
                "lifecyclePolicyText": '{"rules":[{"rulePriority":1,"description":"Expire untagged layers after seven days","selection":{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":7},"action":{"type":"expire"}}]}\n'
            },
        )
        images.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Private immutable image versions; updates deploy the resolved image digest.",
                "must": ["Keep tagged images for rollback; delete only with explicit approval."],
            }
        }
        images.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        log_key = kms.CfnKey(
            self,
            "LogKey",
            description="Nevermind model build and runtime log encryption",
            enable_key_rotation=True,
            key_policy={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": cdk.Fn.sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root")
                        },
                        "Action": "kms:*",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": cdk.Fn.sub("logs.${AWS::Region}.amazonaws.com")},
                        "Action": [
                            "kms:Encrypt",
                            "kms:Decrypt",
                            "kms:ReEncrypt*",
                            "kms:GenerateDataKey*",
                            "kms:DescribeKey",
                        ],
                        "Resource": "*",
                        "Condition": {
                            "ArnLike": {
                                "kms:EncryptionContext:aws:logs:arn": [
                                    cdk.Fn.sub(
                                        "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/codebuild/${AWS::StackName}"
                                    ),
                                    cdk.Fn.sub(
                                        "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/runtimes/*"
                                    ),
                                ]
                            }
                        },
                    },
                ],
            },
        )
        log_key.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Encrypt build and model runtime logs in the same account and region.",
                "must": [
                    "Retain while any encrypted logs exist; restrict use by encryption context."
                ],
            }
        }
        log_key.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        sources = disposable_bucket(self, "Sources", build_inputs=True)
        sources.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Versioned, private build inputs supplied by the deployment script.",
                "must": [
                    "Never upload credentials or runtime data; delete only with confirmed stack destruction."
                ],
            }
        }
        sources.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        build_logs = logs.CfnLogGroup(
            self,
            "BuildLogs",
            log_group_name=cdk.Fn.sub("/aws/codebuild/${AWS::StackName}"),
            kms_key_id=log_key.attr_arn,
            retention_in_days=3,
        )
        add_bucket_policy(
            sources,
            "SourcesPolicy",
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [sources.attr_arn, cdk.Fn.sub("${Sources.Arn}/*")],
                        "Condition": {"Bool": {"aws:SecureTransport": False}},
                    }
                ],
            },
        )
        build_role = iam.CfnRole(
            self,
            "BuildRole",
            assume_role_policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "codebuild.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": cdk.Aws.ACCOUNT_ID},
                            "ArnEquals": {
                                "aws:SourceArn": cdk.Fn.sub(
                                    "arn:${AWS::Partition}:codebuild:${AWS::Region}:${AWS::AccountId}:project/${AWS::StackName}"
                                )
                            },
                        },
                    }
                ],
            },
            policies=[
                {
                    "policyName": "BuildOneImage",
                    "policyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                                "Resource": cdk.Fn.sub("${Sources.Arn}/input/*"),
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetBucketLocation", "s3:GetBucketAcl"],
                                "Resource": sources.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "ecr:GetAuthorizationToken",
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:BatchCheckLayerAvailability",
                                    "ecr:InitiateLayerUpload",
                                    "ecr:UploadLayerPart",
                                    "ecr:CompleteLayerUpload",
                                    "ecr:PutImage",
                                    "ecr:BatchGetImage",
                                    "ecr:GetDownloadUrlForLayer",
                                ],
                                "Resource": images.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                "Resource": cdk.Fn.sub("${BuildLogs.Arn}"),
                            },
                        ],
                    },
                }
            ],
        )
        build_role.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "CodeBuild can read only these build inputs and push only this repository.",
                "must": ["Scope the trust to this project and account."],
            }
        }
        build = codebuild.CfnProject(
            self,
            "Build",
            name=cdk.Aws.STACK_NAME,
            service_role=build_role.attr_arn,
            artifacts={"type": "NO_ARTIFACTS"},
            source={
                "type": "S3",
                "location": cdk.Fn.sub("${Sources}/input/source.zip"),
                "buildSpec": "buildspec.yml",
            },
            environment={
                "type": "ARM_CONTAINER",
                "computeType": "BUILD_GENERAL1_SMALL",
                "image": "aws/codebuild/amazonlinux-aarch64-standard:3.0",
                "privilegedMode": True,
                "imagePullCredentialsType": "CODEBUILD",
            },
            timeout_in_minutes=25,
            queued_timeout_in_minutes=5,
            concurrent_build_limit=1,
            logs_config={"cloudWatchLogs": {"status": "ENABLED", "groupName": build_logs.ref}},
        )
        build.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Build on ARM64 without installing Docker or model weights on the developer machine.",
                "must": ["No webhook builds; bounded build time and one concurrent build."],
            }
        }
        self.source_bucket = sources.ref
        cdk.CfnOutput(
            self,
            "SourceBucket",
            key="SourceBucket",
            description="Private versioned build source bucket.",
            value=str(self.source_bucket),
        )
        self.repository_uri = images.attr_repository_uri
        cdk.CfnOutput(
            self,
            "RepositoryUri",
            key="RepositoryUri",
            description="Private immutable image repository URI.",
            value=str(self.repository_uri),
        )
        self.repository_arn = images.attr_arn
        cdk.CfnOutput(
            self,
            "RepositoryArn",
            key="RepositoryArn",
            description="Repository ARN for the runtime execution policy.",
            value=str(self.repository_arn),
        )
        self.repository_name = images.ref
        cdk.CfnOutput(
            self,
            "RepositoryName",
            key="RepositoryName",
            description="Repository name for resolving the built image digest.",
            value=str(self.repository_name),
        )
        self.build_project = build.ref
        cdk.CfnOutput(
            self,
            "BuildProject",
            key="BuildProject",
            description="ARM64 build project name.",
            value=str(self.build_project),
        )
        self.log_key_arn = log_key.attr_arn
        cdk.CfnOutput(
            self,
            "LogKeyArn",
            key="LogKeyArn",
            description="Log encryption key passed explicitly to the runtime stack.",
            value=str(self.log_key_arn),
        )
        configure_stack(
            self,
            "Private ARM64 Decider image builds for the Nevermind demonstration; no model inference is started here.",
        )
