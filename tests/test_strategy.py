from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from almanak.framework.teardown import TeardownMode
from strategy import BaseWethUsdcMomentumStrategy


def _make_strategy(**overrides):
    config = {
        "chain": "base",
        "protocol": "uniswap_v3",
        "base_token": "WETH",
        "quote_token": "USDC",
        "signal_pool_address": "0xd0b53d9277642d899df5c87a3966a349a798f224",
        "timeframe": "15m",
        "rsi_period": 14,
        "lower_band": 40,
        "upper_band": 60,
        "min_trade_usd": "25",
        "max_slippage_bps": 50,
        "force_action": "",
    }
    config.update(overrides)
    return BaseWethUsdcMomentumStrategy(
        config=config,
        chain="base",
        wallet_address="0x" + "1" * 40,
    )


def _balance(balance: str, balance_usd: str):
    return SimpleNamespace(symbol="", balance=Decimal(balance), balance_usd=Decimal(balance_usd), address="")


def _market(ts: datetime, usdc_usd: str = "100", weth_usd: str = "100"):
    market = MagicMock()
    market.timestamp = ts

    def _balance_lookup(symbol: str):
        if symbol == "USDC":
            return _balance("100", usdc_usd)
        if symbol == "WETH":
            return _balance("0.05", weth_usd)
        raise ValueError("unknown token")

    market.balance.side_effect = _balance_lookup
    market.ohlcv.return_value = pd.DataFrame({"close": [100 + i for i in range(40)]})
    return market


def _intent_type(intent):
    return intent.intent_type.value


def test_startup_requires_true_cross_before_first_trade():
    strategy = _make_strategy()
    market = _market(datetime(2026, 1, 1, 12, 15, tzinfo=UTC))
    strategy._latest_rsi = lambda _: Decimal("60")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"
    assert "true crossing" in intent.reason.lower()


def test_only_processes_each_confirmed_15m_close_once():
    strategy = _make_strategy()
    ts = datetime(2026, 1, 1, 12, 15, 30, tzinfo=UTC)
    market = _market(ts)
    strategy._latest_rsi = lambda _: Decimal("50")

    first = strategy.decide(market)
    second = strategy.decide(market)

    assert _intent_type(first) == "HOLD"
    assert _intent_type(second) == "HOLD"
    assert "awaiting next confirmed 15m candle close" in second.reason.lower()


def test_neutral_to_above_cross_swaps_usdc_to_weth():
    strategy = _make_strategy()
    strategy._prev_rsi_zone = "neutral"
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC), usdc_usd="80")
    strategy._latest_rsi = lambda _: Decimal("61")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "SWAP"
    assert intent.from_token == "USDC"
    assert intent.to_token == "WETH"
    assert intent.amount == "all"


def test_neutral_to_below_cross_swaps_weth_to_usdc():
    strategy = _make_strategy()
    strategy._prev_rsi_zone = "neutral"
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC), weth_usd="80")
    strategy._latest_rsi = lambda _: Decimal("39")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "SWAP"
    assert intent.from_token == "WETH"
    assert intent.to_token == "USDC"
    assert intent.amount == "all"


def test_repeat_direction_locked_until_neutral_reentry():
    strategy = _make_strategy()
    strategy._prev_rsi_zone = "above"
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC))
    strategy._latest_rsi = lambda _: Decimal("58")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"


def test_reenter_neutral_then_recross_unlocks_buy():
    strategy = _make_strategy()
    t1 = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 12, 45, tzinfo=UTC)
    market1 = _market(t1)
    market2 = _market(t2)

    strategy._prev_rsi_zone = "above"
    strategy._latest_rsi = lambda _: Decimal("50")
    neutral_intent = strategy.decide(market1)

    strategy._latest_rsi = lambda _: Decimal("61")
    buy_intent = strategy.decide(market2)

    assert _intent_type(neutral_intent) == "HOLD"
    assert _intent_type(buy_intent) == "SWAP"
    assert buy_intent.from_token == "USDC"


def test_force_action_buy_bypasses_signal_gates():
    strategy = _make_strategy(force_action="buy")
    market = _market(datetime(2026, 1, 1, 12, 15, tzinfo=UTC), usdc_usd="90")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "SWAP"
    assert intent.from_token == "USDC"
    assert intent.to_token == "WETH"


def test_force_action_sell_bypasses_signal_gates():
    strategy = _make_strategy(force_action="sell")
    market = _market(datetime(2026, 1, 1, 12, 15, tzinfo=UTC), weth_usd="90")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "SWAP"
    assert intent.from_token == "WETH"
    assert intent.to_token == "USDC"


def test_insufficient_balance_on_cross_returns_hold():
    strategy = _make_strategy()
    strategy._prev_rsi_zone = "neutral"
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC), usdc_usd="5")
    strategy._latest_rsi = lambda _: Decimal("61")

    intent = strategy.decide(market)

    assert _intent_type(intent) == "HOLD"
    assert "insufficient usdc" in intent.reason.lower()


def test_state_round_trip_persists_gate_and_zone():
    strategy = _make_strategy()
    strategy._prev_rsi_zone = "neutral"
    strategy._last_processed_candle_close_ts = "2026-01-01T12:30:00+00:00"
    strategy._holding_asset = "USDC"

    saved = strategy.get_persistent_state()

    fresh = _make_strategy()
    fresh.load_persistent_state(saved)

    assert fresh.get_persistent_state() == saved


def test_teardown_unwinds_weth_to_usdc_when_position_exists():
    strategy = _make_strategy()
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC), weth_usd="120")

    intents = strategy.generate_teardown_intents(TeardownMode.SOFT, market=market)

    assert len(intents) == 1
    assert _intent_type(intents[0]) == "SWAP"
    assert intents[0].from_token == "WETH"
    assert intents[0].to_token == "USDC"
    assert intents[0].amount == "all"


def test_ohlcv_query_uses_15m_pool_signal_source():
    strategy = _make_strategy()
    market = _market(datetime(2026, 1, 1, 12, 30, tzinfo=UTC))

    rsi_value = strategy._latest_rsi(market)

    assert rsi_value is not None
    market.ohlcv.assert_called_once()
    kwargs = market.ohlcv.call_args.kwargs
    assert kwargs["token"] == "WETH/USDC"
    assert kwargs["timeframe"] == "15m"
    assert kwargs["pool_address"] == "0xd0b53d9277642d899df5c87a3966a349a798f224"
