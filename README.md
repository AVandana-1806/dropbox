<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Log Pipeline Alert</title>
    <style>
        body {
            font-family: -apple-system, "Segoe UI", Arial, sans-serif;
            line-height: 1.5;
            margin: 0;
            padding: 24px 12px;
            background-color: #eef1f5;
            color: #1f2933;
        }

        .container {
            max-width: 720px;
            margin: 0 auto;
            background: #ffffff;
            border: 1px solid #dfe3e8;
            border-radius: 6px;
            overflow: hidden;
        }

        .masthead {
            border-left: 5px solid #c0392b;
            padding: 20px 28px 18px 24px;
            border-bottom: 1px solid #e6e9ee;
        }

        h1 {
            font-size: 15px;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #c0392b;
            margin: 0 0 8px 0;
        }

        .alarm-name {
            font-size: 20px;
            font-weight: 600;
            color: #1f2933;
            word-break: break-all;
        }

        .meta {
            font-size: 13px;
            color: #6b7684;
            margin: 8px 0 0 0;
        }

        .meta strong {
            color: #3e4c59;
            font-weight: 600;
        }

        .body {
            padding: 8px 28px 28px 28px;
        }

        h2 {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: #8694a3;
            margin: 28px 0 0 0;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
            border-radius: 8px;
            overflow: hidden;
        }

        thead {
            background-color: #1a72e8
        }

        th {
            padding: 14px 16px;
            text-align: left;
            font-weight: 700;
            color: #ffffff;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        td {
            padding: 14px 16px;
            border-bottom: 1px solid #e9ecef;
            font-size: 14px;
        }

        tbody tr {
            background: #ffffff;
        }

        tbody tr:nth-child(even) {
            background: #f8f9fa;
        }

        td.alert {
            color: #c0392b;
            font-weight: 700;
        }

        ul {
            list-style-type: none;
            padding: 0;
            margin: 10px 0 0 0;
        }

        li {
            background: #fdf3f2;
            border-left: 4px solid #c0392b;
            padding: 12px 16px;
            font-size: 13px;
            color: #3e4c59;
            border-radius: 0 3px 3px 0;
        }

        li.success {
            background: #f1f8f3;
            border-left-color: #2f855a;
        }

        li.failure {
            background: #fdf3f2;
            border-left-color: #c0392b;
        }

        li.rollback {
            background: #fdf6ed;
            border-left-color: #c2700e;
        }

        li.skipped {
            background: #f5f7fa;
            border-left-color: #8694a3;
        }

        li strong {
            color: #1f2933;
        }

        .actions {
            margin-top: 28px;
        }

        .actions a {
            display: inline-block;
            text-decoration: none;
            font-size: 13px;
            font-weight: 600;
            padding: 9px 18px;
            border-radius: 4px;
            margin-right: 8px;
        }

        .actions a.primary {
            background: #1f3a5f;
            color: #ffffff;
        }

        .actions a.secondary {
            color: #1f3a5f;
            border: 1px solid #c6cdd6;
        }

        .footer {
            padding: 14px 28px;
            background: #f5f7fa;
            border-top: 1px solid #e6e9ee;
            text-align: center;
            font-size: 11px;
            color: #8694a3;
        }

        .footer p {
            margin: 0;
        }
    </style>
</head>

<body>
    <div class="container">

        <div class="masthead">
            <h1>Log Pipeline Alert</h1>
            <div class="alarm-name">splunk-logs-transform-processing-failed</div>
            <p class="meta">
                <strong>dev01</strong> &nbsp;&middot;&nbsp;
                us-west-2 &nbsp;&middot;&nbsp;
                2026-10-20T02:04:17.482+0000
            </p>
        </div>

        <div class="body">

            <h2>Alarm Detail</h2>
            <table>
                <thead>
                    <tr>
                        <th>Field</th>
                        <th>Value</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Metric</td>
                        <td>ProcessingFailed (LogPipeline)</td>
                    </tr>
                    <tr>
                        <td>Condition</td>
                        <td>SUM GreaterThanThreshold 0.0 for 1 period(s) of 300s</td>
                    </tr>
                    <tr>
                        <td>Reason</td>
                        <td>Threshold Crossed: 1 datapoint [11.0 (20/10/26 02:00:00)] was greater than the threshold (0.0).</td>
                    </tr>
                </tbody>
            </table>

            <h2>Pipeline State</h2>
            <table>
                <thead>
                    <tr>
                        <th>Stage</th>
                        <th>Signal</th>
                        <th>Value</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td>CloudWatch to Firehose</td><td>IncomingRecords (15m)</td><td>7,997</td></tr>
<tr><td>Transform</td><td>Errors (15m)</td><td class="alert">11</td></tr>
<tr><td>Transform</td><td>Throttles (15m)</td><td>0</td></tr>
<tr><td>Firehose to Splunk</td><td>Delivery success</td><td>100.00%</td></tr>
<tr><td>Recovery</td><td>Records rejected (15m)</td><td class="alert">11</td></tr>
                </tbody>
            </table>

            <h2>Status</h2>
            <ul>
                <li class="failure"><strong>In alarm.</strong> Records failed transformation and were written to S3 - they are not in Splunk.</li>
            </ul>

            <div class="actions">
                <a class="primary" href="https://console.aws.amazon.com/cloudwatch">View Alarm</a>
                <a class="secondary" href="https://console.aws.amazon.com/cloudwatch/logs">Lambda Logs</a>
            </div>

        </div>

        <div class="footer">
            <p>Report generated by <strong>CDI DevOps</strong></p>
        </div>

    </div>
</body>

</html>
