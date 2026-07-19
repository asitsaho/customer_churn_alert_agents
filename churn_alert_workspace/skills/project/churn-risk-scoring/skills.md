---
name: churn-risk-scoring
description: How to combine inactivity, support ticket, and review signals into a single per-user churn risk score and alert tier.
license: MIT
---

# Churn Risk Scoring Skill

## When to use
Use this skill after `inactive_users_subagent`, `support_ticket_subagent`, and
`review_subagent` have all returned results for the same set of `user_id`s.
It tells you how to merge their outputs into one churn risk verdict per user.

## Inputs
- `inactive_users.json` -- InactiveUsersResponse (days_inactive, inactivity_signal)
- `support_tickets.json` -- SupportTicketsResponse (churn_linked_ticket_flag, unresolved_ticket_count)
- `reviews.json` -- ReviewsResponse (signal_type: NO_REVIEW / LOW_RATING / MIXED / NONE)

## Scoring rubric
Start every user at 0 points, then add:

| Condition | Points |
|---|---|
| `inactivity_signal == "BOTH"` | +40 |
| `inactivity_signal in ("NO_LOGIN","NO_ORDER")` | +20 |
| `days_inactive >= 30` | +10 (additional, on top of the above) |
| `churn_linked_ticket_flag == true` | +25 |
| `unresolved_ticket_count >= 1` (and flag above is false) | +10 |
| `review signal_type == "LOW_RATING"` | +20 |
| `review signal_type == "NO_REVIEW"` | +10 |
| `review signal_type == "MIXED"` | +5 |

Cap the total at 100.

## Alert tiers
- **CRITICAL** (score >= 70): inactive AND has an unresolved/negative support
  experience AND/OR bad reviews -- likely churned because of a bad experience.
  These are the users to prioritize for a win-back outreach.
- **HIGH** (50-69): inactive with at least one negative signal (ticket or review).
- **MEDIUM** (30-49): inactive but no clear negative signal found -- may just be
  low-engagement, not necessarily an unhappy customer.
- **LOW** (< 30): weak or no churn signal; do not alert.

## Output format
For each user produce a row with: `user_id, full_name, email, days_inactive,
inactivity_signal, unresolved_ticket_count, churn_linked_ticket_flag,
review_signal_type, worst_review_text, churn_score, alert_tier, primary_reason`.

`primary_reason` is a one-sentence, human-readable explanation combining the
highest-weighted contributing signals (e.g. "Inactive 42 days after an
unresolved URGENT delivery-delay ticket and a 1-star review").

Sort the final report by `churn_score` descending. Only include users with
`alert_tier` in (CRITICAL, HIGH, MEDIUM) in the alert report; still keep LOW
users in the raw JSON for completeness.
