"""
churn_alert_agent.py
=====================
Main "deep agent" for the Customer Churn Alert system. Mirrors the design of
question_paper_agent_response_format.py:

    - 3 sub-agents (SQL-tool-using LLM agents), each with a structured
      response_format
    - a top-level deep agent with planning (todos), a filesystem workspace,
      and a project skill (SKILL.md) that tells it HOW to decide churn risk
      from the sub-agents' data
    - subagents are called via the built-in `task` tool; delegation, not
      hardcoded control flow

Run:
    python churn_alert_agent.py                     # default: last 14 days
    python churn_alert_agent.py --days 21            # custom lookback window
"""

import argparse

from dotenv import load_dotenv

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

from utils import get_model
from subagents import (
    inactive_users_subagent,
    support_ticket_subagent,
    review_subagent,
)

load_dotenv()

model = get_model()

# Filesystem workspace for this agent -- todos, intermediate JSON, and the
# final churn alert report all live here. `virtual_mode=True` means the
# agent addresses files with virtual paths like "/todos.md" and
# "/skills/project/..." which map onto this root_dir on disk.
backend = FilesystemBackend(
    root_dir="./churn_alert_workspace",
    virtual_mode=True,
)

SYSTEM_PROMPT = """
You are the Customer Churn Alert Agent for a quick-commerce platform.

Goal: identify customers who have gone inactive in the last N days (N is
given in the user's request, default 14) and figure out WHY -- correlating
their inactivity with support ticket history and review/rating behavior --
then produce a prioritized churn alert report.

You have three sub-agents available via the `task` tool:
  - inactive_users_subagent: returns users inactive for the last N days
  - support_ticket_subagent: given user_ids, returns their support ticket
    history and a churn_linked_ticket_flag per user
  - review_subagent: given user_ids, returns each user's review signal
    (no review left, low ratings, etc.)

You also have a project skill loaded ("churn-risk-scoring") that defines the
exact scoring rubric and alert tiers -- READ IT FIRST via `read_file` on
`/skills/project/churn-risk-scoring/SKILL.md` (or rely on it being injected
into your context) before you compute any scores. Follow it exactly; do not
invent your own scoring logic.

Workflow (write this as your plan to /todos.md before starting, and keep it
updated as you go):
  1. Call inactive_users_subagent with the requested lookback window N.
     Save its raw JSON response to /inactive_users.json.
  2. Take the list of user_ids from step 1. Call support_ticket_subagent
     with that full list of user_ids in one call. Save the response to
     /support_tickets.json.
  3. Call review_subagent with the same list of user_ids. Save the response
     to /reviews.json.
  4. Re-read the churn-risk-scoring skill and apply its rubric to merge the
     three JSON files into one row per user with a churn_score and
     alert_tier.
  5. Write the full merged data (all tiers, sorted by churn_score desc) to
     /churn_risk_scores.json.
  6. Write a human-readable alert report -- CRITICAL/HIGH/MEDIUM users only,
     with their primary_reason -- to /churn_alert_report.md in clean
     markdown (a summary table plus a short section per CRITICAL user).
  7. Confirm all four files exist (/todos.md, /churn_risk_scores.json,
     /churn_alert_report.md, plus the three raw sub-agent JSON files) and
     report back a short summary: total inactive users, counts per alert
     tier, and the top 5 highest-risk users.

Do not skip calling a sub-agent to save time -- every user in the final
report must have been through all three checks. If a sub-agent call fails,
retry once with a smaller batch of user_ids before giving up on that batch.
"""

agent = create_deep_agent(
    model=model,
    backend=backend,
    subagents=[
        inactive_users_subagent,
        support_ticket_subagent,
        review_subagent,
    ],
    skills=["/skills/project/"],
    system_prompt=SYSTEM_PROMPT,
)


def run(lookback_days: int = 14):
    result = agent.invoke(
        input={
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Identify customers inactive in the last {lookback_days} days, "
                        "correlate with their support ticket and review history, and "
                        "produce a prioritized churn alert report."
                    ),
                }
            ]
        }
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Customer Churn Alert deep agent.")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Inactivity lookback window in days (default: 14)",
    )
    args = parser.parse_args()

    result = run(lookback_days=args.days)
    print(result["messages"][-1].content)
