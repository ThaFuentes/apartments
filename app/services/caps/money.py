"""Gas, food, miles, and the odometer."""
from app.services.caps.schema import Capability

CAPS = [
    Capability(
        tool="log_expense",
        label="file gas, food, or a receipt",
        says=(
            "gas 42.18 at shell",
            "lunch was 12.50",
            "file this receipt",
        ),
        howto="Say gas, food, or other with the amount. It always waits for your yes.",
    ),
    Capability(
        tool="log_miles",
        label="log miles traveled",
        says=(
            "drove 30 miles",
            "log 12 miles for the parts run",
        ),
        howto="Say drove or log miles with the number.",
    ),
    Capability(
        tool="log_odometer",
        label="save an odometer reading",
        says=(
            "odometer 120440",
        ),
        howto="Say odometer with the number on the dash. Miles since the last reading get counted.",
    ),
]
