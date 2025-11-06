# app.py - 股票K线图可视化 Streamlit 应用
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 导入现有模块
from plot_stock import load_data, add_ma, _attach_indicators

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="📈 股票K线图可视化系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== 工具函数 ====================

@st.cache_data
def get_stock_list(data_dir="./data"):
    """扫描数据目录，获取所有可用股票代码"""
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    csv_files = sorted(data_path.glob("*.csv"))
    return [f.stem for f in csv_files]

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
    show_volume: bool = True
):
    """
    创建 Plotly 图表（优化版，返回 fig 对象而不是保存 HTML）
    """
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
            go.Scattergl(  # 使用 Scattergl 加速
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

    fig.update_layout(
        title=f"{code} 蜡烛图 + 技术指标",
        height=800,  # 固定高度
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

# ==================== 主应用 ====================

def main():
    st.title("📈 股票K线图可视化系统")
    st.markdown("---")
    
    # 侧边栏配置
    st.sidebar.header("📊 选择股票")
    
    # 获取股票列表
    data_dir = st.sidebar.text_input("数据目录", value="./data")
    stocks = get_stock_list(data_dir)
    
    if not stocks:
        st.error(f"❌ 未在 `{data_dir}` 目录下找到任何股票数据文件！")
        st.info("💡 请先使用 `fetch_kline.py` 抓取股票数据。")
        return
    
    st.sidebar.success(f"✅ 找到 {len(stocks)} 只股票")
    
    # 股票选择
    selected_stock = st.sidebar.selectbox(
        "选择股票代码",
        stocks,
        index=0,
        help="从数据目录中选择一只股票"
    )
    
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
    st.sidebar.header("⚙️ 图表配置")
    
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
            fig = create_plotly_chart(
                df, selected_stock, ma_close, ma_vol if ma_vol > 0 else None,
                up_color, down_color, show_bbi, show_macd, show_kdj, show_volume
            )
            
            # 显示图表
            st.plotly_chart(fig, use_container_width=True, config={
                'scrollZoom': True,
                'displayModeBar': True,
                'displaylogo': False
            })
            
        except Exception as e:
            st.error(f"❌ 绘图失败：{e}")
            st.exception(e)
    
    # 数据预览
    with st.expander("📄 查看原始数据"):
        st.dataframe(df.tail(50), use_container_width=True)


if __name__ == "__main__":
    main()

