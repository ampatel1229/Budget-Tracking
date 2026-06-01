# LLM 1 - Purdue Budget Assistant Starter

This folder contains a fine-tuning and privacy guardrail starter pack.

## Files
- `ft_train_messages_clean.jsonl`: deduped train set in messages format
- `ft_eval_messages_clean.jsonl`: eval set in messages format
- `ft_train_policy_only.jsonl`: smaller policy-focused train set (recommended first)
- `redaction_pairs.jsonl`: raw-upload to sanitized output pairs
- `blocked_prompts.txt`: prompts your pre-chat guard should block
- `upload_finetune.py`: validate/upload files and create fine-tune job
- `pii_filter.py`: regex + intent-based pre-chat PII blocker
- `chat_api.py`: minimal FastAPI endpoint wired to the PII filter + Responses API

## Quick start
1. Create venv and install deps:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
2. Set API key:
   - `export OPENAI_API_KEY=your_key_here`
3. Validate training files:
   - `python upload_finetune.py --train ./ft_train_policy_only.jsonl --eval ./ft_eval_messages_clean.jsonl --dry-run`
4. Start fine-tune job:
   - `python upload_finetune.py --train ./ft_train_policy_only.jsonl --eval ./ft_eval_messages_clean.jsonl --model gpt-4.1-mini --suffix purdue-budget-v1`
5. Run chat API locally:
   - `uvicorn chat_api:app --reload --port 8000`

## Example chat request
```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How much did I spend on food this month?",
    "sanitized_context": [
      {"type":"expense","amount":18.75,"category":"food","date":"2026-04-02"},
      {"type":"expense","amount":9.40,"category":"food","date":"2026-04-06"}
    ],
    "model": "gpt-5.4-mini"
  }'
```

## Security note
Never pass raw uploads or real PII to the model. Send only sanitized records.

## One-command run
- Smoke test (starts API, runs allowed + blocked prompt checks, then stops):
  - `./run.sh`
- Serve API continuously:
  - `./run.sh serve`

## Web UI
1. Run API:
   - `PORT=8010 ./run.sh serve`
2. Open browser:
   - `http://127.0.0.1:8010/`
3. Enter `user_id` + question, then click **Ask Chat**.

## Upload and Redact
- Supported upload types: `.txt`, `.csv`, `.json`, `.md`, `.log`, `.pdf`, image receipts (`.png`, `.jpg`, `.jpeg`, `.webp`)
- Endpoint: `POST /upload-document` (multipart form)
- Form fields:
  - `file` (required)
  - `user_id` (optional, default `USER_001`)
  - `persist` (`true/false`) to save extracted sanitized records into `sanitized_ledger`

For image OCR on macOS:
- `brew install tesseract`

Example:
```bash
curl -X POST http://127.0.0.1:8010/upload-document \
  -F "file=@./sample_receipt.txt" \
  -F "user_id=USER_001" \
  -F "persist=true"
```

## New Budget App Features
- Purdue-themed UI with dark mode + light mode toggle
- Document Redaction tab now supports per-row actions:
  - `Add Personal`
  - `Add Owe` (asks who you owe, and whether already paid)
- Upload preview persistence:
  - Redacted/unredacted preview + extracted rows are saved per user across refresh/tab switches
- Personal Budget tab:
  - Monthly budget target + progress bar
  - Personal totals, monthly spend, owe totals, budget health score
  - Category relabeling per personal expense
  - Personal transaction remove button
  - Owe tracker with `Already Paid Back` button
- Global reset:
  - `Reset Everything` button uses double-confirm and deletes all records for the current user
