import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from QuantResearch.strategies import load_strategy


def test_ma_crossover_emits_cross_signals():
    strat = load_strategy("ma_crossover", cooldown_bars=0, allow_short=False)

    # First call seeds previous averages
    strat.on_bar(
        {
            "sma_fast": 1.00,
            "sma_slow": 1.01,
            "position_units": 0.0,
            "default_qty": 1000,
            "bar_idx": 0,
        }
    )

    signal = strat.on_bar(
        {
            "sma_fast": 1.02,
            "sma_slow": 1.00,
            "position_units": 0.0,
            "default_qty": 1000,
            "bar_idx": 1,
        }
    )
    assert signal["action"] == "ENTER_LONG"
    assert signal["size"] == 1000

    exit_signal = strat.on_bar(
        {
            "sma_fast": 0.99,
            "sma_slow": 1.01,
            "position_units": 1.0,
            "default_qty": 1000,
            "bar_idx": 2,
        }
    )
    assert exit_signal["action"] == "EXIT_LONG"

    short_strat = load_strategy("ma_crossover", cooldown_bars=0, allow_short=True)
    short_strat.on_bar(
        {
            "sma_fast": 1.01,
            "sma_slow": 1.00,
            "position_units": 0.0,
            "default_qty": 500,
            "bar_idx": 0,
        }
    )
    short_signal = short_strat.on_bar(
        {
            "sma_fast": 0.98,
            "sma_slow": 1.00,
            "position_units": 0.0,
            "default_qty": 500,
            "bar_idx": 1,
        }
    )
    assert short_signal["action"] == "ENTER_SHORT"
    assert short_signal["size"] == 500


def test_momentum_breakout_triggers_long_entry_and_exit():
    strat = load_strategy(
        "momentum_breakout",
        lookback=5,
        enter_threshold=0.01,
        exit_threshold=0.005,
        cooldown_bars=0,
    )
    history = [1.00, 1.01, 1.02, 1.03, 1.04, 1.07]
    signal = strat.on_bar(
        {
            "close_history": history,
            "position_units": 0.0,
            "default_qty": 2000,
            "bar_idx": 10,
        }
    )
    assert signal["action"] == "ENTER_LONG"
    assert signal["size"] == 2000

    flat_signal = strat.on_bar(
        {
            "close_history": [1.00, 1.0, 1.0, 1.0, 1.0, 1.0],
            "position_units": 1.0,
            "default_qty": 2000,
            "bar_idx": 11,
        }
    )
    assert flat_signal["action"] == "EXIT_LONG"
