We are building this feature using a 4-agent workflow.

Use these agents in order:
1. planner for scope, assumptions, acceptance criteria, and task breakdown.
2. architect for technical design, file-level plan, and test strategy.
3. builder for implementation only after plan/design are approved.
4. reviewer in a fresh context to validate the final result against the plan.

Rules:
- Do not skip planning for non-trivial work.
- Do not let builder expand scope beyond the approved plan.
- Reviewer must not modify code; reviewer only returns findings.
- If requirements are ambiguous, planner must ask clarifying questions first.
- Keep implementations minimal and iterative.
- Prefer small diffs and verifiable steps.

For every task, return:
- Current phase
- Agent being used
- Short rationale
- Result / next action