import aws_cdk as cdk
import aws_cdk.aws_apigatewayv2 as apigatewayv2
import aws_cdk.aws_cloudfront as cloudfront
import aws_cdk.aws_cognito as cognito
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_lambda as aws_lambda
import aws_cdk.aws_logs as logs
import aws_cdk.aws_sqs as sqs
from aws_cdk import Stack
from constructs import Construct

from .helpers import add_bucket_policy, configure_stack, disposable_bucket


class ApplicationStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, env: cdk.Environment, stack_name: str
    ) -> None:
        super().__init__(scope, construct_id, env=env, stack_name=stack_name)
        code_bucket = cdk.CfnParameter(
            self, "CodeBucket", type="String", allowed_pattern="[a-z0-9.-]+"
        )
        code_key = cdk.CfnParameter(
            self, "CodeKey", type="String", allowed_pattern="[A-Za-z0-9/_.-]+"
        )
        runtime_arn = cdk.CfnParameter(
            self,
            "RuntimeArn",
            type="String",
            allowed_pattern="arn:aws:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime/[A-Za-z0-9_-]+",
        )
        runtime_qualifier = cdk.CfnParameter(
            self,
            "RuntimeQualifier",
            type="String",
            default="probe",
            allowed_pattern="[A-Za-z0-9_]+",
        )
        failed_jobs = sqs.CfnQueue(
            self, "FailedJobs", sqs_managed_sse_enabled=True, message_retention_period=604800
        )
        http_api = apigatewayv2.CfnApi(
            self, "HttpApi", name=cdk.Fn.sub("${AWS::StackName}-api"), protocol_type="HTTP"
        )
        log_key = kms.CfnKey(
            self,
            "LogKey",
            description="Nevermind application log encryption",
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
                                        "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/${AWS::StackName}-*"
                                    ),
                                    cdk.Fn.sub(
                                        "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/apigateway/${AWS::StackName}"
                                    ),
                                ]
                            }
                        },
                    },
                ],
            },
        )
        log_key.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        no_api_cache = cloudfront.CfnCachePolicy(
            self,
            "NoApiCache",
            cache_policy_config={
                "name": cdk.Fn.sub("${AWS::StackName}-no-api-cache"),
                "defaultTtl": 0,
                "minTtl": 0,
                "maxTtl": 0,
                "parametersInCacheKeyAndForwardedToOrigin": {
                    "enableAcceptEncodingGzip": False,
                    "cookiesConfig": {"cookieBehavior": "none"},
                    "headersConfig": {"headerBehavior": "none"},
                    "queryStringsConfig": {"queryStringBehavior": "none"},
                },
            },
        )
        no_api_cache.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "API responses contain per-user plans and must never be cached.",
                "must": ["Keep all cache lifetimes at zero."],
            }
        }
        state = dynamodb.CfnTable(
            self,
            "State",
            billing_mode="PAY_PER_REQUEST",
            attribute_definitions=[
                {"attributeName": "pk", "attributeType": "S"},
                {"attributeName": "sk", "attributeType": "S"},
            ],
            key_schema=[
                {"attributeName": "pk", "keyType": "HASH"},
                {"attributeName": "sk", "keyType": "RANGE"},
            ],
            sse_specification={"sseEnabled": True},
            point_in_time_recovery_specification={"pointInTimeRecoveryEnabled": True},
            time_to_live_specification={"attributeName": "expires_at", "enabled": True},
        )
        state.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Durable per-user workspace, immutable previews, and action history.",
                "must": ["Preserve conditional transactions and retain data on replacement."],
            }
        }
        state.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        users = cognito.CfnUserPool(
            self,
            "Users",
            user_pool_name=cdk.Fn.sub("${AWS::StackName}-users"),
            admin_create_user_config={"allowAdminCreateUserOnly": True},
            policies={
                "passwordPolicy": {
                    "minimumLength": 14,
                    "requireLowercase": True,
                    "requireUppercase": True,
                    "requireNumbers": True,
                    "requireSymbols": True,
                }
            },
            mfa_configuration="OPTIONAL",
            enabled_mfas=["SOFTWARE_TOKEN_MFA"],
        )
        users.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Invite-only access prevents anonymous model spending.",
                "must": ["Disable public registration; each user owns a separate sandbox."],
            }
        }
        users.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        web_access = cloudfront.CfnOriginAccessControl(
            self,
            "WebAccess",
            origin_access_control_config={
                "name": cdk.Fn.sub("${AWS::StackName}-web"),
                "originAccessControlOriginType": "s3",
                "signingBehavior": "always",
                "signingProtocol": "sigv4",
            },
        )
        web_files = disposable_bucket(self, "WebFiles", build_inputs=False)
        web_files.cfn_options.deletion_policy = cdk.CfnDeletionPolicy.RETAIN
        web_headers = cloudfront.CfnResponseHeadersPolicy(
            self,
            "WebHeaders",
            response_headers_policy_config={
                "name": cdk.Fn.sub("${AWS::StackName}-headers"),
                "securityHeadersConfig": {
                    "contentTypeOptions": {"override": True},
                    "frameOptions": {"frameOption": "DENY", "override": True},
                    "referrerPolicy": {"referrerPolicy": "no-referrer", "override": True},
                    "strictTransportSecurity": {
                        "accessControlMaxAgeSec": 63072000,
                        "includeSubdomains": True,
                        "override": True,
                    },
                    "contentSecurityPolicy": {
                        "override": True,
                        "contentSecurityPolicy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://*.amazoncognito.com; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://*.amazoncognito.com",
                    },
                },
            },
        )
        api_log = logs.CfnLogGroup(
            self,
            "ApiLog",
            log_group_name=cdk.Fn.sub("/aws/lambda/${AWS::StackName}-api"),
            kms_key_id=log_key.attr_arn,
            retention_in_days=7,
        )
        sqs.CfnQueuePolicy(
            self,
            "FailedQueuePolicy",
            queues=[failed_jobs.ref],
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "sqs:*",
                        "Resource": failed_jobs.attr_arn,
                        "Condition": {"Bool": {"aws:SecureTransport": False}},
                    }
                ],
            },
        )
        logs.CfnLogGroup(
            self,
            "HttpLog",
            log_group_name=cdk.Fn.sub("/aws/apigateway/${AWS::StackName}"),
            kms_key_id=log_key.attr_arn,
            retention_in_days=7,
        )
        jobs = sqs.CfnQueue(
            self,
            "Jobs",
            sqs_managed_sse_enabled=True,
            visibility_timeout=1440,
            receive_message_wait_time_seconds=20,
            message_retention_period=86400,
            redrive_policy={"deadLetterTargetArn": failed_jobs.attr_arn, "maxReceiveCount": 2},
        )
        jobs.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Model startup can exceed a synchronous HTTP request timeout.",
                "must": ["Visibility timeout must exceed six times the worker timeout."],
            }
        }
        login_domain = cognito.CfnUserPoolDomain(
            self,
            "LoginDomain",
            user_pool_id=users.ref,
            domain=cdk.Fn.sub("${AWS::StackName}-${AWS::AccountId}"),
        )
        website = cloudfront.CfnDistribution(
            self,
            "Website",
            distribution_config={
                "enabled": True,
                "defaultRootObject": "index.html",
                "httpVersion": "http2and3",
                "priceClass": "PriceClass_100",
                "origins": [
                    {
                        "id": "files",
                        "domainName": web_files.attr_regional_domain_name,
                        "originAccessControlId": web_access.ref,
                        "s3OriginConfig": {"originAccessIdentity": ""},
                    },
                    {
                        "id": "api",
                        "domainName": cdk.Fn.sub(
                            "${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"
                        ),
                        "customOriginConfig": {
                            "originProtocolPolicy": "https-only",
                            "originSslProtocols": ["TLSv1.2"],
                        },
                    },
                ],
                "defaultCacheBehavior": {
                    "targetOriginId": "files",
                    "viewerProtocolPolicy": "redirect-to-https",
                    "allowedMethods": ["GET", "HEAD", "OPTIONS"],
                    "cachedMethods": ["GET", "HEAD"],
                    "compress": True,
                    "cachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                    "responseHeadersPolicyId": web_headers.ref,
                },
                "cacheBehaviors": [
                    {
                        "pathPattern": "/api/*",
                        "targetOriginId": "api",
                        "viewerProtocolPolicy": "https-only",
                        "allowedMethods": [
                            "GET",
                            "HEAD",
                            "OPTIONS",
                            "PUT",
                            "PATCH",
                            "POST",
                            "DELETE",
                        ],
                        "cachedMethods": ["GET", "HEAD"],
                        "cachePolicyId": no_api_cache.ref,
                        "originRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
                        "responseHeadersPolicyId": web_headers.ref,
                    }
                ],
            },
        )
        worker_log = logs.CfnLogGroup(
            self,
            "WorkerLog",
            log_group_name=cdk.Fn.sub("/aws/lambda/${AWS::StackName}-worker"),
            kms_key_id=log_key.attr_arn,
            retention_in_days=7,
        )
        api_role = iam.CfnRole(
            self,
            "ApiRole",
            assume_role_policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            policies=[
                {
                    "policyName": "WorkspaceApi",
                    "policyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:ConditionCheckItem",
                                    "dynamodb:Query",
                                ],
                                "Resource": state.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": jobs.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                "Resource": api_log.attr_arn,
                            },
                        ],
                    },
                }
            ],
        )
        apigatewayv2.CfnStage(
            self,
            "ApiStage",
            api_id=http_api.ref,
            stage_name="$default",
            auto_deploy=True,
            default_route_settings={"throttlingBurstLimit": 5, "throttlingRateLimit": 2},
            access_log_settings={
                # API Gateway stores this ARN without the :* suffix returned by GetAtt Arn.
                "destinationArn": cdk.Fn.sub(
                    "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${HttpLog}"
                ),
                "format": '{"requestId":"$context.requestId","status":"$context.status","route":"$context.routeKey"}',
            },
        )
        browser_client = cognito.CfnUserPoolClient(
            self,
            "BrowserClient",
            user_pool_id=users.ref,
            client_name=cdk.Fn.sub("${AWS::StackName}-browser"),
            generate_secret=False,
            allowed_o_auth_flows_user_pool_client=True,
            allowed_o_auth_flows=["code"],
            allowed_o_auth_scopes=["openid", "profile"],
            supported_identity_providers=["COGNITO"],
            callback_ur_ls=[cdk.Fn.sub("https://${Website.DomainName}/")],
            logout_ur_ls=[cdk.Fn.sub("https://${Website.DomainName}/")],
            access_token_validity=15,
            id_token_validity=15,
            refresh_token_validity=1,
            token_validity_units={
                "accessToken": "minutes",
                "idToken": "minutes",
                "refreshToken": "days",
            },
            enable_token_revocation=True,
            prevent_user_existence_errors="ENABLED",
            explicit_auth_flows=["ALLOW_USER_SRP_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"],
        )
        sqs.CfnQueuePolicy(
            self,
            "QueuePolicy",
            queues=[jobs.ref],
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "sqs:*",
                        "Resource": jobs.attr_arn,
                        "Condition": {"Bool": {"aws:SecureTransport": False}},
                    }
                ],
            },
        )
        add_bucket_policy(
            web_files,
            "WebPolicy",
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "cloudfront.amazonaws.com"},
                        "Action": "s3:GetObject",
                        "Resource": cdk.Fn.sub("${WebFiles.Arn}/*"),
                        "Condition": {
                            "StringEquals": {
                                "AWS:SourceArn": cdk.Fn.sub(
                                    "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Website}"
                                ),
                                "AWS:SourceAccount": cdk.Aws.ACCOUNT_ID,
                            }
                        },
                    },
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [web_files.attr_arn, cdk.Fn.sub("${WebFiles.Arn}/*")],
                        "Condition": {"Bool": {"aws:SecureTransport": False}},
                    },
                ],
            },
        )
        worker_role = iam.CfnRole(
            self,
            "WorkerRole",
            assume_role_policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            policies=[
                {
                    "policyName": "AnalyzeOnly",
                    "policyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:ConditionCheckItem",
                                ],
                                "Resource": state.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": jobs.attr_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "bedrock:InvokeModel",
                                "Resource": [
                                    cdk.Fn.sub(
                                        "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-2-lite-v1:0"
                                    ),
                                    cdk.Fn.sub(
                                        "arn:${AWS::Partition}:bedrock:us-*::foundation-model/amazon.nova-2-lite-v1:0"
                                    ),
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "bedrock-agentcore:InvokeAgentRuntime",
                                    "bedrock-agentcore:StopRuntimeSession",
                                ],
                                "Resource": [
                                    runtime_arn.value_as_string,
                                    cdk.Fn.sub(
                                        "${RuntimeArn}/runtime-endpoint/${RuntimeQualifier}"
                                    ),
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                "Resource": worker_log.attr_arn,
                            },
                        ],
                    },
                }
            ],
        )
        api_function = aws_lambda.CfnFunction(
            self,
            "ApiFunction",
            function_name=cdk.Fn.sub("${AWS::StackName}-api"),
            runtime="python3.12",
            architectures=["x86_64"],
            handler="nevermind.handler.handler",
            role=api_role.attr_arn,
            code={"s3Bucket": code_bucket.value_as_string, "s3Key": code_key.value_as_string},
            timeout=20,
            memory_size=256,
            environment={
                "variables": {
                    "NEVERMIND_TABLE_NAME": state.ref,
                    "NEVERMIND_QUEUE_URL": jobs.ref,
                    "NEVERMIND_RUNTIME_ARN": runtime_arn.value_as_string,
                    "NEVERMIND_RUNTIME_QUALIFIER": runtime_qualifier.value_as_string,
                }
            },
        )
        api_function.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Short authenticated API requests never wait for model inference.",
                "must": ["No public function URL; only API Gateway invokes this function."],
            }
        }
        authorizer = apigatewayv2.CfnAuthorizer(
            self,
            "Authorizer",
            api_id=http_api.ref,
            name="SignedInUsers",
            authorizer_type="JWT",
            identity_source=["$request.header.Authorization"],
            jwt_configuration={
                "audience": [browser_client.ref],
                "issuer": cdk.Fn.sub("https://cognito-idp.${AWS::Region}.amazonaws.com/${Users}"),
            },
        )
        worker_function = aws_lambda.CfnFunction(
            self,
            "WorkerFunction",
            function_name=cdk.Fn.sub("${AWS::StackName}-worker"),
            runtime="python3.12",
            architectures=["x86_64"],
            handler="nevermind.handler.handler",
            role=worker_role.attr_arn,
            code={"s3Bucket": code_bucket.value_as_string, "s3Key": code_key.value_as_string},
            timeout=240,
            memory_size=512,
            environment={
                "variables": {
                    "NEVERMIND_TABLE_NAME": state.ref,
                    "NEVERMIND_QUEUE_URL": jobs.ref,
                    "NEVERMIND_RUNTIME_ARN": runtime_arn.value_as_string,
                    "NEVERMIND_RUNTIME_QUALIFIER": runtime_qualifier.value_as_string,
                }
            },
        )
        worker_function.cfn_options.metadata = {
            "com.aws.cloudformation.Context": {
                "why": "Bounded background analysis prepares previews but cannot approve them.",
                "must": ["Do not expose a public invocation endpoint for this worker."],
            }
        }
        aws_lambda.CfnPermission(
            self,
            "ApiPermission",
            action="lambda:InvokeFunction",
            function_name=api_function.ref,
            principal="apigateway.amazonaws.com",
            source_account=cdk.Aws.ACCOUNT_ID,
            source_arn=cdk.Fn.sub(
                "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*"
            ),
        )
        apigatewayv2.CfnIntegration(
            self,
            "Integration",
            api_id=http_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=api_function.attr_arn,
            payload_format_version="2.0",
            timeout_in_millis=20000,
        )
        aws_lambda.CfnEventSourceMapping(
            self,
            "WorkerSource",
            event_source_arn=jobs.attr_arn,
            function_name=worker_function.ref,
            batch_size=1,
            function_response_types=["ReportBatchItemFailures"],
            scaling_config={"maximumConcurrency": 2},
        )
        apigatewayv2.CfnRoute(
            self,
            "ApiRoute",
            api_id=http_api.ref,
            route_key="ANY /api/{proxy+}",
            authorization_type="JWT",
            authorization_scopes=["openid"],
            authorizer_id=authorizer.ref,
            target=cdk.Fn.sub("integrations/${Integration}"),
        )
        self.website_url = cdk.Fn.sub("https://${Website.DomainName}")
        cdk.CfnOutput(self, "WebsiteUrl", key="WebsiteUrl", value=str(self.website_url))
        self.web_bucket = web_files.ref
        cdk.CfnOutput(self, "WebBucket", key="WebBucket", value=str(self.web_bucket))
        self.distribution_id = website.ref
        cdk.CfnOutput(self, "DistributionId", key="DistributionId", value=str(self.distribution_id))
        self.user_pool_id = users.ref
        cdk.CfnOutput(self, "UserPoolId", key="UserPoolId", value=str(self.user_pool_id))
        self.client_id = browser_client.ref
        cdk.CfnOutput(self, "ClientId", key="ClientId", value=str(self.client_id))
        self.login_url = cdk.Fn.sub("https://${LoginDomain}.auth.${AWS::Region}.amazoncognito.com")
        cdk.CfnOutput(self, "LoginUrl", key="LoginUrl", value=str(self.login_url))
        self.api_url = cdk.Fn.sub("https://${HttpApi}.execute-api.${AWS::Region}.amazonaws.com")
        cdk.CfnOutput(self, "ApiUrl", key="ApiUrl", value=str(self.api_url))
        self.state_table = state.ref
        cdk.CfnOutput(self, "StateTable", key="StateTable", value=str(self.state_table))
        self.queue_url = jobs.ref
        cdk.CfnOutput(self, "QueueUrl", key="QueueUrl", value=str(self.queue_url))
        users.user_pool_tier = "ESSENTIALS"
        login_domain.managed_login_version = 2
        cognito.CfnManagedLoginBranding(
            self,
            "LoginBranding",
            user_pool_id=users.ref,
            client_id=browser_client.ref,
            use_cognito_provided_values=True,
        )
        configure_stack(
            self,
            "Nevermind protected workshop sandbox with asynchronous model analysis and transactional approvals.",
        )
