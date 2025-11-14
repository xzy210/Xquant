# app.py - 股票K线图可视化 + 选股分析 Streamlit 应用
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import importlib
import logging
from typing import Dict, Any, List

# 导入现有模块
from plot_stock import load_data, add_ma, _attach_indicators

# 导入数据拉取模块
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import tushare as ts

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="📈 股票分析系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== 选股相关函数 ====================

def load_selector_config(cfg_path: str = "./configs.json") -> List[Dict[str, Any]]:
    """加载选股器配置"""
    cfg_file = Path(cfg_path)
    if not cfg_file.exists():
        return []
    
    with cfg_file.open(encoding="utf-8") as f:
        cfg_raw = json.load(f)
    
    if isinstance(cfg_raw, list):
        return cfg_raw
    elif isinstance(cfg_raw, dict) and "selectors" in cfg_raw:
        return cfg_raw["selectors"]
    else:
        return [cfg_raw]

def instantiate_selector(cfg: Dict[str, Any]):
    """动态加载并实例化 Selector 类"""
    cls_name = cfg.get("class")
    if not cls_name:
        raise ValueError("缺少 class 字段")
    
    try:
        module = importlib.import_module("Selector")
        cls = getattr(module, cls_name)
    except (ModuleNotFoundError, AttributeError) as e:
        raise ImportError(f"无法加载 Selector.{cls_name}: {e}") from e
    
    params = cfg.get("params", {})
    alias = cfg.get("alias", cls_name)
    return alias, cls(**params)

def load_all_stock_data(data_dir: str) -> Dict[str, pd.DataFrame]:
    """加载所有股票数据"""
    data_path = Path(data_dir)
    if not data_path.exists():
        return {}
    
    data = {}
    csv_files = list(data_path.glob("*.csv"))
    
    for fp in csv_files:
        try:
            df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
            data[fp.stem] = df
        except Exception as e:
            st.warning(f"加载 {fp.name} 失败: {e}")
            continue
    
    return data

def run_stock_selection(data_dir: str, config_path: str = "./configs.json") -> Dict[str, List[str]]:
    """
    运行所有选股策略
    返回: {策略名称: [股票代码列表]}
    """
    # 加载配置
    selector_cfgs = load_selector_config(config_path)
    if not selector_cfgs:
        st.error("未找到选股器配置")
        return {}
    
    # 加载所有股票数据
    with st.spinner("正在加载股票数据..."):
        data = load_all_stock_data(data_dir)
    
    if not data:
        st.error("未能加载任何股票数据")
        return {}
    
    # 获取最新交易日
    trade_date = max(df["date"].max() for df in data.values())
    
    results = {}
    
    # 逐个运行选股器
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, cfg in enumerate(selector_cfgs):
        if cfg.get("activate", True) is False:
            continue
        
        try:
            alias, selector = instantiate_selector(cfg)
            status_text.text(f"正在运行: {alias}...")
            
            # 运行选股
            picks = selector.select(trade_date, data)
            results[alias] = sorted(picks)
            
            # 更新进度
            progress_bar.progress((idx + 1) / len(selector_cfgs))
            
        except Exception as e:
            st.error(f"运行 {cfg.get('alias', cfg.get('class'))} 失败: {e}")
            continue
    
    progress_bar.empty()
    status_text.empty()
    
    return results

def save_selection_results(results: Dict[str, List[str]], filepath: str = "output/selection_results.json"):
    """保存选股结果到文件"""
    # 确保 output 目录存在
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    
    save_data = {
        "timestamp": datetime.now().isoformat(),
        "results": results
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

def load_selection_results(filepath: str = "output/selection_results.json") -> Dict[str, Any]:
    """加载选股结果"""
    file_path = Path(filepath)
    if not file_path.exists():
        return None
    
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# ==================== K线图相关函数 ====================

@st.cache_data
def load_stock_name_map(stocklist_path="./stocklist.csv") -> Dict[str, str]:
    """加载股票代码到名称的映射"""
    try:
        if not Path(stocklist_path).exists():
            st.warning(f"股票列表文件不存在: {stocklist_path}")
            return {}
        
        # 强制将 symbol 列读取为字符串类型，避免丢失前导零
        df = pd.read_csv(stocklist_path, dtype={'symbol': str})
        
        # 检查必需的列
        if 'symbol' not in df.columns or 'name' not in df.columns:
            st.error(f"股票列表文件缺少必需的列。当前列: {df.columns.tolist()}")
            return {}
        
        # 创建代码到名称的映射，symbol列已经是字符串类型
        # 确保去除可能的空格
        name_map = {str(code).strip(): name for code, name in zip(df['symbol'], df['name'])}
        
        return name_map
    except Exception as e:
        st.error(f"加载股票列表文件失败: {e}")
        import traceback
        st.code(traceback.format_exc())
        return {}

@st.cache_data
def get_stock_list(data_dir="./data"):
    """扫描数据目录，获取所有可用股票代码"""
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    csv_files = sorted(data_path.glob("*.csv"))
    return [f.stem for f in csv_files]

def format_stock_display(code: str, name_map: Dict[str, str]) -> str:
    """格式化股票显示：代码 - 名称"""
    # 确保 code 是字符串类型
    code_str = str(code).strip()
    name = name_map.get(code_str, "")
    if name:
        return f"{code_str} - {name}"
    return code_str

@st.cache_data
def get_stock_info(code, data_dir="./data"):
    """获取股票基本信息"""
    try:
        df = load_data(code, data_dir)
        if df.empty:
            return None
        
        latest = df.iloc[-1]
        first = df.iloc[0]
        
        return {
            "code": code,
            "start_date": first.name.strftime("%Y-%m-%d"),
            "end_date": latest.name.strftime("%Y-%m-%d"),
            "total_days": len(df),
            "latest_close": latest["Close"],
            "latest_volume": latest["Volume"]
        }
    except Exception as e:
        return None

@st.cache_data
def load_stock_data(code, data_dir, start_date=None, end_date=None):
    """加载股票数据（带缓存）"""
    try:
        df = load_data(code, data_dir)
        if start_date:
            df = df[df.index >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df.index <= pd.to_datetime(end_date)]
        return df
    except Exception as e:
        st.error(f"加载数据失败：{e}")
        return None

def create_plotly_chart(
    df: pd.DataFrame,
    code: str,
    ma_close: list,
    ma_vol: int,
    up_color: str,
    down_color: str,
    show_bbi: bool = True,
    show_macd: bool = True,
    show_kdj: bool = True,
    show_volume: bool = True,
    stock_name: str = ""
):
    """创建 Plotly 图表"""
    # 颜色配置
    MA_COLORS = ["#d62728", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]
    BBI_COLOR = "#ff7f0e"
    DIF_COLOR = "#e377c2"
    DEA_COLOR = "#1f77b4"
    K_COLOR, D_COLOR, J_COLOR = "#f1c40f", "#3498db", "#8e44ad"
    VOL_MA_COLOR = "#555"

    # 构建标题
    ma_tags = " / ".join(
        [f"<span style='color:{MA_COLORS[i%len(MA_COLORS)]}'>MA{w}</span>"
         for i, w in enumerate(ma_close or [])]
    ) or "MA"
    
    bbi_tag = f" + <span style='color:{BBI_COLOR}'>BBI</span>" if show_bbi else ""
    price_title = f"价格：K线 + {ma_tags}{bbi_tag}"
    
    macd_title = (
        f"MACD： <span style='color:{up_color}'>■</span>/"
        f"<span style='color:{down_color}'>■</span> + "
        f"<span style='color:{DIF_COLOR}'>— DIF</span> + "
        f"<span style='color:{DEA_COLOR}'>— DEA</span>"
    )
    
    kdj_title = (
        f"KDJ： <span style='color:{K_COLOR}'>— K</span> + "
        f"<span style='color:{D_COLOR}'>— D</span> + "
        f"<span style='color:{J_COLOR}'>— J</span>"
    )
    
    vol_ma_tag = (
        f" + <span style='color:{VOL_MA_COLOR}'>— MA{ma_vol}(Volume)</span>"
        if (ma_vol and ma_vol > 1) else ""
    )
    vol_title = (
        f"成交量： <span style='color:{up_color}'>■</span>/"
        f"<span style='color:{down_color}'>■</span>{vol_ma_tag}"
    )

    # 添加均线
    df = add_ma(df.copy(), ma_close, "Close")
    
    # 添加指标
    df = _attach_indicators(df)
    
    # 准备绘图数据
    df_plot = df.reset_index()
    x_axis = list(range(len(df_plot)))
    dates = df_plot["Date"]
    date_strings = [d.strftime("%Y-%m-%d") for d in dates]

    # 动态构建子图配置
    subplot_configs = [price_title]
    row_heights = [0.50]
    n_rows = 1
    
    if show_macd:
        subplot_configs.append(macd_title)
        row_heights.append(0.20)
        n_rows += 1
    
    if show_kdj:
        subplot_configs.append(kdj_title)
        row_heights.append(0.17)
        n_rows += 1
    
    if show_volume:
        subplot_configs.append(vol_title)
        row_heights.append(0.13)
        n_rows += 1

    # 创建子图
    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_configs,
    )

    # 自定义 hover 文本
    hover_texts = [
        f"{date_str}<br>开盘: {open_val:.4f}<br>最高: {high_val:.4f}<br>最低: {low_val:.4f}<br>收盘: {close_val:.4f}"
        for date_str, open_val, high_val, low_val, close_val in zip(
            date_strings, df_plot["Open"], df_plot["High"], df_plot["Low"], df_plot["Close"]
        )
    ]

    # ========== 价格主图 ==========
    current_row = 1
    
    # K线
    candle = go.Candlestick(
        x=x_axis,
        open=df_plot["Open"],
        high=df_plot["High"],
        low=df_plot["Low"],
        close=df_plot["Close"],
        text=hover_texts,
        hoverinfo="text",
        increasing_line_color=up_color,
        decreasing_line_color=down_color,
        increasing_fillcolor=up_color,
        decreasing_fillcolor=down_color,
        name="K线",
        showlegend=False,
    )
    fig.add_trace(candle, row=current_row, col=1)

    # 均线
    for i, w in enumerate(ma_close or []):
        name = f"MA{w}(Close)"
        color = MA_COLORS[i % len(MA_COLORS)]
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot[name], mode="lines",
                name=name, line=dict(width=1.2, color=color),
                hovertemplate=f"{name}: %{{y:.4f}}<extra></extra>"
            ),
            row=current_row, col=1
        )

    # BBI
    if show_bbi:
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["BBI"], mode="lines",
                name="BBI", line=dict(width=1.3, color=BBI_COLOR),
                hovertemplate="BBI: %{y:.4f}<extra></extra>"
            ),
            row=current_row, col=1
        )

    # ========== MACD 面板 ==========
    if show_macd:
        current_row += 1
        macd_colors = [up_color if v >= 0 else down_color for v in df_plot["MACD"].fillna(0)]
        fig.add_trace(
            go.Bar(
                x=x_axis, y=df_plot["MACD"], marker_color=macd_colors, name="MACD",
                customdata=date_strings,
                hovertemplate="<b>%{customdata}</b><br>MACD: %{y:.4f}<extra></extra>"
            ),
            row=current_row, col=1
        )
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["DIF"], mode="lines",
                name="DIF", line=dict(width=1.2, color=DIF_COLOR),
                hovertemplate="DIF: %{y:.4f}<extra></extra>"
            ),
            row=current_row, col=1
        )
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["DEA"], mode="lines",
                name="DEA", line=dict(width=1.2, color=DEA_COLOR),
                hovertemplate="DEA: %{y:.4f}<extra></extra>"
            ),
            row=current_row, col=1
        )

    # ========== KDJ 面板 ==========
    if show_kdj:
        current_row += 1
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["K"], mode="lines",
                name="K", line=dict(width=1.1, color=K_COLOR),
                customdata=date_strings,
                hovertemplate="<b>%{customdata}</b><br>K: %{y:.2f}<extra></extra>"
            ),
            row=current_row, col=1
        )
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["D"], mode="lines",
                name="D", line=dict(width=1.1, color=D_COLOR),
                hovertemplate="D: %{y:.2f}<extra></extra>"
            ),
            row=current_row, col=1
        )
        fig.add_trace(
            go.Scattergl(
                x=x_axis, y=df_plot["J"], mode="lines",
                name="J", line=dict(width=1.1, color=J_COLOR),
                hovertemplate="J: %{y:.2f}<extra></extra>"
            ),
            row=current_row, col=1
        )

    # ========== 成交量面板 ==========
    if show_volume:
        current_row += 1
        vol_colors = [up_color if c >= o else down_color 
                      for o, c in zip(df_plot["Open"], df_plot["Close"])]
        fig.add_trace(
            go.Bar(
                x=x_axis, y=df_plot["Volume"], marker_color=vol_colors, name="Volume",
                customdata=date_strings,
                hovertemplate="<b>%{customdata}</b><br>成交量: %{y:.0f}<extra></extra>"
            ),
            row=current_row, col=1
        )
        if ma_vol and ma_vol > 1:
            vma = df_plot["Volume"].rolling(ma_vol).mean()
            fig.add_trace(
                go.Scattergl(
                    x=x_axis, y=vma, mode="lines",
                    name=f"MA{ma_vol}(Volume)",
                    line=dict(width=1.0, color=VOL_MA_COLOR),
                    hovertemplate=f"MA{ma_vol}(Volume): %{{y:.0f}}<extra></extra>"
                ),
                row=current_row, col=1
            )

    # ========== 布局配置 ==========
    tick_step = max(1, len(dates) // 10)
    tickvals = list(range(0, len(dates), tick_step))
    ticktext = [dates.iloc[i].strftime("%Y-%m-%d") for i in tickvals]

    # 构建图表标题
    chart_title = f"{code} {stock_name}" if stock_name else code
    
    fig.update_layout(
        title=chart_title,
        height=800,
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        margin=dict(l=40, r=20, t=80, b=40),
    )
    
    # 设置 x 轴
    fig.update_xaxes(
        tickvals=tickvals,
        ticktext=ticktext,
        tickangle=45,
        rangeslider_visible=False
    )
    
    # 最后一个子图添加 rangeslider
    fig.update_xaxes(
        rangeslider=dict(visible=True, thickness=0.08),
        row=current_row, col=1
    )

    # 设置 y 轴
    row_idx = 1
    fig.update_yaxes(title_text="价格", autorange=True, row=row_idx, col=1)
    
    if show_macd:
        row_idx += 1
        fig.update_yaxes(title_text="MACD", autorange=True, zeroline=True, row=row_idx, col=1)
    
    if show_kdj:
        row_idx += 1
        fig.update_yaxes(title_text="KDJ", autorange=True, row=row_idx, col=1)
    
    if show_volume:
        row_idx += 1
        fig.update_yaxes(title_text="成交量", autorange=True, row=row_idx, col=1)

    return fig

# ==================== 页面: K线图查看 ====================

def page_kline_viewer():
    st.title("📈 K线图查看")
    st.markdown("---")
    
    # 侧边栏配置
    data_dir = st.session_state.get("data_dir", "./data")
    stocks = get_stock_list(data_dir)
    
    if not stocks:
        st.error(f"❌ 未在 `{data_dir}` 目录下找到任何股票数据文件！")
        st.info("💡 请先使用 `fetch_kline.py` 抓取股票数据。")
        return
    
    st.sidebar.success(f"✅ 找到 {len(stocks)} 只股票")
    
    # 加载股票名称映射
    name_map = load_stock_name_map()
    
    # 添加搜索/筛选功能
    st.sidebar.markdown("### 🔍 搜索股票")
    search_query = st.sidebar.text_input(
        "输入股票代码或名称",
        value="",
        placeholder="例如: 000001 或 平安银行",
        help="支持模糊搜索，输入部分代码或名称即可",
        key="stock_search_input"
    )
    
    # 筛选股票列表
    if search_query.strip():
        # 模糊搜索：代码或名称包含搜索词
        filtered_stocks = []
        search_lower = search_query.lower().strip()
        for code in stocks:
            name = name_map.get(code, "")
            if search_lower in code.lower() or search_lower in name.lower():
                filtered_stocks.append(code)
        
        if filtered_stocks:
            display_stocks = filtered_stocks
            st.sidebar.info(f"🎯 找到 {len(filtered_stocks)} 只匹配的股票")
        else:
            display_stocks = stocks
            st.sidebar.warning("❌ 未找到匹配的股票，显示全部")
    else:
        display_stocks = stocks
    
    # 创建显示选项（代码 - 名称）
    stock_display_options = [format_stock_display(code, name_map) for code in display_stocks]
    
    # 获取默认索引：如果session_state中有记录的股票，使用它的索引，否则使用0
    default_index = 0
    if "kline_selected_stock" in st.session_state:
        last_stock = st.session_state["kline_selected_stock"]
        # 查找上次选择的股票在当前列表中的位置
        for i, code in enumerate(display_stocks):
            if code == last_stock:
                default_index = i
                break
    
    # 添加上一只/下一只按钮（放在选择框之前）
    st.sidebar.markdown("### 🔄 快速切换")
    col_prev, col_next = st.sidebar.columns(2)
    
    # 获取当前股票在列表中的索引
    current_idx = default_index
    
    with col_prev:
        if st.button("⬅️ 上一只", width="stretch", key="prev_stock_btn", disabled=(current_idx <= 0)):
            if current_idx > 0:
                prev_stock = display_stocks[current_idx - 1]
                st.session_state["kline_selected_stock"] = prev_stock
                st.rerun()
    
    with col_next:
        if st.button("下一只 ➡️", width="stretch", key="next_stock_btn", disabled=(current_idx >= len(display_stocks) - 1)):
            if current_idx < len(display_stocks) - 1:
                next_stock = display_stocks[current_idx + 1]
                st.session_state["kline_selected_stock"] = next_stock
                st.rerun()
    
    # 显示当前位置信息
    st.sidebar.caption(f"📍 当前: {current_idx + 1} / {len(display_stocks)}")
    
    # 股票选择
    selected_display = st.sidebar.selectbox(
        "选择股票",
        stock_display_options,
        index=default_index,
        help="从筛选结果中选择一只股票",
        key="kline_stock_selector"
    )
    
    # 从显示文本中提取股票代码
    selected_stock = selected_display.split(" - ")[0] if " - " in selected_display else selected_display
    
    # 保存当前选择到 session_state（只在从下拉框选择时）
    st.session_state["kline_selected_stock"] = selected_stock
    
    # 显示股票信息
    if selected_stock:
        info = get_stock_info(selected_stock, data_dir)
        if info:
            st.sidebar.markdown("### 📋 股票信息")
            st.sidebar.text(f"代码：{info['code']}")
            st.sidebar.text(f"数据起始：{info['start_date']}")
            st.sidebar.text(f"数据结束：{info['end_date']}")
            st.sidebar.text(f"总交易日：{info['total_days']} 天")
            st.sidebar.text(f"最新价格：{info['latest_close']:.2f}")
    
    st.sidebar.markdown("---")
    
    # 日期范围选择
    date_range_option = st.sidebar.radio(
        "日期范围",
        ["全部", "最近1个月", "最近3个月", "最近6个月", "最近1年", "自定义"],
        index=0
    )
    
    start_date = None
    end_date = None
    
    if date_range_option != "全部":
        if date_range_option == "自定义":
            col1, col2 = st.sidebar.columns(2)
            with col1:
                start_date = st.date_input("起始日期", value=None)
            with col2:
                end_date = st.date_input("结束日期", value=None)
        else:
            days_map = {
                "最近1个月": 30,
                "最近3个月": 90,
                "最近6个月": 180,
                "最近1年": 365
            }
            days = days_map.get(date_range_option, 365)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
    
    # 均线配置
    st.sidebar.subheader("📈 均线设置")
    ma_preset = st.sidebar.selectbox(
        "均线预设",
        ["5/10/20", "5/10/20/60", "10/20/30/60", "自定义"],
        index=0
    )
    
    if ma_preset == "自定义":
        ma_input = st.sidebar.text_input("自定义均线（用空格分隔）", "5 10 20")
        ma_close = [int(x) for x in ma_input.split() if x.isdigit()]
    else:
        ma_map = {
            "5/10/20": [5, 10, 20],
            "5/10/20/60": [5, 10, 20, 60],
            "10/20/30/60": [10, 20, 30, 60]
        }
        ma_close = ma_map[ma_preset]
    
    ma_vol = st.sidebar.number_input("成交量均线", min_value=0, max_value=60, value=5, step=1)
    
    # 指标开关
    st.sidebar.subheader("📊 技术指标")
    show_bbi = st.sidebar.checkbox("显示 BBI", value=True)
    show_macd = st.sidebar.checkbox("显示 MACD", value=True)
    show_kdj = st.sidebar.checkbox("显示 KDJ", value=True)
    show_volume = st.sidebar.checkbox("显示成交量", value=True)
    
    # 颜色配置
    with st.sidebar.expander("🎨 颜色设置"):
        up_color = st.color_picker("上涨颜色", "#ec0000")
        down_color = st.color_picker("下跌颜色", "#00da3c")
    
    # 加载并绘制图表
    st.markdown("---")
    
    with st.spinner(f"正在加载 {selected_stock} 的数据..."):
        df = load_stock_data(selected_stock, data_dir, start_date, end_date)
    
    if df is None or df.empty:
        st.warning("⚠️ 没有可显示的数据，请调整日期范围。")
        return
    
    # 显示数据统计
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("数据点数", len(df))
    with col2:
        st.metric("最高价", f"{df['High'].max():.2f}")
    with col3:
        st.metric("最低价", f"{df['Low'].min():.2f}")
    with col4:
        st.metric("平均成交量", f"{df['Volume'].mean():.0f}")
    with col5:
        latest_close = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2] if len(df) > 1 else latest_close
        change_pct = ((latest_close - prev_close) / prev_close * 100) if prev_close != 0 else 0
        st.metric("最新价", f"{latest_close:.2f}", f"{change_pct:+.2f}%")
    
    st.markdown("---")
    
    # 绘制图表
    with st.spinner("正在生成图表..."):
        try:
            # 获取股票名称
            stock_name = name_map.get(selected_stock, "")
            
            fig = create_plotly_chart(
                df, selected_stock, ma_close, ma_vol if ma_vol > 0 else None,
                up_color, down_color, show_bbi, show_macd, show_kdj, show_volume,
                stock_name=stock_name
            )
            
            st.plotly_chart(fig, config={
                'scrollZoom': True,
                'displayModeBar': True,
                'displaylogo': False
            })
            
        except Exception as e:
            st.error(f"❌ 绘图失败：{e}")
            st.exception(e)
    
    # 数据预览
    with st.expander("📄 查看原始数据"):
        st.dataframe(df.tail(50))

# ==================== 选股策略介绍 ====================

STRATEGY_DESCRIPTIONS = {
    "BBIKDJSelector": {
        "name": "BBI趋势回踩选股法",
        "summary": "基于BBI上升趋势和KDJ低位的选股策略",
        "principles": [
            "📊 **价格波动约束**：最近一定周期内收盘价波动幅度受限，确保价格相对稳定",
            "📈 **BBI上升趋势**：多周期均线指标BBI呈现上升态势，允许一定程度的回撤",
            "📉 **KDJ低位**：J值处于低位（小于阈值或分位数），表示短期超卖，具有反弹潜力",
            "🎯 **MACD金叉**：DIF > 0，表示趋势向好",
            "📐 **MA60条件**：当前收盘价站上MA60，且近期存在有效上穿MA60",
            "✅ **知行约束**：收盘价高于长期线，且短期线高于长期线，确保趋势向上"
        ],
        "适用场景": "适合寻找处于上升趋势中，短期回调到位，即将反弹的股票"
    },
    "SuperB1Selector": {
        "name": "SuperB1战法",
        "summary": "基于BBI趋势回踩选股法信号后的回调买点策略",
        "principles": [
            "🔍 **基础信号**：在回看期内存在满足BBI趋势回踩选股法（BBIKDJSelector）的交易日",
            "📊 **横盘整理**：从信号日到当前的收盘价波动率较小，表示横盘整理",
            "📉 **当日下跌**：当前交易日相对前一日出现一定幅度的下跌",
            "📉 **KDJ低位**：J值重新回到低位区域",
            "✅ **双重知行约束**：",
            "   • 信号日：收盘价高于长期线，且短期线高于长期线",
            "   • 当前日：短期线仍高于长期线，保持趋势"
        ],
        "适用场景": "适合在BBI趋势回踩选股法信号出现后，经过横盘整理并再次下探的二次买点"
    },
    "BBIShortLongSelector": {
        "name": "补票战法",
        "summary": "基于长短周期RSV配合BBI的选股策略",
        "principles": [
            "📈 **BBI上升**：多周期均线指标BBI整体呈现上升趋势",
            "📊 **长周期RSV强势**：最近一定天数内，长周期RSV值全部保持在高位（≥上阈值）",
            "🔄 **短周期RSV波动**：短周期RSV出现\"先高后低再高\"的波动模式",
            "   • 先达到高位（≥上阈值）",
            "   • 然后回落到低位（<下阈值）",
            "   • 当前重新回到高位（≥上阈值）",
            "🎯 **MACD金叉**：DIF > 0，表示整体趋势向好",
            "✅ **知行约束**：收盘价高于长期线，且短期线高于长期线"
        ],
        "适用场景": "适合捕捉强势股短期回调后的补涨机会"
    },
    "PeakKDJSelector": {
        "name": "填坑战法",
        "summary": "基于历史峰值回归的选股策略",
        "principles": [
            "🏔️ **峰值识别**：基于开盘价和收盘价的最大值识别历史峰值点",
            "📍 **有效参照峰**：选择合适的历史参照峰值点",
            "   • 最新峰值必须高于参照峰值",
            "   • 参照峰值必须高于区间最低价一定幅度",
            "   • 区间内其他峰值不影响判断",
            "🎯 **价格回归**：当前收盘价接近参照峰值（波动率在阈值内）",
            "📉 **KDJ低位**：J值处于低位，表示短期超卖",
            "✅ **知行约束**：收盘价高于长期线，且短期线高于长期线"
        ],
        "适用场景": "适合寻找股价回到前期高点附近，有望突破或填坑的机会"
    },
    "MA60CrossVolumeWaveSelector": {
        "name": "上穿60放量战法",
        "summary": "基于MA60上穿伴随放量的选股策略",
        "principles": [
            "📈 **有效上穿MA60**：最近一定周期内存在有效上穿MA60均线",
            "📊 **放量上涨**：上穿日到最高点期间的平均成交量显著高于上穿前",
            "   • 上涨波段平均量 ≥ 倍数 × 上穿前平均量",
            "   • 确认上穿伴随明显的资金介入",
            "📐 **MA60上升趋势**：MA60近期呈现向上的回归斜率",
            "📉 **KDJ低位**：J值处于低位区域",
            "✅ **知行约束**：收盘价高于长期线，且短期线高于长期线"
        ],
        "适用场景": "适合捕捉突破MA60压力位并伴随放量的强势股票"
    }
}

# ==================== 页面: 选股分析 ====================

def page_stock_selection():
    st.title("🎯 选股分析")
    st.markdown("---")
    
    data_dir = st.session_state.get("data_dir", "./data")
    config_path = st.session_state.get("config_path", "./configs.json")
    
    # 显示配置信息
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"📂 数据目录: `{data_dir}`")
    with col2:
        st.info(f"⚙️ 配置文件: `{config_path}`")
    
    # 加载配置
    selector_cfgs = load_selector_config(config_path)
    
    if not selector_cfgs:
        st.error("❌ 未找到选股器配置文件或配置为空")
        st.info("💡 请确保 `configs.json` 文件存在且格式正确")
        return
    
    # 显示可用的选股策略
    st.subheader("📋 可用的选股策略")
    
    active_selectors = [cfg for cfg in selector_cfgs if cfg.get("activate", True)]
    
    if not active_selectors:
        st.warning("⚠️ 没有激活的选股策略")
        return
    
    # 以展开式卡片形式显示策略
    for idx, cfg in enumerate(active_selectors):
        class_name = cfg.get('class')
        alias = cfg.get('alias', class_name)
        
        # 获取策略介绍
        description = STRATEGY_DESCRIPTIONS.get(class_name, {})
        strategy_name = description.get("name", alias)
        summary = description.get("summary", "暂无介绍")
        principles = description.get("principles", [])
        scenario = description.get("适用场景", "")
        
        # 创建展开式卡片
        with st.expander(f"**{idx+1}. {strategy_name}** ({class_name})", expanded=(idx == 0)):
            st.markdown(f"**📝 策略简介**")
            st.info(summary)
            
            if principles:
                st.markdown(f"**🎯 核心原理**")
                for principle in principles:
                    st.markdown(f"- {principle}")
            
            if scenario:
                st.markdown(f"**💡 适用场景**")
                st.success(scenario)
            
            # 显示参数配置
            if cfg.get("params"):
                with st.expander("⚙️ 查看参数配置", expanded=False):
                    st.json(cfg.get("params"))
    
    st.markdown("---")
    
    # 运行选股按钮
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        run_button = st.button("🚀 开始选股分析", width="stretch", type="primary")
    
    if run_button:
        st.markdown("### 📊 选股进度")
        
        # 运行选股
        results = run_stock_selection(data_dir, config_path)
        
        if results:
            # 保存结果
            save_selection_results(results)
            st.session_state["selection_results"] = results
            st.session_state["selection_timestamp"] = datetime.now()
            
            st.success("✅ 选股分析完成！")
            
            # 显示汇总结果
            st.markdown("### 📈 选股结果汇总")
            
            summary_data = []
            for strategy, stocks in results.items():
                summary_data.append({
                    "策略名称": strategy,
                    "选中股票数": len(stocks),
                    "股票代码": ", ".join(stocks[:5]) + ("..." if len(stocks) > 5 else "")
                })
            
            summary_df = pd.DataFrame(summary_data)
            st.dataframe(summary_df, hide_index=True)
            
            st.info("💡 切换到「选股结果」页面查看详细信息并浏览股票")
        else:
            st.warning("⚠️ 选股分析未返回任何结果")
    
    # 显示历史结果
    if "selection_results" in st.session_state:
        st.markdown("---")
        st.markdown("### 📜 上次选股结果")
        
        timestamp = st.session_state.get("selection_timestamp")
        if timestamp:
            st.text(f"分析时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        
        results = st.session_state["selection_results"]
        total_stocks = sum(len(stocks) for stocks in results.values())
        st.metric("总选中股票数", total_stocks)

# ==================== 页面: 数据拉取 ====================

def page_fetch_data():
    """从 Tushare 拉取股票数据页面"""
    st.title("📥 从 Tushare 拉取股票数据")
    st.markdown("---")
    
    # 导入 fetch_kline 模块的函数
    try:
        from fetch_kline import (
            set_api, load_codes_from_stocklist, fetch_one
        )
    except ImportError as e:
        st.error(f"❌ 无法导入 fetch_kline 模块: {e}")
        st.info("💡 请确保 fetch_kline.py 文件存在")
        return
    
    # 参数设置区域
    st.subheader("⚙️ 参数设置")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### 🔑 Tushare Token")
        # 从 TuShareToken.txt 读取 token
        token_file = Path("TuShareToken.txt")
        if token_file.exists():
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    tushare_token = f.read().strip()
                if tushare_token:
                    st.success("✅ 已从 TuShareToken.txt 读取 Token")
                else:
                    st.error("❌ TuShareToken.txt 文件为空")
                    tushare_token = ""
            except Exception as e:
                st.error(f"❌ 读取 TuShareToken.txt 失败: {e}")
                tushare_token = ""
        else:
            st.error("❌ TuShareToken.txt 文件不存在")
            st.info("💡 请在工程目录下创建 TuShareToken.txt 文件并输入您的 token")
            tushare_token = ""
        
        st.markdown("#### 📅 日期范围")
        date_range_type = st.radio(
            "日期范围类型",
            ["自定义日期", "快速选择"],
            index=1
        )
        
        if date_range_type == "快速选择":
            quick_range = st.selectbox(
                "快速选择",
                ["最近1年", "最近2年", "最近3年", "最近5年", "全部历史"],
                index=0
            )
            
            end_date_dt = datetime.now()
            if quick_range == "最近1年":
                start_date_dt = end_date_dt - timedelta(days=365)
            elif quick_range == "最近2年":
                start_date_dt = end_date_dt - timedelta(days=730)
            elif quick_range == "最近3年":
                start_date_dt = end_date_dt - timedelta(days=1095)
            elif quick_range == "最近5年":
                start_date_dt = end_date_dt - timedelta(days=1825)
            else:  # 全部历史
                start_date_dt = datetime(2019, 1, 1)
            
            start_date = start_date_dt.date()
            end_date = end_date_dt.date()
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")
        else:
            start_date = st.date_input(
                "起始日期",
                value=(datetime.now() - timedelta(days=365)).date(),
                min_value=datetime(2010, 1, 1).date(),
                max_value=datetime.now().date()
            )
            end_date = st.date_input(
                "结束日期",
                value=datetime.now().date(),
                min_value=datetime(2010, 1, 1).date(),
                max_value=datetime.now().date()
            )
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")
    
    with col2:
        st.markdown("#### 📋 股票列表")
        stocklist_path = st.text_input(
            "股票列表文件路径",
            value="./stocklist.csv",
            help="包含股票代码的 CSV 文件路径"
        )
        
        st.markdown("#### 🚫 排除板块")
        exclude_gem = st.checkbox("排除创业板 (300/301)", value=False)
        exclude_star = st.checkbox("排除科创板 (688)", value=False)
        exclude_bj = st.checkbox("排除北交所 (4/8开头)", value=False)
        
        exclude_boards = set()
        if exclude_gem:
            exclude_boards.add("gem")
        if exclude_star:
            exclude_boards.add("star")
        if exclude_bj:
            exclude_boards.add("bj")
        
        st.markdown("#### 📂 输出设置")
        output_dir = st.text_input(
            "输出目录",
            value=st.session_state.get("data_dir", "./data"),
            help="数据保存的目录路径"
        )
        
        workers = st.number_input(
            "并发线程数",
            min_value=1,
            max_value=20,
            value=6,
            step=1,
            help="同时下载的股票数量，建议 4-8"
        )
    
    st.markdown("---")
    
    # 显示参数摘要
    with st.expander("📋 参数摘要", expanded=False):
        summary_data = {
            "Token 来源": "TuShareToken.txt",
            "起始日期": start_str,
            "结束日期": end_str,
            "股票列表": stocklist_path,
            "排除板块": ", ".join(sorted(exclude_boards)) if exclude_boards else "无",
            "输出目录": output_dir,
            "并发线程数": workers
        }
        for key, value in summary_data.items():
            st.text(f"{key}: {value}")
    
    # 开始拉取按钮
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        fetch_button = st.button("🚀 开始拉取数据", type="primary", use_container_width=True)
    
    if fetch_button:
        # 验证参数
        if not tushare_token:
            st.error("❌ 请设置 Tushare Token")
            return
        
        if not Path(stocklist_path).exists():
            st.error(f"❌ 股票列表文件不存在: {stocklist_path}")
            return
        
        # 验证日期范围
        if start_date > end_date:
            st.error("❌ 起始日期不能晚于结束日期")
            return
        
        # 初始化 Tushare API
        try:
            os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
            os.environ["no_proxy"] = os.environ["NO_PROXY"]
            ts.set_token(tushare_token)
            pro = ts.pro_api()
            set_api(pro)
        except Exception as e:
            st.error(f"❌ Tushare API 初始化失败: {e}")
            return
        
        # 创建输出目录
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载股票代码列表
        try:
            with st.spinner("正在加载股票列表..."):
                codes = load_codes_from_stocklist(Path(stocklist_path), exclude_boards)
            
            if not codes:
                st.warning("⚠️ 股票列表为空或被过滤后无代码")
                return
            
            st.success(f"✅ 成功加载 {len(codes)} 只股票")
            
        except Exception as e:
            st.error(f"❌ 加载股票列表失败: {e}")
            import traceback
            st.code(traceback.format_exc())
            return
        
        # 开始拉取数据
        st.markdown("### 📊 拉取进度")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()
        log_messages = []
        
        def update_log(message):
            log_messages.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            if len(log_messages) > 50:
                log_messages.pop(0)
            log_container.text_area("日志", "\n".join(log_messages[-20:]), height=200)
        
        update_log(f"开始拉取 {len(codes)} 只股票的数据...")
        update_log(f"日期范围: {start_str} → {end_str}")
        update_log(f"输出目录: {out_dir.resolve()}")
        
        success_count = 0
        fail_count = 0
        
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(fetch_one, code, start_str, end_str, out_dir): code
                    for code in codes
                }
                
                completed = 0
                for future in as_completed(futures):
                    code = futures[future]
                    completed += 1
                    
                    try:
                        future.result()  # 获取结果，如果有异常会抛出
                        success_count += 1
                        update_log(f"✅ {code} 拉取成功 ({completed}/{len(codes)})")
                    except Exception as e:
                        fail_count += 1
                        update_log(f"❌ {code} 拉取失败: {str(e)[:50]} ({completed}/{len(codes)})")
                    
                    # 更新进度条
                    progress = completed / len(codes)
                    progress_bar.progress(progress)
                    status_text.text(f"进度: {completed}/{len(codes)} (成功: {success_count}, 失败: {fail_count})")
            
            # 完成
            progress_bar.progress(1.0)
            st.markdown("---")
            st.success(f"✅ 数据拉取完成！")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("总股票数", len(codes))
            with col2:
                st.metric("成功", success_count, delta=f"{success_count/len(codes)*100:.1f}%")
            with col3:
                st.metric("失败", fail_count, delta=f"-{fail_count/len(codes)*100:.1f}%")
            
            st.info(f"💾 数据已保存至: {out_dir.resolve()}")
            st.info("💡 您可以在「K线图查看」页面查看拉取的数据")
            
            # 更新 session state 中的数据目录
            st.session_state["data_dir"] = output_dir
            
        except Exception as e:
            st.error(f"❌ 拉取过程中发生错误: {e}")
            import traceback
            st.code(traceback.format_exc())

# ==================== 页面: 选股结果 ====================

def page_selection_results():
    st.title("📊 选股结果")
    st.markdown("---")
    
    # 加载结果
    if "selection_results" not in st.session_state:
        # 尝试从文件加载
        saved_data = load_selection_results()
        if saved_data:
            st.session_state["selection_results"] = saved_data["results"]
            st.session_state["selection_timestamp"] = datetime.fromisoformat(saved_data["timestamp"])
    
    if "selection_results" not in st.session_state:
        st.warning("⚠️ 暂无选股结果")
        st.info("💡 请先在「选股分析」页面运行选股分析")
        return
    
    results = st.session_state["selection_results"]
    timestamp = st.session_state.get("selection_timestamp")
    
    # 显示时间戳
    if timestamp:
        st.info(f"📅 分析时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 策略选择
    st.sidebar.header("🎯 选择策略")
    strategy_names = list(results.keys())
    
    if not strategy_names:
        st.warning("没有可用的选股策略结果")
        return
    
    selected_strategy = st.sidebar.selectbox(
        "策略名称",
        strategy_names,
        index=0
    )
    
    # 显示该策略的选中股票
    stocks = results[selected_strategy]
    
    st.subheader(f"📋 {selected_strategy}")
    st.metric("选中股票数", len(stocks))
    
    if not stocks:
        st.warning("该策略未选中任何股票")
        return
    
    st.markdown("---")
    
    # 股票列表展示（使用表格）
    st.markdown("### 📈 选中的股票列表")
    
    # 创建股票信息表格
    data_dir = st.session_state.get("data_dir", "./data")
    name_map = load_stock_name_map()
    stock_info_list = []
    
    with st.spinner("正在加载股票信息..."):
        for code in stocks:
            info = get_stock_info(code, data_dir)
            stock_name = name_map.get(code, "")
            if info:
                stock_info_list.append({
                    "股票代码": code,
                    "股票名称": stock_name,
                    "最新价格": f"{info['latest_close']:.2f}",
                    "数据起始": info['start_date'],
                    "数据结束": info['end_date'],
                    "交易日数": info['total_days']
                })
            else:
                stock_info_list.append({
                    "股票代码": code,
                    "股票名称": stock_name,
                    "最新价格": "N/A",
                    "数据起始": "N/A",
                    "数据结束": "N/A",
                    "交易日数": "N/A"
                })
    
    if stock_info_list:
        stock_df = pd.DataFrame(stock_info_list)
        st.dataframe(stock_df, hide_index=True)
    
    st.markdown("---")
    
    # 查看单个股票的K线图
    st.markdown("### 📊 查看股票K线图")
    
    # 添加搜索/筛选功能
    col_search, col_clear = st.columns([3, 1])
    with col_search:
        result_search_query = st.text_input(
            "🔍 搜索股票",
            value="",
            placeholder="输入股票代码或名称快速筛选",
            help="支持模糊搜索",
            key="result_stock_search_input"
        )
    
    # 筛选股票列表
    if result_search_query.strip():
        # 模糊搜索：代码或名称包含搜索词
        filtered_result_stocks = []
        search_lower = result_search_query.lower().strip()
        for code in stocks:
            name = name_map.get(code, "")
            if search_lower in code.lower() or search_lower in name.lower():
                filtered_result_stocks.append(code)
        
        if filtered_result_stocks:
            display_result_stocks = filtered_result_stocks
            st.info(f"🎯 找到 {len(filtered_result_stocks)} 只匹配的股票")
        else:
            display_result_stocks = stocks
            st.warning("❌ 未找到匹配的股票，显示全部")
    else:
        display_result_stocks = stocks
    
    # 创建显示选项（代码 - 名称）
    stock_display_options = [format_stock_display(code, name_map) for code in display_result_stocks]
    
    # 获取默认索引：如果session_state中有记录的股票，使用它的索引，否则使用0
    default_result_index = 0
    if "result_selected_stock" in st.session_state:
        last_stock = st.session_state["result_selected_stock"]
        # 查找上次选择的股票在当前列表中的位置
        for i, code in enumerate(display_result_stocks):
            if code == last_stock:
                default_result_index = i
                break
    
    # 添加上一只/下一只按钮（放在选择框之前）
    col_nav_prev, col_nav_info, col_nav_next = st.columns([1, 2, 1])
    
    # 获取当前股票在列表中的索引
    current_result_idx = default_result_index
    
    with col_nav_prev:
        if st.button("⬅️ 上一只", width="stretch", key="result_prev_stock_btn", disabled=(current_result_idx <= 0)):
            if current_result_idx > 0:
                prev_stock = display_result_stocks[current_result_idx - 1]
                st.session_state["result_selected_stock"] = prev_stock
                st.rerun()
    
    with col_nav_info:
        st.info(f"📍 {current_result_idx + 1} / {len(display_result_stocks)}")
    
    with col_nav_next:
        if st.button("下一只 ➡️", width="stretch", key="result_next_stock_btn", disabled=(current_result_idx >= len(display_result_stocks) - 1)):
            if current_result_idx < len(display_result_stocks) - 1:
                next_stock = display_result_stocks[current_result_idx + 1]
                st.session_state["result_selected_stock"] = next_stock
                st.rerun()
    
    selected_display = st.selectbox(
        "选择要查看的股票",
        stock_display_options,
        index=default_result_index,
        key="result_stock_selector"
    )
    
    # 从显示文本中提取股票代码
    selected_stock = selected_display.split(" - ")[0] if " - " in selected_display else selected_display
    
    # 保存当前选择到 session_state（只在从下拉框选择时）
    st.session_state["result_selected_stock"] = selected_stock
    
    if selected_stock:
        # 简化的参数设置
        col1, col2 = st.columns(2)
        with col1:
            date_range = st.selectbox(
                "时间范围",
                ["最近3个月", "最近6个月", "最近1年", "全部"],
                index=0
            )
        with col2:
            ma_preset = st.selectbox(
                "均线预设",
                ["5/10/20", "5/10/20/60", "10/20/30/60"],
                index=0,
                key="result_ma_preset"
            )
        
        # 处理日期范围
        start_date = None
        if date_range != "全部":
            days_map = {
                "最近3个月": 90,
                "最近6个月": 180,
                "最近1年": 365
            }
            days = days_map.get(date_range, 90)
            start_date = datetime.now() - timedelta(days=days)
        
        # 处理均线
        ma_map = {
            "5/10/20": [5, 10, 20],
            "5/10/20/60": [5, 10, 20, 60],
            "10/20/30/60": [10, 20, 30, 60]
        }
        ma_close = ma_map[ma_preset]
        
        # 加载并显示图表
        with st.spinner(f"正在加载 {selected_stock} 的K线图..."):
            df = load_stock_data(selected_stock, data_dir, start_date, None)
            
            if df is not None and not df.empty:
                # 获取股票名称
                stock_name = name_map.get(selected_stock, "")
                
                fig = create_plotly_chart(
                    df, selected_stock, ma_close, 5,
                    "#ec0000", "#00da3c", True, True, True, True,
                    stock_name=stock_name
                )
                
                st.plotly_chart(fig, use_container_width=True, config={
                    'scrollZoom': True,
                    'displayModeBar': True,
                    'displaylogo': False
                })
            else:
                st.error(f"无法加载 {selected_stock} 的数据")

# ==================== 主应用 ====================

def main():
    # 初始化 session state
    if "data_dir" not in st.session_state:
        st.session_state["data_dir"] = "./data"
    if "config_path" not in st.session_state:
        st.session_state["config_path"] = "./configs.json"
    
    # 侧边栏：页面导航
    st.sidebar.title("📊 股票分析系统")
    st.sidebar.markdown("---")
    
    page = st.sidebar.radio(
        "导航",
        ["📈 K线图查看", "📥 数据拉取", "🎯 选股分析", "📊 选股结果"],
        index=0
    )
    
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ 全局设置")
    
    # 数据目录设置
    data_dir = st.sidebar.text_input("数据目录", value=st.session_state["data_dir"])
    st.session_state["data_dir"] = data_dir
    
    # 配置文件设置（仅在选股相关页面显示）
    if "选股" in page:
        config_path = st.sidebar.text_input("配置文件", value=st.session_state["config_path"])
        st.session_state["config_path"] = config_path
    
    # 路由到对应页面
    if page == "📈 K线图查看":
        page_kline_viewer()
    elif page == "📥 数据拉取":
        page_fetch_data()
    elif page == "🎯 选股分析":
        page_stock_selection()
    elif page == "📊 选股结果":
        page_selection_results()


if __name__ == "__main__":
    main()
