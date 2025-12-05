# widgets/drawing_tools.py
"""
K线图画线工具模块
"""
import json
import os
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QToolBar, QButtonGroup, QColorDialog, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF, QEvent
from PyQt6.QtGui import QColor, QPen, QAction, QIcon, QPainterPath, QFont

import pyqtgraph as pg
import pandas as pd
import numpy as np
from pathlib import Path


class DrawingType(Enum):
    NONE = "none"
    LINE = "line"          # 直线（无限延伸）
    SEGMENT = "segment"    # 线段
    RECT = "rect"          # 矩形
    ARC = "arc"            # U形线/圆弧
    TEXT_B = "text_b"      # B点标记
    TEXT_S = "text_s"      # S点标记


class DrawingItem:
    """绘图项基类"""
    def __init__(self, item_type: DrawingType, pen_color: str = "#ffff00", pen_width: int = 2):
        self.item_type = item_type
        self.pen_color = pen_color
        self.pen_width = pen_width
        self.graphics_item = None
        self.points: List[Tuple[float, float]] = []  # (x_index, price)
        self.dates: List[str] = []  # 对应的日期字符串，用于持久化恢复
        self.is_selected = False
    
    def set_pen(self, color: str, width: int):
        self.pen_color = color
        self.pen_width = width
        if self.graphics_item:
            self._apply_style()
            
    def set_selected(self, selected: bool):
        """设置选中状态"""
        self.is_selected = selected
        if self.graphics_item:
            self._apply_style()
            
    def _apply_style(self):
        """应用样式（包括选中状态）"""
        if not self.graphics_item:
            return
            
        color = QColor(self.pen_color)
        width = self.pen_width
        
        if self.is_selected:
            # 选中时颜色变亮或加粗
            color = color.lighter(150)
            width += 2
            
        pen = pg.mkPen(color, width=width)
        
        if hasattr(self.graphics_item, 'setPen'):
            self.graphics_item.setPen(pen)
        elif isinstance(self.graphics_item, pg.TextItem):
            self.graphics_item.setColor(color)
            # 文本项选中时可以加个边框或背景，这里简单处理为变色
            
    def move_by(self, dx: float, dy: float):
        """移动图形"""
        if not self.points:
            return
            
        # 更新所有点的位置
        new_points = []
        for x, y in self.points:
            new_points.append((x + dx, y + dy))
        self.points = new_points
        
        self.update_geometry()
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        """更新图形几何形状（子类实现）"""
        pass

    def remove_from_plot(self, plot_item: pg.PlotItem):
        if self.graphics_item:
            plot_item.removeItem(self.graphics_item)
            self.graphics_item = None

    def to_dict(self) -> Dict:
        return {
            "type": self.item_type.value,
            "points": self.points,
            "dates": self.dates,
            "color": self.pen_color,
            "width": self.pen_width
        }

    @staticmethod
    def from_dict(data: Dict) -> 'DrawingItem':
        item_type = DrawingType(data.get("type", "line"))
        item = create_drawing_item(item_type)
        item.points = data.get("points", [])
        # 确保 points 是 float 类型
        item.points = [(float(p[0]), float(p[1])) for p in item.points]
        item.dates = data.get("dates", [])
        item.pen_color = data.get("color", "#ffff00")
        item.pen_width = data.get("width", 2)
        return item


class LineDrawingItem(DrawingItem):
    """直线（无限延伸）"""
    def __init__(self, **kwargs):
        super().__init__(DrawingType.LINE, **kwargs)
        
    def update_preview(self, p1: QPointF, p2: QPointF, plot_item: pg.PlotItem):
        pass

    def create_final_item(self, points: List[QPointF], plot_item: pg.PlotItem):
        if len(points) < 2:
            return
        
        p1, p2 = points[0], points[1]
        self.points = [(p1.x(), p1.y()), (p2.x(), p2.y())]
        
        self.graphics_item = pg.PlotCurveItem(
            pen=pg.mkPen(self.pen_color, width=self.pen_width)
        )
        self.graphics_item.setZValue(10)
        plot_item.addItem(self.graphics_item)
        self.update_geometry(plot_item)
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        if not self.graphics_item or len(self.points) < 2:
            return
            
        p1 = QPointF(*self.points[0])
        p2 = QPointF(*self.points[1])
        
        # 获取当前视图范围
        vb = None
        if plot_item:
            vb = plot_item.vb
        elif self.graphics_item.getViewBox():
            vb = self.graphics_item.getViewBox()
            
        if not vb:
            return
            
        view_range = vb.viewRange()
        x_min, x_max = view_range[0]
        span = x_max - x_min if x_max > x_min else 100
        
        x_start = x_min - span * 100
        x_end = x_max + span * 100
        
        if abs(p2.x() - p1.x()) < 1e-6: # 垂直线
            x_start, x_end = p1.x(), p1.x()
            y_start = -1e9
            y_end = 1e9
        else:
            m = (p2.y() - p1.y()) / (p2.x() - p1.x())
            b = p1.y() - m * p1.x()
            y_start = m * x_start + b
            y_end = m * x_end + b
            
        self.graphics_item.setData(x=[x_start, x_end], y=[y_start, y_end])


class SegmentDrawingItem(DrawingItem):
    """线段"""
    def __init__(self, **kwargs):
        super().__init__(DrawingType.SEGMENT, **kwargs)
        
    def create_final_item(self, points: List[QPointF], plot_item: pg.PlotItem):
        if len(points) < 2:
            return
        p1, p2 = points[0], points[1]
        self.points = [(p1.x(), p1.y()), (p2.x(), p2.y())]
        
        self.graphics_item = pg.PlotCurveItem(
            pen=pg.mkPen(self.pen_color, width=self.pen_width)
        )
        self.graphics_item.setZValue(10)
        plot_item.addItem(self.graphics_item)
        self.update_geometry(plot_item)
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        if not self.graphics_item or len(self.points) < 2:
            return
        p1 = self.points[0]
        p2 = self.points[1]
        self.graphics_item.setData(x=[p1[0], p2[0]], y=[p1[1], p2[1]])


class RectDrawingItem(DrawingItem):
    """矩形"""
    def __init__(self, **kwargs):
        super().__init__(DrawingType.RECT, **kwargs)
        
    def create_final_item(self, points: List[QPointF], plot_item: pg.PlotItem):
        if len(points) < 2:
            return
        p1, p2 = points[0], points[1]
        self.points = [(p1.x(), p1.y()), (p2.x(), p2.y())]
        
        self.graphics_item = pg.PlotCurveItem(
            pen=pg.mkPen(self.pen_color, width=self.pen_width)
        )
        self.graphics_item.setZValue(10)
        plot_item.addItem(self.graphics_item)
        self.update_geometry(plot_item)
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        if not self.graphics_item or len(self.points) < 2:
            return
        p1 = QPointF(*self.points[0])
        p2 = QPointF(*self.points[1])
        
        rect = QRectF(p1, p2).normalized()
        x = [rect.left(), rect.right(), rect.right(), rect.left(), rect.left()]
        y = [rect.top(), rect.top(), rect.bottom(), rect.bottom(), rect.top()]
        
        self.graphics_item.setData(x=x, y=y)


class ArcDrawingItem(DrawingItem):
    """U形线"""
    def __init__(self, **kwargs):
        super().__init__(DrawingType.ARC, **kwargs)
        
    def create_final_item(self, points: List[QPointF], plot_item: pg.PlotItem):
        if len(points) < 2:
            return
        
        p1 = points[0]
        p_mouse = points[1]
        self.points = [(p1.x(), p1.y()), (p_mouse.x(), p_mouse.y())]
        
        self.graphics_item = pg.QtWidgets.QGraphicsPathItem()
        self.graphics_item.setPen(pg.mkPen(self.pen_color, width=self.pen_width))
        self.graphics_item.setZValue(10)
        plot_item.addItem(self.graphics_item)
        self.update_geometry(plot_item)
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        if not self.graphics_item or len(self.points) < 2:
            return
            
        p1 = QPointF(*self.points[0])
        p_mouse = QPointF(*self.points[1])
        
        p2 = QPointF(p_mouse.x(), p1.y())
        
        cy = (4 * p_mouse.y() - p1.y()) / 3
        c1 = QPointF(p1.x(), cy)
        c2 = QPointF(p2.x(), cy)
        
        path = QPainterPath()
        path.moveTo(p1)
        path.cubicTo(c1, c2, p2)
        
        self.graphics_item.setPath(path)


class TextDrawingItem(DrawingItem):
    """文本标记 (B/S)"""
    def __init__(self, text: str, color: str, **kwargs):
        item_type = DrawingType.TEXT_B if text == "B" else DrawingType.TEXT_S
        super().__init__(item_type, pen_color=color, **kwargs)
        self.text = text
        
    def create_final_item(self, points: List[QPointF], plot_item: pg.PlotItem):
        if not points:
            return
        p = points[0]
        self.points = [(p.x(), p.y())]
        
        self.graphics_item = pg.TextItem(
            text=self.text, 
            color=self.pen_color,
            anchor=(0.5, 1) if self.text == "B" else (0.5, 0)
        )
        font = QFont()
        font.setBold(True)
        font.setPointSize(12)
        self.graphics_item.setFont(font)
        
        self.graphics_item.setPos(p.x(), p.y())
        self.graphics_item.setZValue(10)
        plot_item.addItem(self.graphics_item)
        
    def update_geometry(self, plot_item: Optional[pg.PlotItem] = None):
        if not self.graphics_item or not self.points:
            return
        p = self.points[0]
        self.graphics_item.setPos(p[0], p[1])


def create_drawing_item(item_type: DrawingType) -> DrawingItem:
    if item_type == DrawingType.LINE:
        return LineDrawingItem()
    elif item_type == DrawingType.SEGMENT:
        return SegmentDrawingItem()
    elif item_type == DrawingType.RECT:
        return RectDrawingItem()
    elif item_type == DrawingType.ARC:
        return ArcDrawingItem()
    elif item_type == DrawingType.TEXT_B:
        return TextDrawingItem("B", "#ff0000")
    elif item_type == DrawingType.TEXT_S:
        return TextDrawingItem("S", "#00ff00")
    else:
        return DrawingItem(DrawingType.NONE)


class DrawingManager(QWidget):
    """绘图管理器"""
    
    def __init__(self, kline_widget):
        super().__init__(kline_widget)
        self.kline_widget = kline_widget
        self.plot_item = kline_widget.price_plot
        
        self.current_tool = DrawingType.NONE
        self.drawing_items: List[DrawingItem] = []
        self.temp_points: List[QPointF] = []
        self.temp_item: Optional[pg.GraphicsObject] = None
        self.proxy = None
        
        # 选中和拖动状态
        self.selected_item: Optional[DrawingItem] = None
        self.is_dragging = False
        self.last_drag_pos: Optional[QPointF] = None
        
        # 计算数据目录路径
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        self.data_dir = project_root / "data"
        
        # 确保目录存在
        if not self.data_dir.exists():
            # 如果项目结构不同，尝试在当前工作目录下查找
            self.data_dir = Path.cwd() / "data"
            if not self.data_dir.exists():
                self.data_dir.mkdir(parents=True, exist_ok=True)
                
        self.save_path = self.data_dir / "drawings.json"
        
        self.setup_ui()
        self.load_drawings()
        
        # 连接鼠标事件
        self.connect_signals()

    def connect_signals(self):
        """连接信号"""
        if self.plot_item and self.plot_item.scene():
            # 使用 SignalProxy 处理移动事件以提高性能（主要用于绘图预览）
            self.proxy = pg.SignalProxy(self.plot_item.scene().sigMouseMoved, rateLimit=60, slot=self.on_mouse_moved)
            
            # 连接点击事件（主要用于绘图点击）
            try:
                self.plot_item.scene().sigMouseClicked.disconnect(self.on_mouse_clicked)
            except TypeError:
                pass
            self.plot_item.scene().sigMouseClicked.connect(self.on_mouse_clicked)
            
            # 安装事件过滤器以处理拖动
            self.plot_item.scene().installEventFilter(self)

    def eventFilter(self, obj, event):
        """事件过滤器，处理拖动逻辑"""
        if not self.plot_item or not self.plot_item.scene():
            return super().eventFilter(obj, event)

        if obj != self.plot_item.scene():
            return super().eventFilter(obj, event)
            
        if self.is_drawing_active:
            return False
            
        if event.type() == QEvent.Type.GraphicsSceneMousePress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.scenePos()
                # 检查是否在 ViewBox 内
                if not self.plot_item.vb.sceneBoundingRect().contains(pos):
                    return False
                    
                # 检查是否点击了某个图形
                clicked_items = self.plot_item.scene().items(pos)
                found_item = None
                
                for drawing_item in self.drawing_items:
                    if drawing_item.graphics_item in clicked_items:
                        found_item = drawing_item
                        break
                
                # 更新选中状态
                if found_item:
                    if self.selected_item and self.selected_item != found_item:
                        self.selected_item.set_selected(False)
                    
                    self.selected_item = found_item
                    self.selected_item.set_selected(True)
                    
                    # 准备拖动
                    self.is_dragging = True
                    self.last_drag_pos = self.plot_item.vb.mapSceneToView(pos)
                    return True # 消费事件，防止 ViewBox 拖动
                else:
                    # 点击空白处，取消选中
                    if self.selected_item:
                        self.selected_item.set_selected(False)
                        self.selected_item = None
                    self.is_dragging = False
                    self.last_drag_pos = None
                    
        elif event.type() == QEvent.Type.GraphicsSceneMouseMove:
            if self.is_dragging and self.selected_item and self.last_drag_pos:
                pos = event.scenePos()
                mouse_point = self.plot_item.vb.mapSceneToView(pos)
                
                dx = mouse_point.x() - self.last_drag_pos.x()
                dy = mouse_point.y() - self.last_drag_pos.y()
                
                self.selected_item.move_by(dx, dy)
                self.last_drag_pos = mouse_point
                return True # 消费事件
                
        elif event.type() == QEvent.Type.GraphicsSceneMouseRelease:
            if self.is_dragging:
                self.is_dragging = False
                self.last_drag_pos = None
                self.save_drawings()
                return True # 消费事件
                
        return super().eventFilter(obj, event)

    @property
    def is_drawing_active(self) -> bool:
        """是否处于绘图模式"""
        return self.current_tool != DrawingType.NONE

    def update_plot_item(self, new_plot_item):
        """更新 PlotItem 引用（当 KLineWidget 重建图表时调用）"""
        if self.plot_item == new_plot_item:
            return

        # 移除旧的事件过滤器
        if self.plot_item and self.plot_item.scene():
            self.plot_item.scene().removeEventFilter(self)

        self.plot_item = new_plot_item
        self.temp_item = None # 清除临时项引用，因为它属于旧的 plot
        
        # 重新连接信号
        self.connect_signals()
        
        # 如果当前有选中的工具，需要重新应用鼠标模式设置
        if self.current_tool != DrawingType.NONE:
            self.set_tool(self.current_tool)

    def setup_ui(self):
        """创建工具栏"""
        # 这里我们创建一个独立的工具栏窗口部件，可以被添加到主布局中
        self.toolbar = QToolBar("绘图工具")
        self.toolbar.setStyleSheet("""
            QToolBar {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3c3c3c;
                spacing: 5px;
            }
            QToolButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                color: #ffffff;
                padding: 3px;
            }
            QToolButton:hover {
                background-color: #3c3c3c;
                border: 1px solid #555555;
            }
            QToolButton:checked {
                background-color: #0078d4;
                border: 1px solid #0078d4;
            }
        """)
        
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        
        # 添加工具按钮
        tools = [
            ("直线", DrawingType.LINE),
            ("线段", DrawingType.SEGMENT),
            ("矩形", DrawingType.RECT),
            ("U形线", DrawingType.ARC),
            ("B点", DrawingType.TEXT_B),
            ("S点", DrawingType.TEXT_S)
        ]
        
        for name, tool_type in tools:
            action = QAction(name, self)
            action.setCheckable(True)
            action.setData(tool_type)
            action.triggered.connect(lambda checked, t=tool_type: self.set_tool(t if checked else DrawingType.NONE))
            self.toolbar.addAction(action)
            # 这里的 button 需要从 widgetForAction 获取，或者直接用 QToolButton
            # 为了简单，我们依赖 QAction 的 checkable 状态
            
        self.toolbar.addSeparator()
        
        clear_action = QAction("清除", self)
        clear_action.triggered.connect(self.clear_current_stock_drawings)
        self.toolbar.addAction(clear_action)
        
        save_action = QAction("保存", self)
        save_action.triggered.connect(self.save_drawings)
        self.toolbar.addAction(save_action)

    def set_tool(self, tool_type: DrawingType):
        """设置当前工具"""
        self.current_tool = tool_type
        self.temp_points = []
        if self.temp_item:
            self.plot_item.removeItem(self.temp_item)
            self.temp_item = None
            
        # 更新 UI 状态
        for action in self.toolbar.actions():
            if action.data() == tool_type:
                action.setChecked(True)
            elif action.isCheckable():
                action.setChecked(False)
                
        # 设置鼠标模式
        if tool_type != DrawingType.NONE:
            self.plot_item.vb.setMouseEnabled(x=False, y=False)
            self.kline_widget.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.plot_item.vb.setMouseEnabled(x=True, y=True)
            self.kline_widget.setCursor(Qt.CursorShape.ArrowCursor)

    def on_mouse_clicked(self, evt):
        """处理点击事件"""
        # 右键点击
        if evt.button() == Qt.MouseButton.RightButton:
            if self.current_tool != DrawingType.NONE:
                # 取消当前绘制
                self.set_tool(DrawingType.NONE)
            return
            
        if evt.button() != Qt.MouseButton.LeftButton:
            return
            
        # 如果处于绘图模式
        if self.current_tool != DrawingType.NONE:
            pos = evt.scenePos()
            # 检查是否在 ViewBox 内
            if not self.plot_item.vb.sceneBoundingRect().contains(pos):
                 return
                 
            mouse_point = self.plot_item.vb.mapSceneToView(pos)
            self.temp_points.append(mouse_point)
            
            if self.check_drawing_finished():
                self.finish_drawing()
            else:
                self.update_temp_preview()
            return

        # 选择模式的点击逻辑已移至 eventFilter 处理

    def on_mouse_moved(self, evt):
        """处理移动事件"""
        pos = evt[0]
        mouse_point = self.plot_item.vb.mapSceneToView(pos)
        
        # 绘图模式
        if self.current_tool != DrawingType.NONE and self.temp_points:
            self.update_temp_preview(mouse_point)
            return
            
        # 拖动模式已移至 eventFilter 处理

    def check_drawing_finished(self) -> bool:
        """检查是否完成绘制"""
        count = len(self.temp_points)
        if self.current_tool in [DrawingType.LINE, DrawingType.SEGMENT, DrawingType.RECT, DrawingType.ARC]:
            return count >= 2
        elif self.current_tool in [DrawingType.TEXT_B, DrawingType.TEXT_S]:
            return count >= 1
        return False

    def update_temp_preview(self, current_pos: Optional[QPointF] = None):
        """更新临时预览图形"""
        if not self.temp_points:
            return
            
        points = self.temp_points.copy()
        if current_pos:
            points.append(current_pos)
            
        if self.temp_item:
            self.plot_item.removeItem(self.temp_item)
            self.temp_item = None
            
        # 根据工具类型绘制预览
        if self.current_tool == DrawingType.LINE:
            if len(points) >= 2:
                # 预览直线
                p1, p2 = points[0], points[1]
                
                # 计算延伸
                view_range = self.plot_item.viewRange()
                x_min, x_max = view_range[0]
                span = x_max - x_min if x_max > x_min else 100
                x_start = x_min - span * 100
                x_end = x_max + span * 100
                
                if abs(p2.x() - p1.x()) < 1e-6:
                    x_start, x_end = p1.x(), p1.x()
                    y_start, y_end = -1e9, 1e9
                else:
                    m = (p2.y() - p1.y()) / (p2.x() - p1.x())
                    b = p1.y() - m * p1.x()
                    y_start = m * x_start + b
                    y_end = m * x_end + b

                self.temp_item = pg.PlotCurveItem(
                    x=[x_start, x_end], y=[y_start, y_end],
                    pen=pg.mkPen("#ffff00", width=1, style=Qt.PenStyle.DashLine)
                )
                self.temp_item.setZValue(100) # 预览层级最高
                self.plot_item.addItem(self.temp_item)
                
        elif self.current_tool == DrawingType.SEGMENT:
            if len(points) >= 2:
                p1, p2 = points[0], points[1]
                self.temp_item = pg.PlotCurveItem(
                    x=[p1.x(), p2.x()], y=[p1.y(), p2.y()],
                    pen=pg.mkPen("#ffff00", width=1, style=Qt.PenStyle.DashLine)
                )
                self.temp_item.setZValue(100)
                self.plot_item.addItem(self.temp_item)
                
        elif self.current_tool == DrawingType.RECT:
            if len(points) >= 2:
                p1, p2 = points[0], points[1]
                rect = QRectF(p1, p2).normalized()
                x = [rect.left(), rect.right(), rect.right(), rect.left(), rect.left()]
                y = [rect.top(), rect.top(), rect.bottom(), rect.bottom(), rect.top()]
                self.temp_item = pg.PlotCurveItem(
                    x=x, y=y,
                    pen=pg.mkPen("#ffff00", width=1, style=Qt.PenStyle.DashLine)
                )
                self.temp_item.setZValue(100)
                self.plot_item.addItem(self.temp_item)
                
        elif self.current_tool == DrawingType.ARC:
            if len(points) >= 2:
                p1 = points[0]
                p_mouse = points[1]
                
                # 计算终点（与起点等高）
                p2 = QPointF(p_mouse.x(), p1.y())
                
                # 计算控制点
                cy = (4 * p_mouse.y() - p1.y()) / 3
                c1 = QPointF(p1.x(), cy)
                c2 = QPointF(p2.x(), cy)
                
                path = QPainterPath()
                path.moveTo(p1)
                path.cubicTo(c1, c2, p2)
                
                self.temp_item = pg.QtWidgets.QGraphicsPathItem(path)
                self.temp_item.setPen(pg.mkPen("#ffff00", width=1, style=Qt.PenStyle.DashLine))
                self.temp_item.setZValue(100)
                self.plot_item.addItem(self.temp_item)

    def finish_drawing(self):
        """完成绘制"""
        item = create_drawing_item(self.current_tool)
        item.create_final_item(self.temp_points, self.plot_item)
        
        # 记录日期信息用于持久化
        if self.kline_widget.data is not None:
            dates = self.kline_widget.data["date"].values
            item.dates = []
            for p in self.temp_points:
                idx = int(round(p.x()))
                if 0 <= idx < len(dates):
                    item.dates.append(pd.Timestamp(dates[idx]).strftime("%Y-%m-%d"))
                else:
                    item.dates.append("")
        
        self.drawing_items.append(item)
        
        # 清理临时状态
        self.temp_points = []
        if self.temp_item:
            self.plot_item.removeItem(self.temp_item)
            self.temp_item = None
            
        # 自动保存
        self.save_drawings()
        
        # 重置工具（可选：保持工具选中以便连续绘制）
        # self.set_tool(DrawingType.NONE) 
        # 保持工具选中，但需要重置状态
        self.temp_points = []

    def save_drawings(self):
        """保存绘图数据"""
        if not self.kline_widget.stock_code:
            return
            
        try:
            all_drawings = {}
            if os.path.exists(self.save_path):
                with open(self.save_path, 'r', encoding='utf-8') as f:
                    all_drawings = json.load(f)
            
            # 更新当前股票的绘图
            current_drawings = [item.to_dict() for item in self.drawing_items]
            all_drawings[self.kline_widget.stock_code] = current_drawings
            
            with open(self.save_path, 'w', encoding='utf-8') as f:
                json.dump(all_drawings, f, indent=2)
                
        except Exception as e:
            print(f"Error saving drawings: {e}")

    def load_drawings(self):
        """加载绘图数据"""
        # 这个方法只加载数据到内存，不负责渲染（因为可能还没有K线数据）
        # 渲染逻辑在 restore_drawings 中
        pass

    def restore_drawings(self):
        """恢复绘图显示"""
        # 清除现有绘图
        for item in self.drawing_items:
            item.remove_from_plot(self.plot_item)
        self.drawing_items = []
        
        if not self.kline_widget.stock_code or not os.path.exists(self.save_path):
            return
            
        try:
            with open(self.save_path, 'r', encoding='utf-8') as f:
                all_drawings = json.load(f)
                
            stock_drawings = all_drawings.get(self.kline_widget.stock_code, [])
            
            # 获取当前K线数据的日期映射
            date_to_idx = {}
            if self.kline_widget.data is not None:
                dates = self.kline_widget.data["date"].values
                for i, d in enumerate(dates):
                    date_str = pd.Timestamp(d).strftime("%Y-%m-%d")
                    date_to_idx[date_str] = i
            
            for data in stock_drawings:
                item = DrawingItem.from_dict(data)
                
                # 尝试修正坐标
                points = []
                for i, p_data in enumerate(item.points):
                    x, y = p_data
                    # 如果有日期信息，优先使用日期匹配索引
                    if i < len(item.dates) and item.dates[i] in date_to_idx:
                        x = float(date_to_idx[item.dates[i]])
                    points.append(QPointF(x, y))
                
                item.create_final_item(points, self.plot_item)
                self.drawing_items.append(item)
                
        except Exception as e:
            print(f"Error loading drawings: {e}")

    def clear_current_stock_drawings(self):
        """清除当前股票的所有绘图"""
        for item in self.drawing_items:
            item.remove_from_plot(self.plot_item)
        self.drawing_items = []
        self.save_drawings()

    def delete_selection(self):
        """删除选中的图形"""
        if self.selected_item:
            self.selected_item.remove_from_plot(self.plot_item)
            if self.selected_item in self.drawing_items:
                self.drawing_items.remove(self.selected_item)
            self.selected_item = None
            self.save_drawings()