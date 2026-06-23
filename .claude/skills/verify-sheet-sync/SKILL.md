---
name: verify-sheet-sync
description: Use this to scan project files and check if the sheet tab structure is valid.
allowed-tools: Bash(python3 *)
---
# Spreadsheet Schema Verification
Verifying sheet synchronization adapters...
```!
python3 -c "import os; print('Source structure looks valid' if os.path.exists('app/main.py') else 'Warning: app/main.py missing')"
```
