#!/usr/bin/env python3
"""CDK app for the Universe Model repo (replaces the Terraform Cloud workspace)."""
import os

import aws_cdk as cdk

from stacks.universe_model_stack import UniverseModelStack

app = cdk.App()

account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"

UniverseModelStack(
    app,
    "EuclideanUniverseModel",
    stack_name="euclidean-universe-model",
    env=cdk.Environment(account=account, region=region),
)

app.synth()
