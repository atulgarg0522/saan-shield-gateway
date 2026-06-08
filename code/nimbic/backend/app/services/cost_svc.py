from decimal import Decimal
import structlog

logger = structlog.get_logger()

# Model rate configurations mapping USD cost per 1,000 tokens
MODEL_COST_TABLE = {
    "gpt-4o": {
        "input": Decimal("0.005"),
        "output": Decimal("0.015")
    },
    "gpt-4o-mini": {
        "input": Decimal("0.000150"),
        "output": Decimal("0.000600")
    },
    "gpt-4-turbo": {
        "input": Decimal("0.010"),
        "output": Decimal("0.030")
    },
    "gpt-3.5-turbo": {
        "input": Decimal("0.000500"),
        "output": Decimal("0.001500")
    },
    "claude-opus-4-6": {
        "input": Decimal("0.015"),
        "output": Decimal("0.075")
    },
    "claude-sonnet-4-6": {
        "input": Decimal("0.003"),
        "output": Decimal("0.015")
    },
    "claude-haiku-4-5": {
        "input": Decimal("0.00025"),
        "output": Decimal("0.00125")
    },
    "gemini-3-pro": {
        "input": Decimal("0.007"),
        "output": Decimal("0.021")
    },
    "gemini-3-5-flash": {
        "input": Decimal("0.000375"),
        "output": Decimal("0.0015")
    },
}


async def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """
    Calculates exact query billing cost based on input (prompt) and output (completion) tokens.
    Guarantees floating point precision safety using Python Decimals.
    """
    rates = MODEL_COST_TABLE.get(model)
    
    if not rates:
        await logger.awarning(
            "Target model not registered in cost table. Defaulting cost calculation to $0.000000",
            model=model
        )
        return Decimal("0.000000")

    prompt_cost = (Decimal(prompt_tokens) * rates["input"]) / Decimal("1000")
    completion_cost = (Decimal(completion_tokens) * rates["output"]) / Decimal("1000")
    
    total_cost = prompt_cost + completion_cost
    
    # Quantize to 6 decimal places to match decimal column scale in requests_log table
    return total_cost.quantize(Decimal("1.000000"))
