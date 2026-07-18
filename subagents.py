"""
subagents.py
============
The three churn-signal sub-agents, defined the same way as the
physics/mathematics/chemistry sub-agents in
question_paper_agent_response_format.py: a dict with name, description,
system_prompt, tools, and a structured response_format.

Each sub-agent is itself an LLM-based agent (deepagents wires it up via
`create_agent`) that reasons over the qcommerce.db schema using the
SQLDatabaseToolkit tools from db_tools.py -- it writes and runs its own SQL,
it is not hardcoded queries.

    1. inactive_users_subagent   -> users inactive in the last N days
    2. support_ticket_subagent   -> support tickets raised by a given user
    3. review_subagent           -> worst / missing reviews for a given user
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from db_tools import get_sql_tools

# Instantiated once and shared across all three sub-agents (each gets its own
# SQLDatabaseToolkit tool instances, but wired to the same underlying DB).
SQL_TOOLS = get_sql_tools()

# --------------------------------------------------------------------------- #
# 1. Inactive users sub-agent
# --------------------------------------------------------------------------- #


class InactiveUser(BaseModel):
    user_id: int = Field(description="users.user_id")
    full_name: str = Field(description="users.full_name")
    email: str = Field(description="users.email")
    city: Optional[str] = Field(default=None, description="users.city")
    account_status: str = Field(description="users.account_status")
    last_login_at: Optional[str] = Field(
        default=None, description="Most recent successful auth_audit_log LOGIN event_timestamp"
    )
    last_order_at: Optional[str] = Field(
        default=None, description="Most recent orders.placed_at"
    )
    days_inactive: int = Field(
        description="Days since the more recent of last_login_at / last_order_at"
    )
    inactivity_signal: Literal["NO_LOGIN", "NO_ORDER", "BOTH"] = Field(
        description="Which activity stream has been silent for the lookback window"
    )


class InactiveUsersResponse(BaseModel):
    lookback_days: int = Field(description="N -- the inactivity window that was evaluated")
    total_inactive: int = Field(description="Count of users returned")
    inactive_users: list[InactiveUser] = Field(
        min_length=0, description="Users with no login AND/OR no order in the last N days"
    )


inactive_users_subagent = {
    "name": "inactive_users_subagent",
    "description": (
        "Finds customers who have been inactive on the platform for the last N days. "
        "Use this first to build the candidate list of at-risk users before checking "
        "their support tickets or reviews."
    ),
    "system_prompt": """
You are the Inactive Users Detection Agent for a quick-commerce churn alert system.

You have SQL tools (sql_db_list_tables, sql_db_schema, sql_db_query, sql_db_query_checker)
against a SQLite database with these relevant tables:
  - users(user_id, full_name, email, city, state, account_status, created_at, updated_at)
  - orders(user_id, order_status, placed_at, delivered_at)
  - auth_audit_log(user_id, event_type, event_status, event_timestamp)

Your task, given an inactivity window of N days (default 14 if not specified):
1. Always call sql_db_list_tables and sql_db_schema first to confirm actual column
   names before writing queries -- do not assume.
2. For every CUSTOMER user_type user whose account_status is not 'DELETED', compute:
   - last_login_at: MAX(event_timestamp) from auth_audit_log where event_type='LOGIN'
     and event_status='SUCCESS'
   - last_order_at: MAX(placed_at) from orders (any order_status)
3. A user is "inactive" if neither of those timestamps falls within the last N days
   (treat a NULL timestamp, i.e. never logged in / never ordered, as inactive too).
4. days_inactive = days between today and the MORE RECENT of last_login_at / last_order_at
   (if both are NULL, use days since users.created_at and note it in the value).
5. Set inactivity_signal:
   - "NO_LOGIN" if only login activity is stale but orders are recent (rare)
   - "NO_ORDER" if only order activity is stale but logins are recent
   - "BOTH" if neither login nor order activity is within N days (most common)
6. Run the queries yourself with sql_db_query -- do not fabricate data.
7. Return every inactive user found; do not cap or sample the list.

Respond only with the structured InactiveUsersResponse.
""",
    "tools": SQL_TOOLS,
    "response_format": InactiveUsersResponse,
}


# --------------------------------------------------------------------------- #
# 2. Support ticket sub-agent
# --------------------------------------------------------------------------- #


class SupportTicketSummary(BaseModel):
    ticket_id: int
    subject: str
    category: str
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
    status: Literal["OPEN", "IN_PROGRESS", "WAITING_ON_CUSTOMER", "RESOLVED", "CLOSED"]
    created_at: str
    resolved_at: Optional[str] = None
    churn_relevance: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="HIGH: unresolved/urgent/high-priority ticket close to the user's "
        "last activity. MEDIUM: resolved but was HIGH/URGENT, or unresolved MEDIUM. "
        "LOW: minor/closed/resolved-quickly."
    )
    rationale: str = Field(description="One-line reason for the churn_relevance rating")


class UserTicketReport(BaseModel):
    user_id: int
    full_name: str
    has_tickets: bool
    unresolved_ticket_count: int
    churn_linked_ticket_flag: bool = Field(
        description="True if at least one ticket is rated HIGH churn_relevance"
    )
    tickets: list[SupportTicketSummary]


class SupportTicketsResponse(BaseModel):
    user_reports: list[UserTicketReport]


support_ticket_subagent = {
    "name": "support_ticket_subagent",
    "description": (
        "For a given list of user_ids, fetches every support ticket they have raised "
        "and flags whether any of those tickets plausibly contributed to the user "
        "going inactive (unresolved, high priority/urgent, or delivery/payment/quality "
        "complaints)."
    ),
    "system_prompt": """
You are the Support Ticket Risk Agent for a quick-commerce churn alert system.

You have SQL tools against a SQLite database with:
  - support_tickets(ticket_id, user_id, order_id, subject, description, category,
                     priority, status, resolution_notes, created_at, updated_at, resolved_at)
  - users(user_id, full_name)

Your task, given a list of user_ids from the caller:
1. Call sql_db_list_tables / sql_db_schema first to confirm columns.
2. For each user_id, query ALL of their rows in support_tickets ordered by created_at DESC.
3. If a user has zero tickets, still return an entry for them with has_tickets=false and
   an empty tickets list -- do not skip them.
4. For each ticket assign churn_relevance:
   - HIGH: status in ('OPEN','IN_PROGRESS','WAITING_ON_CUSTOMER') AND priority in
     ('HIGH','URGENT'); OR category in ('DELIVERY_DELAY','PRODUCT_QUALITY','PAYMENT',
     'REFUND') and status is not RESOLVED/CLOSED.
   - MEDIUM: resolved/closed but priority was HIGH/URGENT, or unresolved MEDIUM priority.
   - LOW: everything else (resolved quickly, LOW priority, OTHER category).
5. unresolved_ticket_count = count of tickets NOT in ('RESOLVED','CLOSED').
6. churn_linked_ticket_flag = true if ANY ticket has churn_relevance == "HIGH".
7. Base every rationale on the actual row data you queried, not assumptions.

Respond only with the structured SupportTicketsResponse.
""",
    "tools": SQL_TOOLS,
    "response_format": SupportTicketsResponse,
}


# --------------------------------------------------------------------------- #
# 3. Review sub-agent
# --------------------------------------------------------------------------- #


class ReviewSignal(BaseModel):
    user_id: int
    full_name: str
    signal_type: Literal["NO_REVIEW", "LOW_RATING", "MIXED", "NONE"] = Field(
        description="NO_REVIEW: has delivered orders but left zero reviews. "
        "LOW_RATING: average rating <= 2 or has at least one 1-star review. "
        "MIXED: some reviews present but neither NO_REVIEW nor LOW_RATING clearly applies "
        "and average rating is borderline (2 < avg <= 3). "
        "NONE: healthy review history, no churn signal here."
    )
    review_count: int
    average_rating: Optional[float] = None
    worst_rating: Optional[int] = None
    worst_review_text: Optional[str] = None
    delivered_orders_without_review: int = Field(
        description="Count of DELIVERED orders that have no matching row in reviews"
    )


class ReviewsResponse(BaseModel):
    review_signals: list[ReviewSignal]


review_subagent = {
    "name": "review_subagent",
    "description": (
        "For a given list of user_ids, checks their review history: finds users who "
        "left no review despite having delivered orders, and users whose reviews "
        "skew negative (low ratings / bad review text)."
    ),
    "system_prompt": """
You are the Review Signal Agent for a quick-commerce churn alert system.

You have SQL tools against a SQLite database with:
  - reviews(review_id, user_id, product_id, order_id, rating, review_title,
            review_text, created_at)
  - orders(order_id, user_id, order_status, placed_at)
  - users(user_id, full_name)

Your task, given a list of user_ids from the caller:
1. Call sql_db_list_tables / sql_db_schema first to confirm columns.
2. For each user_id, query their rows in reviews (rating, review_title, review_text).
3. Also count their DELIVERED orders (orders.order_status = 'DELIVERED') and how many
   of those order_ids do NOT appear in reviews.order_id for that user -- this is
   delivered_orders_without_review.
4. Compute average_rating and worst_rating (MIN(rating)) from their reviews, if any.
5. Assign signal_type:
   - "NO_REVIEW": delivered_orders_without_review > 0 AND review_count == 0
   - "LOW_RATING": average_rating <= 2, OR worst_rating == 1
   - "MIXED": review_count > 0 and 2 < average_rating <= 3
   - "NONE": otherwise (healthy reviews, or no delivered orders to review at all)
6. worst_review_text should be the review_text of the row with the lowest rating,
   when review_count > 0, else null.
7. Include one entry per user_id even if they have no reviews and no delivered orders
   (signal_type "NONE" in that case).

Respond only with the structured ReviewsResponse.
""",
    "tools": SQL_TOOLS,
    "response_format": ReviewsResponse,
}
