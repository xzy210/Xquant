"""Compatibility wrapper for ``common.agent.decision_run_context``."""
from common.agent.decision_run_context import *  # noqa: F401,F403


def _trading_day_resolver(day):
    from live_rotation.holiday_calendar import is_trading_day

    return is_trading_day(day)


set_trading_day_resolver(_trading_day_resolver)
