"""Base WETH/USDC momentum dashboard."""

from typing import Any

import streamlit as st
from almanak.framework.dashboard.templates import (
    get_rsi_config,
    prepare_ta_session_state,
    render_ta_dashboard,
)


def _build_rsi_config(strategy_config: dict[str, Any]):
    config = get_rsi_config(
        period=int(strategy_config.get("rsi_period", 14)),
        overbought=float(strategy_config.get("upper_band", 55)),
        oversold=float(strategy_config.get("lower_band", 45)),
    )
    config.base_token = str(strategy_config.get("base_token", config.base_token))
    config.quote_token = str(strategy_config.get("quote_token", config.quote_token))
    config.chain = str(strategy_config.get("chain", config.chain))
    config.protocol = str(strategy_config.get("protocol", config.protocol))
    return config


def render_custom_dashboard(
    strategy_id: str,
    strategy_config: dict[str, Any],
    api_client: Any,
    session_state: dict[str, Any],
) -> None:
    st.title("Base WETH/USDC Momentum")

    config = _build_rsi_config(strategy_config)

    try:
        prepared_state = prepare_ta_session_state(
            api_client,
            session_state=session_state,
            config=config,
        )
    except Exception:
        prepared_state = session_state

    render_ta_dashboard(strategy_id, strategy_config, prepared_state, config)
