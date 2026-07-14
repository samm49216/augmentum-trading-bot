"""STRATEGY STUB — ships intentionally empty.

WHAT to trade is the ACCOUNT OWNER'S decision, not this package's. Implement
`decide()` to return trade decisions based on your own rules/signals. The executor
enforces safety around whatever this returns; it does not create trades itself.

Each decision is a tuple:
    (OrderIntent, build_order_request)
where build_order_request is a zero-arg callable returning the SDK OrderRequest
used only if/when the order is placed live.
"""
from typing import Callable, List, Tuple

from guardrails import OrderIntent

Decision = Tuple[OrderIntent, Callable[[], object]]


class Strategy:
    def decide(self, market) -> List[Decision]:
        # No trades by default. Implement your own logic here.
        return []
