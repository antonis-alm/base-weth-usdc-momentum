from unittest.mock import MagicMock, patch

from dashboard.ui import _build_rsi_config, render_custom_dashboard


def test_build_rsi_config_maps_strategy_fields():
    strategy_config = {
        "rsi_period": 21,
        "upper_band": 62,
        "lower_band": 38,
        "base_token": "WETH",
        "quote_token": "USDC",
        "chain": "base",
        "protocol": "uniswap_v3",
    }

    config = _build_rsi_config(strategy_config)

    assert config.indicator_period == 21
    assert config.upper_threshold == 62
    assert config.lower_threshold == 38
    assert config.base_token == "WETH"
    assert config.quote_token == "USDC"
    assert config.chain == "base"
    assert config.protocol == "uniswap_v3"


def test_render_custom_dashboard_uses_prepared_state():
    strategy_config = {"rsi_period": 14, "upper_band": 55, "lower_band": 45}
    session_state = {"existing": True}
    prepared_state = {"enriched": True}

    with (
        patch("dashboard.ui.st.title") as mock_title,
        patch("dashboard.ui.prepare_ta_session_state", return_value=prepared_state) as mock_prepare,
        patch("dashboard.ui.render_ta_dashboard") as mock_render,
    ):
        render_custom_dashboard(
            "base_weth_usdc_momentum",
            strategy_config,
            MagicMock(),
            session_state,
        )

    mock_title.assert_called_once_with("Base WETH/USDC Momentum")
    mock_prepare.assert_called_once()
    mock_render.assert_called_once()
    render_args = mock_render.call_args.args
    assert render_args[0] == "base_weth_usdc_momentum"
    assert render_args[1] == strategy_config
    assert render_args[2] == prepared_state


def test_render_custom_dashboard_falls_back_when_prepare_fails():
    strategy_config = {"rsi_period": 14}
    session_state = {"existing": True}

    with (
        patch("dashboard.ui.st.title"),
        patch("dashboard.ui.prepare_ta_session_state", side_effect=RuntimeError("gateway down")),
        patch("dashboard.ui.render_ta_dashboard") as mock_render,
    ):
        render_custom_dashboard(
            "base_weth_usdc_momentum",
            strategy_config,
            MagicMock(),
            session_state,
        )

    assert mock_render.call_args.args[2] == session_state
