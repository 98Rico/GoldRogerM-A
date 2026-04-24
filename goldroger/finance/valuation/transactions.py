from dataclasses import dataclass


@dataclass
class TransactionInput:
    revenue: float
    multiple: float


@dataclass
class TransactionOutput:
    implied_value: float


def compute_transaction(inp: TransactionInput) -> TransactionOutput:
    return TransactionOutput(
        implied_value=inp.revenue * inp.multiple
    )