AWSTemplateFormatVersion: "2010-09-09"
Transform: AWS::Serverless-2016-10-31
Description: >
  CloudWatch-to-Splunk log pipeline (minimal design): Firehose transform Lambda,
  scheduled S3 failed-event replay Lambda, and the alarm suite that guards
  against breakage, runaway invocations, and runaway cost.

Globals:
  Function:
    Runtime: python3.13
    Architectures: [arm64]          # ~20% cheaper per GB-second than x86_64
    Tracing: Active
    PropagateTags: true
    Tags:
      Pipeline: splunk-logs

Parameters:
  BackupBucketName:
    Type: String
    Description: Firehose backup bucket (splunk-failed/, processing-failed/)
  DeliveryStreamName:
    Type: String
    Description: Firehose delivery stream name
  FirehoseRoleArn:
    Type: String
    Description: Role Firehose assumes; granted lambda:InvokeFunction on the transform alias
  HecEndpoint:
    Type: String
    Description: Splunk HEC base URL, e.g. https://splunk-host:8088
  HecSecretArn:
    Type: String
  KmsKeyArn:
    Type: String
  AlarmEmail:
    Type: String
    Description: Address subscribed to the alarm topic
  EnvironmentName:
    Type: String
    Description: Label shown in alert email headers, e.g. dev01
  AlertFromEmail:
    Type: String
    Description: SES-verified sender for formatted transform alerts
  AlertRecipients:
    Type: CommaDelimitedList
    Description: Recipients of formatted transform alerts
  LogRetentionDays:
    Type: Number
    Default: 30
    Description: Lambda log retention. Never leave this unset - unbounded log storage is a real cost line
  # --- alarm thresholds, tuned at ~100 GB/day -------------------------------
  TransformInvocationCeiling:
    Type: Number
    Default: 20000
    Description: Invocations per hour that should never be exceeded (normal ~4200/hr at 1 MB buffers)
  TransformConcurrencyWarn:
    Type: Number
    Default: 16
    Description: Concurrent executions warning level (80% of the reserved 20)
  ErrorBurstThreshold:
    Type: Number
    Default: 100
    Description: Function errors in 5 minutes that indicate systemic breakage
  MonthlyBudgetUsd:
    Type: Number
    Default: 400
    Description: Monthly cost budget for resources tagged Pipeline=splunk-logs

Resources:

  # ===========================================================================
  # Alerting target
  # ===========================================================================
  AlarmTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: !Sub "${AWS::StackName}-alarms"
      Subscription:
        - Protocol: email
          Endpoint: !Ref AlarmEmail

  TransformAlertTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: !Sub "${AWS::StackName}-transform-alerts"

  # ===========================================================================
  # Alert Lambda - formats transform alarms into the HTML email
  # ===========================================================================
  AlertFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub "${AWS::StackName}-alert-notification"
      Description: Sends formatted HTML emails for transform Lambda alarms
      CodeUri: ./src/alert/
      Handler: alert_notification.handler
      MemorySize: 256
      Timeout: 30
      Environment:
        Variables:
          FROM_EMAIL: !Ref AlertFromEmail
          TO_EMAILS: !Join [",", !Ref AlertRecipients]
          ENVIRONMENT: !Ref EnvironmentName
          DELIVERY_STREAM_NAME: !Ref DeliveryStreamName
          TRANSFORM_FUNCTION: !Ref TransformFunction
          BACKUP_BUCKET: !Ref BackupBucketName
      Policies:
        - Statement:
            - Sid: SendAlertEmail
              Effect: Allow
              Action: ses:SendEmail
              Resource: "*"
            - Sid: ReadPipelineMetrics
              Effect: Allow
              Action: cloudwatch:GetMetricData
              Resource: "*"
      Events:
        TransformAlarms:
          Type: SNS
          Properties:
            Topic: !Ref TransformAlertTopic

  AlertLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/aws/lambda/${AlertFunction}"
      RetentionInDays: !Ref LogRetentionDays

  # ===========================================================================
  # Lambda 1 - Firehose transform (hot path)
  # ===========================================================================
  TransformFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub "${AWS::StackName}-cw-splunk-transform"
      Description: Decompresses CloudWatch batches into Splunk HEC raw lines
      CodeUri: ./src/transform/
      Handler: cw_splunk_transform.handler
      MemorySize: 1024
      Timeout: 300                        # Firehose's maximum for a processor
      ReservedConcurrentExecutions: 20    # guarantees capacity AND caps spend
      AutoPublishAlias: live              # Firehose points at the alias
      Policies:
        - Statement:
            - Sid: ReingestOverflow
              Effect: Allow
              Action: firehose:PutRecordBatch
              Resource: !Sub "arn:${AWS::Partition}:firehose:${AWS::Region}:${AWS::AccountId}:deliverystream/${DeliveryStreamName}"

  TransformLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/aws/lambda/${TransformFunction}"
      RetentionInDays: !Ref LogRetentionDays

  # Firehose invokes through its own role, so the grant lives on the function.
  TransformInvokePermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref TransformFunction.Alias
      Action: lambda:InvokeFunction
      Principal: !Ref FirehoseRoleArn

  # ===========================================================================
  # Lambda 2 - scheduled S3 failed-event replay (recovery path)
  # ===========================================================================
  ReplayFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub "${AWS::StackName}-s3-failed-event-replay"
      Description: Retries Firehose failure objects; quarantines permanent rejections
      CodeUri: ./src/replay/
      Handler: s3_failed_event_replay.handler
      MemorySize: 512
      Timeout: 600
      ReservedConcurrentExecutions: 1     # only one copy ever runs
      Environment:
        Variables:
          BUCKET_NAME: !Ref BackupBucketName
          HEC_ENDPOINT: !Ref HecEndpoint
          HEC_SECRET_ARN: !Ref HecSecretArn
          DELIVERY_STREAM_NAME: !Ref DeliveryStreamName
          FAILED_PREFIX: "failed-events/"
          MAX_OBJECTS_PER_RUN: "200"
          MIN_AGE_MINUTES: "15"
          PERMANENT_ERROR_CODES: Lambda.ProcessingFailedStatus
      Policies:
        - Statement:
            - Sid: ListBucket
              Effect: Allow
              Action: s3:ListBucket
              Resource: !Sub "arn:${AWS::Partition}:s3:::${BackupBucketName}"
            - Sid: ReadMoveObjects
              Effect: Allow
              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject]
              Resource: !Sub "arn:${AWS::Partition}:s3:::${BackupBucketName}/*"
            - Sid: Reingest
              Effect: Allow
              Action: firehose:PutRecordBatch
              Resource: !Sub "arn:${AWS::Partition}:firehose:${AWS::Region}:${AWS::AccountId}:deliverystream/${DeliveryStreamName}"
            - Sid: ReadHecToken
              Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Ref HecSecretArn
            - Sid: UseKey
              Effect: Allow
              Action: [kms:Decrypt, kms:GenerateDataKey]
              Resource: !Ref KmsKeyArn
      Events:
        HalfHourly:
          Type: ScheduleV2               # EventBridge Scheduler
          Properties:
            ScheduleExpression: rate(30 minutes)
            FlexibleTimeWindow:
              Mode: "OFF"                # exact cadence, no jitter
            RetryPolicy:
              MaximumRetryAttempts: 2    # covers a throttle if the prior run is still going
              MaximumEventAgeInSeconds: 600

  ReplayLogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub "/aws/lambda/${ReplayFunction}"
      RetentionInDays: !Ref LogRetentionDays

  # ===========================================================================
  # A. Breakage alarms - the function itself is failing
  # ===========================================================================
  TransformErrorBurstAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-error-burst"
      AlarmDescription: !Sub "${ErrorBurstThreshold}+ transform errors in 5 minutes - systemic breakage, records are heading to S3"
      Namespace: AWS/Lambda
      MetricName: Errors
      Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: !Ref ErrorBurstThreshold
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformErrorRateAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-error-rate"
      AlarmDescription: Transform error rate above 1% for 15 minutes - catches a slow bleed the burst alarm misses
      Metrics:
        - Id: errors
          MetricStat:
            Metric:
              Namespace: AWS/Lambda
              MetricName: Errors
              Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
            Period: 300
            Stat: Sum
          ReturnData: false
        - Id: invocations
          MetricStat:
            Metric:
              Namespace: AWS/Lambda
              MetricName: Invocations
              Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
            Period: 300
            Stat: Sum
          ReturnData: false
        - Id: rate
          Expression: "100 * errors / MAX([invocations, 1])"
          Label: Error rate %
          ReturnData: true
      EvaluationPeriods: 3
      DatapointsToAlarm: 3
      Threshold: 1
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformThrottleAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-throttled"
      AlarmDescription: Transform is being throttled - reserved concurrency is too low or something is looping
      Namespace: AWS/Lambda
      MetricName: Throttles
      Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformNearTimeoutAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-near-timeout"
      AlarmDescription: p99 duration above 80% of the 300 s timeout - the next step is failed invocations
      Namespace: AWS/Lambda
      MetricName: Duration
      Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
      ExtendedStatistic: p99
      Period: 300
      EvaluationPeriods: 3
      Threshold: 240000
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformDroppedAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-records-dropped"
      AlarmDescription: >
        Records were dropped without being delivered. Expected only for
        CloudWatch control probes and empty batches; a sustained rate means
        envelopes are arriving with no log events.
      Namespace: LogPipeline
      MetricName: Dropped
      Statistic: Sum
      Period: 3600
      EvaluationPeriods: 2
      DatapointsToAlarm: 2
      Threshold: 50
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformProcessingFailedAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-records-rejected"
      AlarmDescription: Records were marked ProcessingFailed and sent to S3 - corrupt data or an event too large to split
      Namespace: LogPipeline
      MetricName: ProcessingFailed
      Statistic: Sum
      Period: 900
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  # ===========================================================================
  # B. Cost-runaway alarms - nothing is "broken" but spend is climbing
  # ===========================================================================
  TransformInvocationCeilingAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-invocations-high"
      AlarmDescription: Invocations far above the expected hourly rate - log storm, buffer misconfiguration, or a re-ingestion loop
      Namespace: AWS/Lambda
      MetricName: Invocations
      Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
      Statistic: Sum
      Period: 3600
      EvaluationPeriods: 1
      Threshold: !Ref TransformInvocationCeiling
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformInvocationAnomalyAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-invocations-anomalous"
      AlarmDescription: Invocations outside the learned normal band - catches a doubling long before the fixed ceiling does
      Metrics:
        - Id: invocations
          MetricStat:
            Metric:
              Namespace: AWS/Lambda
              MetricName: Invocations
              Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
            Period: 900
            Stat: Sum
          ReturnData: true
        - Id: band
          Expression: ANOMALY_DETECTION_BAND(invocations, 3)
          ReturnData: true
      ThresholdMetricId: band
      EvaluationPeriods: 3
      DatapointsToAlarm: 3
      ComparisonOperator: GreaterThanUpperThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  ReingestionLoopAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-reingestion-sustained"
      AlarmDescription: >
        Records are being re-ingested continuously. Each re-ingested record is
        billed twice (once deferred, once re-processed) and re-enters Firehose,
        so a sustained rate is the pipeline's main silent cost leak. Usually
        means the Firehose buffer is too large for the log groups' event sizes.
      Namespace: LogPipeline
      MetricName: Reingested
      Statistic: Sum
      Period: 3600
      EvaluationPeriods: 3
      DatapointsToAlarm: 3
      Threshold: 500
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  TransformConcurrencyAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-transform-concurrency-high"
      AlarmDescription: Concurrent executions approaching the reserved limit - throttling and cost both follow
      Namespace: AWS/Lambda
      MetricName: ConcurrentExecutions
      Dimensions: [{Name: FunctionName, Value: !Ref TransformFunction}]
      Statistic: Maximum
      Period: 300
      EvaluationPeriods: 2
      Threshold: !Ref TransformConcurrencyWarn
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref TransformAlertTopic]

  PipelineBudget:
    Type: AWS::Budgets::Budget
    Properties:
      Budget:
        BudgetName: !Sub "${AWS::StackName}-monthly"
        BudgetType: COST
        TimeUnit: MONTHLY
        BudgetLimit:
          Amount: !Ref MonthlyBudgetUsd
          Unit: USD
        CostFilters:
          TagKeyValue: ["user:Pipeline$splunk-logs"]
      NotificationsWithSubscribers:
        - Notification:
            NotificationType: FORECASTED
            ComparisonOperator: GREATER_THAN
            Threshold: 100
            ThresholdType: PERCENTAGE
          Subscribers:
            - SubscriptionType: EMAIL
              Address: !Ref AlarmEmail

  # ===========================================================================
  # C. Recovery-path alarms
  # ===========================================================================
  ReplayFunctionErrorAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-replay-function-error"
      AlarmDescription: The replay Lambda itself crashed - failed objects are not being retried
      Namespace: AWS/Lambda
      MetricName: Errors
      Dimensions: [{Name: FunctionName, Value: !Ref ReplayFunction}]
      Statistic: Sum
      Period: 1800
      EvaluationPeriods: 2
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref AlarmTopic]

  ReplayNotRunningAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-replay-not-running"
      AlarmDescription: No replay invocation in 2 hours - the schedule is disabled or failing, so failures are piling up unnoticed
      Namespace: AWS/Lambda
      MetricName: Invocations
      Dimensions: [{Name: FunctionName, Value: !Ref ReplayFunction}]
      Statistic: Sum
      Period: 7200
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: LessThanThreshold
      TreatMissingData: breaching       # missing data IS the failure here
      AlarmActions: [!Ref AlarmTopic]

  ReplayNearTimeoutAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-replay-near-timeout"
      AlarmDescription: Replay runs are hitting the time guard - the backlog is draining slower than it arrives
      Namespace: AWS/Lambda
      MetricName: Duration
      Dimensions: [{Name: FunctionName, Value: !Ref ReplayFunction}]
      Statistic: Maximum
      Period: 1800
      EvaluationPeriods: 3
      Threshold: 480000
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref AlarmTopic]

  # Needs a bucket MetricsConfiguration with Prefix quarantine/ (bucket stack).
  QuarantineNotEmptyAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-quarantine-not-empty"
      AlarmDescription: An object was permanently rejected and moved to quarantine/ - needs human triage
      Namespace: AWS/S3
      MetricName: NumberOfObjects
      Dimensions:
        - {Name: BucketName, Value: !Ref BackupBucketName}
        - {Name: FilterId, Value: quarantine}
      Statistic: Maximum
      Period: 86400
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref AlarmTopic]

  # ===========================================================================
  # D. Delivery alarms - Firehose and the silent-gap case
  # ===========================================================================
  SplunkDeliveryFailureAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-splunk-delivery-failing"
      AlarmDescription: Firehose delivery success below 99.9% - records are routing to S3
      Namespace: AWS/Firehose
      MetricName: DeliveryToSplunk.Success
      Dimensions: [{Name: DeliveryStreamName, Value: !Ref DeliveryStreamName}]
      Statistic: Average
      Period: 300
      EvaluationPeriods: 2
      Threshold: 0.999
      ComparisonOperator: LessThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref AlarmTopic]

  SplunkDataFreshnessAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-splunk-data-stale"
      AlarmDescription: Records are aging in Firehose - Splunk is slow or backpressured
      Namespace: AWS/Firehose
      MetricName: DeliveryToSplunk.DataFreshness
      Dimensions: [{Name: DeliveryStreamName, Value: !Ref DeliveryStreamName}]
      Statistic: Maximum
      Period: 300
      EvaluationPeriods: 2
      Threshold: 900
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref AlarmTopic]

  SilentGapAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-silent-ingestion-gap"
      AlarmDescription: >
        Firehose is receiving almost nothing while the pipeline normally runs.
        This is the one failure S3 cannot see - nothing failed, logs simply
        stopped entering. Logs remain in CloudWatch for 14 days; follow the
        manual backfill runbook.
      Namespace: AWS/Firehose
      MetricName: IncomingRecords
      Dimensions: [{Name: DeliveryStreamName, Value: !Ref DeliveryStreamName}]
      Statistic: Sum
      Period: 1800
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: LessThanThreshold
      TreatMissingData: breaching
      AlarmActions: [!Ref AlarmTopic]

  # ===========================================================================
  # E. One composite alarm to page on
  # ===========================================================================
  PipelineUnhealthyAlarm:
    Type: AWS::CloudWatch::CompositeAlarm
    Properties:
      AlarmName: !Sub "${AWS::StackName}-PIPELINE-UNHEALTHY"
      AlarmDescription: Page on this one; the individual alarms below say which part broke
      AlarmRule: !Sub
        - "ALARM(${A}) OR ALARM(${B}) OR ALARM(${C}) OR ALARM(${D}) OR ALARM(${E})"
        - A: !Ref TransformErrorBurstAlarm
          B: !Ref TransformThrottleAlarm
          C: !Ref SplunkDeliveryFailureAlarm
          D: !Ref SilentGapAlarm
          E: !Ref ReplayNotRunningAlarm
      ActionsEnabled: true
      AlarmActions: [!Ref AlarmTopic]

Outputs:
  TransformFunctionAlias:
    Description: Point the Firehose processor at this ARN
    Value: !Ref TransformFunction.Alias
  ReplayFunctionArn:
    Value: !GetAtt ReplayFunction.Arn
  AlarmTopicArn:
    Value: !Ref AlarmTopic
  TransformAlertTopicArn:
    Value: !Ref TransformAlertTopic
