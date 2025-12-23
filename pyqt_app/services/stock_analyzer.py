"""
Stock Analyzer Service Module

This module provides AI-powered stock analysis functionality.
It loads analysis guidelines, formats K-line data, and generates analysis prompts.
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import pandas as pd

logger = logging.getLogger(__name__)

# Default paths
CONFIG_DIR = Path(__file__).parent.parent / "config"
ANALYSIS_RESULTS_DIR = Path(__file__).parent.parent / "analysis_results"
DEFAULT_GUIDE_FILE = CONFIG_DIR / "stock_analysis_guide.md"


class StockAnalyzer:
    """Stock analysis service using AI"""
    
    def __init__(self, guide_path: Optional[str] = None):
        """
        Initialize the stock analyzer.
        
        Args:
            guide_path: Path to the analysis guide file. Uses default if not provided.
        """
        self.guide_path = Path(guide_path) if guide_path else DEFAULT_GUIDE_FILE
        self._ensure_directories()
        self._guide_content: Optional[str] = None
    
    def _ensure_directories(self):
        """Ensure required directories exist"""
        ANALYSIS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    def load_guide(self) -> str:
        """
        Load the analysis guide file.
        
        Returns:
            The content of the guide file.
        """
        if self._guide_content is not None:
            return self._guide_content
        
        if not self.guide_path.exists():
            logger.warning(f"Guide file not found: {self.guide_path}")
            self._guide_content = self._get_default_guide()
        else:
            try:
                with open(self.guide_path, 'r', encoding='utf-8') as f:
                    self._guide_content = f.read()
            except Exception as e:
                logger.error(f"Failed to load guide file: {e}")
                self._guide_content = self._get_default_guide()
        
        return self._guide_content
    
    def _get_default_guide(self) -> str:
        """Get default guide content when file is not available"""
        return """
# 股票技术分析基本指导

## 分析要点
1. 观察趋势方向（上升/下降/横盘）
2. 识别关键支撑位和压力位
3. 分析成交量变化
4. 结合MACD/KDJ等技术指标
5. 关注异常放量情况

## 注意事项
- 综合多个指标进行判断
- 关注量价配合关系
- 识别主力资金动向
"""
    
    def reload_guide(self):
        """Force reload the guide file"""
        self._guide_content = None
        return self.load_guide()
    
    def format_kline_data(
        self, 
        df: pd.DataFrame, 
        stock_code: str,
        stock_name: str,
        max_days: int = 120,
        include_indicators: bool = True
    ) -> str:
        """
        Format K-line DataFrame into text for AI analysis.
        
        Args:
            df: K-line DataFrame with columns like date, open, high, low, close, volume
            stock_code: Stock code
            stock_name: Stock name
            max_days: Maximum number of days to include (most recent)
            include_indicators: Whether to include technical indicators
        
        Returns:
            Formatted text representation of K-line data
        """
        if df is None or df.empty:
            return "无可用的K线数据"
        
        # Use most recent data
        df = df.tail(max_days).copy()
        
        # Basic info
        lines = [
            f"# 股票: {stock_name} ({stock_code})",
            f"# 数据范围: 最近 {len(df)} 个交易日",
            ""
        ]
        
        # Statistics summary
        if len(df) > 0:
            latest = df.iloc[-1]
            earliest = df.iloc[0]
            
            # Calculate key metrics
            price_change = ((latest['close'] - earliest['close']) / earliest['close'] * 100) if earliest['close'] > 0 else 0
            highest = df['high'].max()
            lowest = df['low'].min()
            avg_volume = df['volume'].mean()
            
            lines.extend([
                "## 数据统计摘要",
                f"- 期间涨跌幅: {price_change:.2f}%",
                f"- 最高价: {highest:.2f}",
                f"- 最低价: {lowest:.2f}",
                f"- 最新收盘价: {latest['close']:.2f}",
                f"- 日均成交量: {avg_volume:,.0f}",
                ""
            ])
            
            # Recent trend
            if len(df) >= 5:
                recent_5 = df.tail(5)
                recent_change = ((recent_5.iloc[-1]['close'] - recent_5.iloc[0]['close']) / recent_5.iloc[0]['close'] * 100)
                lines.append(f"- 近5日涨跌幅: {recent_change:.2f}%")
            
            if len(df) >= 20:
                recent_20 = df.tail(20)
                recent_change = ((recent_20.iloc[-1]['close'] - recent_20.iloc[0]['close']) / recent_20.iloc[0]['close'] * 100)
                lines.append(f"- 近20日涨跌幅: {recent_change:.2f}%")
            
            lines.append("")
        
        # Detailed K-line data (most recent 60 days for detailed view)
        detail_df = df.tail(60).copy()
        
        lines.extend([
            "## 详细K线数据（最近60个交易日）",
            ""
        ])
        
        # Determine columns to include
        basic_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        indicator_cols = []
        
        if include_indicators:
            # MA columns
            for ma in [5, 10, 20, 60]:
                col = f'MA{ma}'
                if col in df.columns:
                    indicator_cols.append(col)
            
            # MACD columns
            for col in ['DIF', 'DEA', 'MACD']:
                if col in df.columns:
                    indicator_cols.append(col)
            
            # KDJ columns
            for col in ['K', 'D', 'J']:
                if col in df.columns:
                    indicator_cols.append(col)
        
        # Available columns
        all_cols = basic_cols + indicator_cols
        available_cols = [c for c in all_cols if c in detail_df.columns]
        
        # Format header
        header = "| " + " | ".join(available_cols) + " |"
        separator = "| " + " | ".join(["---"] * len(available_cols)) + " |"
        lines.extend([header, separator])
        
        # Format data rows
        for _, row in detail_df.iterrows():
            values = []
            for col in available_cols:
                val = row[col]
                if col == 'date':
                    if hasattr(val, 'strftime'):
                        val = val.strftime('%Y-%m-%d')
                    else:
                        val = str(val)
                elif col == 'volume':
                    val = f"{val:,.0f}"
                elif pd.notna(val):
                    val = f"{val:.2f}"
                else:
                    val = "-"
                values.append(val)
            lines.append("| " + " | ".join(values) + " |")
        
        return "\n".join(lines)
    
    def build_analysis_prompt(
        self,
        kline_text: str,
        stock_code: str,
        stock_name: str,
        custom_instructions: Optional[str] = None
    ) -> str:
        """
        Build the analysis prompt for AI.
        
        Args:
            kline_text: Formatted K-line data text
            stock_code: Stock code
            stock_name: Stock name
            custom_instructions: Additional custom instructions
        
        Returns:
            Complete prompt for AI analysis
        """
        guide = self.load_guide()
        
        prompt_parts = [
            "你是一位专业的股票技术分析师。请根据以下分析指导手册和K线数据，对股票进行全面的技术分析。",
            "",
            "=" * 50,
            "【分析指导手册】",
            "=" * 50,
            guide,
            "",
            "=" * 50,
            "【K线数据】",
            "=" * 50,
            kline_text,
            "",
        ]
        
        if custom_instructions:
            prompt_parts.extend([
                "=" * 50,
                "【额外要求】",
                "=" * 50,
                custom_instructions,
                ""
            ])
        
        prompt_parts.extend([
            "=" * 50,
            "【分析任务】",
            "=" * 50,
            f"请对 {stock_name}({stock_code}) 进行全面的技术分析，包括：",
            "",
            "1. **趋势判断**: 当前处于什么趋势？趋势强度如何？",
            "2. **量价分析**: 近期成交量有什么特点？是否出现爆量？量价关系如何？",
            "3. **主力行为**: 根据K线形态和量能，推测主力可能在做什么（吸筹/出货/洗盘）？",
            "4. **关键位置**: 当前重要的支撑位和压力位在哪里？",
            "5. **形态识别**: 是否存在箱体结构、三角形整理或其他典型形态？",
            "6. **指标分析**: MACD和KDJ指标显示什么信号？是否存在背离？",
            "7. **风险提示**: 当前存在哪些风险点需要注意？",
            "8. **操作建议**: 给出明确的操作建议（买入/持有/卖出/观望）和理由。",
            "",
            "请用中文回答，分析要有理有据，结论要明确。"
        ])
        
        return "\n".join(prompt_parts)
    
    def save_analysis_result(
        self,
        stock_code: str,
        stock_name: str,
        analysis_result: str,
        kline_summary: Optional[str] = None
    ) -> str:
        """
        Save analysis result to a file.
        
        Args:
            stock_code: Stock code
            stock_name: Stock name  
            analysis_result: The analysis text from AI
            kline_summary: Optional summary of K-line data
        
        Returns:
            Path to the saved file
        """
        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y%m%d")
        time_str = timestamp.strftime("%H%M%S")
        
        # Create filename
        filename = f"{stock_code}_{date_str}_{time_str}.md"
        filepath = ANALYSIS_RESULTS_DIR / filename
        
        # Build content
        content_parts = [
            f"# {stock_name} ({stock_code}) 技术分析报告",
            "",
            f"**分析时间**: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            ""
        ]
        
        if kline_summary:
            content_parts.extend([
                "## 数据概要",
                "",
                kline_summary,
                "",
                "---",
                ""
            ])
        
        content_parts.extend([
            "## 分析结果",
            "",
            analysis_result
        ])
        
        content = "\n".join(content_parts)
        
        # Save file
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Analysis result saved to: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to save analysis result: {e}")
            raise
    
    def get_analysis_history(self, stock_code: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get list of historical analysis records.
        
        Args:
            stock_code: Filter by stock code (optional)
            limit: Maximum number of records to return
        
        Returns:
            List of analysis record info dicts
        """
        records = []
        
        if not ANALYSIS_RESULTS_DIR.exists():
            return records
        
        for filepath in ANALYSIS_RESULTS_DIR.glob("*.md"):
            try:
                # Parse filename: {stock_code}_{date}_{time}.md
                name_parts = filepath.stem.split("_")
                if len(name_parts) >= 3:
                    file_code = name_parts[0]
                    file_date = name_parts[1]
                    file_time = name_parts[2]
                    
                    # Filter by stock code if specified
                    if stock_code and file_code != stock_code:
                        continue
                    
                    # Parse datetime
                    try:
                        dt = datetime.strptime(f"{file_date}{file_time}", "%Y%m%d%H%M%S")
                    except:
                        dt = datetime.fromtimestamp(filepath.stat().st_mtime)
                    
                    # Read first few lines to get stock name
                    stock_name = ""
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            first_line = f.readline().strip()
                            if first_line.startswith("# "):
                                # Extract stock name from "# 股票名 (代码) 技术分析报告"
                                import re
                                match = re.match(r"# (.+?) \(", first_line)
                                if match:
                                    stock_name = match.group(1)
                    except:
                        pass
                    
                    records.append({
                        "filepath": str(filepath),
                        "stock_code": file_code,
                        "stock_name": stock_name,
                        "datetime": dt,
                        "datetime_str": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "filename": filepath.name
                    })
            except Exception as e:
                logger.debug(f"Error parsing analysis file {filepath}: {e}")
                continue
        
        # Sort by datetime descending
        records.sort(key=lambda x: x["datetime"], reverse=True)
        
        return records[:limit]
    
    def read_analysis_result(self, filepath: str) -> Optional[str]:
        """
        Read an analysis result file.
        
        Args:
            filepath: Path to the analysis file
        
        Returns:
            Content of the file, or None if failed
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read analysis file {filepath}: {e}")
            return None
    
    def delete_analysis_result(self, filepath: str) -> bool:
        """
        Delete an analysis result file.
        
        Args:
            filepath: Path to the analysis file
        
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            Path(filepath).unlink()
            logger.info(f"Deleted analysis file: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete analysis file {filepath}: {e}")
            return False


# Singleton instance
_analyzer_instance: Optional[StockAnalyzer] = None

def get_analyzer() -> StockAnalyzer:
    """Get the singleton StockAnalyzer instance"""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = StockAnalyzer()
    return _analyzer_instance
