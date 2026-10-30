{
  "Records": [
    {
      "EventSource": "aws:sns",
      "EventVersion": "1.0",
      "EventSubscriptionArn": "arn:aws:sns:us-west-2:123456789012:splunk-logs-transform-alerts:11111111-2222-3333-4444-555555555555",
      "Sns": {
        "Type": "Notification",
        "MessageId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "TopicArn": "arn:aws:sns:us-west-2:123456789012:splunk-logs-transform-alerts",
        "Subject": "ALARM: \"splunk-logs-transform-processing-failed\" in US West (Oregon)",
        "Message": "{\"AlarmName\": \"splunk-logs-transform-processing-failed\", \"AlarmDescription\": \"Records failed transformation and were written to S3 - they are not in Splunk\", \"AWSAccountId\": \"123456789012\", \"NewStateValue\": \"ALARM\", \"NewStateReason\": \"Threshold Crossed: 1 datapoint [11.0 (20/10/26 02:00:00)] was greater than the threshold (0.0).\", \"StateChangeTime\": \"2026-10-20T02:04:17.482+0000\", \"Region\": \"US West (Oregon)\", \"AlarmArn\": \"arn:aws:cloudwatch:us-west-2:123456789012:alarm:splunk-logs-transform-processing-failed\", \"OldStateValue\": \"OK\", \"Trigger\": {\"MetricName\": \"ProcessingFailed\", \"Namespace\": \"LogPipeline\", \"StatisticType\": \"Statistic\", \"Statistic\": \"SUM\", \"Unit\": null, \"Dimensions\": [], \"Period\": 300, \"EvaluationPeriods\": 1, \"ComparisonOperator\": \"GreaterThanThreshold\", \"Threshold\": 0.0, \"TreatMissingData\": \"notBreaching\"}}",
        "Timestamp": "2026-10-20T02:04:17.600Z",
        "SignatureVersion": "1",
        "Signature": "EXAMPLE",
        "SigningCertUrl": "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-EXAMPLE.pem",
        "UnsubscribeUrl": "https://sns.us-west-2.amazonaws.com/?Action=Unsubscribe",
        "MessageAttributes": {}
      }
    }
  ]
}
