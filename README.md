filter @message like /task-0/
| parse @message /event=(?<event>[A-Z_]+)/
| parse @message /(?<plain>Received sink record batch|Loading Civica person|Civica SAVE request|Executing Salesforce|Loading Salesforce|HTTP call failed|HTTP_STALE_CONNECTION_RETRY)/
| fields coalesce(event, plain) as what
| filter ispresent(what)
| stats count() as cnt by what
| sort cnt desc
