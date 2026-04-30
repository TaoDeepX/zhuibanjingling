# -*- coding: utf-8 -*-
"""
股票异动语音播报工具 - GUI版本
带界面控制，支持开启/关闭播报
"""

import tkinter as tk
from tkinter import ttk, messagebox
import requests
import time
import threading
import pyttsx3
from datetime import datetime
from collections import deque
import configparser
import os
import queue


class StockVoiceAlertGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("股票异动语音播报")
        self.root.geometry("500x450")
        self.root.resizable(True, True)
        
        # 状态
        self.running = False
        self.engine = None
        self.announced_cache = deque(maxlen=200)
        self.message_queue = queue.Queue()
        
        # 配置
        self.config = {
            'enable_涨停': tk.BooleanVar(value=True),
            'enable_跌停': tk.BooleanVar(value=True),
            'enable_拉升': tk.BooleanVar(value=True),
            'enable_跳水': tk.BooleanVar(value=True),
            'enable_异动': tk.BooleanVar(value=True),
            'interval': tk.IntVar(value=3),
            'rate': tk.IntVar(value=180),
        }
        
        self.load_original_config()
        self.init_tts()
        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def load_original_config(self):
        """读取追板精灵原配置"""
        ini_path = os.path.join(os.path.dirname(__file__), '..', '配置.ini')
        if os.path.exists(ini_path):
            try:
                cp = configparser.ConfigParser()
                cp.read(ini_path, encoding='utf-8')
                if cp.has_option('追板精灵', '封涨停'):
                    self.config['enable_涨停'].set(cp.get('追板精灵', '封涨停') == '真')
                if cp.has_option('追板精灵', '封跌停板'):
                    self.config['enable_跌停'].set(cp.get('追板精灵', '封跌停板') == '真')
                if cp.has_option('追板精灵', '大幅拉升'):
                    self.config['enable_拉升'].set(cp.get('追板精灵', '大幅拉升') == '真')
                if cp.has_option('追板精灵', '快速跳水'):
                    self.config['enable_跳水'].set(cp.get('追板精灵', '快速跳水') == '真')
            except:
                pass
    
    def init_tts(self):
        """初始化TTS"""
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty('rate', self.config['rate'].get())
            self.engine.setProperty('volume', 1.0)
            voices = self.engine.getProperty('voices')
            for voice in voices:
                if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                    self.engine.setProperty('voice', voice.id)
                    break
        except Exception as e:
            messagebox.showerror("错误", f"TTS初始化失败: {e}")
            self.engine = None
    
    def create_ui(self):
        """创建界面"""
        # 标题
        title_frame = tk.Frame(self.root)
        title_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(title_frame, text="📢 股票异动语音播报", font=("微软雅黑", 14, "bold")).pack()
        
        # 状态显示
        self.status_var = tk.StringVar(value="● 已停止")
        self.status_label = tk.Label(title_frame, textvariable=self.status_var, 
                                      font=("微软雅黑", 10), fg="gray")
        self.status_label.pack()
        
        # 配置区
        config_frame = ttk.LabelFrame(self.root, text="播报设置")
        config_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # 复选框
        row1 = tk.Frame(config_frame)
        row1.pack(fill=tk.X, padx=5, pady=2)
        ttk.Checkbutton(row1, text="涨停板", variable=self.config['enable_涨停']).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(row1, text="跌停板", variable=self.config['enable_跌停']).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(row1, text="大幅拉升", variable=self.config['enable_拉升']).pack(side=tk.LEFT, padx=10)
        
        row2 = tk.Frame(config_frame)
        row2.pack(fill=tk.X, padx=5, pady=2)
        ttk.Checkbutton(row2, text="快速跳水", variable=self.config['enable_跳水']).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(row2, text="其他异动", variable=self.config['enable_异动']).pack(side=tk.LEFT, padx=10)
        
        # 语速设置
        row3 = tk.Frame(config_frame)
        row3.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(row3, text="语速:").pack(side=tk.LEFT, padx=5)
        ttk.Scale(row3, from_=100, to=300, variable=self.config['rate'], 
                  orient=tk.HORIZONTAL, length=150).pack(side=tk.LEFT)
        tk.Label(row3, textvariable=self.config['rate']).pack(side=tk.LEFT, padx=5)
        
        # 刷新间隔
        tk.Label(row3, text="  刷新间隔(秒):").pack(side=tk.LEFT, padx=5)
        ttk.Spinbox(row3, from_=1, to=10, textvariable=self.config['interval'], 
                    width=5).pack(side=tk.LEFT)
        
        # 控制按钮
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="▶ 开始播报", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止播报", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        ttk.Button(btn_frame, text="🔊 测试语音", command=self.test_voice).pack(side=tk.LEFT, padx=5)
        
        # 日志区
        log_frame = ttk.LabelFrame(self.root, text="播报日志")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, height=12, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # 定时检查消息队列
        self.check_queue()
    
    def log(self, msg):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.message_queue.put(f"[{timestamp}] {msg}\n")
    
    def check_queue(self):
        """检查消息队列"""
        try:
            while True:
                msg = self.message_queue.get_nowait()
                self.log_text.insert(tk.END, msg)
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.check_queue)
    
    def speak(self, text):
        """语音播报"""
        if self.engine is None:
            return
        try:
            self.engine.setProperty('rate', self.config['rate'].get())
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            self.log(f"播报错误: {e}")
    
    def test_voice(self):
        """测试语音"""
        threading.Thread(target=lambda: self.speak("语音播报测试成功"), daemon=True).start()
    
    def fetch_data(self):
        """获取数据"""
        alerts = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/"
        }
        
        type_map = {
            8201: "大幅拉升", 8202: "快速跳水", 8193: "封涨停板",
            8194: "打开涨停板", 8207: "竞价抢筹", 8209: "尾盘拉升", 64: "封跌停板"
        }
        
        try:
            # 异动快报
            url = "https://push2ex.eastmoney.com/getAllStockChanges"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wzchanges",
                "type": "8201,8202,8193,8194,8207,8209,64",
                "pageindex": 0, "pagesize": 30,
                "_": int(time.time() * 1000)
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            data = resp.json()
            
            if data.get('data') and data['data'].get('allstock'):
                for item in data['data']['allstock'][:15]:
                    code = item.get('c', '')
                    name = item.get('n', '')
                    change_type = item.get('t', 0)
                    tm = item.get('tm', '')
                    
                    type_name = type_map.get(change_type, '异动')
                    key = f"{type_name}_{code}_{tm}"
                    
                    # 根据配置过滤
                    should_alert = False
                    if '拉升' in type_name and self.config['enable_拉升'].get():
                        should_alert = True
                    elif '跳水' in type_name and self.config['enable_跳水'].get():
                        should_alert = True
                    elif '涨停' in type_name and self.config['enable_涨停'].get():
                        should_alert = True
                    elif '跌停' in type_name and self.config['enable_跌停'].get():
                        should_alert = True
                    elif self.config['enable_异动'].get():
                        should_alert = True
                    
                    if should_alert and key not in self.announced_cache:
                        self.announced_cache.append(key)
                        alerts.append(f"{name}{type_name}")
        except Exception as e:
            self.log(f"获取数据失败: {e}")
        
        return alerts
    
    def is_trading_time(self):
        """判断交易时间"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        ct = now.hour * 100 + now.minute
        return (915 <= ct <= 1130) or (1300 <= ct <= 1500)
    
    def worker(self):
        """工作线程"""
        self.speak("语音播报已启动")
        
        while self.running:
            try:
                if not self.is_trading_time():
                    self.log("非交易时间，等待中...")
                    time.sleep(30)
                    continue
                
                alerts = self.fetch_data()
                for alert in alerts:
                    if not self.running:
                        break
                    self.log(f"播报: {alert}")
                    self.speak(alert)
                    time.sleep(0.3)
                
                time.sleep(self.config['interval'].get())
            except Exception as e:
                self.log(f"错误: {e}")
                time.sleep(5)
        
        self.speak("播报已停止")
    
    def start(self):
        """开始"""
        if self.running:
            return
        self.running = True
        self.status_var.set("● 运行中")
        self.status_label.config(fg="green")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log("开始监控股票异动...")
        threading.Thread(target=self.worker, daemon=True).start()
    
    def stop(self):
        """停止"""
        self.running = False
        self.status_var.set("● 已停止")
        self.status_label.config(fg="gray")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.log("已停止播报")
    
    def on_close(self):
        """关闭窗口"""
        self.running = False
        time.sleep(0.5)
        self.root.destroy()
    
    def run(self):
        """运行"""
        self.root.mainloop()


if __name__ == "__main__":
    app = StockVoiceAlertGUI()
    app.run()
