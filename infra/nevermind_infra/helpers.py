from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct


def disposable_bucket(
    scope: Construct, logical_id: str, *, build_inputs: bool = False
) -> s3.CfnBucket:
    bucket = s3.Bucket(
        scope,
        logical_id,
        encryption=s3.BucketEncryption.S3_MANAGED,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        versioned=True,
        removal_policy=cdk.RemovalPolicy.DESTROY,
        auto_delete_objects=True,
        lifecycle_rules=[
            s3.LifecycleRule(
                id="OldInputs",
                noncurrent_version_expiration=cdk.Duration.days(7),
                abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
            )
        ]
        if build_inputs
        else None,
    )
    resource = bucket.node.default_child
    if not isinstance(resource, s3.CfnBucket):
        raise TypeError("Expected the CDK bucket's CloudFormation resource.")
    resource.override_logical_id(logical_id)
    return resource


def add_bucket_policy(bucket: s3.CfnBucket, logical_id: str, document: dict[str, object]) -> None:
    parent = bucket.node.scope
    if not isinstance(parent, s3.Bucket):
        raise TypeError("Expected a managed CDK bucket.")
    statements = document["Statement"]
    if not isinstance(statements, list):
        raise TypeError("Bucket statements must be a list.")
    for statement in statements:
        if not isinstance(statement, dict):
            raise TypeError("Each bucket statement must be an object.")
        parent.add_to_resource_policy(iam.PolicyStatement.from_json(statement))
    if parent.policy is None:
        raise ValueError("The bucket policy was not created.")
    resource = parent.policy.node.default_child
    if not isinstance(resource, s3.CfnBucketPolicy):
        raise TypeError("Expected a CloudFormation bucket policy.")
    resource.override_logical_id(logical_id)
    resource.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.DELETE


def configure_stack(stack: cdk.Stack, description: str) -> None:
    stack.template_options.description = description
    for child in stack.node.children:
        if isinstance(child, cdk.CfnResource):
            # Existing logical IDs are the migration boundary; changing them replaces live resources.
            child.override_logical_id(child.node.id)
            child.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.DELETE
            if child.cfn_resource_type in {
                "AWS::DynamoDB::Table",
                "AWS::Cognito::UserPool",
                "AWS::KMS::Key",
                "AWS::ECR::Repository",
            }:
                child.cfn_options.update_replace_policy = cdk.CfnDeletionPolicy.RETAIN
            if isinstance(child, kms.CfnKey):
                child.pending_window_in_days = 7
            if isinstance(child, ecr.CfnRepository):
                child.empty_on_delete = True
    for child in stack.node.find_all():
        if isinstance(child, s3.CfnBucket):
            child.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.DELETE
            child.cfn_options.update_replace_policy = cdk.CfnDeletionPolicy.DELETE
