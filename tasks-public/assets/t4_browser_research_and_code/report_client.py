API_PATH = "/v1/reports"
REQUIRED_HEADERS = ["Authorization"]

# Rate-limit + payload guards the agent must set to match the published
# reporting API contract. Starter values are wrong on purpose.
RATE_LIMIT_PER_MINUTE = None
MAX_PAYLOAD_BYTES = None
