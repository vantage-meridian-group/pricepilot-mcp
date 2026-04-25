"""Pricing classification thresholds used by the MCP server."""

# r_mid = price / category_median
# Below 0.90 = Value, 0.90–1.10 = Parity, above 1.10 = Premium
PARITY_LOWER_BOUND = 0.90
PARITY_UPPER_BOUND = 1.10

# Trend computation
TREND_WINDOW_OBSERVATIONS = 30
TREND_THRESHOLD = 0.05
