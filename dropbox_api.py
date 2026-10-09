      - |
        ls cert_*.pem | grep -v "cert_${CERT_COUNT}.pem" | sort -r | while read -r f; do
          openssl x509 -in "$f" -noout -issuer | grep -qE "USERTrust|AAA Certificate Services" || cat "$f"
        done > ca_chain.pem
