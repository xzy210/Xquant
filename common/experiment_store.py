# -*- coding: utf-8 -*-
"""Persistent experiment records for backtest runs."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is optional for serialization
    np = None

try:
    import pandas as pd
except Exception:  # pragma: no cover - pandas is optional for serialization
    pd = None


@dataclass(frozen=True)
class ExperimentRecord:
    """Metadata row stored in the experiment index."""

    run_id: str
    strategy_id: str
    params_hash: str
    data_version: str = ""
    strategy_version: str = ""
    engine_version: str = ""
    code_commit: str = ""
    mode: str = ""
    final_value: float | None = None
    created_at: str = ""
    path: str = ""
    schema_version: str = "experiment_record.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExperimentStore:
    """File-backed store for reproducible backtest experiments."""

    schema_version = "experiment_store.v1"

    def __init__(self, root_dir: str | os.PathLike[str] = "experiments") -> None:
        self.root_dir = Path(root_dir)
        self.index_path = self.root_dir / "index.json"

    def save(self, result: Any, events: Iterable[Any] | None = None, params: Any | None = None) -> ExperimentRecord:
        """Persist a backtest result with events and params under experiments/<run_id>/."""
        result_payload = self._result_to_payload(result)
        params_payload = self._to_jsonable({} if params is None else params)
        events_payload = [self._event_to_payload(event) for event in (events or [])]

        run_id = self._pick_text(result_payload, "run_id") or uuid4().hex
        strategy_id = self._pick_text(result_payload, "strategy_id")
        params_hash = self._pick_text(result_payload, "params_hash")
        data_version = self._pick_text(result_payload, "data_version")
        strategy_version = self._pick_text(result_payload, "strategy_version")
        engine_version = self._pick_text(result_payload, "engine_version")
        code_commit = self._pick_text(result_payload, "code_commit")
        mode = self._pick_text(result_payload, "mode")
        final_value = self._pick_float(result_payload.get("final_value"))
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        run_dir = self.root_dir / run_id
        self._ensure_root()
        run_dir.mkdir(parents=True, exist_ok=True)

        record = ExperimentRecord(
            run_id=run_id,
            strategy_id=strategy_id,
            params_hash=params_hash,
            data_version=data_version,
            strategy_version=strategy_version,
            engine_version=engine_version,
            code_commit=code_commit,
            mode=mode,
            final_value=final_value,
            created_at=created_at,
            path=str(run_dir),
        )

        metadata = {
            "schema_version": self.schema_version,
            "record": record.to_dict(),
            "files": {
                "result": "result.json",
                "params": "params.json",
                "events": "events.jsonl",
            },
        }

        self._write_json(run_dir / "result.json", result_payload)
        self._write_json(run_dir / "params.json", params_payload)
        self._write_json(run_dir / "metadata.json", metadata)
        self._write_jsonl(run_dir / "events.jsonl", events_payload)
        self._upsert_index(record)
        return record

    def query(self, strategy_id: str | None = None, params_hash: str | None = None) -> list[ExperimentRecord]:
        """Return records matching strategy_id and params_hash, newest first."""
        records = self._load_index()
        if strategy_id:
            records = [record for record in records if record.strategy_id == strategy_id]
        if params_hash:
            records = [record for record in records if record.params_hash == params_hash]
        return sorted(records, key=lambda item: item.created_at or "", reverse=True)

    def get(self, run_id: str) -> ExperimentRecord | None:
        """Return one record by run id."""
        for record in self._load_index():
            if record.run_id == run_id:
                return record
        metadata_path = self.root_dir / run_id / "metadata.json"
        if not metadata_path.exists():
            return None
        payload = self._read_json(metadata_path)
        return self._record_from_payload(payload.get("record", {}))

    def load_result(self, run_id: str) -> dict[str, Any]:
        return self._read_json(self.root_dir / run_id / "result.json")

    def load_params(self, run_id: str) -> Any:
        return self._read_json(self.root_dir / run_id / "params.json")

    def load_events(self, run_id: str) -> list[dict[str, Any]]:
        events_path = self.root_dir / run_id / "events.jsonl"
        if not events_path.exists():
            return []
        with events_path.open("r", encoding="utf-8") as file_obj:
            return [json.loads(line) for line in file_obj if line.strip()]

    def rebuild_index(self) -> list[ExperimentRecord]:
        """Rebuild index.json by scanning metadata files."""
        self._ensure_root()
        records: list[ExperimentRecord] = []
        for metadata_path in sorted(self.root_dir.glob("*/metadata.json")):
            payload = self._read_json(metadata_path)
            record = self._record_from_payload(payload.get("record", {}))
            if record is not None:
                records.append(record)
        self._write_index(records)
        return records

    def _ensure_root(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _upsert_index(self, record: ExperimentRecord) -> None:
        records = [item for item in self._load_index() if item.run_id != record.run_id]
        records.append(record)
        self._write_index(records)

    def _load_index(self) -> list[ExperimentRecord]:
        if not self.index_path.exists():
            return []
        payload = self._read_json(self.index_path)
        rows = payload.get("records", []) if isinstance(payload, dict) else []
        records: list[ExperimentRecord] = []
        for row in rows:
            record = self._record_from_payload(row)
            if record is not None:
                records.append(record)
        return records

    def _write_index(self, records: list[ExperimentRecord]) -> None:
        payload = {
            "schema_version": self.schema_version,
            "records": [record.to_dict() for record in sorted(records, key=lambda item: item.created_at or "")],
        }
        self._write_json(self.index_path, payload)

    @classmethod
    def _record_from_payload(cls, payload: Any) -> ExperimentRecord | None:
        if not isinstance(payload, dict):
            return None
        run_id = str(payload.get("run_id", "") or "")
        if not run_id:
            return None
        return ExperimentRecord(
            run_id=run_id,
            strategy_id=str(payload.get("strategy_id", "") or ""),
            params_hash=str(payload.get("params_hash", "") or ""),
            data_version=str(payload.get("data_version", "") or ""),
            strategy_version=str(payload.get("strategy_version", "") or ""),
            engine_version=str(payload.get("engine_version", "") or ""),
            code_commit=str(payload.get("code_commit", "") or ""),
            mode=str(payload.get("mode", "") or ""),
            final_value=cls._pick_float(payload.get("final_value")),
            created_at=str(payload.get("created_at", "") or ""),
            path=str(payload.get("path", "") or ""),
            schema_version=str(payload.get("schema_version", "experiment_record.v1") or "experiment_record.v1"),
        )

    @classmethod
    def _result_to_payload(cls, result: Any) -> dict[str, Any]:
        if hasattr(result, "to_serializable_dict") and callable(result.to_serializable_dict):
            payload = result.to_serializable_dict()
        elif hasattr(result, "to_dict") and callable(result.to_dict):
            payload = result.to_dict()
            if isinstance(payload, dict) and isinstance(payload.get("serializable_result"), dict):
                payload = payload["serializable_result"]
        else:
            payload = result
        payload = cls._to_jsonable(payload)
        if not isinstance(payload, dict):
            raise TypeError("result must be a BacktestResult-like object or dict")
        return payload

    @classmethod
    def _event_to_payload(cls, event: Any) -> dict[str, Any]:
        if hasattr(event, "__dataclass_fields__"):
            payload = {key: getattr(event, key) for key in event.__dataclass_fields__}
        elif isinstance(event, dict):
            payload = event
        else:
            payload = cls._to_jsonable(event)
        payload = cls._to_jsonable(payload)
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return payload

    @classmethod
    def _to_jsonable(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if pd is not None:
            if isinstance(value, pd.DataFrame):
                return cls._to_jsonable(value.to_dict(orient="records"))
            if isinstance(value, pd.Series):
                return cls._to_jsonable(value.to_dict())
            if isinstance(value, pd.Timestamp):
                return value.isoformat()
            if value is pd.NaT:
                return None
        if np is not None:
            if isinstance(value, np.generic):
                return cls._to_jsonable(value.item())
            if isinstance(value, np.ndarray):
                return cls._to_jsonable(value.tolist())
        if is_dataclass(value):
            return cls._to_jsonable(asdict(value))
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return cls._to_jsonable(value.to_dict())
        if isinstance(value, dict):
            return {str(key): cls._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._to_jsonable(item) for item in value]
        if hasattr(value, "item") and callable(value.item):
            try:
                return cls._to_jsonable(value.item())
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _pick_text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key, "")
        return str(value or "")

    @staticmethod
    def _pick_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _read_json(cls, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    @classmethod
    def _write_json(cls, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(cls._to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True)
        cls._atomic_write_text(path, text + "\n")

    @classmethod
    def _write_jsonl(cls, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(cls._to_jsonable(row), ensure_ascii=False, sort_keys=True) for row in rows]
        cls._atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file_obj:
            tmp_name = file_obj.name
            file_obj.write(text)
        os.replace(tmp_name, path)


__all__ = ["ExperimentRecord", "ExperimentStore"]
