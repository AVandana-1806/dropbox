SELECT Id, Target_Table__c, Record_Indicator__c, CreatedDate, MDM_Status__c
FROM Trigger_Table__c
WHERE CreatedDate >= 2026-10-23T16:50:00Z AND CreatedDate < 2026-10-23T17:00:00Z
  AND Key__c LIKE '%PER-001898272623%'
ORDER BY CreatedDate
