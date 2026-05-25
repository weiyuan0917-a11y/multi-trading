"""将策略意图映射为可提交到 LongPort 的多腿结构（合约代码需由期权链解析）。"""
from __future__ import annotations

from typing import Any

from .state import TradeIntent


def intent_to_legs_template(
    intent: TradeIntent,
    *,
    limit_price_per_share: float | None = None,
) -> dict[str, Any]:
    """
    返回与 OptionOrderBody 兼容的 legs 模板；symbol 需替换为真实 OPRA 代码。
    """
    px = float(limit_price_per_share) if limit_price_per_share is not None else 0.0
    return {
        "legs": [
            {
                "symbol": "REPLACE_WITH_OPRA_SYMBOL",
                "side": "buy",
                "contracts": int(intent.contracts),
                "price": max(0.0, px),
            }
        ],
        "note": "请先 GET /options/chain 按 underlying + expiry + strike + call/put 解析 OPRA 代码后替换 symbol。",
        "meta": {
            "underlying": intent.underlying,
            "strike": intent.strike,
            "right": intent.right,
            "reason": intent.reason,
        },
    }


def intent_to_legs_resolved(
    intent: TradeIntent,
    op_symbol: str,
    *,
    limit_price_per_share: float | None = None,
) -> dict[str, Any]:
    """在已解析 OPRA 后生成可提交的多腿结构（与 OptionOrderBody 兼容）。"""
    px = float(limit_price_per_share) if limit_price_per_share is not None else 0.0
    sym = str(op_symbol or "").strip().upper()
    if not sym:
        raise ValueError("op_symbol 不能为空")
    return {
        "legs": [
            {
                "symbol": sym,
                "side": "buy",
                "contracts": int(intent.contracts),
                "price": max(0.0, px),
            }
        ],
        "meta": {
            "underlying": intent.underlying,
            "strike": intent.strike,
            "right": intent.right,
            "reason": intent.reason,
        },
    }
