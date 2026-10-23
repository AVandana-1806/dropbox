filter @message like /task-0/
| parse @message /event=(?<event>[A-Z_]+)/
| parse @message /(?<plain>Received sink record batch|Loading Civica person|Civica SAVE request|Executing Salesforce|Loading Salesforce|HTTP call failed|HTTP_STALE_CONNECTION_RETRY)/
| stats count() by coalesce(event, plain) as what
| sort count() desc
