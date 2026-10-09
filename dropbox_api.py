Get-ChildItem Cert:\CurrentUser\Disallowed, Cert:\LocalMachine\Disallowed |
  Where-Object Subject -like "*Root R46*" |
  Select-Object PSParentPath, Subject, Issuer, Thumbprint
