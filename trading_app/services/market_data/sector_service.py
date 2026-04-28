# sector_service.py - 板块数据服务
"""
基于 xtquant 的板块数据服务

功能：
- 获取申万行业、概念板块列表
- 获取板块实时行情（涨跌幅、成交额等）
- 获取板块成分股
- 计算板块热度排行

参考文档: https://dict.thinktrader.net/nativeApi/xtdata.html
"""
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date
import threading

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# 设置日志
logger = logging.getLogger(__name__)

# 检查 xtquant 是否可用
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None
    logger.warning("xtquant 未安装，板块数据功能不可用")


@dataclass
class SectorData:
    """板块数据结构"""
    code: str                    # 板块代码
    name: str = ""               # 板块名称
    change_pct: float = 0.0      # 涨跌幅（百分比）
    amount: float = 0.0          # 成交额（元）
    volume: float = 0.0          # 成交量
    rise_count: int = 0          # 上涨家数
    fall_count: int = 0          # 下跌家数
    flat_count: int = 0          # 平盘家数
    leading_stock: str = ""      # 领涨股代码
    leading_stock_name: str = "" # 领涨股名称
    leading_change: float = 0.0  # 领涨股涨幅
    timestamp: datetime = field(default_factory=datetime.now)
    
    # ========== 新增热度指标 ==========
    # 成交额占比
    amount_ratio: float = 0.0    # 成交额占全市场比例（%）
    
    # 换手率
    turnover_rate: float = 0.0   # 板块平均换手率（%）
    
    # 量比
    volume_ratio: float = 0.0    # 量比 = 当前成交量 / 过去5日平均成交量
    
    # 资金流向
    net_inflow: float = 0.0      # 主力净流入（元）
    net_inflow_ratio: float = 0.0  # 净流入占成交额比例（%）
    
    # 涨停跌停统计
    limit_up_count: int = 0      # 涨停家数
    limit_down_count: int = 0    # 跌停家数
    
    # 多日涨幅
    change_pct_3d: float = 0.0   # 近3日累计涨幅（%）
    change_pct_5d: float = 0.0   # 近5日累计涨幅（%）
    
    # 热度指数（综合评分）
    hotness_score: float = 0.0   # 热度指数（0-100分）
    
    @property
    def is_up(self) -> bool:
        """是否上涨"""
        return self.change_pct > 0
    
    @property
    def total_stocks(self) -> int:
        """板块总股票数"""
        return self.rise_count + self.fall_count + self.flat_count
    
    @property
    def rise_ratio(self) -> float:
        """上涨比例"""
        total = self.total_stocks
        return self.rise_count / total * 100 if total > 0 else 0
    
    @property
    def limit_up_ratio(self) -> float:
        """涨停比例"""
        total = self.total_stocks
        return self.limit_up_count / total * 100 if total > 0 else 0


@dataclass
class SectorStockData:
    """板块成分股数据"""
    code: str
    name: str = ""
    change_pct: float = 0.0
    last_price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0


# 板块类型映射
SECTOR_TYPES = {
    "sw_l1": "申万一级行业",
    "sw_l2": "申万二级行业", 
    "concept": "概念板块",
    "thematic": "题材板块",
    "region": "地域板块",
}

# 申万一级行业（2021版，共31个）- 标准中文名称
SW_L1_INDUSTRIES = {
    "农林牧渔": "农林牧渔",
    "基础化工": "基础化工",
    "钢铁": "钢铁",
    "有色金属": "有色金属",
    "电子": "电子",
    "汽车": "汽车",
    "家用电器": "家用电器",
    "食品饮料": "食品饮料",
    "纺织服饰": "纺织服饰",
    "轻工制造": "轻工制造",
    "医药生物": "医药生物",
    "公用事业": "公用事业",
    "交通运输": "交通运输",
    "房地产": "房地产",
    "商贸零售": "商贸零售",
    "社会服务": "社会服务",
    "银行": "银行",
    "非银金融": "非银金融",
    "综合": "综合",
    "建筑材料": "建筑材料",
    "建筑装饰": "建筑装饰",
    "电力设备": "电力设备",
    "机械设备": "机械设备",
    "国防军工": "国防军工",
    "计算机": "计算机",
    "传媒": "传媒",
    "通信": "通信",
    "煤炭": "煤炭",
    "石油石化": "石油石化",
    "环保": "环保",
    "美容护理": "美容护理",
}

# xtquant 板块名称到中文名称的映射（用于处理不同格式）
SECTOR_NAME_MAPPING = {}

# 常见概念板块关键词
CONCEPT_KEYWORDS = [
    "人工智能", "AI", "芯片", "半导体", "新能源", "光伏", "锂电池", 
    "储能", "充电桩", "智能驾驶", "无人驾驶", "机器人", "数字经济",
    "华为", "鸿蒙", "算力", "数据中心", "云计算", "大数据", "区块链",
    "元宇宙", "VR", "AR", "游戏", "医美", "中药", "创新药", "CXO",
    "军工", "航空航天", "卫星", "国产替代", "信创", "EDA", "碳中和",
    "氢能", "风电", "核电", "特高压", "智能电网", "新基建", "一带一路",
]


class SectorService(QObject):
    """
    板块数据服务
    
    提供板块列表、板块行情、板块成分股等数据获取功能。
    
    信号：
        sector_data_updated: 板块数据更新信号，参数为 List[SectorData]
        sector_stocks_updated: 板块成分股更新信号，参数为 (sector_code, List[SectorStockData])
        connection_status_changed: 连接状态变化信号
    """
    
    # PyQt 信号
    sector_data_updated = pyqtSignal(list)  # List[SectorData]
    sector_stocks_updated = pyqtSignal(str, list)  # sector_code, List[SectorStockData]
    connection_status_changed = pyqtSignal(bool, str)
    
    def __init__(self, parent=None, poll_interval: int = 5000):
        """
        初始化板块服务
        
        Args:
            parent: 父对象
            poll_interval: 轮询间隔（毫秒），默认5000ms
        """
        super().__init__(parent)
        
        self._sector_cache: Dict[str, SectorData] = {}
        self._sector_list: List[str] = []
        self._is_running = False
        self._poll_interval = max(3000, poll_interval)
        self._current_sector_type = "sw_l1"
        
        # 轮询定时器
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_sector_data)
        
        # 数据锁
        self._lock = threading.Lock()
        
        # 缓存板块列表
        self._all_sectors: Dict[str, List[str]] = {}
        
        # 板块名称映射（原始代码 -> 显示名称）
        self._sector_name_map: Dict[str, str] = {}
    
    @property
    def is_available(self) -> bool:
        """检查服务是否可用"""
        return HAS_XTQUANT
    
    @property
    def is_running(self) -> bool:
        """检查服务是否正在运行"""
        return self._is_running
    
    def get_sector_list(self, sector_type: str = "sw_l1") -> List[str]:
        """
        获取板块列表
        
        Args:
            sector_type: 板块类型 (sw_l1, sw_l2, concept, thematic, region)
        
        Returns:
            板块名称列表
        """
        if not HAS_XTQUANT:
            return []
        
        # 检查缓存
        if sector_type in self._all_sectors and self._all_sectors[sector_type]:
            return self._all_sectors[sector_type]
        
        try:
            # 先下载板块数据
            xtdata.download_sector_data()
            
            # 获取所有板块
            all_sectors = xtdata.get_sector_list()
            
            if not all_sectors:
                logger.warning("未获取到板块列表，使用预定义列表")
                # 使用预定义的申万一级行业
                self._all_sectors = {
                    "sw_l1": list(SW_L1_INDUSTRIES.keys()),
                    "sw_l2": [],
                    "concept": [],
                    "thematic": [],
                    "region": [],
                    "all": list(SW_L1_INDUSTRIES.keys())
                }
                return self._all_sectors.get(sector_type, [])
            
            logger.info(f"xtquant 返回 {len(all_sectors)} 个板块")
            
            # 按类型分类
            sw_l1 = []
            sw_l2 = []
            concept = []
            thematic = []
            region = []
            
            # 构建名称映射
            self._sector_name_map = {}
            
            # 先收集所有带"加权"的板块名称（用于过滤等权板块）
            weighted_sector_bases = set()
            for sector in all_sectors:
                if sector and "加权" in sector:
                    # "传媒加权" -> "传媒"
                    weighted_sector_bases.add(sector.replace("加权", ""))
            
            for sector in all_sectors:
                # 跳过空值
                if not sector or len(sector) < 2:
                    continue
                
                # 保留"加权"板块，过滤掉对应的等权板块
                # 例如: "传媒" vs "传媒加权"，只保留后者
                if "加权" not in sector and sector in weighted_sector_bases:
                    continue
                
                # 检查是否是申万行业
                is_sw = False
                display_name = sector
                
                # 处理 SW 开头的格式（如 SW1银行, SW2IT服务）
                if sector.startswith("SW") or sector.startswith("sw"):
                    is_sw = True
                    # 提取行业名称
                    if len(sector) > 3:
                        # SW1银行 -> 银行, SW2IT服务 -> IT服务
                        name_part = sector[3:] if sector[2].isdigit() else sector[2:]
                        display_name = name_part
                        
                        # 判断级别
                        if "1" in sector[:4] or sector[2] == "1":
                            sw_l1.append(sector)
                        else:
                            sw_l2.append(sector)
                    self._sector_name_map[sector] = display_name
                    
                # 检查是否包含申万一级行业关键词
                elif any(ind in sector for ind in SW_L1_INDUSTRIES.keys()):
                    is_sw = True
                    for ind_name in SW_L1_INDUSTRIES.keys():
                        if ind_name in sector:
                            display_name = ind_name
                            self._sector_name_map[sector] = display_name
                            # 判断是一级还是二级
                            if "一级" in sector or "Ⅰ" in sector or sector == ind_name or f"申万{ind_name}" == sector:
                                sw_l1.append(sector)
                            else:
                                sw_l2.append(sector)
                            break
                
                # 检查是否是概念/题材板块
                elif any(kw in sector for kw in CONCEPT_KEYWORDS):
                    concept.append(sector)
                    self._sector_name_map[sector] = sector
                
                # 检查是否是地域板块
                elif any(region_name in sector for region_name in ["北京", "上海", "深圳", "广东", "江苏", "浙江", "山东", "四川", "湖北", "湖南"]):
                    region.append(sector)
                    self._sector_name_map[sector] = sector
                
                # 题材板块（GN 开头通常是概念题材）
                elif sector.startswith("GN") or sector.startswith("gn"):
                    thematic.append(sector)
                    # 去掉 GN 前缀
                    display_name = sector[2:] if len(sector) > 2 else sector
                    self._sector_name_map[sector] = display_name
                
                # 其他板块归类为概念
                elif not is_sw and len(sector) <= 12:
                    concept.append(sector)
                    self._sector_name_map[sector] = sector
            
            # 如果申万一级行业为空，尝试其他方式查找
            if not sw_l1:
                # 尝试直接匹配行业名称
                for sector in all_sectors:
                    for ind_name in SW_L1_INDUSTRIES.keys():
                        if sector == ind_name or sector == f"申万{ind_name}" or sector.endswith(ind_name):
                            if sector not in sw_l1:
                                sw_l1.append(sector)
                                self._sector_name_map[sector] = ind_name
            
            # 如果还是为空，使用预定义列表
            if not sw_l1:
                logger.warning("未能识别申万一级行业，使用预定义列表")
                sw_l1 = list(SW_L1_INDUSTRIES.keys())
                for name in sw_l1:
                    self._sector_name_map[name] = name
            
            # 缓存结果
            self._all_sectors = {
                "sw_l1": sw_l1,
                "sw_l2": sw_l2,
                "concept": concept,
                "thematic": thematic,
                "region": region,
                "all": all_sectors
            }
            
            logger.info(f"板块分类完成: 申万一级={len(sw_l1)}, 申万二级={len(sw_l2)}, 概念={len(concept)}, 题材={len(thematic)}")
            return self._all_sectors.get(sector_type, [])
            
        except Exception as e:
            logger.error(f"获取板块列表失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []
    
    def get_sector_display_name(self, sector_code: str) -> str:
        """
        获取板块的显示名称（中文友好名称）
        
        Args:
            sector_code: 板块代码或原始名称
        
        Returns:
            显示名称
        """
        if hasattr(self, '_sector_name_map') and sector_code in self._sector_name_map:
            name = self._sector_name_map[sector_code]
            # 去掉 "加权" 后缀
            if name.endswith("加权"):
                name = name[:-2]
            return name
        
        # 处理常见格式
        name = sector_code
        
        # 去掉 SW 前缀
        if name.startswith("SW") or name.startswith("sw"):
            name = name[2:]
            if name and name[0].isdigit():
                name = name[1:]
        
        # 去掉 GN 前缀
        if name.startswith("GN") or name.startswith("gn"):
            name = name[2:]
        
        # 去掉 "申万" 前缀
        if name.startswith("申万"):
            name = name[2:]
        
        # 去掉 "加权" 后缀
        if name.endswith("加权"):
            name = name[:-2]
        
        return name if name else sector_code
    
    def get_sector_stocks(self, sector_name: str) -> List[str]:
        """
        获取板块成分股
        
        Args:
            sector_name: 板块名称
        
        Returns:
            成分股代码列表
        """
        if not HAS_XTQUANT:
            return []
        
        try:
            stocks = xtdata.get_stock_list_in_sector(sector_name)
            return stocks if stocks else []
        except Exception as e:
            logger.error(f"获取板块 {sector_name} 成分股失败: {e}")
            return []
    
    def fetch_sector_quotes(self, sector_names: List[str] = None) -> List[SectorData]:
        """
        获取板块行情数据
        
        通过获取板块成分股的行情，计算板块整体涨跌幅等指标。
        
        Args:
            sector_names: 板块名称列表，None则获取当前类型的所有板块
        
        Returns:
            板块数据列表
        """
        if not HAS_XTQUANT:
            return []
        
        if sector_names is None:
            sector_names = self.get_sector_list(self._current_sector_type)
        
        if not sector_names:
            return []
        
        results = []
        
        try:
            for sector_name in sector_names:
                sector_data = self._calc_sector_data(sector_name)
                if sector_data:
                    results.append(sector_data)
            
            # ========== 计算全局指标 ==========
            # 1. 计算成交额占比
            total_market_amount = sum(s.amount for s in results) if results else 1
            for sector in results:
                if total_market_amount > 0:
                    sector.amount_ratio = round(sector.amount / total_market_amount * 100, 2)
            
            # 2. 计算热度指数（综合评分）
            self._calc_hotness_scores(results)
            
            # 按热度指数排序（而非单纯涨跌幅）
            results.sort(key=lambda x: x.hotness_score, reverse=True)
            
            # 更新缓存
            with self._lock:
                for data in results:
                    self._sector_cache[data.name] = data
            
            return results
            
        except Exception as e:
            logger.error(f"获取板块行情失败: {e}")
            return []
    
    def _calc_hotness_scores(self, sectors: List[SectorData]):
        """
        计算板块热度指数
        
        热度公式:
        热度总分 = (涨幅得分 × 35%) + (成交额占比得分 × 25%) + 
                   (换手率得分 × 20%) + (涨停比例得分 × 10%) + (上涨比例得分 × 10%)
        
        每个指标在板块内进行排名归一化（0-100分）
        """
        if not sectors:
            return
        
        n = len(sectors)
        
        # 1. 涨幅排名（越高越好）
        sorted_by_change = sorted(sectors, key=lambda x: x.change_pct, reverse=True)
        for i, s in enumerate(sorted_by_change):
            s._change_score = (n - i) / n * 100
        
        # 2. 成交额占比排名（越高越好）
        sorted_by_amount = sorted(sectors, key=lambda x: x.amount_ratio, reverse=True)
        for i, s in enumerate(sorted_by_amount):
            s._amount_score = (n - i) / n * 100
        
        # 3. 换手率排名（越高越好）
        sorted_by_turnover = sorted(sectors, key=lambda x: x.turnover_rate, reverse=True)
        for i, s in enumerate(sorted_by_turnover):
            s._turnover_score = (n - i) / n * 100
        
        # 4. 涨停比例排名（越高越好）
        sorted_by_limit = sorted(sectors, key=lambda x: x.limit_up_count, reverse=True)
        for i, s in enumerate(sorted_by_limit):
            s._limit_score = (n - i) / n * 100
        
        # 5. 上涨比例排名（越高越好）
        sorted_by_rise = sorted(sectors, key=lambda x: x.rise_ratio, reverse=True)
        for i, s in enumerate(sorted_by_rise):
            s._rise_score = (n - i) / n * 100
        
        # 计算综合热度指数
        for s in sectors:
            s.hotness_score = round(
                s._change_score * 0.35 +      # 涨幅权重 35%
                s._amount_score * 0.25 +      # 成交额权重 25%
                s._turnover_score * 0.20 +    # 换手率权重 20%
                s._limit_score * 0.10 +       # 涨停比例权重 10%
                s._rise_score * 0.10,         # 上涨比例权重 10%
                1
            )
            
            # 清理临时属性
            delattr(s, '_change_score')
            delattr(s, '_amount_score')
            delattr(s, '_turnover_score')
            delattr(s, '_limit_score')
            delattr(s, '_rise_score')
    
    def _calc_sector_data(self, sector_name: str) -> Optional[SectorData]:
        """
        计算单个板块的数据
        
        Args:
            sector_name: 板块名称（xtquant 原始名称）
        
        Returns:
            SectorData 或 None
        """
        try:
            # 获取成分股
            stocks = xtdata.get_stock_list_in_sector(sector_name)
            if not stocks:
                logger.debug(f"板块 {sector_name} 无成分股")
                return None
            
            # 限制成分股数量，避免请求过多
            all_stocks = stocks
            if len(stocks) > 100:
                stocks = stocks[:100]
            
            # 获取成分股行情
            tick_data = xtdata.get_full_tick(stocks)
            if not tick_data:
                return None
            
            # 统计数据
            total_change = 0.0
            total_amount = 0.0
            total_volume = 0.0
            total_turnover = 0.0  # 换手率累计
            total_float_value = 0.0  # 流通市值累计
            rise_count = 0
            fall_count = 0
            flat_count = 0
            valid_count = 0
            limit_up_count = 0    # 涨停数
            limit_down_count = 0  # 跌停数
            
            leading_stock = ""
            leading_stock_name = ""
            leading_change = -999.0
            
            for stock_code, tick in tick_data.items():
                if not tick:
                    continue
                
                last_price = float(tick.get('lastPrice') or 0)
                prev_close = float(tick.get('lastClose') or 0)
                amount = float(tick.get('amount') or 0)
                volume = float(tick.get('volume') or 0)
                
                if prev_close > 0 and last_price > 0:
                    change_pct = (last_price - prev_close) / prev_close * 100
                    total_change += change_pct
                    valid_count += 1
                    
                    if change_pct > 0.1:
                        rise_count += 1
                    elif change_pct < -0.1:
                        fall_count += 1
                    else:
                        flat_count += 1
                    
                    # 判断涨停/跌停（主板10%，科创板/创业板20%）
                    is_kcb_cyb = stock_code.startswith(('688', '300', '301'))
                    limit_pct = 20.0 if is_kcb_cyb else 10.0
                    
                    if change_pct >= limit_pct - 0.5:  # 允许0.5%误差
                        limit_up_count += 1
                    elif change_pct <= -limit_pct + 0.5:
                        limit_down_count += 1
                    
                    # 记录领涨股
                    if change_pct > leading_change:
                        leading_change = change_pct
                        leading_stock = stock_code.split('.')[0] if '.' in stock_code else stock_code
                        # 尝试获取股票名称
                        try:
                            detail = xtdata.get_instrument_detail(stock_code)
                            if detail:
                                leading_stock_name = detail.get('InstrumentName', '')
                        except:
                            pass
                    
                    # 尝试获取换手率（通过流通股本计算）
                    try:
                        detail = xtdata.get_instrument_detail(stock_code)
                        if detail:
                            float_shares = float(detail.get('FloatVolume') or 0)  # 流通股本
                            if float_shares > 0 and volume > 0:
                                turnover = volume / float_shares * 100
                                total_turnover += turnover
                                total_float_value += float_shares * last_price
                    except:
                        pass
                
                total_amount += amount
                total_volume += volume
            
            if valid_count == 0:
                return None
            
            # 计算平均涨跌幅
            avg_change = total_change / valid_count
            
            # 计算平均换手率
            avg_turnover = total_turnover / valid_count if valid_count > 0 else 0
            
            # 获取板块显示名称
            display_name = self.get_sector_display_name(sector_name)
            
            return SectorData(
                code=sector_name,
                name=display_name,  # 使用友好的显示名称
                change_pct=round(avg_change, 2),
                amount=total_amount,
                volume=total_volume,
                rise_count=rise_count,
                fall_count=fall_count,
                flat_count=flat_count,
                leading_stock=leading_stock,
                leading_stock_name=leading_stock_name,
                leading_change=round(leading_change, 2) if leading_change > -999 else 0,
                timestamp=datetime.now(),
                # 新增指标
                turnover_rate=round(avg_turnover, 2),
                limit_up_count=limit_up_count,
                limit_down_count=limit_down_count,
            )
            
        except Exception as e:
            logger.debug(f"计算板块 {sector_name} 数据失败: {e}")
            return None
    
    def start(self, sector_type: str = "sw_l1") -> bool:
        """
        启动板块数据服务
        
        Args:
            sector_type: 板块类型
        
        Returns:
            是否成功启动
        """
        if not HAS_XTQUANT:
            logger.error("xtquant 未安装，无法启动板块服务")
            self.connection_status_changed.emit(False, "xtquant 未安装")
            return False
        
        if self._is_running:
            logger.debug("板块服务已在运行中")
            return True
        
        try:
            self._current_sector_type = sector_type
            self._sector_list = self.get_sector_list(sector_type)
            
            if not self._sector_list:
                logger.warning("未获取到板块列表")
                self.connection_status_changed.emit(False, "未获取到板块列表")
                return False
            
            self._is_running = True
            self._poll_timer.start(self._poll_interval)
            
            # 立即获取一次数据
            self._poll_sector_data()
            
            logger.info(f"板块服务已启动 (类型: {sector_type}, 数量: {len(self._sector_list)})")
            self.connection_status_changed.emit(True, f"板块服务已启动 ({len(self._sector_list)} 个板块)")
            return True
            
        except Exception as e:
            logger.error(f"启动板块服务失败: {e}")
            self.connection_status_changed.emit(False, f"启动失败: {e}")
            return False
    
    def stop(self):
        """停止板块服务"""
        if not self._is_running:
            return
        
        try:
            self._poll_timer.stop()
            
            with self._lock:
                self._sector_cache.clear()
            
            self._is_running = False
            logger.info("板块服务已停止")
            self.connection_status_changed.emit(False, "板块服务已停止")
            
        except Exception as e:
            logger.error(f"停止板块服务失败: {e}")
    
    def set_sector_type(self, sector_type: str):
        """
        切换板块类型
        
        Args:
            sector_type: 板块类型
        """
        self._current_sector_type = sector_type
        self._sector_list = self.get_sector_list(sector_type)
        
        if self._is_running:
            # 立即刷新数据
            self._poll_sector_data()
    
    def set_poll_interval(self, interval_ms: int):
        """设置轮询间隔"""
        self._poll_interval = max(3000, interval_ms)
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._poll_interval)
    
    def _poll_sector_data(self):
        """定时轮询获取板块数据"""
        if not self._sector_list:
            return
        
        try:
            results = self.fetch_sector_quotes(self._sector_list)
            if results:
                self.sector_data_updated.emit(results)
                
        except Exception as e:
            logger.error(f"轮询板块数据失败: {e}")
    
    def get_cached_data(self) -> List[SectorData]:
        """获取缓存的板块数据"""
        with self._lock:
            data_list = list(self._sector_cache.values())
        # 按涨跌幅排序
        data_list.sort(key=lambda x: x.change_pct, reverse=True)
        return data_list
    
    def refresh(self):
        """手动刷新数据"""
        if self._is_running:
            self._poll_sector_data()


# 全局单例
_sector_service_instance: Optional[SectorService] = None


def get_sector_service() -> SectorService:
    """
    获取全局板块服务实例（单例模式）
    
    Returns:
        SectorService 实例
    """
    global _sector_service_instance
    if _sector_service_instance is None:
        _sector_service_instance = SectorService()
    return _sector_service_instance

