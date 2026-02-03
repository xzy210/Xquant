import sys
import os
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QThread, pyqtSignal

# Import from scripts directory
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from scripts import fetch_kline
    from scripts import fetch_kline_xtquant
except ImportError:
    # Direct import if scripts is in path
    sys.path.insert(0, str(project_root / "scripts"))
    import fetch_kline
    import fetch_kline_xtquant

# xtquant 延迟导入
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None

# Tushare 延迟导入（可能未安装）
try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    ts = None


class DataUpdateThread(QThread):
    """
    Background thread for updating stock data.
    
    支持多数据源：
    - tushare: 使用 Tushare API（需要 token）
    - xtquant: 使用 xtquant/miniQMT（需要本地运行 miniQMT）
    
    支持多周期：
    - 1d: 日线
    - 1m/5m/15m/30m/60m: 分钟线
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(
        self,
        data_dir: str,
        stocklist_path: str,
        tushare_token: str = "",
        full_update: bool = False,
        exclude_boards: List[str] = None,
        max_workers: int = 6,
        codes: List[str] = None,
        start_date: str = None,
        data_source: str = "tushare",  # "tushare" or "xtquant"
        period: str = "1d",  # "1d", "1m", "5m", "15m", "30m", "60m"
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.stocklist_path = Path(stocklist_path) if stocklist_path else None
        self.tushare_token = tushare_token
        self.full_update = full_update
        self.exclude_boards = set(exclude_boards) if exclude_boards else set()
        self.max_workers = max_workers
        self.codes = codes
        self.start_date = start_date
        self.data_source = data_source
        self.period = period
        self._is_running = True

    def run(self):
        try:
            if self.data_source == "tushare":
                self._run_tushare()
            elif self.data_source == "xtquant":
                self._run_xtquant()
            else:
                self.finished_signal.emit(False, f"未知数据源: {self.data_source}")
        except Exception as e:
            self.finished_signal.emit(False, f"发生错误: {str(e)}")

    def _run_tushare(self):
        """使用 Tushare 数据源更新"""
        if not HAS_TUSHARE:
            self.finished_signal.emit(False, "Tushare 未安装，请执行: pip install tushare")
            return
        
        self.log_message.emit("正在初始化 Tushare API...")
        if not self.tushare_token:
            self.finished_signal.emit(False, "Tushare Token 未提供")
            return

        ts.set_token(self.tushare_token)
        pro = ts.pro_api()
        fetch_kline.set_api(pro)

        # 获取股票代码列表
        codes = self._get_stock_codes(fetch_kline.load_codes_from_stocklist)
        if codes is None:
            return

        total_stocks = len(codes)
        if not self.codes:
            self.log_message.emit(
                f"找到 {total_stocks} 只股票（排除板块: {', '.join(self.exclude_boards) or '无'}）。开始更新..."
            )

        # 确保输出目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数（Tushare 只支持日线）
        if self.period != "1d":
            self.log_message.emit("注意: Tushare 数据源仅支持日线，已自动切换为日线模式")
        
        fetch_func = fetch_kline.fetch_one_full if self.full_update else fetch_kline.fetch_one

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        self._execute_update(codes, fetch_func, start_date, end_date)

    def _run_xtquant(self):
        """使用 xtquant/miniQMT 数据源更新"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self.finished_signal.emit(
                False, 
                "xtquant 未安装，请从迅投官网下载安装"
            )
            return

        self.log_message.emit("正在检查 miniQMT 连接状态...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self.finished_signal.emit(False, f"miniQMT 连接失败: {msg}")
            return
        
        self.log_message.emit("miniQMT 连接正常")

        # 获取股票代码列表
        codes = self._get_stock_codes(fetch_kline_xtquant.load_codes_from_stocklist)
        if codes is None:
            return

        total_stocks = len(codes)
        period_name = self._get_period_name()
        if not self.codes:
            self.log_message.emit(
                f"找到 {total_stocks} 只股票（排除板块: {', '.join(self.exclude_boards) or '无'}）。"
                f"开始更新 {period_name} 数据..."
            )

        # 确保输出目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数
        if self.full_update:
            fetch_func = lambda code, start, end, out_dir: \
                fetch_kline_xtquant.fetch_one_full(code, start, end, out_dir, self.period)
        else:
            fetch_func = lambda code, start, end, out_dir: \
                fetch_kline_xtquant.fetch_one(code, start, end, out_dir, self.period)

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        self._execute_update(codes, fetch_func, start_date, end_date)

    def _get_stock_codes(self, load_func) -> Optional[List[str]]:
        """获取股票代码列表"""
        if self.codes:
            self.log_message.emit(f"正在更新 {len(self.codes)} 只指定股票...")
            return self.codes
        elif self.stocklist_path:
            self.log_message.emit(f"正在从 {self.stocklist_path} 加载股票列表...")
            codes = load_func(self.stocklist_path, self.exclude_boards)
            if not codes:
                self.finished_signal.emit(False, "未找到股票代码")
                return None
            return codes
        else:
            self.finished_signal.emit(False, "未提供股票代码或股票列表路径")
            return None

    def _get_period_name(self) -> str:
        """获取周期的中文名称"""
        period_names = {
            "1d": "日线",
            "1m": "1分钟",
            "5m": "5分钟",
            "15m": "15分钟",
            "30m": "30分钟",
            "60m": "60分钟",
        }
        return period_names.get(self.period, self.period)

    def _execute_update(self, codes: List[str], fetch_func, start_date: str, end_date: str):
        """执行数据更新"""
        total_stocks = len(codes)
        completed_count = 0

        # 分批提交任务，避免一次性向线程池塞入数千个任务导致无法及时停止
        batch_size = 50
        for i in range(0, total_stocks, batch_size):
            if not self._is_running:
                break
                
            batch_codes = codes[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(fetch_func, code, start_date, end_date, self.data_dir): code
                    for code in batch_codes
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self.finished_signal.emit(False, "更新已取消")
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"已更新 {code} ({completed_count}/{total_stocks})"
                    except Exception as e:
                        msg = f"更新 {code} 失败: {str(e)}"
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_stocks, msg)

        if not self._is_running:
            self.finished_signal.emit(False, "更新已停止")
        else:
            self.finished_signal.emit(True, "数据更新完成")

    def stop(self):
        self._is_running = False
        self.wait()


class ETFUpdateThread(QThread):
    """
    Background thread for updating ETF data.
    使用 xtquant/miniQMT 获取ETF数据
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(
        self,
        data_dir: str,
        etf_config_path: str = None,
        full_update: bool = False,
        max_workers: int = 4,
        codes: List[str] = None,
        start_date: str = None,
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.etf_config_path = Path(etf_config_path) if etf_config_path else None
        self.full_update = full_update
        self.max_workers = max_workers
        self.codes = codes
        self.start_date = start_date
        self._is_running = True

    def run(self):
        try:
            self._run_etf_update()
        except Exception as e:
            self.finished_signal.emit(False, f"发生错误: {str(e)}")

    def _run_etf_update(self):
        """使用 xtquant 更新ETF数据"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self.finished_signal.emit(
                False, 
                "xtquant 未安装，请从迅投官网下载安装"
            )
            return

        self.log_message.emit("正在检查 miniQMT 连接状态...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self.finished_signal.emit(False, f"miniQMT 连接失败: {msg}")
            return
        
        self.log_message.emit("miniQMT 连接正常")

        # 获取ETF代码列表
        if self.codes:
            codes = self.codes
            self.log_message.emit(f"正在更新 {len(codes)} 只指定ETF...")
        elif self.etf_config_path and self.etf_config_path.exists():
            codes = fetch_kline_xtquant.load_etf_codes_from_config(self.etf_config_path)
            if not codes:
                self.finished_signal.emit(False, "未找到ETF代码")
                return
            self.log_message.emit(f"从配置文件找到 {len(codes)} 只ETF，开始更新...")
        else:
            self.finished_signal.emit(False, "未提供ETF代码或配置文件路径")
            return

        # 确保输出目录存在
        etf_dir = self.data_dir / "etf"
        etf_dir.mkdir(parents=True, exist_ok=True)

        # 确定获取函数
        if self.full_update:
            fetch_func = fetch_kline_xtquant.fetch_etf_one_full
        else:
            fetch_func = fetch_kline_xtquant.fetch_etf_one

        # 日期范围
        start_date = self.start_date if self.start_date else "20190101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # 执行更新
        total_etfs = len(codes)
        completed_count = 0

        batch_size = 20
        for i in range(0, total_etfs, batch_size):
            if not self._is_running:
                break
                
            batch_codes = codes[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(fetch_func, code, start_date, end_date, self.data_dir): code
                    for code in batch_codes
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self.finished_signal.emit(False, "ETF更新已取消")
                        return

                    code = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"已更新ETF {code} ({completed_count}/{total_etfs})"
                    except Exception as e:
                        msg = f"更新ETF {code} 失败: {str(e)}"
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_etfs, msg)

        if not self._is_running:
            self.finished_signal.emit(False, "ETF更新已停止")
        else:
            self.finished_signal.emit(True, f"ETF数据更新完成，共更新 {completed_count} 只")

    def stop(self):
        self._is_running = False
        self.wait()


class IndexUpdateThread(QThread):
    """
    Background thread for updating index data.
    Uses xtquant/miniQMT to get index data
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(
        self,
        data_dir: str,
        index_config_path: str = None,
        full_update: bool = False,
        max_workers: int = 4,
        start_date: str = None,
    ):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.index_config_path = Path(index_config_path) if index_config_path else None
        self.full_update = full_update
        self.max_workers = max_workers
        self.start_date = start_date
        self._is_running = True

    def run(self):
        try:
            self._run_index_update()
        except Exception as e:
            self.finished_signal.emit(False, f"Error occurred: {str(e)}")

    def _run_index_update(self):
        """Update index data using xtquant"""
        if not fetch_kline_xtquant.check_xtquant_available():
            self.finished_signal.emit(
                False, 
                "xtquant not installed, please download from XT website"
            )
            return

        self.log_message.emit("Checking miniQMT connection status...")
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self.finished_signal.emit(False, f"miniQMT connection failed: {msg}")
            return
        
        self.log_message.emit("miniQMT connected successfully")

        # Get index list
        indices = fetch_kline_xtquant.load_index_codes_from_config(self.index_config_path)
        if not indices:
            self.finished_signal.emit(False, "No index codes found")
            return
        
        self.log_message.emit(f"Found {len(indices)} indices to update...")

        # Ensure output directory exists
        index_dir = self.data_dir / "index"
        index_dir.mkdir(parents=True, exist_ok=True)

        # Determine fetch function
        if self.full_update:
            fetch_func = fetch_kline_xtquant.fetch_index_one_full
        else:
            fetch_func = fetch_kline_xtquant.fetch_index_one

        # Date range
        start_date = self.start_date if self.start_date else "19900101"
        end_date = pd.Timestamp.now().strftime("%Y%m%d")

        # Execute update
        total_indices = len(indices)
        completed_count = 0

        batch_size = 10
        for i in range(0, total_indices, batch_size):
            if not self._is_running:
                break
                
            batch_indices = indices[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        fetch_func, 
                        idx.get("code"), 
                        start_date, 
                        end_date, 
                        self.data_dir, 
                        "1d",
                        idx.get("exchange")
                    ): idx 
                    for idx in batch_indices
                }

                for future in as_completed(futures):
                    if not self._is_running:
                        executor.shutdown(wait=False)
                        self.finished_signal.emit(False, "Index update cancelled")
                        return

                    idx = futures[future]
                    completed_count += 1
                    try:
                        future.result()
                        msg = f"Updated index {idx.get('code')} ({idx.get('name', '')}) ({completed_count}/{total_indices})"
                    except Exception as e:
                        msg = f"Update index {idx.get('code')} failed: {str(e)}"
                        self.log_message.emit(msg)

                    self.progress_updated.emit(completed_count, total_indices, msg)

        if not self._is_running:
            self.finished_signal.emit(False, "Index update stopped")
        else:
            self.finished_signal.emit(True, f"Index data update completed, updated {completed_count} indices")

    def stop(self):
        self._is_running = False
        self.wait()


class ETFListUpdateThread(QThread):
    """
    后台线程：从 xtquant 获取完整的ETF列表并更新配置文件
    """
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, dict)  # success, message, stats

    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = Path(config_path)
        self._is_running = True

    def run(self):
        try:
            self._update_etf_list()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False, f"发生错误: {str(e)}", {})

    def _update_etf_list(self):
        """从 xtquant 获取完整ETF列表并更新配置"""
        if not HAS_XTQUANT:
            self.finished_signal.emit(
                False, 
                "xtquant 未安装，请从迅投官网下载安装或执行 pip install xtquant",
                {}
            )
            return

        # 检查连接
        self.log_message.emit("正在检查 miniQMT 连接状态...")
        if not fetch_kline_xtquant.check_xtquant_available():
            self.finished_signal.emit(False, "xtquant 不可用", {})
            return
            
        connected, msg = fetch_kline_xtquant.check_connection()
        if not connected:
            self.finished_signal.emit(False, f"miniQMT 连接失败: {msg}", {})
            return
        
        self.log_message.emit("miniQMT 连接正常")
        
        # 下载板块数据
        self.log_message.emit("正在下载板块分类数据...")
        try:
            xtdata.download_sector_data()
        except Exception as e:
            self.log_message.emit(f"下载板块数据警告: {e}")
        
        # 获取ETF相关板块
        self.log_message.emit("正在获取ETF板块列表...")
        try:
            sectors = xtdata.get_sector_list()
            etf_sectors = [s for s in sectors if 'ETF' in s.upper()]
            self.log_message.emit(f"找到 {len(etf_sectors)} 个ETF相关板块: {etf_sectors}")
        except Exception as e:
            self.log_message.emit(f"获取板块列表失败: {e}")
            etf_sectors = []
        
        # 从各板块获取ETF
        all_etf_codes = set()
        
        # 尝试常见的ETF板块名称
        sector_names_to_try = list(set(etf_sectors + ["沪深ETF", "上证ETF", "深证ETF", "ETF"]))
        
        for sector_name in sector_names_to_try:
            if not self._is_running:
                self.finished_signal.emit(False, "已取消", {})
                return
                
            try:
                etfs = xtdata.get_stock_list_in_sector(sector_name)
                if etfs:
                    self.log_message.emit(f"  [{sector_name}] 包含 {len(etfs)} 只ETF")
                    all_etf_codes.update(etfs)
            except Exception as e:
                self.log_message.emit(f"  [{sector_name}] 获取失败: {e}")
        
        if not all_etf_codes:
            self.finished_signal.emit(False, "未能获取到ETF列表，请确保miniQMT已正确登录", {})
            return
        
        self.log_message.emit(f"\n共获取到 {len(all_etf_codes)} 只ETF")
        
        # 获取ETF详细信息并分类
        self.log_message.emit("\n正在获取ETF详细信息...")
        etf_info_list = []
        total = len(all_etf_codes)
        
        for i, code in enumerate(sorted(all_etf_codes)):
            if not self._is_running:
                self.finished_signal.emit(False, "已取消", {})
                return
            
            if (i + 1) % 50 == 0:
                self.progress_updated.emit(i + 1, total, f"获取 {code} 信息")
                self.log_message.emit(f"  已处理 {i + 1}/{total}...")
            
            info = self._get_etf_info(code)
            if info:
                etf_info_list.append(info)
        
        self.log_message.emit(f"成功获取 {len(etf_info_list)} 只ETF信息")
        
        # 读取现有配置
        existing_codes = set()
        existing_config = None
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    existing_config = json.load(f)
                for category in existing_config.get("categories", []):
                    for etf in category.get("etfs", []):
                        existing_codes.add(etf.get("code", ""))
            except Exception as e:
                self.log_message.emit(f"读取现有配置失败: {e}")
        
        # 智能分类新增ETF
        self.log_message.emit("\n正在分类ETF...")
        categorized = self._categorize_etfs(etf_info_list, existing_config)
        
        # 保存更新后的配置
        self.log_message.emit("正在保存配置...")
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(categorized, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.finished_signal.emit(False, f"保存配置失败: {e}", {})
            return
        
        # 统计信息
        new_codes = {e.get("code") for cat in categorized.get("categories", []) for e in cat.get("etfs", [])}
        added_count = len(new_codes - existing_codes)
        
        stats = {
            "total": len(new_codes),
            "added": added_count,
            "existing": len(existing_codes),
            "sh_count": len([c for c in new_codes if c.startswith(("51", "56", "58"))]),
            "sz_count": len([c for c in new_codes if c.startswith(("15", "16"))]),
        }
        
        self.log_message.emit(f"\n✓ ETF列表更新完成!")
        self.log_message.emit(f"  总计: {stats['total']} 只")
        self.log_message.emit(f"  新增: {stats['added']} 只")
        self.log_message.emit(f"  上交所: {stats['sh_count']} 只")
        self.log_message.emit(f"  深交所: {stats['sz_count']} 只")
        
        self.finished_signal.emit(
            True, 
            f"ETF列表更新成功！共 {stats['total']} 只ETF，新增 {stats['added']} 只",
            stats
        )

    def _get_etf_info(self, code: str) -> Optional[Dict[str, Any]]:
        """获取单只ETF的详细信息"""
        try:
            info = xtdata.get_instrument_detail(code)
            if info:
                code_part = code.split('.')[0] if '.' in code else code
                exchange = code.split('.')[1] if '.' in code else ''
                return {
                    "code": code_part,
                    "name": info.get('InstrumentName', '').replace(' ', ''),
                    "exchange": exchange,
                    "full_name": info.get('InstrumentName', ''),
                }
        except Exception:
            pass
        
        # 如果获取失败，返回基本信息
        parts = code.split('.')
        return {
            "code": parts[0] if parts else code,
            "name": "",
            "exchange": parts[1] if len(parts) > 1 else "",
        }

    def _categorize_etfs(self, etf_list: List[Dict], existing_config: Optional[Dict]) -> Dict:
        """
        智能分类ETF
        保留现有分类结构，将新ETF添加到合适的分类中
        """
        # 分类关键词映射
        category_keywords = {
            "宽基ETF": ["沪深300", "中证500", "上证50", "创业板", "科创", "中证1000", "红利", "A50", "MSCI", "恒生指数"],
            "行业ETF-科技": ["半导体", "芯片", "5G", "通信", "计算机", "电子", "软件", "互联网", "人工智能", "AI", "大数据", "云计算", "网络安全"],
            "行业ETF-新能源": ["新能源", "光伏", "风电", "储能", "电池", "锂电", "充电", "氢能", "新能车", "电动车"],
            "行业ETF-消费": ["消费", "食品", "饮料", "酒", "白酒", "家电", "汽车", "零售", "旅游", "酒店", "餐饮"],
            "行业ETF-医药": ["医药", "医疗", "生物", "创新药", "中药", "疫苗", "器械", "健康", "养老"],
            "行业ETF-金融地产": ["银行", "证券", "券商", "保险", "金融", "地产", "房地产", "非银"],
            "行业ETF-周期资源": ["有色", "金属", "钢铁", "煤炭", "石油", "化工", "建材", "建筑", "基建", "机械", "军工", "国防", "航空", "船舶"],
            "行业ETF-农业": ["农业", "畜牧", "养殖", "猪", "粮食", "种业"],
            "行业ETF-传媒娱乐": ["传媒", "游戏", "影视", "文化", "动漫", "体育"],
            "跨境ETF": ["纳指", "标普", "恒生", "港股", "中概", "美股", "日经", "德国", "法国", "越南", "印度", "东南亚", "亚太"],
            "债券ETF": ["国债", "债券", "利率", "信用", "可转债", "城投", "金融债", "公司债"],
            "商品ETF": ["黄金", "白银", "原油", "能源", "豆粕", "有色金属", "铜", "铝"],
            "货币ETF": ["货币", "现金"],
        }
        
        # 初始化分类
        if existing_config and "categories" in existing_config:
            # 使用现有配置作为基础
            categories = {cat["name"]: cat.copy() for cat in existing_config["categories"]}
            # 清空ETF列表，重新填充
            for cat in categories.values():
                cat["etfs"] = []
        else:
            # 创建新的分类结构
            categories = {}
            for cat_name in category_keywords.keys():
                categories[cat_name] = {"name": cat_name, "etfs": []}
        
        # 未分类ETF
        uncategorized = []
        
        # 对每个ETF进行分类
        for etf in etf_list:
            name = etf.get("name", "") or etf.get("full_name", "")
            code = etf.get("code", "")
            
            if not code:
                continue
            
            # 构建ETF条目
            etf_entry = {
                "code": code,
                "name": name,
                "exchange": etf.get("exchange", "")
            }
            
            # 尝试匹配分类
            matched = False
            for cat_name, keywords in category_keywords.items():
                for kw in keywords:
                    if kw in name:
                        if cat_name not in categories:
                            categories[cat_name] = {"name": cat_name, "etfs": []}
                        categories[cat_name]["etfs"].append(etf_entry)
                        matched = True
                        break
                if matched:
                    break
            
            if not matched:
                uncategorized.append(etf_entry)
        
        # 将未分类的放入"其他ETF"
        if uncategorized:
            categories["其他ETF"] = {"name": "其他ETF", "etfs": uncategorized}
        
        # 构建最终配置
        # 确保每个分类内的ETF去重并排序
        for cat in categories.values():
            seen = set()
            unique_etfs = []
            for etf in cat["etfs"]:
                if etf["code"] not in seen:
                    seen.add(etf["code"])
                    unique_etfs.append(etf)
            cat["etfs"] = sorted(unique_etfs, key=lambda x: x["code"])
        
        # 按预定义顺序排列分类
        category_order = [
            "宽基ETF", "行业ETF-科技", "行业ETF-新能源", "行业ETF-消费", 
            "行业ETF-医药", "行业ETF-金融地产", "行业ETF-周期资源",
            "行业ETF-农业", "行业ETF-传媒娱乐", "跨境ETF", 
            "债券ETF", "商品ETF", "货币ETF", "其他ETF"
        ]
        
        sorted_categories = []
        for name in category_order:
            if name in categories and categories[name]["etfs"]:
                sorted_categories.append(categories[name])
        
        # 添加不在预定义顺序中的分类
        for name, cat in categories.items():
            if name not in category_order and cat["etfs"]:
                sorted_categories.append(cat)
        
        return {
            "version": "2.0",
            "description": "ETF基金列表配置 (自动更新)",
            "auto_updated": True,
            "categories": sorted_categories
        }

    def stop(self):
        self._is_running = False
        self.wait()
