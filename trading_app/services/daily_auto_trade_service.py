from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from common.broker_session_service import BrokerSessionService, get_broker_session_service

from .agent_context_service import BrokerContext
from .auto_trade_config_service import AutoTradeConfig, get_auto_trade_config_service
from .trade_decision_models import DecisionOutcome, TradeAction
from .trade_execution_service import ExecutionRequest, ExecutionResult, TradeExecutionService, get_trade_execution_service
from .trade_record_service import TradeRecordService, get_trade_record_service
from .decision_tracker_service import DecisionTrackerService

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "daily_auto_trade_state.json"
_RUNNING_STATE_STALE_AFTER = timedelta(minutes=30)


@dataclass
class PlannedOrder:
    symbol_code: str
    symbol_name: str
    action: str
    priority: float
    planned_volume: int
    price: float
    decision_record_id: str = ""
    reason: str = ""
    decision_payload: Dict[str, Any] = field(default_factory=dict)
    risk_payload: Dict[str, Any] = field(default_factory=dict)


class DailyAutoTradeService(QObject):
    status_changed = pyqtSignal(str)
    cycle_finished = pyqtSignal(str, bool, str, dict)
    reconcile_finished = pyqtSignal(bool, str)

    def __init__(
        self,
        broker_service: Optional[BrokerSessionService] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.broker_service = broker_service or get_broker_session_service()
        self.execution_service: TradeExecutionService = get_trade_execution_service()
        self.trade_service: TradeRecordService = get_trade_record_service()
        self.decision_tracker = DecisionTrackerService()
        self.config_service = get_auto_trade_config_service()
        self._state_path = _STATE_PATH
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.timeout.connect(self._on_reconcile_timer)
        self._schedule_next_reconcile()

    def begin_task(self, task_id: str, task_config: dict) -> tuple[bool, str]:
        state = self._get_task_state(task_id)
        if state.get("status") == "completed":
            return False, "今日该自动任务已完成，跳过重复执行"
        if state.get("status") == "running":
            if self._is_running_state_stale(state):
                logger.warning("检测到陈旧自动任务状态，自动回收: %s", task_id)
                self._update_task_state(
                    task_id,
                    status="failed",
                    completed_at=self._now(),
                    error="检测到陈旧运行状态，已自动回收",
                )
            else:
                return False, "今日该自动任务正在执行中"
        self._update_task_state(task_id, status="running", task_name=task_config.get("name", task_id), started_at=self._now())
        self.status_changed.emit(f"自动任务开始: {task_config.get('name', task_id)}")
        return True, "自动任务开始"

    def finish_task(
        self,
        task_id: str,
        success: bool,
        message: str,
        summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        summary = summary or {
            "planned": [],
            "executed": [],
            "skipped": True,
            "reason": message,
        }
        self._update_task_state(
            task_id,
            status="completed" if success else "failed",
            completed_at=self._now(),
            summary=summary,
            error="" if success else message,
        )

    def handle_scan_results(
        self,
        task_id: str,
        task_config: dict,
        scan_results: List[Dict[str, Any]],
        broker_context: BrokerContext,
    ) -> None:
        try:
            cfg = self.config_service.get_config()
            stats = self._summarize_scan_results(scan_results)
            logger.info(
                "自动任务 %s 巡检完成: 总计 %d, 可执行 %d, hold/观望 %d, 风控拦截 %d",
                task_id,
                stats["total"],
                stats["actionable"],
                stats["hold_like"],
                stats["risk_blocked"],
            )
            if not bool(task_config.get("auto_execute", False)):
                summary = {
                    "planned": [],
                    "executed": [],
                    "skipped": True,
                    "reason": "当前任务未启用自动执行",
                }
                self._update_task_state(task_id, status="completed", completed_at=self._now(), summary=summary)
                self.cycle_finished.emit(task_id, True, "任务分析完成，未启用自动执行", summary)
                return

            guard_error = self._check_daily_guard(cfg, broker_context)
            if guard_error:
                message = self._format_guard_skip_message(guard_error)
                summary = {"planned": [], "executed": [], "skipped": True, "reason": guard_error}
                self._update_task_state(task_id, status="completed", completed_at=self._now(), summary=summary)
                logger.info("自动任务 %s 因风控规则跳过执行: %s", task_id, guard_error)
                self.cycle_finished.emit(task_id, True, message, summary)
                return

            plan = self._build_daily_plan(scan_results, broker_context, cfg)
            if not plan:
                summary = {"planned": [], "executed": [], "skipped": True, "reason": "没有满足条件的自动执行候选"}
                self._update_task_state(task_id, status="completed", completed_at=self._now(), summary=summary)
                self.cycle_finished.emit(task_id, True, "没有满足条件的自动执行候选", summary)
                return

            logger.info(
                "自动任务 %s 完成交易规划: 卖出 %d 笔, 买入 %d 笔",
                task_id,
                sum(1 for item in plan if item.action in (TradeAction.SELL.value, TradeAction.REDUCE.value)),
                sum(1 for item in plan if item.action not in (TradeAction.SELL.value, TradeAction.REDUCE.value)),
            )
            self.status_changed.emit(f"自动交易规划完成: {len(plan)} 笔待执行")
            executed: List[Dict[str, Any]] = []
            failures = 0
            for item in plan:
                self.status_changed.emit(f"自动执行: {item.symbol_name} {item.action} {item.planned_volume}股")
                latest_broker = self._load_broker_context()
                guard_error = self._check_daily_guard(cfg, latest_broker)
                if guard_error:
                    message = self._format_guard_skip_message(guard_error)
                    executed.append({
                        "symbol_code": item.symbol_code,
                        "symbol_name": item.symbol_name,
                        "action": item.action,
                        "success": True,
                        "message": message,
                        "execution_mode": "skipped",
                    })
                    logger.info("自动任务 %s 在执行前被风控拦截: %s", task_id, guard_error)
                    break

                result = self._execute_planned_order(item)
                executed.append({
                    "symbol_code": item.symbol_code,
                    "symbol_name": item.symbol_name,
                    "action": item.action,
                    "success": result.success,
                    "message": result.message,
                    "broker_order_id": result.broker_order_id,
                    "execution_mode": result.execution_mode,
                })
                self._record_execution_result(task_id, result)
                if result.success and item.decision_record_id:
                    self.decision_tracker.update_outcome(
                        item.decision_record_id,
                        outcome=DecisionOutcome.EXECUTED.value,
                        broker_order_id=result.broker_order_id,
                    )
                    if item.action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
                        self.decision_tracker.auto_close_by_symbol(
                            item.symbol_code,
                            item.price,
                            broker_order_id=result.broker_order_id,
                        )
                if not result.success:
                    failures += 1
                    if failures >= cfg.max_intraday_failures:
                        break

            summary = {
                "planned": [asdict(item) for item in plan],
                "executed": executed,
                "skipped": bool(executed) and all(item.get("execution_mode") == "skipped" for item in executed),
                "reason": "",
            }
            success = bool(executed) or not plan
            self._update_task_state(
                task_id,
                status="completed" if success else "failed",
                completed_at=self._now(),
                summary=summary,
            )
            if summary["skipped"]:
                message = f"自动执行已跳过: 计划 {len(plan)} 笔，因风控未实际下单"
            else:
                message = f"自动执行完成: 计划 {len(plan)} 笔，结果 {len(executed)} 笔"
            self.cycle_finished.emit(task_id, success, message, summary)
        except Exception as exc:
            logger.exception("Daily auto trade handling failed")
            self._update_task_state(task_id, status="failed", completed_at=self._now(), error=str(exc))
            self.cycle_finished.emit(task_id, False, f"自动执行异常: {exc}", {})

    def run_end_of_day_reconcile(self) -> tuple[bool, str]:
        if not self.config_service.get_config().auto_reconcile_enabled:
            return False, "日终对账未启用"
        if not self.broker_service.is_connected:
            return False, "券商未连接，无法执行日终对账"
        try:
            self.status_changed.emit("开始日终对账")
            orders = self.broker_service.query_stock_orders() or []
            name_map = {}
            self.trade_service.sync_from_orders(orders, source="broker_sync", name_map=name_map)
            self.trade_service.sync_order_records_from_orders(orders)
            asset = self.broker_service.query_stock_asset()
            positions = self.broker_service.query_stock_positions() or []
            snapshot = self.trade_service.save_daily_pnl(
                snapshot_date=datetime.now().strftime("%Y-%m-%d"),
                total_asset=float(getattr(asset, "total_asset", 0) or 0),
                cash=float(getattr(asset, "cash", 0) or 0),
                market_value=float(getattr(asset, "market_value", 0) or 0),
                position_count=len([p for p in positions if int(getattr(p, "volume", 0) or 0) > 0]),
                remark="AI交易中心日终自动对账",
            )
            if snapshot is None:
                return False, "保存日终快照失败"
            self._mark_reconciled_today()
            self.status_changed.emit("日终对账完成")
            return True, f"日终对账完成，总资产 {snapshot.total_asset:,.2f}"
        except Exception as exc:
            logger.exception("End-of-day reconcile failed")
            return False, f"日终对账异常: {exc}"

    def _execute_planned_order(self, item: PlannedOrder) -> ExecutionResult:
        decision = item.decision_payload
        risk = item.risk_payload
        from .trade_decision_models import TradeDecision, RiskCheckResult, RiskCheckItem

        decision_obj = TradeDecision.from_dict(decision)
        risk_obj = None
        if risk:
            risk_obj = RiskCheckResult(
                passed=bool(risk.get("passed", False)),
                overall_risk_level=str(risk.get("overall_risk_level", "low")),
                warnings=list(risk.get("warnings", []) or []),
                blocked_reasons=list(risk.get("blocked_reasons", []) or []),
                checks=[RiskCheckItem(**c) for c in list(risk.get("checks", []) or [])],
            )
        request = ExecutionRequest(
            stock_code=item.symbol_code,
            stock_name=item.symbol_name,
            order_type=23 if item.action in (TradeAction.BUY.value, TradeAction.ADD.value) else 24,
            order_volume=item.planned_volume,
            price_type=5,
            price=item.price,
            source="ai_agent",
            trigger="auto",
            strategy_name="AI_Daily_AutoTrade",
            remark=item.reason or "每日自动交易",
            decision=decision_obj,
            risk_result=risk_obj,
            decision_record_id=item.decision_record_id,
            require_approval=False,
            approved=True,
        )
        return self.execution_service.execute(request)

    def _build_daily_plan(
        self,
        scan_results: List[Dict[str, Any]],
        broker_context: BrokerContext,
        cfg: AutoTradeConfig,
    ) -> List[PlannedOrder]:
        positions = {self._plain_code(item.get("code", "")) for item in broker_context.top_positions}
        sell_candidates: List[PlannedOrder] = []
        buy_candidates: List[PlannedOrder] = []
        tradable_cash = self._calc_tradable_cash(broker_context, cfg)

        for result in scan_results:
            decision = result.get("decision")
            risk_result = result.get("risk_result")
            if decision is None or risk_result is None or not getattr(risk_result, "passed", False):
                continue
            if not getattr(decision, "is_actionable", False):
                continue
            code = self._plain_code(getattr(decision, "symbol_code", "") or result.get("symbol_code", ""))
            name = getattr(decision, "symbol_name", "") or result.get("symbol_name", code)
            price = float(getattr(decision, "current_price", 0) or 0)
            if not code or price <= 0:
                continue

            decision_record_id = str(result.get("decision_record_id", "") or "")
            priority = float(getattr(decision, "confidence", 0) or 0) - float(getattr(decision, "risk_score", 0) or 0) * 0.2
            action = getattr(decision, "action", "")
            if action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
                volume = self.execution_service.estimate_volume_for_decision(decision)
                if volume <= 0:
                    continue
                sell_candidates.append(
                    PlannedOrder(
                        symbol_code=code,
                        symbol_name=name,
                        action=action,
                        priority=priority + 1.0,
                        planned_volume=volume,
                        price=price,
                        decision_record_id=decision_record_id,
                        reason="卖出类信号优先执行",
                        decision_payload=decision.to_dict(),
                        risk_payload=risk_result.to_dict(),
                    )
                )
                continue

            buy_candidates.append(
                PlannedOrder(
                    symbol_code=code,
                    symbol_name=name,
                    action=action,
                    priority=priority,
                    planned_volume=0,
                    price=price,
                    decision_record_id=decision_record_id,
                    reason="买入候选",
                    decision_payload=decision.to_dict(),
                    risk_payload=risk_result.to_dict(),
                )
            )

        sell_candidates.sort(key=lambda item: item.priority, reverse=True)
        buy_candidates.sort(key=lambda item: item.priority, reverse=True)

        planned: List[PlannedOrder] = list(sell_candidates[: cfg.max_sell_orders_per_day])
        if tradable_cash <= 0 or cfg.max_buy_orders_per_day <= 0 or cfg.max_new_positions_per_day <= 0:
            return planned

        chosen_buys: List[PlannedOrder] = []
        new_position_count = 0
        for item in buy_candidates:
            is_new = item.symbol_code not in positions
            if is_new and new_position_count >= cfg.max_new_positions_per_day:
                continue
            if len(chosen_buys) >= cfg.max_buy_orders_per_day:
                break
            chosen_buys.append(item)
            if is_new:
                new_position_count += 1

        remaining_cash = tradable_cash
        remaining_slots = len(chosen_buys)
        for item in chosen_buys:
            if remaining_slots <= 0:
                break
            decision_position_pct = float(item.decision_payload.get("position_pct", 0.1) or 0.1)
            target_cash = min(
                remaining_cash / remaining_slots,
                max(0.0, broker_context.total_asset * decision_position_pct),
            )
            volume = int(target_cash / max(item.price, 0.01) / 100) * 100
            if volume <= 0:
                remaining_slots -= 1
                continue
            item.planned_volume = volume
            item.reason = f"自动分配 {volume} 股"
            planned.append(item)
            remaining_cash -= volume * item.price
            remaining_slots -= 1

        planned.sort(key=lambda item: (0 if item.action in (TradeAction.SELL.value, TradeAction.REDUCE.value) else 1, -item.priority))
        return planned

    def _check_daily_guard(self, cfg: AutoTradeConfig, broker_context: BrokerContext) -> str:
        if not self.broker_service.is_connected:
            return "券商未连接，停止自动交易"
        state = self._get_today_state()
        if int(state.get("failed_orders", 0) or 0) >= cfg.max_intraday_failures:
            return "今日失败次数已达上限，停止自动交易"
        if broker_context.total_asset > 0:
            today_str = datetime.now().strftime("%Y-%m-%d")
            prev = self.trade_service.get_previous_pnl_snapshot(today_str)
            if prev and prev.total_asset > 0:
                expected_snapshot_date = self._latest_expected_snapshot_date()
                if prev.snapshot_date != expected_snapshot_date:
                    logger.warning(
                        "跳过单日熔断检查: 基准快照过旧，期望=%s，实际=%s",
                        expected_snapshot_date,
                        prev.snapshot_date,
                    )
                    return ""
                pnl_pct = (broker_context.total_asset - prev.total_asset) / prev.total_asset
                if pnl_pct <= -abs(cfg.max_daily_loss_pct):
                    return f"触发单日熔断，当前收益率 {pnl_pct:.2%}"
        return ""

    @staticmethod
    def _latest_expected_snapshot_date() -> str:
        expected = date.today() - timedelta(days=1)
        while expected.weekday() >= 5:
            expected -= timedelta(days=1)
        return expected.strftime("%Y-%m-%d")

    def _format_guard_skip_message(self, guard_error: str) -> str:
        if "熔断" in guard_error:
            return f"因风控熔断跳过自动执行: {guard_error}"
        return f"因风控规则跳过自动执行: {guard_error}"

    def _summarize_scan_results(self, scan_results: List[Dict[str, Any]]) -> Dict[str, int]:
        total = len(scan_results)
        actionable = 0
        hold_like = 0
        risk_blocked = 0
        for result in scan_results:
            decision = result.get("decision")
            risk_result = result.get("risk_result")
            if risk_result is not None and not getattr(risk_result, "passed", False):
                risk_blocked += 1
            action = str(getattr(decision, "action", "") or "").lower()
            is_actionable = bool(getattr(decision, "is_actionable", False)) if decision is not None else False
            if is_actionable:
                actionable += 1
            elif action in {"hold", "watch", "observe", "观望", "持有", ""}:
                hold_like += 1
        return {
            "total": total,
            "actionable": actionable,
            "hold_like": hold_like,
            "risk_blocked": risk_blocked,
        }

    def _record_execution_result(self, task_id: str, result: ExecutionResult) -> None:
        state = self._get_task_state(task_id)
        executions = list(state.get("executions", []) or [])
        executions.append({
            "time": self._now(),
            "success": result.success,
            "message": result.message,
            "broker_order_id": result.broker_order_id,
            "mode": result.execution_mode,
        })
        failed_orders = int(state.get("failed_orders", 0) or 0) + (0 if result.success else 1)
        success_orders = int(state.get("success_orders", 0) or 0) + (1 if result.success else 0)
        self._update_task_state(
            task_id,
            executions=executions,
            failed_orders=failed_orders,
            success_orders=success_orders,
        )

    def _calc_tradable_cash(self, broker_context: BrokerContext, cfg: AutoTradeConfig) -> float:
        reserve_target = max(broker_context.total_asset * cfg.reserve_cash_pct, 0.0)
        return max(0.0, broker_context.available_cash - reserve_target)

    def _load_broker_context(self) -> BrokerContext:
        try:
            asset = self.broker_service.query_stock_asset()
            positions = self.broker_service.query_stock_positions() or []
            top = [
                {"code": getattr(p, "stock_code", ""), "volume": int(getattr(p, "volume", 0) or 0)}
                for p in positions
                if int(getattr(p, "volume", 0) or 0) > 0
            ]
            return BrokerContext(
                connected=True,
                total_asset=float(getattr(asset, "total_asset", 0) or 0),
                available_cash=float(getattr(asset, "cash", 0) or 0),
                position_count=len(top),
                top_positions=top,
            )
        except Exception:
            return BrokerContext(connected=self.broker_service.is_connected)

    def _schedule_next_reconcile(self) -> None:
        cfg = self.config_service.get_config()
        if not cfg.auto_reconcile_enabled:
            self._reconcile_timer.stop()
            return
        try:
            hour, minute = map(int, cfg.reconcile_time.split(":"))
        except Exception:
            hour, minute = 15, 10
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        self._reconcile_timer.start(max(int((target - now).total_seconds() * 1000), 1000))

    def _on_reconcile_timer(self) -> None:
        success, message = self.run_end_of_day_reconcile()
        self.reconcile_finished.emit(success, message)
        self._schedule_next_reconcile()

    def _mark_reconciled_today(self) -> None:
        state = self._load_state()
        today = self._today()
        state.setdefault(today, {})
        state[today]["reconciled_at"] = self._now()
        self._save_state(state)

    def _load_state(self) -> Dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text("utf-8"))
        except Exception:
            return {}

    def _save_state(self, data: Dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_today_state(self) -> Dict[str, Any]:
        data = self._load_state()
        return dict(data.get(self._today(), {}) or {})

    def _get_task_state(self, task_id: str) -> Dict[str, Any]:
        state = self._get_today_state()
        return dict(state.get(task_id, {}) or {})

    def _update_task_state(self, task_id: str, **fields) -> None:
        data = self._load_state()
        today = self._today()
        day = dict(data.get(today, {}) or {})
        task_state = dict(day.get(task_id, {}) or {})
        task_state.update(fields)
        day[task_id] = task_state
        data[today] = day
        self._save_state(data)

    @staticmethod
    def _plain_code(code: str) -> str:
        value = (code or "").strip().upper()
        return value.split(".")[0] if "." in value else value

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _is_running_state_stale(state: Dict[str, Any]) -> bool:
        started_at = str(state.get("started_at", "") or "").strip()
        if not started_at:
            return True
        try:
            started_dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return True
        return datetime.now() - started_dt >= _RUNNING_STATE_STALE_AFTER


_daily_auto_trade_service: Optional[DailyAutoTradeService] = None


def get_daily_auto_trade_service() -> DailyAutoTradeService:
    global _daily_auto_trade_service
    if _daily_auto_trade_service is None:
        _daily_auto_trade_service = DailyAutoTradeService()
    return _daily_auto_trade_service
