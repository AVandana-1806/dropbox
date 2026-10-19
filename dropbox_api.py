What broke: your URL is https://$GITHUB_TOKEN@github.com/.... Git reads that as token-as-username with an empty password. The newer libcurl (8.5.0-2ubuntu10.16) still sends the Authorization header on the first request, but not on the follow-up git-upload-pack request when the URL has a username and an empty password. GitHub then redirects .git → .git/, and Git, now holding no credentials, fails with “could not read Username”. The x-access-token:<token> form keeps working on that version.


https://github.com/renovatebot/renovate/discussions/47071
