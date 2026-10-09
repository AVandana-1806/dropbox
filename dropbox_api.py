for h in working-host.yourdomain.com new-host.yourdomain.com; do
  echo "== $h"
  openssl s_client -connect $h:443 -servername $h </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates
done
