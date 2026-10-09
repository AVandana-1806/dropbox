openssl s_client -connect api.yourdomain.com:443 -servername api.yourdomain.com -showcerts </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -ext subjectAltName -dates
