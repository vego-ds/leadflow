---
name: mock-payment-webhook
description: Use this to test payment confirmations, simulate a successful payment, or check if the conversion stage works.
disable-model-invocation: true
allowed-tools: Bash(curl *)
---
# Payment Confirmation Simulation
Running automation call via curl...
```!
curl -X POST http://localhost:8000/webhook/payment -H 'Content-Type: application/json' -d '{"lead_id": "$0", "payment_id": "$1"}'
```
