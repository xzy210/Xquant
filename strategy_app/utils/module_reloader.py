"""
模块热重载工具

用于在不重启应用的情况下重新加载修改后的Python代码
"""
import sys
import importlib
from typing import List, Optional


class ModuleReloader:
    """模块热重载管理器"""
    
    @classmethod
    def reload_strategy_modules(cls, parent_widget=None) -> bool:
        """
        重新加载策略相关的模块
        
        Args:
            parent_widget: 父窗口，用于显示消息框
            
        Returns:
            是否成功重新加载
        """
        try:
            # 收集需要重新加载的模块
            modules_to_reload = []
            
            for name in list(sys.modules.keys()):
                # 只重新加载策略App的模块
                if name.startswith(('strategies.', 'factors.', 'backtest.', 'widgets.', 'utils.')):
                    modules_to_reload.append(name)
            
            if not modules_to_reload:
                if parent_widget:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.information(parent_widget, "热重载", "没有找到需要重新加载的模块")
                return False
            
            # 按依赖顺序排序（基础模块先加载）
            modules_to_reload.sort(key=lambda x: (
                0 if 'base' in x else 
                1 if 'registry' in x else 
                2 if 'models' in x else 
                3
            ))
            
            # 重新加载模块
            reloaded = []
            failed = []
            
            for name in modules_to_reload:
                try:
                    module = sys.modules[name]
                    importlib.reload(module)
                    reloaded.append(name)
                except Exception as e:
                    failed.append((name, str(e)))
            
            # 显示结果
            if parent_widget:
                from PyQt6.QtWidgets import QMessageBox
                msg = f"成功重新加载 {len(reloaded)} 个模块\n"
                if failed:
                    msg += f"\n失败 {len(failed)} 个模块:\n"
                    for name, error in failed[:5]:  # 只显示前5个错误
                        msg += f"  - {name}: {error}\n"
                
                QMessageBox.information(parent_widget, "热重载完成", msg)
            
            return len(failed) == 0
            
        except Exception as e:
            if parent_widget:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(parent_widget, "热重载失败", f"重新加载模块时出错:\n{str(e)}")
            return False
    
    @classmethod
    def reload_specific_module(cls, module_name: str, parent_widget=None) -> bool:
        """
        重新加载指定的模块
        
        Args:
            module_name: 模块名称，如 'strategies.etf_three_factor_momentum_strategy'
            parent_widget: 父窗口
            
        Returns:
            是否成功
        """
        try:
            if module_name not in sys.modules:
                # 模块尚未导入，尝试导入
                __import__(module_name)
                if parent_widget:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.information(parent_widget, "热重载", f"模块 {module_name} 已导入")
                return True
            
            # 重新加载
            module = sys.modules[module_name]
            importlib.reload(module)
            
            if parent_widget:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(parent_widget, "热重载完成", f"成功重新加载: {module_name}")
            
            return True
            
        except Exception as e:
            if parent_widget:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(parent_widget, "热重载失败", 
                                   f"重新加载 {module_name} 失败:\n{str(e)}")
            return False
    
    @classmethod
    def get_loaded_modules(cls, prefix: Optional[str] = None) -> List[str]:
        """
        获取已加载的模块列表
        
        Args:
            prefix: 模块前缀过滤器，如 'strategies.'
            
        Returns:
            模块名称列表
        """
        modules = list(sys.modules.keys())
        
        if prefix:
            modules = [m for m in modules if m.startswith(prefix)]
        else:
            # 默认只显示策略App的模块
            modules = [m for m in modules if m.startswith(
                ('strategies.', 'factors.', 'backtest.', 'widgets.', 'utils.')
            )]
        
        return sorted(modules)


def add_reload_menu_to_window(window):
    """
    为窗口添加热重载菜单
    
    使用方法:
        from utils.module_reloader import add_reload_menu_to_window
        add_reload_menu_to_window(self)
    """
    # 获取菜单栏
    menubar = window.menuBar()
    
    # 创建开发菜单
    dev_menu = menubar.addMenu("开发(&D)")
    
    from PyQt6.QtGui import QAction, QKeySequence
    
    # 重新加载所有策略模块
    reload_all_action = QAction("重新加载所有模块(&R)", window)
    reload_all_action.setShortcut(QKeySequence("F5"))
    reload_all_action.triggered.connect(lambda: ModuleReloader.reload_strategy_modules(window))
    dev_menu.addAction(reload_all_action)
    
    # 重新加载指定模块
    reload_specific_action = QAction("重新加载指定模块(&S)...", window)
    reload_specific_action.triggered.connect(lambda: _show_reload_dialog(window))
    dev_menu.addAction(reload_specific_action)
    
    dev_menu.addSeparator()
    
    # 查看已加载模块
    list_modules_action = QAction("查看已加载模块(&L)", window)
    list_modules_action.triggered.connect(lambda: _show_loaded_modules(window))
    dev_menu.addAction(list_modules_action)


def _show_reload_dialog(parent):
    """显示重新加载指定模块的对话框"""
    from PyQt6.QtWidgets import QInputDialog
    
    modules = ModuleReloader.get_loaded_modules()
    
    module_name, ok = QInputDialog.getItem(
        parent,
        "重新加载模块",
        "选择要重新加载的模块:",
        modules,
        editable=True
    )
    
    if ok and module_name:
        ModuleReloader.reload_specific_module(module_name, parent)


def _show_loaded_modules(parent):
    """显示已加载模块列表"""
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
    
    dialog = QDialog(parent)
    dialog.setWindowTitle("已加载模块")
    dialog.resize(600, 400)
    
    layout = QVBoxLayout(dialog)
    
    text = QTextEdit()
    text.setReadOnly(True)
    
    # 按类别分组显示
    modules = ModuleReloader.get_loaded_modules()
    
    content = f"共加载 {len(modules)} 个模块\n\n"
    
    categories = {
        'strategies.': "策略模块",
        'factors.': "因子模块", 
        'backtest.': "回测模块",
        'widgets.': "界面组件",
        'utils.': "工具模块",
    }
    
    for prefix, name in categories.items():
        category_modules = [m for m in modules if m.startswith(prefix)]
        if category_modules:
            content += f"=== {name} ({len(category_modules)}) ===\n"
            for m in category_modules:
                content += f"  {m}\n"
            content += "\n"
    
    text.setText(content)
    layout.addWidget(text)
    
    btn = QPushButton("关闭")
    btn.clicked.connect(dialog.accept)
    layout.addWidget(btn)
    
    dialog.exec()
