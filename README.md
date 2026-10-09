Subject: Chrome ERR_CERT_AUTHORITY_INVALID on new certs – USERTrust cross-sign in bundle (Account [ACCOUNT_ID])

Hello Sectigo Support,

We are seeing Chrome reject recently issued certificates from our account, and we'd like to confirm the cause and your plans for the certificate bundles.

Account: [ACCOUNT_ID]
Affected orders: [ORDER_NUMBERS]
Affected domains: [DOMAINS]

Issue
- Certificates issued in October 2026 fail in Chrome with NET::ERR_CERT_AUTHORITY_INVALID.
- Certificates issued in August 2026, deployed with the same bundle structure, still work.
- The same October certificates validate in Edge and with OpenSSL (Verify return code: 0).
- Chrome builds the path leaf → Sectigo Public Server Authentication CA [DV/OV] R36 → Sectigo Public Server Authentication Root R46 (cross-signed by USERTrust RSA Certification Authority) → USERTrust RSA Certification Authority.

Our understanding is that Chrome no longer trusts newly issued certificates that chain through USERTrust RSA, and that the USERTrust cross-sign of R46 included in the downloaded bundle is causing Chrome to take that path. Removing the cross-sign from our server chain (serving leaf + R36 only) resolves it.

Questions
1. Can you confirm the date Chrome began enforcing the USERTrust RSA SCTNotAfter restriction for newly issued certificates?
2. Which download format provides leaf + R36 only, without the USERTrust cross-sign?
3. When do you plan to stop including the USERTrust cross-sign in default bundles for our account?
4. Is there a recommended chain configuration for customers who still have legacy clients that do not trust R46, ahead of the 2027-04-15 USERTrust distrust?

Thank you,
[NAME]
[TEAM / COMPANY]
[CONTACT]
