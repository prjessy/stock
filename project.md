Build a local-first stock alert assistant.

Project goals:
- Monitor Samsung Electronics price moves.
- Send Telegram alerts for +3%, +6%, +9%, -3%, -6%, -9%.
- Add US futures morning briefing.
- Prepare for later Hermes integration and VPS migration.

Workflow:
1. planner defines scope, assumptions, and acceptance criteria.
2. architect defines modules: price fetcher, threshold engine, dedupe state, telegram notifier, report generator.
3. builder implements one small vertical slice first:
   - Samsung +3% / -3% alerts only
   - local run
   - duplicate alert prevention
4. reviewer validates correctness and missing edge cases.

Constraints:
- Start local-first, no VPS yet.
- Keep secrets in env files, never hardcode tokens.
- Use minimal dependencies.
- Optimize for maintainability, not novelty.
- Prepare structure so Hermes can be added later without major rewrites.