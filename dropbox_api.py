openssl s_client -connect yourhost:443 -servername yourhost -showcerts </dev/null 2>/dev/null | grep -E "^ *[0-9] s:|^ *i:"
