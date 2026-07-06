"""Universe Model stack — two zip-packaged Lambdas (universe downloader + SIC
worker) with a daily 05:30 UTC schedule on the downloader.

Faithful CDK translation of `UniverseModel/terraform/*.tf`. See ExecutionModel's
stack for the import-safety rationale behind the literal role ARN, the
`import_mode` guards, and `value_from_lookup` for the SSM contract.

Zip-code note: unlike the image-based repos there is no container build — CDK
zips `lambdas/<name>/` as an asset (exactly what Terraform's `archive_file`
did) and the converge/CI `cdk deploy` uploads it and updates the function code
in one step. Push to main → deploy, same contract as the TFC apply had.

Import note: the EventBridge *target* and the Lambda invoke-permission are
guarded behind `import_mode` — a CloudFormation import changeset must contain
only importable resources, and `AWS::Lambda::Permission` is not importable.
The converge deploy re-creates the permission (inert; see plan) and wires the
target back onto the imported rule.
"""
import os

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_ssm as ssm,
)
from constructs import Construct

DOWNLOADER_NAME = "euclidean-universe-downloader"
SIC_WORKER_NAME = "euclidean-universe-sic-worker"
SCHEDULE_RULE_NAME = "euclidean-universe-downloader-schedule"
# SEC EDGAR user-agent identity (was a TFC workspace variable)
EDGAR_IDENTITY = "apodrez21@gmail.com"

LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "lambdas")


class UniverseModelStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        import_mode = self.node.try_get_context("import_mode")
        bucket_name = ssm.StringParameter.value_from_lookup(self, "/euclidean/s3_bucket_name")
        bucket_arn = ssm.StringParameter.value_from_lookup(self, "/euclidean/s3_bucket_arn")
        sic_worker_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:{SIC_WORKER_NAME}"

        # ---- log groups ----
        logs.LogGroup(
            self,
            "DownloaderLogGroup",
            log_group_name=f"/aws/lambda/{DOWNLOADER_NAME}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        logs.LogGroup(
            self,
            "SicWorkerLogGroup",
            log_group_name=f"/aws/lambda/{SIC_WORKER_NAME}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ---- IAM roles (inline policy name == role name, matching Terraform) ----
        iam.Role(
            self,
            "DownloaderRole",
            role_name=DOWNLOADER_NAME,
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
            inline_policies={
                DOWNLOADER_NAME: iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="S3ReadUniverse",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject"],
                            resources=[f"{bucket_arn}/universe/universe.csv"],
                        ),
                        iam.PolicyStatement(
                            sid="S3WriteWork",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:PutObject"],
                            resources=[f"{bucket_arn}/universe/work/*"],
                        ),
                        iam.PolicyStatement(
                            sid="InvokeSicWorker",
                            effect=iam.Effect.ALLOW,
                            actions=["lambda:InvokeFunction"],
                            resources=[sic_worker_arn],
                        ),
                    ]
                )
            },
        )

        iam.Role(
            self,
            "SicWorkerRole",
            role_name=SIC_WORKER_NAME,
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
            inline_policies={
                SIC_WORKER_NAME: iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="S3ReadWork",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject"],
                            resources=[f"{bucket_arn}/universe/work/*"],
                        ),
                        iam.PolicyStatement(
                            sid="S3WriteWork",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:PutObject", "s3:DeleteObject"],
                            resources=[f"{bucket_arn}/universe/work/*"],
                        ),
                        iam.PolicyStatement(
                            sid="S3WriteUniverse",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:PutObject"],
                            resources=[f"{bucket_arn}/universe/universe.csv"],
                        ),
                        iam.PolicyStatement(
                            sid="SelfInvoke",
                            effect=iam.Effect.ALLOW,
                            actions=["lambda:InvokeFunction"],
                            resources=[sic_worker_arn],
                        ),
                    ]
                )
            },
        )

        # ---- Lambda functions (zip assets, literal role ARNs for import safety) ----
        downloader_role_ref = iam.Role.from_role_arn(
            self, "DownloaderRoleRef", f"arn:aws:iam::{self.account}:role/{DOWNLOADER_NAME}", mutable=False
        )
        sic_worker_role_ref = iam.Role.from_role_arn(
            self, "SicWorkerRoleRef", f"arn:aws:iam::{self.account}:role/{SIC_WORKER_NAME}", mutable=False
        )

        downloader = _lambda.Function(
            self,
            "DownloaderFunction",
            function_name=DOWNLOADER_NAME,
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(LAMBDAS_DIR, "universe_downloader")),
            role=downloader_role_ref,
            memory_size=256,
            timeout=Duration.seconds(60),
            environment={
                "S3_BUCKET": bucket_name,
                "UNIVERSE_KEY": "universe/universe.csv",
                "MANIFEST_PREFIX": "universe/work",
                "EDGAR_IDENTITY": EDGAR_IDENTITY,
                "SIC_WORKER_FUNCTION_NAME": SIC_WORKER_NAME,
            },
        )

        _lambda.Function(
            self,
            "SicWorkerFunction",
            function_name=SIC_WORKER_NAME,
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(LAMBDAS_DIR, "universe_sic_worker")),
            role=sic_worker_role_ref,
            memory_size=256,
            timeout=Duration.seconds(900),
            environment={
                "S3_BUCKET": bucket_name,
                "UNIVERSE_KEY": "universe/universe.csv",
                "MANIFEST_PREFIX": "universe/work",
                "EDGAR_IDENTITY": EDGAR_IDENTITY,
            },
        )

        # ---- daily schedule; runs before fama-french-daily (06:15) so
        # universe.csv is ready for ticker filtering ----
        rule = events.Rule(
            self,
            "DownloaderSchedule",
            rule_name=SCHEDULE_RULE_NAME,
            description="Daily trigger for universe_downloader at 05:30 UTC",
            schedule=events.Schedule.expression("cron(30 5 * * ? *)"),
        )

        if not import_mode:
            rule.add_target(targets.LambdaFunction(downloader))
            Tags.of(self).add("Project", "euclidean")
            Tags.of(self).add("ManagedBy", "cdk")
            Tags.of(self).add("Component", "universe")
