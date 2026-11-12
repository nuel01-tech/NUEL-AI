# NUEL-AI

A WhatsApp message desk with AI-assisted summaries and reply drafts. The user remains in control: drafts are never sent automatically.

## Features
- Receives direct WhatsApp messages through a Baileys bridge.
- Displays incoming messages in a lightweight web dashboard.
- Excludes configured family contacts from AI summaries and draft generation.
- Uses Groq for summaries and suggested replies on request.
- Allows drafts to be edited, approved, or rejected.
- Exposes /api/health for Render health checks.

## Safety and reliability
- NUEL-AI does not send WhatsApp replies. Approval only records a decision.
- Group chats, status updates, and messages sent by your own account are ignored.
- Message content is sent to Groq only when an AI feature is requested.
- The bridge webhook is protected by BRIDGE_WEBHOOK_TOKEN.
- Render Free services may sleep and local files are ephemeral. WhatsApp sessions and inbox data can be lost after restarts without persistent storage.

## Render setup
Create a Docker web service from this repository, branch main, region Frankfurt. Health check path: /api/health.

Set these environment variables in Render:
- GROQ_API_KEY: Groq API key (required for AI).
- GROQ_MODEL: optional; defaults to openai/gpt-oss-120b.
- WHATSAPP_PHONE_NUMBER: WhatsApp number with country code, digits only.
- BRIDGE_WEBHOOK_TOKEN: long random secret shared by the app and bridge.
- FAMILY_BLOCKLIST_NAMES: comma-separated name fragments; defaults to Mom,Dad,Auntie.
- FAMILY_BLOCKLIST_NUMBERS: comma-separated phone numbers to exclude.

Never commit secrets to GitHub.

## Pairing
After configuring environment variables and deploying, open Render service logs. Follow the printed pairing instructions and enter the code in WhatsApp under Linked devices > Link a device > Link with phone number instead.

## Local run
Install Python requirements and npm dependencies, set environment variables, then run:
    uvicorn app:app --host 0.0.0.0 --port 10000

The Python app starts the Node bridge when WHATSAPP_PHONE_NUMBER is set.

## API
- GET /api/health
- GET /api/bridge-status
- GET /api/messages
- POST /api/incoming (authenticated webhook)
- POST /api/messages/{message_id}/draft
- PATCH /api/drafts/{draft_id}
- POST /api/summary
