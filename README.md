filter @message like /Loading Civica person by/
| parse @message /(contactId=|PersSFId=)(?<person>\S+)/
| stats count() as searches by bin(5m) as window, person
| stats sum(searches) as totalSearches, count() as personsPerWindow
