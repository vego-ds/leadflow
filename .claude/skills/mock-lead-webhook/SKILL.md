---
name: mock-lead-webhook
description: Use this to test inbound leads, simulate sign-ups, or check if the backend catch system works.
disable-model-invocation: true
allowed-tools: Bash(curl *)
---
# Ingest Payload Simulation
Running automation call via curl...
```!
curl -X POST http://localhost:8000/webhook/new-lead -H 'Content-Type: application/json' -d '{"name": "$0", "phone": "$1", "preferred_language": "$2"}'
```
