"""CloudWatch-to-Splunk Firehose Transform
==========================================

Firehose data processor: decompresses CloudWatch Logs batches and emits one
JSON line per log event for the Splunk HEC raw endpoint. Records that would
push the response past Firehose's 6 MB synchronous limit are split and
re-ingested into the delivery stream for a later invocation rather than
being lost.

Event selection happens upstream: only the required log groups carry
subscription filters, so this processor forwards everything it receives.

Emits Reingested / ProcessingFailed / Dropped / DeliveredBytes as EMF
counters for the pipeline alarms.

Environment variables:
    METRIC_NAMESPACE   EMF namespace for the counters (default: LogPipeline)
"""
