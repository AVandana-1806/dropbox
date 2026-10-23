fields @timestamp, @message
| parse @message /\] \[(?<logTime>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})\]/
| filter @message like /task-0/
| filter @message like /FLOW_CIVICA|Loading Civica|Civica SAVE|Civica GET|FLOW_STATUS_UPDATE|HTTP_ERROR|HTTP call failed|STALE_CONNECTION|Executing Salesforce|Loading Salesforce|Received sink record batch/
| sort logTime asc
| limit 10000
