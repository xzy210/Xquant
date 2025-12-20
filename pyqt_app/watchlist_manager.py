import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

class WatchlistManager:
    def __init__(self, filepath: str = "output/favorites.json"):
        self.filepath = Path(filepath)
        self.favorites_groups: Dict[str, List[str]] = {}
        self.load_favorites()

    def load_favorites(self):
        """加载自选股数据"""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.favorites_groups = data.get("groups", {})
            except Exception as e:
                print(f"Error loading favorites: {e}")
                self.favorites_groups = {}
        else:
            self.favorites_groups = {}

    def save_favorites(self):
        """保存自选股数据"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        save_data = {
            "timestamp": datetime.now().isoformat(),
            "groups": self.favorites_groups
        }
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving favorites: {e}")

    def get_all_groups(self) -> List[str]:
        """获取所有分组名称"""
        return list(self.favorites_groups.keys())

    def get_group_stocks(self, group_name: str) -> List[str]:
        """获取指定分组的所有股票"""
        return self.favorites_groups.get(group_name, [])

    def create_group(self, group_name: str) -> Tuple[bool, str]:
        """创建新分组"""
        if not group_name or not group_name.strip():
            return False, "分组名称不能为空"
        
        if group_name in self.favorites_groups:
            return False, f"分组 '{group_name}' 已存在"
        
        self.favorites_groups[group_name] = []
        self.save_favorites()
        return True, f"分组 '{group_name}' 创建成功"

    def delete_group(self, group_name: str) -> Tuple[bool, str]:
        """删除分组"""
        if group_name not in self.favorites_groups:
            return False, f"分组 '{group_name}' 不存在"
        
        del self.favorites_groups[group_name]
        self.save_favorites()
        return True, f"分组 '{group_name}' 已删除"

    def add_to_group(self, group_name: str, stock_code: str) -> Tuple[bool, str]:
        """添加股票到分组"""
        if group_name not in self.favorites_groups:
            return False, f"分组 '{group_name}' 不存在"
        
        if stock_code in self.favorites_groups[group_name]:
            return False, f"股票 {stock_code} 已在分组中"
        
        self.favorites_groups[group_name].append(stock_code)
        self.save_favorites()
        return True, f"已添加 {stock_code} 到 '{group_name}'"

    def remove_from_group(self, group_name: str, stock_code: str) -> Tuple[bool, str]:
        """从分组移除股票"""
        if group_name not in self.favorites_groups:
            return False, f"分组 '{group_name}' 不存在"
        
        if stock_code not in self.favorites_groups[group_name]:
            return False, f"股票 {stock_code} 不在分组中"
        
        self.favorites_groups[group_name].remove(stock_code)
        self.save_favorites()
        return True, f"已从 '{group_name}' 移除 {stock_code}"

    def import_stocks(self, group_name: str, stock_codes: List[str]) -> Tuple[bool, str, int]:
        """批量导入股票到分组"""
        if not group_name:
            return False, "分组名称不能为空", 0
            
        if group_name not in self.favorites_groups:
            self.favorites_groups[group_name] = []
            
        added_count = 0
        for code in stock_codes:
            if code not in self.favorites_groups[group_name]:
                self.favorites_groups[group_name].append(code)
                added_count += 1
                
        self.save_favorites()
        return True, f"成功导入 {added_count} 只股票", added_count

    def update_group_stocks(self, group_name: str, stock_codes: List[str]) -> Tuple[bool, str]:
        """更新（替换）分组内的所有股票"""
        if not group_name:
            return False, "分组名称不能为空"
            
        self.favorites_groups[group_name] = stock_codes
        self.save_favorites()
        return True, f"已更新分组 '{group_name}'"