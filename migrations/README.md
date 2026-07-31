# Functional-library split migrations

These scripts record the one-time migration from the retired standards-based
libraries (`lib_posix`, `lib_posix_optional`, `lib_posix_xsi`, and `lib_unix`)
to the functional libraries listed in `../repos.txt`.

They are historical migration aids, not stack maintenance commands:

- `migrate-domain-libraries.py` reconstructs the functional repositories from
  local checkouts of the retired repositories.
- `migrate-domain-consumers.py` rewrites consumers from retired public headers
  and link targets to functional owners.

Both scripts require the retired repositories to be present locally. Fresh
workspaces intentionally do not clone those repositories. The maintained
contract is `../check-functional-library-split.py`; run that check to validate
the resulting repository graph.
