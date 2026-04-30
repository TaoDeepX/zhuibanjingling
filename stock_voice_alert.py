# -*- coding: utf-8 -*-
"""
股票异动语音播报工具
独立运行，监控股票异动并进行语音播报
"""

import requests
import json
import time
import threading
import pyttsx3
from datetime import datetime
from collections import deque
import configparser
import os

class StockVoiceAlert:
    def __init__(self):
        self.engine = None
        self.init_tts()
        self.announced_cache = deque(maxlen=200)  # 已播报缓存，避免重复
        self.running = False
        self.config = self.load_config()
        
    def init_tts(self):
        """初始化TTS引擎"""
        try:
            self.engine = pyttsx3.init()
            # 设置语速
            self.engine.setProperty('rate', 180)
            # 设置音量
            self.engine.setProperty('volume', 1.0)
            # 获取可用的声音
            voices = self.engine.getProperty('voices')
            # 尝试使用中文声音
            for voice in voices:
                if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                    self.engine.setProperty('voice', voice.id)
                    break
            print("[TTS] 语音引擎初始化成功")
        except Exception as e:
            print(f"[TTS] 初始化失败: {e}")
            self.engine = None
    
    def load_config(self):
        """加载配置"""
        config = {
            'enable_涨停': True,
            'enable_跌停': True,
            'enable_拉升': True,
            'enable_跳水': True,
            'enable_异动': True,
            'interval': 3,  # 刷新间隔(秒)
        }
        
        # 尝试读取原软件配置
        ini_path = os.path.join(os.path.dirname(__file__), '..', '配置.ini')
        if os.path.exists(ini_path):
            try:
                cp = configparser.ConfigParser()
                cp.read(ini_path, encoding='utf-8')
                if cp.has_option('追板精灵', '封涨停'):
                    config['enable_涨停'] = cp.get('追板精灵', '封涨停') == '真'
                if cp.has_option('追板精灵', '封跌停板'):
                    config['enable_跌停'] = cp.get('追板精灵', '封跌停板') == '真'
                if cp.has_option('追板精灵', '大幅拉升'):
                    config['enable_拉升'] = cp.get('追板精灵', '大幅拉升') == '真'
                if cp.has_option('追板精灵', '快速跳水'):
                    config['enable_跳水'] = cp.get('追板精灵', '快速跳水') == '真'
                print("[配置] 已加载原软件配置")
            except Exception as e:
                print(f"[配置] 读取配置失败，使用默认配置: {e}")
        return config
    
    def speak(self, text):
        """语音播报"""
        if self.engine is None:
            print(f"[播报-无TTS] {text}")
            return
        try:
            print(f"[播报] {text}")
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as e:
            print(f"[播报错误] {e}")
            # 重新初始化引擎
            self.init_tts()
    
    def get_stock_changes(self):
        """获取股票异动数据 - 从东方财富获取"""
        alerts = []
        
        # 获取涨停板数据
        if self.config['enable_涨停']:
            alerts.extend(self.fetch_limit_up())
        
        # 获取跌停板数据
        if self.config['enable_跌停']:
            alerts.extend(self.fetch_limit_down())
        
        # 获取异动数据
        if self.config['enable_异动']:
            alerts.extend(self.fetch_unusual_moves())
        
        return alerts
    
    def fetch_limit_up(self):
        """获取涨停数据"""
        alerts = []
        try:
            url = "https://push2ex.eastmoney.com/getTopicZTPool"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageidx": 1,
                "pagesize": 20,
                "sort": "fbt:asc",
                "date": datetime.now().strftime("%Y%m%d"),
                "_": int(time.time() * 1000)
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            data = resp.json()
            
            if data.get('data') and data['data'].get('pool'):
                for item in data['data']['pool'][:10]:  # 只取前10个
                    code = item.get('c', '')
                    name = item.get('n', '')
                    reason = item.get('hybk', '')  # 涨停原因/板块
                    key = f"涨停_{code}"
                    
                    if key not in self.announced_cache:
                        self.announced_cache.append(key)
                        msg = f"{name}涨停"
                        if reason:
                            msg += f"，{reason}"
                        alerts.append(msg)
        except Exception as e:
            print(f"[涨停数据] 获取失败: {e}")
        return alerts
    
    def fetch_limit_down(self):
        """获取跌停数据"""
        alerts = []
        try:
            url = "https://push2ex.eastmoney.com/getTopicDTPool"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageidx": 1,
                "pagesize": 20,
                "sort": "fund:asc",
                "date": datetime.now().strftime("%Y%m%d"),
                "_": int(time.time() * 1000)
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            data = resp.json()
            
            if data.get('data') and data['data'].get('pool'):
                for item in data['data']['pool'][:5]:
                    code = item.get('c', '')
                    name = item.get('n', '')
                    key = f"跌停_{code}"
                    
                    if key not in self.announced_cache:
                        self.announced_cache.append(key)
                        alerts.append(f"{name}跌停")
        except Exception as e:
            print(f"[跌停数据] 获取失败: {e}")
        return alerts
    
    def fetch_unusual_moves(self):
        """获取异动数据"""
        alerts = []
        try:
            # 东方财富异动快报
            url = "https://push2ex.eastmoney.com/getAllStockChanges"
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wzchanges",
                "type": "8201,8202,8193,8194,8207,8209,64",  # 各种异动类型
                "pageindex": 0,
                "pagesize": 30,
                "_": int(time.time() * 1000)
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/"
            }
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            data = resp.json()
            
            # 异动类型映射
            type_map = {
                8201: "大幅拉升",
                8202: "快速跳水",
                8193: "封涨停板",
                8194: "打开涨停板",
                8207: "竞价抢筹",
                8209: "尾盘拉升",
                64: "封跌停板"
            }
            
            if data.get('data') and data['data'].get('allstock'):
                for item in data['data']['allstock'][:15]:
                    code = item.get('c', '')
                    name = item.get('n', '')
                    change_type = item.get('t', 0)
                    tm = item.get('tm', '')  # 时间
                    
                    type_name = type_map.get(change_type, '异动')
                    key = f"{type_name}_{code}_{tm}"
                    
                    # 根据配置过滤
                    should_alert = False
                    if '拉升' in type_name and self.config['enable_拉升']:
                        should_alert = True
                    elif '跳水' in type_name and self.config['enable_跳水']:
                        should_alert = True
                    elif '涨停' in type_name and self.config['enable_涨停']:
                        should_alert = True
                    elif '跌停' in type_name and self.config['enable_跌停']:
                        should_alert = True
                    elif self.config['enable_异动']:
                        should_alert = True
                    
                    if should_alert and key not in self.announced_cache:
                        self.announced_cache.append(key)
                        alerts.append(f"{name}{type_name}")
        except Exception as e:
            print(f"[异动数据] 获取失败: {e}")
        return alerts
    
    def is_trading_time(self):
        """判断是否在交易时间"""
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:  # 周六日
            return False
        
        current_time = now.hour * 100 + now.minute
        # 9:15-11:30, 13:00-15:00
        if (915 <= current_time <= 1130) or (1300 <= current_time <= 1500):
            return True
        return False
    
    def run(self):
        """主循环"""
        self.running = True
        self.speak("股票语音播报已启动")
        print("=" * 50)
        print("股票异动语音播报工具")
        print("按 Ctrl+C 停止运行")
        print("=" * 50)
        
        while self.running:
            try:
                if not self.is_trading_time():
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易时间，等待中...")
                    time.sleep(30)
                    continue
                
                # 获取异动数据
                alerts = self.get_stock_changes()
                
                # 播报
                for alert in alerts:
                    self.speak(alert)
                    time.sleep(0.5)  # 间隔避免太快
                
                # 等待下次刷新
                time.sleep(self.config['interval'])
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[错误] {e}")
                time.sleep(5)
        
        self.speak("语音播报已停止")
        print("程序已退出")
    
    def stop(self):
        """停止运行"""
        self.running = False


def main():
    alert = StockVoiceAlert()
    try:
        alert.run()
    except KeyboardInterrupt:
        alert.stop()


if __name__ == "__main__":
    main()
