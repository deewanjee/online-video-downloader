# Updates, backups and hosting

Keep history and saved media safe during maintenance.

## Update an existing laptop install

1. Stop the app with Ctrl+C. Download the latest project files from the repository.
2. Replace project code while preserving data, .venv and .env. Keep all selected output folders.
3. Launch Start StreamVault.bat and refresh with Ctrl+F5. The browser and backend versions should match.

## Back up your work

1. Stop the app before backing up history.sqlite3 and the entire data directory. Back up selected output folders separately.
2. Lifetime counters are independent of library entries; deleting history rows does not reset them. Lost/deleted historical data cannot always be reconstructed.

## Public hosting

1. The provided Docker/Caddy templates need a real server, domain, HTTPS and configured owner authentication. They are preparation templates, not evidence of a deployed public service.
2. Hosted operation uses server storage and browser saving, with storage quotas and source-site limits. This app currently uses a single shared owner workspace; it is not a multi-tenant account/billing platform.
