# Customer Churn Alert Agent

An agent that monitors customer activity and triggers churn alerts based on behavioral signals.

## Generating Data

Use `quick_commerce_sim.py` to seed and simulate the database.

```bash
# Create DB + backfill 120 days of history (default path: ./qcommerce.db)
python quick_commerce_sim.py init --db ./qcommerce.db --days 120

# Simulate live traffic on the existing DB (30 ticks, 2s apart)
python quick_commerce_sim.py live --db ./qcommerce.db --interval 2 --ticks 30

# Do both
python quick_commerce_sim.py all --db ./qcommerce.db --days 120
```
