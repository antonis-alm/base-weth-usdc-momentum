"""Base WETH/USDC momentum strategy (Uniswap V3 swaps only)."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from almanak.framework.intents import Intent
from almanak.framework.market import (
    BalanceUnavailableError,
    MarketSnapshot,
    OHLCVUnavailableError,
)
from almanak.framework.strategies import IntentStrategy, almanak_strategy

if TYPE_CHECKING:
    from almanak.framework.teardown import TeardownMode, TeardownPositionSummary

logger = logging.getLogger(__name__)


@almanak_strategy(
    name="base_weth_usdc_momentum",
    description="RSI momentum rotation between WETH and USDC on Base Uniswap V3",
    version="1.0.0",
    author="Generated",
    tags=["momentum", "rsi", "uniswap_v3", "base"],
    supported_chains=["base"],
    supported_protocols=["uniswap_v3"],
    intent_types=["SWAP", "HOLD"],
    default_chain="base",
)
class BaseWethUsdcMomentumStrategy(IntentStrategy):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.protocol = str(self.get_config("protocol", "uniswap_v3"))
        self.base_token = str(self.get_config("base_token", "WETH"))
        self.quote_token = str(self.get_config("quote_token", "USDC"))
        self.signal_pool_address = str(self.get_config("signal_pool_address"))
        self.timeframe = str(self.get_config("timeframe", "15m"))

        self.rsi_period = int(self.get_config("rsi_period", 14))
        self.lower_band = Decimal(str(self.get_config("lower_band", 40)))
        self.upper_band = Decimal(str(self.get_config("upper_band", 60)))

        self.min_trade_usd = Decimal(str(self.get_config("min_trade_usd", "25")))
        self.max_slippage_bps = int(self.get_config("max_slippage_bps", 50))
        self.force_action = str(self.get_config("force_action", "")).strip().lower()

        self._prev_rsi_zone: str | None = None
        self._last_processed_candle_close_ts: str | None = None
        self._holding_asset = "UNKNOWN"

    def decide(self, market: MarketSnapshot) -> Intent | None:
        if self.force_action:
            return self._forced_intent(market)

        candle_close = self._confirmed_15m_close(market.timestamp)
        candle_close_iso = candle_close.isoformat()
        if self._last_processed_candle_close_ts == candle_close_iso:
            return Intent.hold(reason="Awaiting next confirmed 15m candle close")

        rsi_value = self._latest_rsi(market)
        self._last_processed_candle_close_ts = candle_close_iso

        if rsi_value is None:
            return Intent.hold(reason="RSI data unavailable")

        current_zone = self._zone(rsi_value)

        if self._prev_rsi_zone is None:
            self._prev_rsi_zone = current_zone
            return Intent.hold(reason="Startup warm-up complete; waiting for true crossing")

        should_buy = self._prev_rsi_zone == "neutral" and current_zone == "above"
        should_sell = self._prev_rsi_zone == "neutral" and current_zone == "below"
        should_exit_to_neutral = self._prev_rsi_zone == "above" and current_zone == "neutral"

        self._prev_rsi_zone = current_zone

        if should_exit_to_neutral:
            return self._sell_all_base(market)
        if should_buy:
            return self._buy_all_quote(market)
        if should_sell:
            return self._sell_all_base(market)
        return Intent.hold(reason=f"RSI={rsi_value:.2f}, zone={current_zone}")

    def _forced_intent(self, market: MarketSnapshot) -> Intent:
        if self.force_action == "buy":
            return self._buy_all_quote(market)
        if self.force_action == "sell":
            return self._sell_all_base(market)
        return Intent.hold(reason=f"Unknown force_action: {self.force_action}")

    def _buy_all_quote(self, market: MarketSnapshot) -> Intent:
        try:
            quote_balance = market.balance(self.quote_token)
        except (BalanceUnavailableError, ValueError) as exc:
            return Intent.hold(reason=f"{self.quote_token} balance unavailable: {exc}")

        if quote_balance.balance_usd < self.min_trade_usd:
            return Intent.hold(
                reason=f"Insufficient {self.quote_token} (${quote_balance.balance_usd:.2f})"
            )

        return Intent.swap(
            from_token=self.quote_token,
            to_token=self.base_token,
            amount="all",
            max_slippage=self._slippage_decimal,
            protocol=self.protocol,
            chain=self.chain,
        )

    def _sell_all_base(self, market: MarketSnapshot) -> Intent:
        try:
            base_balance = market.balance(self.base_token)
        except (BalanceUnavailableError, ValueError) as exc:
            return Intent.hold(reason=f"{self.base_token} balance unavailable: {exc}")

        if base_balance.balance_usd < self.min_trade_usd:
            return Intent.hold(
                reason=f"Insufficient {self.base_token} (${base_balance.balance_usd:.2f})"
            )

        return Intent.swap(
            from_token=self.base_token,
            to_token=self.quote_token,
            amount="all",
            max_slippage=self._slippage_decimal,
            protocol=self.protocol,
            chain=self.chain,
        )

    @property
    def _slippage_decimal(self) -> Decimal:
        return Decimal(str(self.max_slippage_bps)) / Decimal("10000")

    def _zone(self, rsi_value: Decimal) -> str:
        if rsi_value > self.upper_band:
            return "above"
        if rsi_value < self.lower_band:
            return "below"
        return "neutral"

    def _confirmed_15m_close(self, ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        close_epoch = (epoch // 900) * 900
        return datetime.fromtimestamp(close_epoch, tz=UTC)

    def _latest_rsi(self, market: MarketSnapshot) -> Decimal | None:
        pair = f"{self.base_token}/{self.quote_token}"
        try:
            candles = market.ohlcv(
                token=pair,
                timeframe=self.timeframe,
                limit=max(self.rsi_period + 32, 64),
                pool_address=self.signal_pool_address,
            )
        except (OHLCVUnavailableError, ValueError):
            return None

        if "close" not in candles:
            return None

        closes: list[Decimal] = []
        for value in candles["close"].dropna().tolist():
            closes.append(Decimal(str(value)))

        return self._calculate_rsi(closes)

    def _calculate_rsi(self, closes: list[Decimal]) -> Decimal | None:
        if len(closes) <= self.rsi_period:
            return None

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(delta, Decimal("0")) for delta in deltas]
        losses = [max(-delta, Decimal("0")) for delta in deltas]

        avg_gain = sum(gains[: self.rsi_period], Decimal("0")) / Decimal(self.rsi_period)
        avg_loss = sum(losses[: self.rsi_period], Decimal("0")) / Decimal(self.rsi_period)

        for idx in range(self.rsi_period, len(gains)):
            avg_gain = ((avg_gain * (self.rsi_period - 1)) + gains[idx]) / Decimal(self.rsi_period)
            avg_loss = ((avg_loss * (self.rsi_period - 1)) + losses[idx]) / Decimal(self.rsi_period)

        if avg_loss == 0:
            return Decimal("100")

        rs = avg_gain / avg_loss
        return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))

    def on_intent_executed(self, intent: Intent, success: bool, result: Any) -> None:
        if not success:
            return
        intent_type = getattr(intent, "intent_type", None)
        if not intent_type or getattr(intent_type, "value", "") != "SWAP":
            return

        from_token = getattr(intent, "from_token", "")
        to_token = getattr(intent, "to_token", "")
        if from_token == self.quote_token and to_token == self.base_token:
            self._holding_asset = self.base_token
        elif from_token == self.base_token and to_token == self.quote_token:
            self._holding_asset = self.quote_token

    def get_persistent_state(self) -> dict[str, Any]:
        return {
            "prev_rsi_zone": self._prev_rsi_zone,
            "last_processed_candle_close_ts": self._last_processed_candle_close_ts,
            "holding_asset": self._holding_asset,
        }

    def load_persistent_state(self, state: dict[str, Any]) -> None:
        self._prev_rsi_zone = state.get("prev_rsi_zone")
        self._last_processed_candle_close_ts = state.get("last_processed_candle_close_ts")
        self._holding_asset = state.get("holding_asset", "UNKNOWN")

    def get_open_positions(self) -> "TeardownPositionSummary":
        from datetime import UTC, datetime

        from almanak.framework.teardown import (
            PositionInfo,
            PositionType,
            TeardownPositionSummary,
        )

        try:
            market = self.create_market_snapshot()
            base_balance = market.balance(self.base_token)
        except (BalanceUnavailableError, ValueError):
            return TeardownPositionSummary.empty(self.strategy_id or self.STRATEGY_NAME)

        positions = []
        if base_balance.balance_usd >= self.min_trade_usd:
            positions.append(
                PositionInfo(
                    position_type=PositionType.TOKEN,
                    position_id="base_weth_position",
                    chain=self.chain,
                    protocol=self.protocol,
                    value_usd=base_balance.balance_usd,
                    details={"asset": self.base_token, "balance": str(base_balance.balance)},
                )
            )

        return TeardownPositionSummary(
            strategy_id=self.strategy_id or self.STRATEGY_NAME,
            timestamp=datetime.now(UTC),
            positions=positions,
        )

    def generate_teardown_intents(self, mode: "TeardownMode", market=None) -> list[Intent]:
        from almanak.framework.teardown import TeardownMode

        local_market = market
        if local_market is None:
            try:
                local_market = self.create_market_snapshot()
            except ValueError:
                return []

        try:
            base_balance = local_market.balance(self.base_token)
        except (BalanceUnavailableError, ValueError):
            return []

        if base_balance.balance <= 0 or base_balance.balance_usd < self.min_trade_usd:
            return []

        max_slippage = Decimal("0.03") if mode == TeardownMode.HARD else self._slippage_decimal
        return [
            Intent.swap(
                from_token=self.base_token,
                to_token=self.quote_token,
                amount="all",
                max_slippage=max_slippage,
                protocol=self.protocol,
                chain=self.chain,
            )
        ]
