"""
Calculator Tool for warehouse automation calculations.

This tool performs mathematical calculations commonly needed
in warehouse automation design and decision-making.
"""

import math
import re
from typing import Union

from langchain_core.tools import tool

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Safe math functions available for evaluation
SAFE_MATH_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}


def safe_eval(expression: str) -> Union[float, int, str]:
    """
    Safely evaluate a mathematical expression.

    Args:
        expression: Mathematical expression to evaluate

    Returns:
        Result of the calculation or error message
    """
    # Remove whitespace
    expression = expression.strip()

    # Basic validation - only allow numbers, operators, parentheses, and function names
    allowed_pattern = r'^[\d\s\+\-\*\/\(\)\.\,a-z_]+$'
    if not re.match(allowed_pattern, expression, re.IGNORECASE):
        return "Invalid characters in expression"

    try:
        # Evaluate with restricted globals
        result = eval(expression, {"__builtins__": {}}, SAFE_MATH_FUNCTIONS)
        return result
    except ZeroDivisionError:
        return "Error: Division by zero"
    except Exception as e:
        return f"Calculation error: {str(e)}"


@tool
def calculate(expression: str) -> str:
    """
    Perform mathematical calculations for warehouse automation design.

    Use this tool when you need to calculate:
    - Transit times (distance / speed)
    - Throughput rates (items per hour)
    - Storage capacity (dimensions * quantity)
    - Conveyor speeds and lengths
    - Equipment specifications
    - Unit conversions

    Examples:
    - "100 / 2" -> 50 (transit time: 100m at 2m/s)
    - "3600 / 5" -> 720 (items per hour with 5-second cycle)
    - "sqrt(100)" -> 10
    - "10 * 20 * 3" -> 600 (volume calculation)

    Args:
        expression: Mathematical expression to evaluate.
                   Supports: +, -, *, /, (), sqrt, pow, ceil, floor, round, min, max, pi, e

    Returns:
        The calculated result as a string
    """
    logger.info(f"Calculator evaluating: {expression}")

    result = safe_eval(expression)

    if isinstance(result, (int, float)):
        # Format nicely
        if isinstance(result, float) and result.is_integer():
            formatted = str(int(result))
        elif isinstance(result, float):
            formatted = f"{result:.4f}".rstrip('0').rstrip('.')
        else:
            formatted = str(result)

        logger.info(f"Calculation result: {formatted}")
        return f"Result: {formatted}"
    else:
        # Error message
        logger.warning(f"Calculation failed: {result}")
        return str(result)


# Warehouse-specific calculation helpers
def conveyor_transit_time(length_meters: float, speed_mps: float) -> float:
    """Calculate conveyor transit time in seconds."""
    return length_meters / speed_mps


def throughput_per_hour(cycle_time_seconds: float) -> float:
    """Calculate throughput in items per hour given cycle time."""
    return 3600 / cycle_time_seconds


def storage_capacity(length: float, width: float, height: float, item_volume: float) -> int:
    """Calculate how many items fit in a storage area."""
    total_volume = length * width * height
    return int(total_volume / item_volume)
