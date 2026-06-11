# ---------------------------------------------------------------------------
# Migration import blocks — adopt existing universe Lambdas previously managed
# by EuclideanInfra. Safe to delete after the first successful apply.
#
# AWS account 954976294836, region us-east-1, no env suffix (prod).
# ---------------------------------------------------------------------------

# --- universe_downloader ---
import {
  to = aws_cloudwatch_log_group.universe_downloader
  id = "/aws/lambda/euclidean-universe-downloader"
}
import {
  to = aws_iam_role.universe_downloader
  id = "euclidean-universe-downloader"
}
import {
  to = aws_iam_role_policy_attachment.universe_downloader_basic_logs
  id = "euclidean-universe-downloader/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
import {
  to = aws_iam_role_policy.universe_downloader
  id = "euclidean-universe-downloader:euclidean-universe-downloader"
}
import {
  to = aws_lambda_function.universe_downloader
  id = "euclidean-universe-downloader"
}
import {
  to = aws_cloudwatch_event_rule.universe_downloader_schedule
  id = "euclidean-universe-downloader-schedule"
}
import {
  to = aws_cloudwatch_event_target.universe_downloader_schedule
  id = "euclidean-universe-downloader-schedule/UniverseDownloaderLambda"
}
import {
  to = aws_lambda_permission.universe_downloader_eventbridge
  id = "euclidean-universe-downloader/AllowEventBridgeInvoke"
}

# --- universe_sic_worker ---
import {
  to = aws_cloudwatch_log_group.universe_sic_worker
  id = "/aws/lambda/euclidean-universe-sic-worker"
}
import {
  to = aws_iam_role.universe_sic_worker
  id = "euclidean-universe-sic-worker"
}
import {
  to = aws_iam_role_policy_attachment.universe_sic_worker_basic_logs
  id = "euclidean-universe-sic-worker/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
import {
  to = aws_iam_role_policy.universe_sic_worker
  id = "euclidean-universe-sic-worker:euclidean-universe-sic-worker"
}
import {
  to = aws_lambda_function.universe_sic_worker
  id = "euclidean-universe-sic-worker"
}
