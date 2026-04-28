from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List
from urllib.parse import quote

import pandas as pd
import requests


logger = logging.getLogger(__name__)


POSITIVE_NEWS_KEYWORDS = [
    "增长", "增持", "回购", "中标", "突破", "利好", "签约", "上调", "盈利", "分红",
]
NEGATIVE_NEWS_KEYWORDS = [
    "下滑", "减持", "处罚", "诉讼", "风险", "利空", "亏损", "违约", "问询", "下调",
]


@dataclass
class NewsSnapshotResult:
    code: str
    name: str
    provider: str = ""
    items: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class StockNewsService:
    """Fetch and format stock news snapshots for the agent."""

    def build_snapshot(
        self,
        *,
        code: str,
        name: str = "",
        lookback_days: int = 7,
        limit: int = 8,
    ) -> NewsSnapshotResult:
        code = (code or "").strip()
        name = (name or code).strip()
        if not code:
            return NewsSnapshotResult(
                code="",
                name=name,
                summary="当前没有选中的标的",
                content="- 当前上下文缺少标的代码，无法抓取消息面数据。",
            )

        df, provider, warning = self._fetch_news_dataframe(code=code, name=name)
        if df is None or df.empty:
            content_lines = ["- 暂未获取到可用的个股消息面数据。"]
            if warning:
                content_lines.append(f"- 原因: {warning}")
            content_lines.append("- 建议: 安装 `akshare` 并确认网络可访问东方财富资讯接口。")
            return NewsSnapshotResult(
                code=code,
                name=name,
                provider=provider,
                summary="未获取到个股新闻数据",
                content="\n".join(content_lines),
                metadata={"provider": provider or "none", "item_count": 0},
            )

        normalized = self._normalize_news_df(df, lookback_days=lookback_days, limit=limit)
        items = normalized.to_dict(orient="records")
        if not items:
            return NewsSnapshotResult(
                code=code,
                name=name,
                provider=provider,
                summary=f"最近 {lookback_days} 天未检索到有效消息",
                content=f"- 数据源: {provider}\n- 最近 {lookback_days} 天内没有保留下来的有效新闻。",
                metadata={"provider": provider or "none", "item_count": 0},
            )

        positive_count = sum(1 for item in items if item["sentiment"] == "偏多")
        negative_count = sum(1 for item in items if item["sentiment"] == "偏空")
        neutral_count = len(items) - positive_count - negative_count
        bias = self._resolve_news_bias(positive_count, negative_count)
        summary = (
            f"最近 {lookback_days} 天收集 {len(items)} 条消息，"
            f"偏多 {positive_count} / 偏空 {negative_count} / 中性 {neutral_count}，"
            f"整体 {bias}"
        )

        content_lines = [
            f"- 数据源: {provider}",
            f"- 时间范围: 最近 {lookback_days} 天",
            f"- 消息面倾向: {bias}",
            f"- 条数统计: 偏多 {positive_count} / 偏空 {negative_count} / 中性 {neutral_count}",
            "",
            "## 重点消息",
        ]
        for idx, item in enumerate(items, start=1):
            source = item.get("source") or "-"
            published_at = item.get("published_at") or "-"
            title = item.get("title") or "-"
            sentiment = item.get("sentiment") or "中性"
            snippet = item.get("snippet") or "无摘要"
            content_lines.extend([
                f"{idx}. [{sentiment}] {published_at} | {source} | {title}",
                f"   - 摘要: {snippet}",
            ])

        content_lines.extend([
            "",
            "## 使用建议",
            "- 请结合这些新闻的时效性判断催化是否仍然有效。",
            "- 若消息面与技术面/基本面冲突，请明确指出冲突来源，不要只给单边结论。",
        ])
        return NewsSnapshotResult(
            code=code,
            name=name,
            provider=provider,
            items=items,
            summary=summary,
            content="\n".join(content_lines),
            metadata={
                "provider": provider or "none",
                "item_count": len(items),
                "positive_count": positive_count,
                "negative_count": negative_count,
                "neutral_count": neutral_count,
                "bias": bias,
            },
        )

    def _fetch_news_dataframe(self, code: str, name: str = "") -> tuple[pd.DataFrame | None, str, str]:
        try:
            import akshare as ak
        except ImportError:
            ak = None

        normalized_name = str(name or "").strip()
        search_terms = []
        if normalized_name:
            search_terms.append(normalized_name)
        if code and code not in search_terms:
            search_terms.append(code)

        # Prefer the direct Eastmoney path first. The stock env currently uses an
        # akshare version whose stock_news_em JSONP parsing is known to fail with
        # "Extra data: line 1 column 17".
        eastmoney_warnings: List[str] = []
        for term in search_terms:
            fallback_df, fallback_warning = self._fetch_news_dataframe_eastmoney_direct(term)
            if fallback_df is not None and not fallback_df.empty:
                return fallback_df, "eastmoney.direct_api", fallback_warning
            if fallback_warning:
                eastmoney_warnings.append(f"{term}: {fallback_warning}")

        if ak is not None:
            try:
                df = ak.stock_news_em(symbol=code)
                if df is not None and not df.empty:
                    return df, "akshare.stock_news_em", ""
            except Exception as exc:
                logger.warning("Failed to fetch stock news for %s: %s", code, exc)
                warning_parts = [str(exc)] if str(exc) else []
                warning_parts.extend(eastmoney_warnings)
                return None, "akshare.stock_news_em", " | ".join(part for part in warning_parts if part)
            warning_parts = ["akshare 返回空结果"]
            warning_parts.extend(eastmoney_warnings)
            return None, "akshare.stock_news_em", " | ".join(part for part in warning_parts if part)

        for term in search_terms:
            fallback_df, fallback_warning = self._fetch_news_dataframe_eastmoney_jsonp(term)
            if fallback_df is not None and not fallback_df.empty:
                return fallback_df, "eastmoney.search_api", fallback_warning
            if fallback_warning:
                eastmoney_warnings.append(f"{term}: {fallback_warning}")
        warning = " | ".join(eastmoney_warnings) if eastmoney_warnings else "akshare 未安装且东方财富 fallback 失败"
        return None, "eastmoney.search_api", warning

    def _fetch_news_dataframe_eastmoney_direct(self, keyword: str) -> tuple[pd.DataFrame | None, str]:
        url = "http://search-api-web.eastmoney.com/search/jsonp"
        inner_param = {
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 40,
                    "preTag": " ",
                    "postTag": " ",
                }
            },
        }
        params = {
            "cb": "cb",
            "param": json.dumps(inner_param, ensure_ascii=False, separators=(",", ":")),
        }
        headers = {
            "accept": "*/*",
            "referer": f"https://so.eastmoney.com/news/s?keyword={quote(keyword, safe='')}",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            match = re.search(r"^\w+\((.*)\)\s*$", response.text, flags=re.S)
            if not match:
                return None, "东方财富直连接口返回结果无法解析"
            payload = json.loads(match.group(1))
            result = payload.get("result", {}) or {}
            items = result.get("cmsArticleWebOld", []) or []
            if not items:
                available_keys = [key for key, value in result.items() if value]
                if available_keys:
                    return pd.DataFrame(), f"未命中文章结果，可用分组: {', '.join(available_keys[:5])}"
                return pd.DataFrame(), ""

            frame = pd.DataFrame(items)
            if "code" in frame.columns:
                frame["新闻链接"] = "http://finance.eastmoney.com/a/" + frame["code"].astype(str) + ".html"
            frame["关键词"] = keyword
            rename_map = {
                "title": "新闻标题",
                "content": "新闻内容",
                "date": "发布时间",
                "mediaName": "文章来源",
            }
            frame = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns})
            for column in ["新闻标题", "新闻内容"]:
                if column in frame.columns:
                    frame[column] = (
                        frame[column]
                        .astype(str)
                        .str.replace(r"\( ", "", regex=True)
                        .str.replace(r" \)", "", regex=True)
                        .str.replace(r"<em>", "", regex=True)
                        .str.replace(r"</em>", "", regex=True)
                        .str.replace(r"\u3000", "", regex=True)
                        .str.replace(r"\r\n", " ", regex=True)
                        .str.strip()
                    )
            ordered = [column for column in ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"] if column in frame.columns]
            return frame[ordered], ""
        except Exception as exc:
            logger.warning("Eastmoney direct fetch failed for %s: %s", keyword, exc)
            return None, str(exc)

    def _fetch_news_dataframe_eastmoney_jsonp(self, code: str) -> tuple[pd.DataFrame | None, str]:
        callback = f"jQuery{int(datetime.now().timestamp() * 1000)}"
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner_param = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 20,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": callback,
            "param": json.dumps(inner_param, ensure_ascii=False),
            "_": str(int(datetime.now().timestamp() * 1000)),
        }
        headers = {
            "accept": "*/*",
            "referer": "https://so.eastmoney.com/news/s",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            match = re.search(r"^[^(]+\((.*)\)\s*$", response.text, flags=re.S)
            if not match:
                return None, "东方财富返回结果无法解析"
            payload = json.loads(match.group(1))
            items = payload.get("result", {}).get("cmsArticleWebOld", []) or []
            if not items:
                return pd.DataFrame(), ""

            frame = pd.DataFrame(items)
            if "code" in frame.columns:
                frame["新闻链接"] = "https://finance.eastmoney.com/a/" + frame["code"].astype(str) + ".html"
            frame["关键词"] = code
            rename_map = {
                "title": "新闻标题",
                "content": "新闻内容",
                "date": "发布时间",
                "mediaName": "文章来源",
            }
            frame = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns})
            for column in ["新闻标题", "新闻内容"]:
                if column in frame.columns:
                    frame[column] = (
                        frame[column]
                        .astype(str)
                        .str.replace(r"</?em>", "", regex=True)
                        .str.replace(r"\\u3000", "", regex=True)
                        .str.replace(r"\\r\\n", " ", regex=True)
                    )
            ordered = [column for column in ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"] if column in frame.columns]
            return frame[ordered], "已使用东方财富 JSONP fallback"
        except Exception as exc:
            logger.warning("Eastmoney fallback failed for %s: %s", code, exc)
            return None, str(exc)

    def _normalize_news_df(
        self,
        df: pd.DataFrame,
        *,
        lookback_days: int,
        limit: int,
    ) -> pd.DataFrame:
        frame = df.copy()
        rename_map = {
            "新闻标题": "title",
            "标题": "title",
            "新闻内容": "content",
            "内容": "content",
            "发布时间": "published_at",
            "时间": "published_at",
            "文章来源": "source",
            "来源": "source",
            "新闻链接": "url",
            "链接": "url",
            "关键词": "keyword",
        }
        frame = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns})
        for column in ["title", "content", "published_at", "source", "url"]:
            if column not in frame.columns:
                frame[column] = ""

        frame["title"] = frame["title"].astype(str).str.strip()
        frame["content"] = frame["content"].astype(str).str.strip()
        frame["source"] = frame["source"].astype(str).str.strip()
        frame["url"] = frame["url"].astype(str).str.strip()
        frame["published_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
        cutoff = datetime.now() - timedelta(days=max(1, int(lookback_days)))
        frame = frame[frame["title"] != ""].copy()
        frame = frame.drop_duplicates(subset=["title"], keep="first")
        if frame["published_at"].notna().any():
            frame = frame[frame["published_at"].fillna(pd.Timestamp.max) >= cutoff]
        frame = frame.sort_values("published_at", ascending=False, na_position="last").head(max(1, int(limit)))
        frame["snippet"] = frame["content"].map(self._build_snippet)
        frame["sentiment"] = frame.apply(
            lambda row: self._classify_news_sentiment(f"{row['title']} {row['content']}"),
            axis=1,
        )
        frame["published_at"] = frame["published_at"].dt.strftime("%Y-%m-%d %H:%M").fillna("")
        return frame[["title", "snippet", "published_at", "source", "url", "sentiment"]]

    @staticmethod
    def _build_snippet(text: str, max_length: int = 90) -> str:
        text = " ".join(str(text or "").split())
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    @staticmethod
    def _classify_news_sentiment(text: str) -> str:
        source = str(text or "")
        positive_hits = sum(keyword in source for keyword in POSITIVE_NEWS_KEYWORDS)
        negative_hits = sum(keyword in source for keyword in NEGATIVE_NEWS_KEYWORDS)
        if positive_hits > negative_hits:
            return "偏多"
        if negative_hits > positive_hits:
            return "偏空"
        return "中性"

    @staticmethod
    def _resolve_news_bias(positive_count: int, negative_count: int) -> str:
        if positive_count >= negative_count + 2:
            return "偏多"
        if negative_count >= positive_count + 2:
            return "偏空"
        return "中性偏震荡"
