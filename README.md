   filter @message like /Loading Civica person by/
   | parse @message /(contactId=|PersSFId=)(?<person>\S+)/
   | stats count() as searches by bin(5m) as window, person
   | filter searches > 1
   | sort searches desc
   | limit 20
