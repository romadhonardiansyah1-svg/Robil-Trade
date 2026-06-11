# ADR-010 — Model LLM default

**Status:** ACCEPTED (2026-06-11) — verifikasi ID model terkini saat implementasi P2

## Decision
- Analyst: `gemini/gemini-2.5-flash` (murah, cepat, structured output baik)
- Critic: `anthropic/claude-sonnet-4-6` (penalaran kritis)
- Backup: `openai/gpt-4o`
- Lokal opsional (P3): Ollama
Aplikasi hanya tahu alias LiteLLM.
