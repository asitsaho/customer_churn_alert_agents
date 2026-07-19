"""
db_tools.py
===========
Shared SQL access layer for the Churn Alert Agent.

Wraps the qcommerce.db SQLite database (produced by quick_commerce_sim.py)
in a LangChain SQLDatabaseToolkit so every sub-agent gets the standard
`sql_db_list_tables`, `sql_db_schema`, `sql_db_query`, `sql_db_query_checker`
tools -- i.e. "LLM based agent using create_agent with SQL DatabaseToolkit"
as described in the design.

Schema reference (see quick_commerce_sim.py for full DDL):
    users(user_id, full_name, email, phone_number, city, state,
          account_status, created_at, updated_at, ...)
    orders(order_id, user_id, order_status, total_amount, placed_at, delivered_at)
    auth_audit_log(log_id, user_id, event_type, event_status, event_timestamp, ...)
    support_tickets(ticket_id, user_id, order_id, subject, category, priority,
                     status, resolution_notes, created_at, resolved_at)
    reviews(review_id, user_id, product_id, order_id, rating, review_title,
            review_text, created_at)
"""

import os

from dotenv import load_dotenv
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase

from utils import get_model

# Loaded here (not just in the entrypoint) so this module also works when
# imported on its own, e.g. from subagents.py at module load time.
load_dotenv()

# Path to the DB built by quick_commerce_sim.py (db/qcommerce.db in the
# project shown in the screenshot). Override with QCOMMERCE_DB_PATH env var.
DB_PATH = os.environ.get("QCOMMERCE_DB_PATH", "./db/qcommerce.db")


def get_sql_database() -> SQLDatabase:
    """Open the qcommerce SQLite DB as a LangChain SQLDatabase."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"qcommerce.db not found at '{DB_PATH}'. Run "
            f"`python quick_commerce_sim.py init --db {DB_PATH}` first, or "
            f"set QCOMMERCE_DB_PATH to the correct location."
        )
    return SQLDatabase.from_uri(f"sqlite:///{DB_PATH}")


def get_sql_toolkit() -> SQLDatabaseToolkit:
    """Return a SQLDatabaseToolkit bound to the qcommerce DB and the shared LLM."""
    return SQLDatabaseToolkit(db=get_sql_database(), llm=get_model())


def get_sql_tools():
    """Convenience helper: the list of tools to hand to a sub-agent."""
    return get_sql_toolkit().get_tools()
