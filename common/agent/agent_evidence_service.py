from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = PROJECT_ROOT / "data" / "agent_evidence"
EVIDENCE_RUNS_DIR = EVIDENCE_ROOT / "runs"
EVIDENCE_INDEX_FILE = EVIDENCE_ROOT / "evidence_index.jsonl"
DEFAULT_MAX_RUN_DAYS = 30
DEFAULT_MAX_RUN_FILES = 500
DEFAULT_TEMP_IMAGE_HOURS = 24
TEMP_KLINE_PREFIX = "stocktradebyz_agent_kline_"
TEMP_PASTED_PREFIX = "stocktradebyz_agent_pasted_"
_EVIDENCE_IO_LOCK = threading.RLock()


logger = logging.getLogger(__name__)


def _slugify(text: str, fallback: str = "run") -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text or "").strip("-")
    return normalized[:48] or fallback


@dataclass
class EvidenceItem:
    tool_name: str
    title: str
    summary: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    run_id: str
    task_mode: str
    user_input: str
    created_at: str
    context_summary: List[str] = field(default_factory=list)
    items: List[EvidenceItem] = field(default_factory=list)


class AgentEvidenceService:
    """Persist the tool evidence used by each agent request."""

    def __init__(
        self,
        root_dir: Path | None = None,
        *,
        max_run_days: int = DEFAULT_MAX_RUN_DAYS,
        max_run_files: int = DEFAULT_MAX_RUN_FILES,
        temp_image_hours: int = DEFAULT_TEMP_IMAGE_HOURS,
    ):
        self.root_dir = Path(root_dir) if root_dir else EVIDENCE_ROOT
        self.runs_dir = self.root_dir / "runs"
        self.index_file = self.root_dir / "evidence_index.jsonl"
        self.max_run_days = max(1, int(max_run_days))
        self.max_run_files = max(1, int(max_run_files))
        self.temp_image_hours = max(1, int(temp_image_hours))
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        with _EVIDENCE_IO_LOCK:
            self.cleanup_old_artifacts()

    def save_bundle(self, bundle: EvidenceBundle) -> str:
        with _EVIDENCE_IO_LOCK:
            self.cleanup_old_artifacts()
            report_path = self.runs_dir / self._build_filename(bundle)
            report_path.write_text(self._render_markdown(bundle), encoding="utf-8")
            self._append_index(bundle, report_path)
            return str(report_path)

    def _build_filename(self, bundle: EvidenceBundle) -> str:
        ts = bundle.created_at.replace(":", "").replace("-", "").replace("T", "_")
        return f"{ts}_{_slugify(bundle.task_mode)}_{bundle.run_id}.md"

    def _append_index(self, bundle: EvidenceBundle, report_path: Path) -> None:
        record = {
            "run_id": bundle.run_id,
            "task_mode": bundle.task_mode,
            "created_at": bundle.created_at,
            "user_input": bundle.user_input,
            "context_summary": bundle.context_summary,
            "items": [asdict(item) for item in bundle.items],
            "report_path": str(report_path),
        }
        with self.index_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _render_markdown(self, bundle: EvidenceBundle) -> str:
        lines = [
            "# 股票智能体证据记录",
            "",
            f"- 运行ID: `{bundle.run_id}`",
            f"- 任务模式: `{bundle.task_mode}`",
            f"- 生成时间: `{bundle.created_at}`",
            "",
            "## 用户输入",
            "",
            bundle.user_input or "(空)",
            "",
        ]

        if bundle.context_summary:
            lines.extend([
                "## 运行上下文",
                "",
                *[f"- {line}" for line in bundle.context_summary],
                "",
            ])

        if not bundle.items:
            lines.extend(["## 证据项", "", "- 本次未生成额外证据。", ""])
            return "\n".join(lines)

        lines.extend(["## 证据项", ""])
        for idx, item in enumerate(bundle.items, start=1):
            lines.extend([
                f"### E{idx} {item.title}",
                "",
                f"- 工具: `{item.tool_name}`",
                f"- 摘要: {item.summary}",
            ])
            if item.metadata:
                lines.append(f"- 元数据: `{json.dumps(item.metadata, ensure_ascii=False)}`")
            lines.extend(["", item.content.strip() or "(无内容)", ""])
        return "\n".join(lines)

    @staticmethod
    def now_iso() -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    def cleanup_old_artifacts(self) -> None:
        try:
            self._cleanup_evidence_runs()
        except Exception as exc:
            logger.warning("Failed to cleanup evidence runs: %s", exc)
        try:
            self._cleanup_temp_images()
        except Exception as exc:
            logger.warning("Failed to cleanup temporary images: %s", exc)

    def _cleanup_evidence_runs(self) -> None:
        records = self._load_index_records()
        cutoff = datetime.now() - timedelta(days=self.max_run_days)
        sorted_records = sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)

        kept_records: List[Dict[str, Any]] = []
        removed_paths: List[Path] = []
        for record in sorted_records:
            report_path = Path(str(record.get("report_path", "")).strip())
            created_at = self._parse_iso(record.get("created_at", ""))
            if created_at is None and report_path.exists():
                created_at = self._safe_mtime(report_path)
            is_within_age = created_at is not None and created_at >= cutoff
            if report_path.exists() and is_within_age and len(kept_records) < self.max_run_files:
                kept_records.append(record)
            else:
                if report_path.exists():
                    removed_paths.append(report_path)

        indexed_paths = {Path(str(record.get("report_path", "")).strip()) for record in records}
        for run_file in self.runs_dir.glob("*.md"):
            if run_file not in indexed_paths and self._safe_mtime(run_file) < cutoff:
                removed_paths.append(run_file)

        for file_path in {path for path in removed_paths if path.exists()}:
            try:
                file_path.unlink()
            except OSError:
                logger.debug("Skip deleting evidence file: %s", file_path)

        self._rewrite_index(sorted(kept_records, key=lambda item: item.get("created_at", "")))

    def _cleanup_temp_images(self) -> None:
        cutoff = datetime.now() - timedelta(hours=self.temp_image_hours)
        temp_dir = Path(tempfile.gettempdir())
        for pattern in (f"{TEMP_KLINE_PREFIX}*.png", f"{TEMP_PASTED_PREFIX}*.png"):
            for file_path in temp_dir.glob(pattern):
                if self._safe_mtime(file_path) >= cutoff:
                    continue
                try:
                    file_path.unlink()
                except OSError:
                    logger.debug("Skip deleting temp image: %s", file_path)

    def _load_index_records(self) -> List[Dict[str, Any]]:
        if not self.index_file.exists():
            return []

        records: List[Dict[str, Any]] = []
        with self.index_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _rewrite_index(self, records: List[Dict[str, Any]]) -> None:
        if not records:
            if self.index_file.exists():
                self.index_file.write_text("", encoding="utf-8")
            return

        with self.index_file.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _safe_mtime(file_path: Path) -> datetime:
        try:
            return datetime.fromtimestamp(file_path.stat().st_mtime)
        except OSError:
            return datetime.min


__all__ = [
    "AgentEvidenceService",
    "EvidenceBundle",
    "EvidenceItem",
    "EVIDENCE_ROOT",
    "EVIDENCE_RUNS_DIR",
    "EVIDENCE_INDEX_FILE",
    "TEMP_KLINE_PREFIX",
    "TEMP_PASTED_PREFIX",
]
