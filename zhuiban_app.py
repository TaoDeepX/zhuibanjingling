# -*- coding: utf-8 -*-
"""
追板精灵复刻版 - 主程序
功能：实时监控A股异动，语音播报，点击跳转通达信
"""

APP_VERSION = "v1.8"

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import time
import json
import os
import win32gui
import win32con
import win32com.client
from datetime import datetime
from typing import Optional, Set

from data_source import DataSource, AlertInfo, AlertType, is_trading_time


class Config:
    """配置管理"""
    CONFIG_FILE = "zhuiban_config.json"
    
    DEFAULTS = {
        "voice_enabled": True,
        "voice_rate": 2,
        "voice_engine": "sapi",                       # sapi 本地(机械) / edge 在线神经(真人感)
        "voice_name": "zh-CN-YunyangNeural",          # edge引擎的发音人
        "tdx_title": "通达信金融终端",
        "hotkey": "A",
        "hot_sector_enabled": True,
        "sector_reason_enabled": True,
        "sector_alert_min_pct": 0.7,
        "alert_types": {
            "封涨停": True,
            "逼近涨停": True,
            "打开涨停": True,
            "即将打开涨停": True,
            "封跌停": True,
            "逼近跌停": True,
            "打开跌停": True,
            "新股开板": True,
            "大幅拉升": True,
            "快速跳水": True,
            "板块异动": True,
        },
        "transparency": 0.75,  # 默认75%透明度
        "topmost": True,
        "check_interval": 3,  # 秒
        "monitor_scope": "all_a",  # all_a=所有A股, custom=自定义板块
        "custom_blocks": [],  # 自定义板块列表
    }
    
    def __init__(self):
        self.data = self.DEFAULTS.copy()
        self.load()
    
    def load(self):
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    self.data.update(saved)
            except:
                pass
    
    def save(self):
        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def get(self, key, default=None):
        return self.data.get(key, default)
    
    def set(self, key, value):
        self.data[key] = value
        self.save()


class VoiceEngine:
    """语音引擎（支持本地 SAPI + 在线 edge-tts 真人神经语音）"""
    
    # 可选发音人（edge-tts 常用）
    EDGE_VOICES = [
        ("zh-CN-XiaoxiaoNeural", "晓晓(女声·自然)"),
        ("zh-CN-YunyangNeural",  "云扬(男声·播音腔·财经推荐)"),
        ("zh-CN-YunxiNeural",    "云希(男声·清朗)"),
        ("zh-CN-YunjianNeural",  "云健(男声·沉稳)"),
        ("zh-CN-XiaoyiNeural",   "晓伊(女声·年轻)"),
        ("zh-CN-liaoning-XiaobeiNeural", "晓北(东北话女声)"),
    ]
    
    def __init__(self):
        self.rate = 2
        self.enabled = True
        self.engine = "sapi"  # "sapi" 或 "edge"
        self.voice_name = "zh-CN-YunyangNeural"  # edge 发音人
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
    
    def set_rate(self, rate: int):
        self.rate = rate
    
    def set_engine(self, engine: str, voice_name: str = None):
        if engine in ("sapi", "edge"):
            self.engine = engine
        if voice_name:
            self.voice_name = voice_name
    
    def speak(self, text: str):
        if self.enabled:
            self.queue.put(text)
    
    def _worker(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Rate = self.rate
            while True:
                text = self.queue.get()
                if not self.enabled:
                    continue
                played = False
                if self.engine == "edge":
                    played = self._speak_edge(text)
                if not played:
                    # edge 失败或选用 sapi，走本地 SAPI
                    try:
                        speaker.Rate = self.rate
                        speaker.Speak(text)
                    except Exception as e:
                        print(f"[Voice] SAPI播放失败: {e}")
        finally:
            pythoncom.CoUninitialize()
    
    def _speak_edge(self, text: str) -> bool:
        """用 edge-tts 合成 mp3 并用 Windows MCI 播放。失败返回False让调用方回退SAPI"""
        try:
            import edge_tts
            import asyncio
            import tempfile
            import os
            import ctypes
            # 语速：把 SAPI 的 -5..+8 映射到 edge 的百分比字符串
            # 0 对应 +0%，每档 ±10%，范围 -50%..+80%
            rate_pct = max(-50, min(100, int(self.rate) * 10))
            rate_str = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
            # 临时文件
            fd, mp3_path = tempfile.mkstemp(suffix='.mp3', prefix='zbv_')
            os.close(fd)
            try:
                async def _synth():
                    comm = edge_tts.Communicate(text, self.voice_name, rate=rate_str)
                    await comm.save(mp3_path)
                asyncio.run(_synth())
                # MCI 播放（Windows 自带，支持 mp3，无需第三方播放库）
                winmm = ctypes.windll.winmm
                alias = f"zbv{threading.get_ident()}"
                # 路径转短名避免中文/空格问题
                cmd_open = f'open "{mp3_path}" type mpegvideo alias {alias}'
                r = winmm.mciSendStringW(cmd_open, None, 0, 0)
                if r != 0:
                    return False
                winmm.mciSendStringW(f'play {alias} wait', None, 0, 0)
                winmm.mciSendStringW(f'close {alias}', None, 0, 0)
                return True
            finally:
                try:
                    os.remove(mp3_path)
                except:
                    pass
        except Exception as e:
            print(f"[Voice] edge-tts播放失败，回退SAPI: {type(e).__name__}: {e}")
            return False


class TdxController:
    """通达信控制器"""
    
    def __init__(self, title_keyword: str = "通达信"):
        self.title_keyword = title_keyword
        self.hwnd = None
    
    def find_window(self) -> Optional[int]:
        """查找通达信窗口"""
        result = [None]
        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if self.title_keyword in title:
                    result[0] = hwnd
                    return False
            return True
        win32gui.EnumWindows(callback, None)
        self.hwnd = result[0]
        return self.hwnd
    
    def is_running(self) -> bool:
        """检测通达信是否运行"""
        return self.find_window() is not None
    
    def jump_to_stock(self, code: str) -> tuple:
        """跳转到指定股票，返回(成功, 错误信息)"""
        hwnd = self.find_window()
        if not hwnd:
            return (False, "通达信未打开，请先启动通达信")
        
        try:
            # 清理股票代码（只保留数字）
            clean_code = ''.join(c for c in code if c.isdigit())
            # 去掉前导0（如果有多余的）
            if len(clean_code) > 6:
                clean_code = clean_code[-6:]  # 取后6位
            
            import ctypes
            user32 = ctypes.windll.user32
            
            # 激活窗口
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.2)  # 等待窗口激活
            
            # 发送股票代码（带scancode）
            for char in clean_code:
                vk = ord(char)
                scan = user32.MapVirtualKeyW(vk, 0)
                user32.keybd_event(vk, scan, 0, 0)
                time.sleep(0.03)
                user32.keybd_event(vk, scan, 2, 0)
                time.sleep(0.03)
            
            time.sleep(0.1)
            
            # 发送回车
            vk_return = 0x0D
            scan_return = user32.MapVirtualKeyW(vk_return, 0)
            user32.keybd_event(vk_return, scan_return, 0, 0)
            time.sleep(0.03)
            user32.keybd_event(vk_return, scan_return, 2, 0)
            
            return (True, None)
        except Exception as e:
            return (False, f"跳转失败: {e}")


class AlertListItem(tk.Frame):
    """异动列表项"""
    
    BG_COLOR = "#1a2530"  # 列表项背景色（深色）
    
    def __init__(self, parent, alert: AlertInfo, on_click=None):
        super().__init__(parent, bg=self.BG_COLOR, cursor="hand2")
        self.alert = alert
        self.on_click = on_click
        
        # 时间
        time_str = alert.timestamp.strftime("%H:%M:%S")
        tk.Label(self, text=time_str, fg="#c0c0c0", bg=self.BG_COLOR, 
                 font=("Consolas", 10), width=8).pack(side=tk.LEFT)
        
        # 类型标签颜色（柔和的颜色）
        type_colors = {
            AlertType.LIMIT_UP: "#e85050",      # 涨停红
            AlertType.NEAR_LIMIT_UP: "#e88050", # 逼近涨停橙
            AlertType.OPEN_LIMIT_UP: "#e8a050", # 打开涨停黄
            AlertType.ABOUT_OPEN_UP: "#e8c050",
            AlertType.LIMIT_DOWN: "#50c878",    # 跌停绿
            AlertType.NEAR_LIMIT_DOWN: "#50a878",
            AlertType.OPEN_LIMIT_DOWN: "#508878",
            AlertType.IPO_OPEN: "#c878e8",      # 新股紫
            AlertType.SURGE: "#e87878",         # 拉升红
            AlertType.PLUNGE: "#78c878",        # 跳水绿
            AlertType.SECTOR_SURGE: "#5090e8",  # 板块蓝
            AlertType.SECTOR_NEW_HIGH: "#e8d050", # 板块新高金
        }
        color = type_colors.get(alert.alert_type, "#e0e0e0")
        
        # 类型
        tk.Label(self, text=f"[{alert.alert_type.value}]", fg=color, bg=self.BG_COLOR,
                 font=("微软雅黑", 10), width=10).pack(side=tk.LEFT)
        
        # 股票名称
        tk.Label(self, text=alert.stock.name, fg="#ffffff", bg=self.BG_COLOR,
                 font=("微软雅黑", 11), width=8, anchor="w").pack(side=tk.LEFT)
        
        # 涨跌幅
        pct = alert.stock.change_pct
        pct_color = "#ff4040" if pct > 0 else "#40ff40" if pct < 0 else "#ffffff"
        pct_text = f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"
        tk.Label(self, text=pct_text, fg=pct_color, bg=self.BG_COLOR,
                 font=("Consolas", 11), width=8).pack(side=tk.LEFT)
        
        # 题材
        if alert.stock.sector:
            tk.Label(self, text=alert.stock.sector, fg="#d0d0d0", bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=5)
        
        # 板块驱动事件（来自选股宝，红色显示，类似图2效果）
        sector_event = getattr(alert.stock, 'sector_event', '')
        if sector_event:
            tk.Label(self, text=sector_event, fg="#ff5050", bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=3)
        
        # 个股标签（来自选股宝）
        label = getattr(alert.stock, 'label', '')
        if label:
            tk.Label(self, text=f"[{label}]", fg="#80c0ff", bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=2)
        
        # 异动原因（来自东财盘中异动流）
        if alert.stock.reason:
            tk.Label(self, text=f"【{alert.stock.reason}】", fg="#f0a040", bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=2)
        
        # 资讯/公告（30分钟内，绿色高亮）
        news = getattr(alert.stock, 'news', '')
        if news:
            tk.Label(self, text=f"《{news}》", fg="#50e890", bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=2)
        
        # 主力资金流（仅显示净流入绝对值>=100万时）
        main_net = getattr(alert.stock, 'main_net', 0) or 0
        if abs(main_net) >= 1_000_000:
            if main_net > 0:
                net_text = f"主力+{main_net/1e8:.2f}亿" if main_net >= 1e8 else f"主力+{main_net/1e4:.0f}万"
                net_color = "#ff6060"
            else:
                net_text = f"主力{main_net/1e8:.2f}亿" if main_net <= -1e8 else f"主力{main_net/1e4:.0f}万"
                net_color = "#60ff60"
            tk.Label(self, text=net_text, fg=net_color, bg=self.BG_COLOR,
                     font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=2)
        
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-Button-1>", self._on_click)
        for child in self.winfo_children():
            child.bind("<Button-1>", self._on_click)
            child.bind("<Double-Button-1>", self._on_click)
    
    def _on_click(self, event=None):
        if self.on_click:
            self.on_click(self.alert.stock.code)


class ZhuiBanApp:
    """追板精灵主程序"""
    
    def __init__(self):
        self.config = Config()
        self.voice = VoiceEngine()
        self.voice.enabled = self.config.get("voice_enabled", True)
        self.voice.set_rate(self.config.get("voice_rate", 2))
        self.voice.set_engine(self.config.get("voice_engine", "sapi"),
                              self.config.get("voice_name", "zh-CN-YunyangNeural"))
        
        self.tdx = TdxController(self.config.get("tdx_title", "通达信"))
        self.data_source = DataSource()
        
        self.running = False
        self.alert_queue = queue.Queue()
        self.announced: Set[str] = set()  # 已播报的（防重复）
        
        self.root = tk.Tk()
        self.root.title(f"追板精灵 {APP_VERSION}")
        self.root.geometry("550x450")
        self.root.configure(bg="#0a1520")  # 深色背景
        
        # 设置透明度
        self.root.attributes("-alpha", self.config.get("transparency", 0.9))
        if self.config.get("topmost", True):
            self.root.attributes("-topmost", True)
        
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 启动队列处理
        self.process_queue()
    
    def build_ui(self):
        """构建界面"""
        # 标题栏
        title_frame = tk.Frame(self.root, bg="#0d1a25", height=40)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        
        tk.Label(title_frame, text=f"📈 追板精灵 {APP_VERSION}", font=("微软雅黑", 13, "bold"),
                 fg="#ffffff", bg="#0d1a25").pack(side=tk.LEFT, padx=10, pady=8)
        
        self.status_var = tk.StringVar(value="● 已停止")
        self.status_lbl = tk.Label(title_frame, textvariable=self.status_var,
                                   fg="#00ff00", bg="#0d1a25", font=("微软雅黑", 10))
        self.status_lbl.pack(side=tk.LEFT, padx=10)
        
        # 设置按钮
        ttk.Button(title_frame, text="⚙", width=3, 
                   command=self.show_settings).pack(side=tk.RIGHT, padx=5, pady=5)
        
        # 控制按钮
        ctrl_frame = tk.Frame(self.root, bg="#0a1520")
        ctrl_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = tk.Button(ctrl_frame, text="▶ 开始监控", 
                                   command=self.start, bg="#4a7c59", fg="white",
                                   font=("微软雅黑", 10), relief=tk.FLAT, padx=15,
                                   disabledforeground="#c0c0c0")
        self.start_btn.pack(side=tk.LEFT, padx=3)
        
        self.stop_btn = tk.Button(ctrl_frame, text="⏹ 停止", 
                                  command=self.stop, bg="#7c5a4a", fg="white",
                                  font=("微软雅黑", 10), relief=tk.FLAT, padx=15,
                                  state=tk.DISABLED, disabledforeground="#c0c0c0")
        self.stop_btn.pack(side=tk.LEFT, padx=3)
        
        tk.Button(ctrl_frame, text="🔊 测试语音", command=self.test_voice,
                  bg="#4a5a7c", fg="white", font=("微软雅黑", 10), 
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=3)
        
        # 统计
        self.stat_var = tk.StringVar(value="异动: 0")
        tk.Label(ctrl_frame, textvariable=self.stat_var, fg="#ffcc00",
                 bg="#0a1520", font=("微软雅黑", 10)).pack(side=tk.RIGHT, padx=10)
        
        # 热门板块
        sector_frame = tk.Frame(self.root, bg="#0d1a25")
        sector_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(sector_frame, text="🔥 热门板块", fg="#ff8800", bg="#0d1a25",
                 font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        
        self.sector_var = tk.StringVar(value="加载中...")
        tk.Label(sector_frame, textvariable=self.sector_var, fg="#e0e0e0", bg="#0d1a25",
                 font=("微软雅黑", 9), wraplength=400, justify=tk.LEFT).pack(side=tk.LEFT, padx=5)
        
        # 异动列表
        list_frame = tk.Frame(self.root, bg="#0a1520")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Label(list_frame, text="📋 实时异动 (点击跳转通达信)", fg="#ffffff", bg="#0a1520",
                 font=("微软雅黑", 10)).pack(anchor="w")
        
        # 滚动列表
        canvas_frame = tk.Frame(list_frame, bg="#0d1a25")
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.canvas = tk.Canvas(canvas_frame, bg="#0d1a25", highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        
        self.alert_list = tk.Frame(self.canvas, bg="#0d1a25")
        self.canvas.create_window((0, 0), window=self.alert_list, anchor="nw")
        
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.alert_list.bind("<Configure>", 
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        
        # 鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", 
                             lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        
        # 底部状态
        bottom = tk.Frame(self.root, bg="#0a1520")
        bottom.pack(fill=tk.X, padx=10, pady=5)
        
        self.time_var = tk.StringVar(value="")
        tk.Label(bottom, textvariable=self.time_var, fg="#b0b0b0", bg="#0a1520",
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        
        self.source_var = tk.StringVar(value="数据来源: 东方财富")
        tk.Label(bottom, textvariable=self.source_var, fg="#909090", bg="#0a1520",
                 font=("微软雅黑", 8)).pack(side=tk.RIGHT)
        
        self.update_time()
    
    def update_time(self):
        self.time_var.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.root.after(1000, self.update_time)
    
    def process_queue(self):
        """处理异动队列"""
        try:
            while True:
                alert = self.alert_queue.get_nowait()
                self.add_alert_item(alert)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)
    
    def add_alert_item(self, alert: AlertInfo):
        """添加异动项"""
        item = AlertListItem(self.alert_list, alert, on_click=self.on_stock_click)
        item.pack(fill=tk.X, pady=1)
        
        # 限制列表长度
        children = self.alert_list.winfo_children()
        if len(children) > 100:
            children[0].destroy()
        
        # 滚动到底部
        self.canvas.yview_moveto(1.0)
    
    def on_stock_click(self, code: str):
        """点击股票跳转"""
        success, error = self.tdx.jump_to_stock(code)
        if not success and error:
            from tkinter import messagebox
            messagebox.showwarning("提示", error)
    
    def test_voice(self):
        self.voice.speak("语音测试成功")
    
    def show_settings(self):
        """显示设置窗口"""
        SettingsWindow(self.root, self.config, self.voice, self.tdx)
    
    def worker(self):
        """工作线程"""
        self.voice.speak("开始监控")
        alert_count = 0
        
        # 记录已播报的股票（按类型）
        announced_limit_up = set()
        announced_limit_down = set()
        announced_open = set()
        announced_surge = set()
        announced_plunge = set()
        last_limit_up_codes = set()
        stock_name_cache = {}  # 股票名称缓存 {code: name}
        
        # 先加载热门板块
        self.update_sectors()
        last_poll_time = 0  # 上次异动轮询时间
        
        while self.running:
            try:
                if not is_trading_time():
                    time.sleep(10)
                    continue
                
                now = datetime.now()
                alerts_config = self.config.get("alert_types", {})
                
                # 异动原因流轮询（混合模式默认30秒，降低东财请求频率）
                poll_interval = self.config.get("change_poll_interval", 30)
                if time.time() - last_poll_time >= poll_interval:
                    self.data_source.poll_stock_changes(interval=poll_interval)
                    last_poll_time = time.time()
                
                # 资讯/公告轮询（独立频率：快讯60秒、公告120秒）
                self.data_source.poll_news_flash(interval=60)
                self.data_source.poll_announcements(interval=120)
                
                # 数据源切换后重置状态，防止差异检测产生误报
                if self.data_source.source_just_switched:
                    self.data_source.source_just_switched = False
                    last_limit_up_codes = set()
                    print("[worker] 数据源已切换，重置涨停追踪状态")
                    # 更新UI数据来源显示
                    if self.data_source.hybrid_mode:
                        east_status = "东财✓" if self.data_source._eastmoney_available else "东财✗"
                        self.source_var.set(f"混合模式：新浪+{east_status}")
                    elif self.data_source.use_sina:
                        self.source_var.set("数据来源：新浪财经")
                    elif self.data_source.use_tencent:
                        self.source_var.set("数据来源：腾讯财经")
                    else:
                        self.source_var.set(f"数据来源：东方财富")
                    self.update_sectors()
                
                # 获取涨停股
                limit_up_list = self.data_source.get_limit_up_stocks()
                current_limit_up_codes = {s['code'] for s in limit_up_list}
                
                for stock in limit_up_list:
                    code = stock['code']
                    name = stock['name']
                    pct = stock.get('change_pct', 10)
                    open_count = stock.get('open_count', 0)
                    
                    # 缓存股票名称
                    stock_name_cache[code] = name
                    
                    # 新涨停
                    if code not in announced_limit_up and alerts_config.get("封涨停", True):
                        announced_limit_up.add(code)
                        self._add_alert(code, name, pct, "封涨停", f"{name} 封涨停", now)
                        alert_count += 1
                    
                    # 打开过涨停（open_count > 0）
                    if open_count > 0 and code not in announced_open and alerts_config.get("打开涨停", True):
                        announced_open.add(code)
                        self._add_alert(code, name, pct, "打开涨停", f"{name} 打开涨停", now)
                        alert_count += 1
                
                # 检测打开涨停（之前涨停，现在不在涨停列表）
                # 防护：当前涨停列表为空时跳过（数据源切换/网络抖动会导致瞬时空列表，不应误判为全部打开涨停）
                if current_limit_up_codes or not last_limit_up_codes:
                    opened_stocks = last_limit_up_codes - current_limit_up_codes
                    for code in opened_stocks:
                        if code not in announced_open and alerts_config.get("打开涨停", True):
                            announced_open.add(code)
                            name = stock_name_cache.get(code, code)
                            # 实时查询当前涨幅，避免显示0.00%
                            quote = self.data_source.get_stock_quote(code)
                            real_pct = quote.get('change_pct', 0) if quote else 0
                            real_name = quote.get('name', name) if quote else name
                            self._add_alert(code, real_name, real_pct, "打开涨停",
                                          f"{real_name} 打开涨停 {real_pct:+.2f}%", now)
                            alert_count += 1
                    last_limit_up_codes = current_limit_up_codes
                else:
                    print(f"[worker] 涨停列表为空但上轮有{len(last_limit_up_codes)}只，疑似数据源抖动，跳过打开涨停检测")
                
                # 获取跌停股
                if alerts_config.get("封跌停", True):
                    limit_down_list = self.data_source.get_limit_down_stocks()
                    for stock in limit_down_list:
                        code = stock['code']
                        name = stock['name']
                        if code not in announced_limit_down:
                            announced_limit_down.add(code)
                            self._add_alert(code, name, stock.get('change_pct', -10), "封跌停", f"{name} 封跌停", now)
                            alert_count += 1
                
                # 获取逼近涨停
                if alerts_config.get("逼近涨停", True):
                    near_list = self.data_source.get_near_limit_stocks()
                    for stock in near_list:
                        code = stock['code']
                        name = stock['name']
                        pct = stock['change_pct']
                        stock_name_cache[code] = name
                        key = f"near_{code}"
                        if key not in self.announced and pct >= 9:
                            self.announced.add(key)
                            self._add_alert(code, name, pct, "逼近涨停", f"{name} 逼近涨停 {pct:.2f}%", now)
                            alert_count += 1
                
                # 获取大幅拉升股票
                if alerts_config.get("大幅拉升", True):
                    surge_list = self.data_source.get_surge_stocks()
                    for stock in surge_list:
                        code = stock['code']
                        name = stock['name']
                        pct = stock['change_pct']
                        stock_name_cache[code] = name
                        if code not in announced_surge and 5 <= pct < 9.9:
                            announced_surge.add(code)
                            self._add_alert(code, name, pct, "大幅拉升", f"{name} 大幅拉升 +{pct:.2f}%", now)
                            alert_count += 1
                
                # 获取快速下跌股票
                if alerts_config.get("快速跳水", True):
                    plunge_list = self.data_source.get_plunge_stocks()
                    for stock in plunge_list:
                        code = stock['code']
                        name = stock['name']
                        pct = stock['change_pct']
                        stock_name_cache[code] = name
                        if code not in announced_plunge and pct <= -5:
                            announced_plunge.add(code)
                            self._add_alert(code, name, pct, "快速跳水", f"{name} 快速跳水 {pct:.2f}%", now)
                            alert_count += 1
                
                # 获取板块异动
                if alerts_config.get("板块异动", True):
                    sector_min_pct = self.config.get("sector_alert_min_pct", 0.7)
                    sector_alerts = self.data_source.get_sector_alerts(min_pct=sector_min_pct)
                    for sector in sector_alerts:
                        key = f"sector_{sector['code']}"
                        if key not in self.announced:
                            self.announced.add(key)
                            leaders = self.data_source.get_sector_top_stocks(sector['code'])
                            self._add_alert(sector['code'], sector['name'], sector['change_pct'], 
                                          "板块异动", f"板块异动 {sector['name']} +{sector['change_pct']:.2f}%", now, leaders)
                            alert_count += 1
                
                # 板块新高检测
                if alerts_config.get("板块新高", True):
                    new_high_min = self.config.get("sector_new_high_min_pct", 2.0)
                    new_highs = self.data_source.detect_sector_new_highs(min_pct=new_high_min)
                    for nh in new_highs[:5]:
                        key = f"newhigh_{nh['code']}_{int(nh['change_pct']*10)}"
                        if key not in self.announced:
                            self.announced.add(key)
                            leader_info = ""
                            if nh.get('leader_name'):
                                leader_info = f" 领涨:{nh['leader_name']}+{nh['leader_pct']:.2f}%"
                            msg = f"板块新高 {nh['name']} +{nh['change_pct']:.2f}%{leader_info}"
                            self._add_alert(nh['code'], nh['name'], nh['change_pct'],
                                          "板块新高", msg, now)
                            alert_count += 1
                
                self.stat_var.set(f"异动: {alert_count}")
                
                # 定期更新板块
                self.update_sectors()
                
                time.sleep(self.config.get("check_interval", 3))
            except Exception as e:
                print(f"监控错误: {e}")
                time.sleep(5)
        
        self.voice.speak("监控已停止")
    
    def _add_alert(self, code, name, pct, alert_type, message, timestamp, sector=""):
        """添加异动"""
        from data_source import StockInfo, AlertInfo, AlertType
        
        type_map = {
            "封涨停": AlertType.LIMIT_UP,
            "打开涨停": AlertType.OPEN_LIMIT_UP,
            "逼近涨停": AlertType.NEAR_LIMIT_UP,
            "封跌停": AlertType.LIMIT_DOWN,
            "打开跌停": AlertType.OPEN_LIMIT_DOWN,
            "大幅拉升": AlertType.SURGE,
            "快速跳水": AlertType.PLUNGE,
            "板块异动": AlertType.SECTOR_SURGE,
            "板块新高": AlertType.SECTOR_NEW_HIGH,
        }
        
        # 获取股票所属概念（如果没有传入）
        if not sector and alert_type not in ("板块异动", "板块新高"):
            try:
                sector = self.data_source.get_stock_concept(code)
            except:
                pass
        
        # 获取异动原因（来自东财盘中异动流）和主力资金流
        reason = ""
        main_net = 0.0
        main_pct = 0.0
        label = ""
        sector_event = ""
        news = ""
        if alert_type not in ("板块异动", "板块新高"):
            try:
                reason = self.data_source.get_alert_reason(code) or ""
            except:
                pass
            # 选股宝个股标签
            try:
                labels = self.data_source.xgb_get_stock_labels([code])
                label = labels.get(code, "")
            except:
                pass
            # 选股宝板块驱动事件（用概念名匹配）
            if sector:
                try:
                    first_sector = sector.split('|')[0] if '|' in sector else sector
                    sector_event = self.data_source.xgb_match_plate_event(first_sector)
                except:
                    pass
            # 个股资讯/公告（30分钟内）
            try:
                news = self.data_source.get_stock_news(code) or ""
            except:
                pass
            # 从股票缓存里取资金流
            cached = self.data_source.stock_cache.get(code)
            if cached:
                main_net = cached.main_net
                main_pct = cached.main_pct
        
        stock = StockInfo(
            code=code, name=name, price=0, change_pct=pct,
            limit_up_price=0, limit_down_price=0, prev_close=0,
            sector=sector, reason=reason, main_net=main_net, main_pct=main_pct,
            label=label, sector_event=sector_event, news=news
        )
        alert = AlertInfo(
            stock=stock,
            alert_type=type_map.get(alert_type, AlertType.LIMIT_UP),
            timestamp=timestamp,
            message=message
        )
        
        self.alert_queue.put(alert)
        
        # 语音播报（概念 + 标签 + 板块事件 + 异动原因，全部读出）
        if self.config.get("voice_enabled", True):
            import re as _re
            def _pct_to_cn(m):
                sign = m.group(1)
                num = m.group(2)
                word = "上涨" if sign != '-' else "下跌"
                return f"{word}百分之{num}"
            # 把 "+7.60%" / "-3.5%" / "7.60%" 替换为 "上涨百分之7.60" / "下跌百分之3.5"
            voice_msg = _re.sub(r'([+-]?)(\d+(?:\.\d+)?)\s*%', _pct_to_cn, message)
            extras = []
            if sector and alert_type != "板块异动":
                extras.append(sector.replace('|', '、'))
            if sector_event:
                extras.append(sector_event)
            if label:
                extras.append(label)
            if reason:
                extras.append(reason)
            if news:
                extras.append(news)
            if extras:
                voice_msg = f"{message} {'，'.join(extras)}"
            self.voice.speak(voice_msg)
    
    def update_sectors(self):
        """更新热门板块"""
        try:
            sectors = self.data_source.get_hot_sectors()
            if sectors:
                text = " | ".join([f"{s['name']}({s['change_pct']:+.1f}%)" 
                                   for s in sectors[:6]])
                self.sector_var.set(text)
            else:
                if self.data_source.use_sina:
                    self.sector_var.set("新浪源已连接，暂未取到热门板块")
                elif self.data_source.use_tencent:
                    self.sector_var.set("腾讯源已连接，暂未取到热门板块")
                else:
                    self.sector_var.set("暂未取到热门板块")
        except:
            if self.data_source.use_sina:
                self.sector_var.set("新浪源热门板块获取失败")
            elif self.data_source.use_tencent:
                self.sector_var.set("腾讯源热门板块获取失败")
            else:
                self.sector_var.set("热门板块获取失败")
    
    def start(self):
        if self.running:
            return
        
        # 先测试网络连接
        self.status_var.set("● 测试网络...")
        self.root.update()
        
        ok, msg = self.data_source.test_connection()
        if not ok:
            from tkinter import messagebox
            messagebox.showerror("网络错误", f"无法获取数据：\n{msg}\n\n请检查网络连接后重试")
            self.status_var.set("● 已停止")
            return
        
        self.running = True
        self.status_var.set("● 监控中")
        self.status_lbl.config(fg="#44ff44")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        # 更新数据来源显示
        if self.data_source.use_sina:
            self.source_var.set("数据来源: 新浪财经")
        elif self.data_source.use_tencent:
            self.source_var.set("数据来源: 腾讯财经")
        else:
            self.source_var.set("数据来源: 东方财富")
        
        threading.Thread(target=self.worker, daemon=True).start()
    
    def stop(self):
        self.running = False
        self.status_var.set("● 已停止")
        self.status_lbl.config(fg="#888")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
    
    def on_close(self):
        self.running = False
        self.config.save()
        time.sleep(0.2)
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()


class SettingsWindow:
    """设置窗口"""
    
    def __init__(self, parent, config: Config, voice: VoiceEngine, tdx: TdxController = None):
        self.config = config
        self.voice = voice
        self.parent = parent  # 主窗口引用
        self.tdx = tdx
        
        self.win = tk.Toplevel(parent)
        self.win.title("设置")
        self.win.geometry("450x500")
        self.win.configure(bg="#1a1a2e")
        self.win.transient(parent)
        self.win.grab_set()
        
        notebook = ttk.Notebook(self.win)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 基本设置
        basic_frame = tk.Frame(notebook, bg="#1a1a2e")
        notebook.add(basic_frame, text="基本设置")
        self.build_basic(basic_frame)
        
        # 异动提醒
        alert_frame = tk.Frame(notebook, bg="#1a1a2e")
        notebook.add(alert_frame, text="异动提醒")
        self.build_alerts(alert_frame)
        
        # 板块监控
        block_frame = tk.Frame(notebook, bg="#1a1a2e")
        notebook.add(block_frame, text="板块监控")
        self.build_blocks(block_frame)
        
        # 按钮
        btn_frame = tk.Frame(self.win, bg="#1a1a2e")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Button(btn_frame, text="保存", command=self.save,
                  bg="#2d5a27", fg="white", padx=20).pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="取消", command=self.win.destroy,
                  bg="#5a2727", fg="white", padx=20).pack(side=tk.RIGHT, padx=5)
    
    def build_basic(self, parent):
        """基本设置"""
        # 语音
        self.voice_var = tk.BooleanVar(value=self.config.get("voice_enabled", True))
        tk.Checkbutton(parent, text="启用语音朗读功能", variable=self.voice_var,
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10)).pack(anchor="w", padx=20, pady=10)
        
        # 语速
        rate_frame = tk.Frame(parent, bg="#1a1a2e")
        rate_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(rate_frame, text="语速:", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.rate_var = tk.IntVar(value=self.config.get("voice_rate", 2))
        ttk.Scale(rate_frame, from_=-5, to=8, variable=self.rate_var, length=150).pack(side=tk.LEFT, padx=10)
        tk.Label(rate_frame, textvariable=self.rate_var, fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        
        # 语音引擎
        eng_frame = tk.Frame(parent, bg="#1a1a2e")
        eng_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(eng_frame, text="语音引擎:", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.engine_var = tk.StringVar(value=self.config.get("voice_engine", "sapi"))
        engine_map = {"sapi": "本地SAPI(离线·机械)", "edge": "Edge在线神经语音(真人感·需联网)"}
        engine_combo = ttk.Combobox(eng_frame, textvariable=self.engine_var,
                                     values=list(engine_map.keys()), state="readonly", width=14)
        engine_combo.pack(side=tk.LEFT, padx=10)
        tk.Label(eng_frame, text="sapi=本地机械 / edge=在线真人",
                 fg="#a0a0a0", bg="#1a1a2e", font=("微软雅黑", 8)).pack(side=tk.LEFT)
        
        # 发音人（仅edge引擎有效）
        voice_frame = tk.Frame(parent, bg="#1a1a2e")
        voice_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(voice_frame, text="发音人:", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.voice_name_var = tk.StringVar(value=self.config.get("voice_name", "zh-CN-YunyangNeural"))
        voice_values = [f"{code}  —  {label}" for code, label in VoiceEngine.EDGE_VOICES]
        # 当前选中项的展示值
        current = self.voice_name_var.get()
        current_display = next((f"{c}  —  {l}" for c, l in VoiceEngine.EDGE_VOICES if c == current), voice_values[0])
        self._voice_display_var = tk.StringVar(value=current_display)
        voice_combo = ttk.Combobox(voice_frame, textvariable=self._voice_display_var,
                                    values=voice_values, state="readonly", width=45)
        voice_combo.pack(side=tk.LEFT, padx=10)
        
        # 通达信标题
        tdx_frame = tk.Frame(parent, bg="#1a1a2e")
        tdx_frame.pack(fill=tk.X, padx=20, pady=10)
        tk.Label(tdx_frame, text="联动通达信窗口标题:", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.tdx_var = tk.StringVar(value=self.config.get("tdx_title", "通达信"))
        tk.Entry(tdx_frame, textvariable=self.tdx_var, width=25).pack(side=tk.LEFT, padx=10)
        
        # 热点题材
        self.hot_var = tk.BooleanVar(value=self.config.get("hot_sector_enabled", True))
        tk.Checkbutton(parent, text="启用实时热点题材", variable=self.hot_var,
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10)).pack(anchor="w", padx=20, pady=5)
        
        # 题材理由
        self.reason_var = tk.BooleanVar(value=self.config.get("sector_reason_enabled", True))
        tk.Checkbutton(parent, text="显示题材上涨理由", variable=self.reason_var,
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10)).pack(anchor="w", padx=20, pady=5)
        
        # 置顶
        self.top_var = tk.BooleanVar(value=self.config.get("topmost", True))
        tk.Checkbutton(parent, text="窗口置顶", variable=self.top_var,
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10)).pack(anchor="w", padx=20, pady=5)
        
        # 透明度
        trans_frame = tk.Frame(parent, bg="#1a1a2e")
        trans_frame.pack(fill=tk.X, padx=20, pady=10)
        tk.Label(trans_frame, text="透明度:", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.trans_var = tk.DoubleVar(value=self.config.get("transparency", 0.9))
        ttk.Scale(trans_frame, from_=0.5, to=1.0, variable=self.trans_var, length=150).pack(side=tk.LEFT, padx=10)
        
        # 异动原因轮询间隔
        poll_frame = tk.Frame(parent, bg="#1a1a2e")
        poll_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Label(poll_frame, text="异动原因轮询间隔(秒):", fg="white", bg="#1a1a2e").pack(side=tk.LEFT)
        self.poll_var = tk.IntVar(value=self.config.get("change_poll_interval", 30))
        ttk.Scale(poll_frame, from_=5, to=30, variable=self.poll_var, length=120).pack(side=tk.LEFT, padx=10)
        tk.Label(poll_frame, textvariable=self.poll_var, fg="white", bg="#1a1a2e", width=3).pack(side=tk.LEFT)

        about_frame = tk.Frame(parent, bg="#252540", highlightbackground="#3a3a5a", highlightthickness=1)
        about_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        tk.Label(about_frame, text="说明 / 更新", fg="#ffd36b", bg="#252540",
                 font=("微软雅黑", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
        about_text = (
            "特色：多数据源切换（东方财富 / 新浪 / 腾讯）、板块异动提醒、板块龙头股摘要、通达信联动\n"
            "当前版本更新：\n"
            "1. 新增新浪备用数据源，东方财富不可用时自动切换\n"
            "2. 优化板块异动阈值，默认 0.7% 即可触发\n"
            "3. 板块异动支持显示龙头股摘要\n"
            "4. 热门板块获取失败时显示明确状态，不再一直停留在加载中"
        )
        tk.Label(about_frame, text=about_text, fg="#d8d8d8", bg="#252540",
                 font=("微软雅黑", 9), justify=tk.LEFT, wraplength=360).pack(anchor="w", padx=12, pady=(0, 10))
    
    def build_alerts(self, parent):
        """异动提醒设置"""
        tk.Label(parent, text="选择要提醒的异动类型:", fg="white", bg="#1a1a2e",
                 font=("微软雅黑", 10)).pack(anchor="w", padx=20, pady=10)
        
        alert_types = self.config.get("alert_types", Config.DEFAULTS["alert_types"])
        self.alert_vars = {}
        
        # 两列布局
        row_frame = None
        for i, (name, enabled) in enumerate(alert_types.items()):
            if i % 2 == 0:
                row_frame = tk.Frame(parent, bg="#1a1a2e")
                row_frame.pack(fill=tk.X, padx=20, pady=2)
            
            var = tk.BooleanVar(value=enabled)
            self.alert_vars[name] = var
            tk.Checkbutton(row_frame, text=name, variable=var,
                           bg="#1a1a2e", fg="white", selectcolor="#333",
                           font=("微软雅黑", 9), width=12, anchor="w").pack(side=tk.LEFT, padx=5)
    
    def build_blocks(self, parent):
        """板块监控设置"""
        tk.Label(parent, text="监控范围", fg="white", bg="#1a1a2e",
                 font=("微软雅黑", 10, "bold")).pack(anchor="w", padx=20, pady=(15, 10))
        
        # 监控范围选择
        self.scope_var = tk.StringVar(value=self.config.get("monitor_scope", "all_a"))
        
        # 所有A股
        all_a_frame = tk.Frame(parent, bg="#1a1a2e")
        all_a_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Radiobutton(all_a_frame, text="所有A股", variable=self.scope_var, value="all_a",
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10), activebackground="#1a1a2e",
                       activeforeground="white").pack(side=tk.LEFT)
        
        # 自定义板块
        custom_frame = tk.Frame(parent, bg="#1a1a2e")
        custom_frame.pack(fill=tk.X, padx=20, pady=5)
        tk.Radiobutton(custom_frame, text="自定义板块", variable=self.scope_var, value="custom",
                       bg="#1a1a2e", fg="white", selectcolor="#333",
                       font=("微软雅黑", 10), activebackground="#1a1a2e",
                       activeforeground="white").pack(side=tk.LEFT)
        tk.Button(custom_frame, text="添加", command=self.add_block,
                  bg="#333", fg="white", padx=10).pack(side=tk.LEFT, padx=10)
        tk.Button(custom_frame, text="删除", command=self.del_block,
                  bg="#333", fg="white", padx=10).pack(side=tk.LEFT)
        
        # 板块列表
        list_frame = tk.Frame(parent, bg="#252540")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # 表头
        header = tk.Frame(list_frame, bg="#333")
        header.pack(fill=tk.X)
        tk.Label(header, text="编号", fg="white", bg="#333", width=8).pack(side=tk.LEFT)
        tk.Label(header, text="板块名称", fg="white", bg="#333", width=15).pack(side=tk.LEFT)
        tk.Label(header, text="板块路径", fg="white", bg="#333", width=25).pack(side=tk.LEFT)
        
        # 列表
        self.block_listbox = tk.Listbox(list_frame, bg="#1a1a2e", fg="white",
                                         selectbackground="#444", height=6)
        self.block_listbox.pack(fill=tk.BOTH, expand=True)
        
        # 加载已有板块
        blocks = self.config.get("custom_blocks", [])
        for i, block in enumerate(blocks):
            self.block_listbox.insert(tk.END, f"{i+1}. {block.get('name', '')} - {block.get('path', '')}")
        
        # 提示
        tk.Label(parent, text="当选择自定义板块的时候单击添加按钮选\n择通达信根目录T0002\\blocknew 存\n放的通达信板块",
                 fg="#888", bg="#1a1a2e", font=("微软雅黑", 9), justify=tk.LEFT).pack(anchor="e", padx=20)
    
    def add_block(self):
        """添加自定义板块"""
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            title="选择通达信板块文件",
            filetypes=[("板块文件", "*.blk"), ("所有文件", "*.*")],
            initialdir="C:\\new_tdx\\T0002\\blocknew"
        )
        if filepath:
            import os
            name = os.path.splitext(os.path.basename(filepath))[0]
            blocks = self.config.get("custom_blocks", [])
            blocks.append({"name": name, "path": filepath})
            self.config.set("custom_blocks", blocks)
            self.block_listbox.insert(tk.END, f"{len(blocks)}. {name} - {filepath}")
    
    def del_block(self):
        """删除选中的板块"""
        selection = self.block_listbox.curselection()
        if selection:
            idx = selection[0]
            self.block_listbox.delete(idx)
            blocks = self.config.get("custom_blocks", [])
            if idx < len(blocks):
                blocks.pop(idx)
                self.config.set("custom_blocks", blocks)
    
    def save(self):
        self.config.set("voice_enabled", self.voice_var.get())
        self.config.set("voice_rate", self.rate_var.get())
        # 语音引擎 & 发音人
        engine_val = self.engine_var.get()
        self.config.set("voice_engine", engine_val)
        # 从 "zh-CN-XXX  —  描述" 里抽出code
        voice_disp = self._voice_display_var.get()
        voice_code = voice_disp.split("  —  ")[0].strip() if "  —  " in voice_disp else voice_disp
        self.config.set("voice_name", voice_code)
        self.voice.set_engine(engine_val, voice_code)
        self.config.set("tdx_title", self.tdx_var.get())
        self.config.set("hot_sector_enabled", self.hot_var.get())
        self.config.set("sector_reason_enabled", self.reason_var.get())
        self.config.set("topmost", self.top_var.get())
        self.config.set("transparency", self.trans_var.get())
        self.config.set("change_poll_interval", self.poll_var.get())
        
        alert_types = {name: var.get() for name, var in self.alert_vars.items()}
        self.config.set("alert_types", alert_types)
        
        # 保存板块监控设置
        self.config.set("monitor_scope", self.scope_var.get())
        
        self.voice.enabled = self.voice_var.get()
        self.voice.set_rate(self.rate_var.get())
        
        # 更新主窗口透明度和置顶
        self.parent.attributes("-alpha", self.trans_var.get())
        self.parent.attributes("-topmost", self.top_var.get())
        
        # 更新通达信标题
        if self.tdx:
            self.tdx.title_keyword = self.tdx_var.get()
        
        self.config.save()
        self.win.destroy()


if __name__ == "__main__":
    app = ZhuiBanApp()
    app.run()
