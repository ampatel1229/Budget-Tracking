# 🤖 Budget Assistant Starter

Budget Assistant Starter is a fine-tuning and guardrails starter project for a budget assistant with PII safety controls. The project explores how an AI assistant can answer budget-related questions while also protecting sensitive information and following safer input-handling rules.

This project is not just a basic chatbot. I built it around the idea that LLM applications need structure, validation, and guardrails before they can be trusted with real user inputs. The app includes prompt blocking, document upload and redaction, a chat API, and scripts for working with fine-tuning data.

A major focus of this project was learning how to balance usefulness with safety. I wanted the assistant to be able to answer questions, but only after inputs are checked, sensitive information is handled carefully, and the system stays within a controlled context.

## 📦 Technologies

- Python
- FastAPI
- OpenAI API
- Uvicorn
- pydantic
- psycopg
- Regex-based filtering
- OCR tooling

## ✨ Features

- Fine-tuning dataset files
- Upload and validation scripts
- Blocked-prompt guardrails
- Chat API
- Document upload and redaction flow
- Smoke-test script
- PII safety controls
- Controlled budget Q&A workflow

## 🧠 The Process

I started by thinking through what could go wrong in a budget assistant. Since financial and personal information can be sensitive, I focused first on input checks, redaction, and blocked-prompt guardrails.

After that, I created scripts for preparing and validating fine-tuning data. I also built an API layer using FastAPI so the assistant could be accessed through a structured backend instead of only through loose scripts.

The biggest challenge was designing the workflow so that user inputs are checked before they reach the model. This helped me think more carefully about safer LLM systems and the layers that should exist around the AI itself.

## 📚 What I Learned

This project taught me that building LLM apps is not only about calling an API. A useful AI system also needs safety checks, redaction, testing, controlled context, and clear rules for what should happen when an input is unsafe or out of scope.

I also learned more about backend API design and how to organize a project where the AI layer, validation layer, and application layer all work together.

## 🔧 How It Can Be Improved

- Add a stronger evaluation harness
- Add policy versioning
- Add role-based authentication
- Add production logging and monitoring
- Improve redaction accuracy
- Add more complete test coverage
- Add clearer admin controls for reviewing blocked prompts

## 🚀 Running the Project

```bash
git clone <repo-url>
cd "LLM 1"

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

export OPENAI_API_KEY=your_key

./run.sh serve

# Or run the smoke test
./run.sh
```
