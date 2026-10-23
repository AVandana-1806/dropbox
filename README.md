filter @message like /Loading Civica person by/
| parse @message /(contactId=|PersSFId=)(?<person>\S+)/
| stats count() as searches by person
| stats count() as people, sum(searches) as totalSearches
