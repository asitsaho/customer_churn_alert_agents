# Customer Churn Alert Agent

Deep-agent system for `qcommerce.db` (the DB produced by `quick_commerce_sim.py`),
built with the same pattern as `question_paper_agent_response_format.py`:
a top-level deep agent with planning + a filesystem workspace + a project
skill, delegating to three SQL-tool-using sub-agents.

## Files
```
customer_churn_alert_agent/
├── db_tools.py           # SQLDatabase + SQLDatabaseToolkit setup (shared)
├── subagents.py          # 3 sub-agents + their Pydantic response formats
├── churn_alert_agent.py  # main deep agent (planning, todos, entrypoint)
├── README.md
└── churn_alert_workspace/
    └── skills/
        └── project/
            └── churn-risk-scoring/
                └── SKILL.md   # scoring rubric the agent reads before scoring
```
This project assumes it sits alongside your existing `db/`, `utils.py`
(`get_model()`), and `.env` -- same layout as in your screenshot. Drop these
files into your project root next to `main.py` / `pyproject.toml`. Adjust the
imports if `utils.py` lives elsewhere.

## The 3 sub-agents
1. **inactive_users_subagent** -- queries `users` + `orders` + `auth_audit_log`
   for customers with no login and/or no order in the last N days.
2. **support_ticket_subagent** -- given the user_ids from step 1, pulls every
   `support_tickets` row per user and flags unresolved/urgent/negative-category
   tickets as high churn relevance.
3. **review_subagent** -- given the same user_ids, checks `reviews` vs.
   delivered `orders` to flag users who left no review, or whose ratings/text
   skew negative.

Each is an independent LLM agent (deepagents wires each dict up via
`create_agent` internally) with its own `SQLDatabaseToolkit` tools and a
structured `response_format`, exactly like the physics/mathematics/chemistry
sub-agents in your sample file -- it writes and runs its own SQL against the
live schema rather than using hardcoded queries.

## The main deep agent
`churn_alert_agent.py` creates the top-level agent with:
- `subagents=[...]` -- the three above, called via the built-in `task` tool
- `skills=["/skills/project/"]` -- loads `churn-risk-scoring/SKILL.md`, which
  defines the point-based scoring rubric and CRITICAL/HIGH/MEDIUM/LOW alert
  tiers (edit this file to tune thresholds without touching any code)
- `backend=FilesystemBackend(root_dir="./churn_alert_workspace", virtual_mode=True)`
  -- todos and all intermediate/final files are written under
  `churn_alert_workspace/`

Its system prompt instructs it to:
1. Write a plan to `/todos.md`
2. Call `inactive_users_subagent` for the requested lookback window
3. Call `support_ticket_subagent` and `review_subagent` with those user_ids
4. Apply the `churn-risk-scoring` skill to merge the three signals
5. Save `/churn_risk_scores.json` (all users) and `/churn_alert_report.md`
   (CRITICAL/HIGH/MEDIUM users, sorted, with a one-line reason each)

## Run
```bash
# from your project root, with .venv active and QCOMMERCE_DB_PATH / .env set
python quick_commerce_sim.py init --db ./db/qcommerce.db   # if not already built
python churn_alert_agent.py --days 14
```
Output lands in `churn_alert_workspace/`:
`todos.md`, `inactive_users.json`, `support_tickets.json`, `reviews.json`,
`churn_risk_scores.json`, `churn_alert_report.md`.

## Notes / assumptions
- `db_tools.py` reads the DB path from `QCOMMERCE_DB_PATH` env var, defaulting
  to `./db/qcommerce.db` (matches your screenshot's `db/` folder).
- `utils.get_model()` and `.env` are assumed to already exist in your project,
  same as in `question_paper_agent_response_format.py` -- not recreated here.
- Verified end-to-end against `deepagents==0.6.12` and `langchain-community`:
  the graph builds, the SQL toolkit connects, and the skill loads correctly.
  Full runs still need real model + DB credentials, which weren't available
  in this environment.
