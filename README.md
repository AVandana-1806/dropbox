fields @timestamp, @message
| filter @message like /FLOW_APEX_REQUEST/
| parse @message "idCount=* " as idCount
| stats count() as calls, avg(idCount) as avgIds, max(idCount) as maxIds by bin(5m)


fields @timestamp, @message
| filter @message like /Received sink record batch size=|FLOW_APEX_REQUEST|FLOW_CIVICA_SAVE_RESPONSE/
| sort @timestamp asc
| limit 500
