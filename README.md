fields @timestamp, @message
| filter @message like /FLOW_APEX_REQUEST/
| parse @message "idCount=* " as idCount
| stats count() as calls, avg(idCount) as avgIds, max(idCount) as maxIds by bin(5m)
