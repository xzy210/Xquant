from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.data_portal import DataPortal
from live_rotation.rotation_data_service import RotationDataService


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        data_dir.mkdir(parents=True, exist_ok=True)

        stock_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [10.0, 10.5],
                "high": [10.8, 11.0],
                "low": [9.9, 10.2],
                "close": [10.6, 10.9],
                "volume": [2000, 2400],
            }
        )
        stock_df.to_parquet(data_dir / "000001.parquet", index=False)

        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.25],
                "volume": [1000, 1200],
            }
        )
        df.to_parquet(data_dir / "510880.parquet", index=False)

        etf_dir = data_dir / "etf"
        etf_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(etf_dir / "159915.parquet", index=False)

        index_dir = data_dir / "index"
        index_dir.mkdir(parents=True, exist_ok=True)
        index_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [3000.0, 3010.0],
                "high": [3020.0, 3030.0],
                "low": [2990.0, 3000.0],
                "close": [3015.0, 3025.0],
                "volume": [500000, 520000],
            }
        )
        index_df.to_parquet(index_dir / "000300.parquet", index=False)

        portal = DataPortal(default_data_dir=data_dir)
        stock_bars = portal.get_daily_bars(
            "000001.SZ",
            asset_type="stock",
            data_dir=data_dir,
            use_cache=False,
        )
        assert stock_bars is not None
        assert float(stock_bars["close"].iloc[-1]) == 10.9
        assert portal.list_symbols(asset_type="stock", data_dir=data_dir) == ["000001", "510880"]
        assert portal.get_date_range("000001", asset_type="stock", data_dir=data_dir) == ("2024-01-02", "2024-01-03")

        index_bars = portal.get_daily_bars(
            "000300.SH",
            asset_type="index",
            data_dir=data_dir,
            use_cache=False,
        )
        assert index_bars is not None
        assert float(index_bars["close"].iloc[-1]) == 3025.0
        assert portal.list_symbols(asset_type="index", data_dir=data_dir) == ["000300"]
        index_assets = portal.list_assets(asset_type="index", data_dir=data_dir)
        assert any(item["code"] == "000300" and item["asset_type"] == "index" for item in index_assets)
        assert portal.get_name_map(asset_type="index").get("000300") == "沪深300"

        bars = portal.get_daily_bars(
            "510880.SH",
            asset_type="etf",
            data_dir=data_dir,
            use_cache=False,
        )
        assert bars is not None
        assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
        assert float(bars["close"].iloc[-1]) == 1.25

        metadata = portal.get_daily_metadata(
            "510880.SH",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert metadata.exists
        assert metadata.rows == 2
        assert metadata.first_date == "2024-01-02"
        assert metadata.latest_date == "2024-01-03"
        assert metadata.expected_date == "2024-01-03"
        assert metadata.is_fresh
        assert metadata.reason == "fresh"

        file_metadata = portal.get_daily_file_metadata(
            data_dir / "510880.parquet",
            asset_type="etf",
            now=datetime(2024, 1, 4, 16, 0),
        )
        assert file_metadata.symbol == "510880"
        assert file_metadata.latest_date == "2024-01-03"
        assert not file_metadata.is_fresh
        assert portal.format_daily_status_message(file_metadata) == "2024-01-03"

        missing_metadata = portal.get_daily_metadata(
            "159916",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert not missing_metadata.exists
        assert portal.format_daily_status_message(missing_metadata) == "文件不存在"

        freshness_map = portal.check_daily_freshness_map(
            ["510880", "159915"],
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert freshness_map["510880"].is_fresh
        assert freshness_map["159915"].is_fresh

        etf_assets = portal.list_assets(asset_type="etf", data_dir=data_dir)
        assert any(item["code"] == "159915" and item["latest_date"] == "2024-01-03" for item in etf_assets)

        result = portal.get_bars(
            ["510880.SH"],
            asset_type="etf",
            data_dir=data_dir,
            use_cache=False,
        )
        assert "510880" in result
        assert result["510880"].metadata.schema_version == "daily_bars.v1"
        assert result["510880"].metadata.latest_date == "2024-01-03"
        assert result["510880"].metadata.first_date == "2024-01-02"
        assert result["510880"].metadata.data_path is not None

        bundle = portal.get_market_data_bundle(
            ["000001.SZ"],
            asset_type="stock",
            data_dir=data_dir,
            primary_symbol="000001.SZ",
            benchmark_symbol="000300",
            start="2024-01-02",
            end="2024-01-03",
        )
        assert bundle.schema_version == "market_data_bundle.v1"
        assert bundle.symbols == ["000001"]
        assert bundle.primary_symbol == "000001"
        assert bundle.benchmark_symbol == "000300"
        assert bundle.benchmark is not None
        single_code, single_df = bundle.require_single_frame()
        assert single_code == "000001"
        assert float(single_df["close"].iloc[-1]) == 10.9
        assert list(bundle.to_data_dict().keys()) == ["000001"]
        assert bundle.require("000001.SZ").metadata.asset_type == "stock"
        assert float(bundle.to_frame("000001")["close"].iloc[-1]) == 10.9
        assert [symbol for symbol, _ in bundle.iter_views()] == ["000001"]

        from common.execution_contract import FillReport, OrderIntent, StrategySignal

        signal = StrategySignal(
            symbol="000001.SZ",
            action="buy",
            strategy_id="smoke_strategy",
            strategy_name="Smoke Strategy",
            target_quantity=100,
            price=10.6,
            reason="smoke buy",
        )
        intent = signal.to_order_intent(source="backtest", trigger="strategy")
        assert isinstance(intent, OrderIntent)
        assert intent.symbol == "000001.SZ"
        assert intent.side == "buy"
        assert intent.quantity == 100
        assert intent.signed_quantity == 100
        assert intent.order_type_code == 23
        assert intent.to_execution_request_kwargs()["order_type"] == 23

        stale = portal.check_daily_freshness(
            "510880",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 4, 16, 0),
        )
        assert not stale.is_fresh
        assert stale.latest_date == "2024-01-03"
        assert stale.expected_date == "2024-01-04"

        fresh = portal.check_daily_freshness(
            "510880",
            asset_type="etf",
            data_dir=data_dir,
            now=datetime(2024, 1, 3, 16, 0),
        )
        assert fresh.is_fresh

        rotation_data = RotationDataService(data_dir=data_dir, data_portal=portal)
        rotation_bars = rotation_data.load_daily_bars("510880")
        assert rotation_bars is not None
        assert float(rotation_data.latest_close("510880")) == 1.25
        is_fresh, latest_date = rotation_data.is_code_fresh("510880")
        assert isinstance(is_fresh, bool)
        assert latest_date == "2024-01-03"
        assert rotation_data.is_pool_fresh(["510880"]) == is_fresh

        from trading_app.services.index_service import load_index_data

        compat_index_df = load_index_data("000300", str(data_dir), start_date="2024-01-02", end_date="2024-01-03")
        assert compat_index_df is not None
        assert float(compat_index_df["close"].iloc[-1]) == 3025.0

        from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine

        class NoopSingleStrategy:
            def initialize(self, context):
                pass

            def on_bar(self, context, bars, history=None):
                pass

        single_result = UnifiedBacktestEngine(BacktestConfig(initial_cash=1000, mode="bar")).run(
            NoopSingleStrategy(), bundle, mode="bar"
        )
        assert single_result["final_value"] == 1000
        assert single_result["data_contract"]["schema_version"] == "market_data_bundle.v1"
        assert single_result["data_contract"]["primary_symbol"] == "000001"
        assert single_result["execution_reports"] == []

        class IntentSingleStrategy(NoopSingleStrategy):
            def __init__(self):
                self.done = False

            def on_bar(self, context, bars, history=None):
                if self.done:
                    return
                current_price = float(next(iter(bars.values()))["close"])
                report = context.place_order_intent(
                    OrderIntent(
                        symbol="000001",
                        side="buy",
                        quantity=100,
                        price=current_price,
                        strategy_id="smoke_strategy",
                        reason="intent smoke buy",
                        source="backtest",
                    )
                )
                assert report.accepted
                assert report.filled
                assert report.fills[0].schema_version == "fill_report.v1"
                assert report.fills[0].quantity == 100
                self.done = True

        intent_result = UnifiedBacktestEngine(BacktestConfig(initial_cash=2000, mode="bar")).run(
            IntentSingleStrategy(), bundle, mode="bar"
        )
        assert len(intent_result["trades"]) == 1
        assert len(intent_result["execution_reports"]) == 1
        assert intent_result["execution_reports"][0].schema_version == "order_execution_report.v1"
        assert isinstance(FillReport.from_backtest_trade(intent_result["trades"][0]), FillReport)

        class SignalSingleStrategy(NoopSingleStrategy):
            def __init__(self):
                self.done = False

            def generate_signals(self, data, context=None):
                if self.done:
                    return []
                self.done = True
                price = float(data["bars"][data["code"]]["close"])
                return [
                    StrategySignal(
                        symbol="000001",
                        action="buy",
                        strategy_id="auto_signal_smoke",
                        target_quantity=100,
                        price=price,
                        reason="auto signal smoke buy",
                    )
                ]

        signal_result = UnifiedBacktestEngine(BacktestConfig(initial_cash=2000, mode="bar")).run(
            SignalSingleStrategy(), bundle, mode="bar"
        )
        assert len(signal_result["trades"]) == 1
        assert len(signal_result["execution_reports"]) == 1
        assert signal_result["execution_reports"][0].intent.signal_id
        assert signal_result["execution_reports"][0].intent.intent_type == "target_quantity"
        assert signal_result["execution_reports"][0].fills[0].quantity == 100

        class BundleAwareSingleStrategy(NoopSingleStrategy):
            def __init__(self):
                self.bundle_symbols = []
                self.prepared_asset_type = None

            def on_data_bundle(self, received_bundle):
                self.bundle_symbols = received_bundle.symbols

            def prepare_data_view(self, view):
                self.prepared_asset_type = view.metadata.asset_type
                return view.to_frame()

        bundle_aware_single = BundleAwareSingleStrategy()
        UnifiedBacktestEngine(BacktestConfig(initial_cash=1000, mode="bar")).run(
            bundle_aware_single, bundle, mode="bar"
        )
        assert bundle_aware_single.bundle_symbols == ["000001"]
        assert bundle_aware_single.prepared_asset_type == "stock"

        class NoopCrossSectionalStrategy:
            def initialize(self, context):
                pass

            def prepare_factors(self, data_dict):
                rows = []
                for code, bars_df in data_dict.items():
                    for trade_date in bars_df["date"]:
                        rows.append({"date": trade_date, "code": code, "score": 0.0})
                return pd.DataFrame(rows).set_index(["date", "code"])

            def on_rebalance(self, context, valid_codes, daily_factors):
                pass

        cross_result = UnifiedBacktestEngine(BacktestConfig(initial_cash=1000, mode="cross_sectional")).run(
            NoopCrossSectionalStrategy(), bundle, mode="cross_sectional"
        )
        assert cross_result["final_value"] == 1000
        assert cross_result["data_contract"]["symbols"] == ["000001"]

        class SignalCrossSectionalStrategy(NoopCrossSectionalStrategy):
            def __init__(self):
                self.done = False

            def generate_signals(self, data, context=None):
                if self.done or not data["valid_codes"]:
                    return []
                self.done = True
                code = data["valid_codes"][0]
                price = float(data["prices"][code])
                return [
                    StrategySignal(
                        symbol=code,
                        action="buy",
                        strategy_id="cross_auto_signal_smoke",
                        target_percent=0.5,
                        price=price,
                        reason="cross auto signal smoke buy",
                    )
                ]

        signal_cross_result = UnifiedBacktestEngine(BacktestConfig(initial_cash=3000, mode="cross_sectional")).run(
            SignalCrossSectionalStrategy(), bundle, mode="cross_sectional"
        )
        assert len(signal_cross_result["trades"]) == 1
        assert len(signal_cross_result["execution_reports"]) == 1
        assert signal_cross_result["execution_reports"][0].intent.intent_type == "target_percent"

        class BundleAwareCrossSectionalStrategy(NoopCrossSectionalStrategy):
            def __init__(self):
                self.bundle_symbols = []
                self.factor_asset_types = []

            def on_data_bundle(self, received_bundle):
                self.bundle_symbols = received_bundle.symbols

            def prepare_factors_from_bundle(self, received_bundle):
                self.factor_asset_types = [view.metadata.asset_type for _, view in received_bundle.iter_views()]
                rows = []
                for symbol, view in received_bundle.iter_views():
                    for trade_date in view.data["date"]:
                        rows.append({"date": trade_date, "code": symbol, "score": 1.0})
                return pd.DataFrame(rows).set_index(["date", "code"])

        bundle_aware_cross = BundleAwareCrossSectionalStrategy()
        UnifiedBacktestEngine(BacktestConfig(initial_cash=1000, mode="cross_sectional")).run(
            bundle_aware_cross, bundle, mode="cross_sectional"
        )
        assert bundle_aware_cross.bundle_symbols == ["000001"]
        assert bundle_aware_cross.factor_asset_types == ["stock"]

        from strategy_app.strategies.etf_grid_strategy import ETFGridStrategy, GridConfig

        etf_grid_data = pd.DataFrame({
            "time": pd.date_range("2024-01-02 09:30", periods=5, freq="min"),
            "open": [1.0, 1.01, 0.99, 1.02, 1.0],
            "high": [1.02, 1.03, 1.01, 1.04, 1.02],
            "low": [0.99, 1.0, 0.98, 1.01, 0.99],
            "close": [1.01, 0.99, 1.02, 1.0, 1.03],
            "volume": [100000] * 5,
        })
        etf_grid = ETFGridStrategy(GridConfig(
            initial_capital=10000,
            grid_count=2,
            grid_spacing=0.02,
            use_atr_adaptive=False,
            min_trade_amount=100,
        ))
        etf_grid_result = etf_grid.run_backtest(etf_grid_data)
        assert not hasattr(etf_grid, "backtest")
        assert "engine_result" in etf_grid_result
        assert [key for key in etf_grid_result if key.endswith("_result")] == ["engine_result"]
        assert etf_grid_result["engine_result"]["data_contract"]["schema_version"] == "dataframe_input.v1"
        assert len(etf_grid_result["daily_stats"]) == len(etf_grid_data)

        from trading_app.services.trade_execution_service import TradeExecutionService

        live_service = TradeExecutionService.__new__(TradeExecutionService)
        live_service.broker_service = type(
            "BrokerStub",
            (),
            {
                "query_stock_positions": lambda self: [],
                "query_stock_asset": lambda self: type("Asset", (), {"total_asset": 10000.0})(),
            },
        )()
        live_intent = live_service._signal_to_order_intent(
            StrategySignal(
                symbol="000001.SZ",
                action="buy",
                strategy_id="live_auto_signal_smoke",
                strategy_name="Live Auto Signal Smoke",
                target_percent=0.2,
                price=10.0,
                reason="live signal conversion smoke",
            )
        )
        assert live_intent is not None
        assert live_intent.symbol == "000001.SZ"
        assert live_intent.quantity == 200
        assert live_intent.intent_type == "target_percent"
        assert live_intent.to_execution_request_kwargs()["order_volume"] == 200

        unloaded_cache_result = portal.refresh_loaded_caches(data_dir=data_dir)
        assert not unloaded_cache_result.stock_cache_loaded
        assert not unloaded_cache_result.etf_cache_loaded

        from common.data_loader import get_stock_cache

        stock_cache = get_stock_cache()
        try:
            loaded = stock_cache.preload_all(str(data_dir), ["000001"], max_workers=1)
            assert loaded == 1
            cache_result = portal.refresh_loaded_caches(data_dir=data_dir, stock_codes=["000001"], max_workers=1)
            assert cache_result.stock_cache_loaded
            assert cache_result.stock_count == 1
        finally:
            stock_cache.clear()

        from common.data_portal import set_data_portal
        from trading_app.services.market_data_status_service import MarketDataStatusService

        set_data_portal(portal)
        try:
            ok, message = MarketDataStatusService()._check_daily_freshness(
                stock_codes=["000001"],
                etf_codes=["510880"],
                index_codes=[],
            )
            assert isinstance(ok, bool)
            assert "parquet" in message or "000001" in message or "510880" in message
        finally:
            set_data_portal(None)

    print("data_portal_smoketest_ok")


if __name__ == "__main__":
    main()
