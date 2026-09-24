# common_passwords.txt

Passwords rejected by the password policy because they appear in breach corpora.

- Source: SecLists, `Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt`
  (https://github.com/danielmiessler/SecLists), MIT License, Copyright (c) 2018 Daniel Miessler.
- Derived: entries shorter than 8 characters (code points) removed (the minimum length rule rejects them
  anyway), NFKC-normalized, case-folded, de-duplicated and sorted. Matching applies the same
  normalization, so it is case-insensitive.
