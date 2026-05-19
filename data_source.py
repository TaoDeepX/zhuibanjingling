# -*- coding: utf-8 -*-
"""
数据源模块 - 获取A股实时行情和异动数据
数据来源：东方财富API
"""

import requests
import json
import time
import traceback
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

# 错误日志
_last_error = ""
def get_last_error():
    return _last_error


class AlertType(Enum):
    """异动类型"""
    LIMIT_UP = "封涨停"           # 涨停封板
    NEAR_LIMIT_UP = "逼近涨停"    # 接近涨停
    OPEN_LIMIT_UP = "打开涨停"    # 涨停打开
    ABOUT_OPEN_UP = "即将打开涨停" # 涨停即将打开
    LIMIT_DOWN = "封跌停"         # 跌停封板
    NEAR_LIMIT_DOWN = "逼近跌停"  # 接近跌停
    OPEN_LIMIT_DOWN = "打开跌停"  # 跌停打开
    IPO_OPEN = "新股开板"         # 新股首次打开涨停
    SURGE = "大幅拉升"            # 快速拉升
    PLUNGE = "快速跳水"           # 快速下跌
    SECTOR_SURGE = "板块异动"     # 板块快速上涨
    SECTOR_NEW_HIGH = "板块新高"   # 板块涨幅创日内新高


@dataclass
class StockInfo:
    """股票信息"""
    code: str              # 股票代码
    name: str              # 股票名称
    price: float           # 现价
    change_pct: float      # 涨跌幅(%)
    limit_up_price: float  # 涨停价
    limit_down_price: float # 跌停价
    prev_close: float      # 昨收
    volume: float = 0      # 成交量
    amount: float = 0      # 成交额
    buy1_vol: float = 0    # 买一量（封单）
    sell1_vol: float = 0   # 卖一量
    sector: str = ""       # 所属板块/题材
    is_new: bool = False   # 是否新股
    tags: List[str] = field(default_factory=list)  # 标签：融资、解禁等
    main_net: float = 0    # 主力净流入（元）
    main_pct: float = 0    # 主力净流入占比（%）
    reason: str = ""       # 异动原因（来自getAllStockChanges）
    label: str = ""        # 个股标签（来自选股宝）
    sector_event: str = "" # 板块驱动事件（来自选股宝）
    news: str = ""         # 个股资讯/公告（来自选股宝快讯+东财公告）


@dataclass
class AlertInfo:
    """异动信息"""
    stock: StockInfo
    alert_type: AlertType
    timestamp: datetime
    message: str
    
    def __hash__(self):
        return hash((self.stock.code, self.alert_type.value, self.timestamp.strftime("%H:%M")))


class DataSource:
    """数据源"""
    
    # 备用API域名列表
    API_HOSTS = [
        "https://push2.eastmoney.com",
        "http://push2.eastmoney.com",
        "https://push2ex.eastmoney.com",
        "http://push2ex.eastmoney.com",
        "http://push2his.eastmoney.com",
    ]
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/'
        }
        # 禁用SSL验证（解决部分网络环境问题）
        self.session.verify = False
        # 禁用SSL警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # 当前使用的API基础URL
        self.api_base = self.API_HOSTS[0]
        self.use_tencent = False  # 是否使用腾讯备用数据源
        self.use_sina = False      # 是否使用新浪备用数据源
        # 混合模式：新浪主行情 + 东财辅助（题材/异动原因）
        # 默认关闭：启动时优先尝试东财作为主源，失败时再回退新浪
        self.hybrid_mode = False
        self._eastmoney_available = False  # 东财是否可用（用于辅助查询）
        self._last_concept_query = {}  # 题材查询限流: code -> timestamp
        # 股票历史状态缓存（用于检测状态变化）
        self.stock_cache: Dict[str, StockInfo] = {}
        self.limit_up_stocks: set = set()    # 当前涨停股
        self.limit_down_stocks: set = set()  # 当前跌停股
        self.last_prices: Dict[str, List[Tuple[float, float]]] = {}  # code -> [(time, price), ...]
        # 运行时自动重连机制
        self._consecutive_failures = 0
        self._max_failures_before_reconnect = 3
        self.source_just_switched = False
        # 异动原因缓存：code -> [(timestamp, reason_text), ...]
        self._recent_changes: Dict[str, List[Tuple[float, str]]] = {}
        self._changes_last_poll = 0
        # 东财恢复探测（切到新浪时定期尝试回切）
        self._last_eastmoney_probe = 0
        # 选股宝数据缓存
        self._xgb_label_cache: Dict[str, Tuple[float, str]] = {}  # code -> (time, label)
        self._xgb_plates: List[Dict] = []  # 异动板块列表
        self._xgb_plates_time: float = 0   # 上次刷新时间
        # 板块新高追踪：sector_code -> max_pct (日内最高涨幅)
        self._sector_day_high: Dict[str, float] = {}
        self._sector_day_high_date: str = ""  # 当天日期，换日清空
        # 资讯/公告缓存：code -> [(timestamp, title, source)]
        self._news_cache: Dict[str, List[Tuple[float, str, str]]] = {}
        self._news_last_poll: float = 0
        self._announce_cache: Dict[str, List[Tuple[float, str, str]]] = {}
        self._announce_last_poll: float = 0
    
    def _api_url(self, path: str) -> str:
        """构建API完整URL"""
        return f"{self.api_base}{path}"
    
    def _record_success(self):
        """记录成功，重置失败计数"""
        self._consecutive_failures = 0
    
    def _record_failure_and_maybe_reconnect(self):
        """记录失败，达到阈值时自动重连"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_failures_before_reconnect:
            print(f"[DataSource] 连续失败{self._consecutive_failures}次，尝试切换数据源...")
            self._consecutive_failures = 0
            old_sina, old_tencent = self.use_sina, self.use_tencent
            # 重置当前数据源状态，强制重新检测
            self.use_sina = False
            self.use_tencent = False
            self.api_base = self.API_HOSTS[0]
            ok, msg = self.test_connection()
            print(f"[DataSource] 重连结果: {msg}")
            # 标记数据源发生了切换
            if self.use_sina != old_sina or self.use_tencent != old_tencent:
                self.source_just_switched = True
            return ok
        return False

    # 东财异动类型代码映射（核心代码集）
    CHANGE_TYPE_MAP = {
        4: "有大买盘", 32: "有大卖盘",
        64: "封涨停", 128: "打开涨停", 256: "封跌停", 512: "打开跌停",
        8193: "60日新高", 8194: "60日新低",
        8201: "火箭发射", 8202: "快速反弹", 8203: "加速下跌", 8204: "高台跳水",
        8207: "大笔买入", 8209: "大笔卖出",
        8211: "有大买盘", 8213: "有大卖盘",
        8215: "竞价上涨", 8216: "竞价下跌",
    }

    def poll_stock_changes(self, interval: int = 30):
        """轮询东财异动流，填充异动原因缓存（混合模式下低频调用）"""
        # 混合模式下只要东财可用就尝试拉取；非混合模式下仅东财主源时拉取
        if not self.hybrid_mode and (self.use_sina or self.use_tencent):
            return
        if self.hybrid_mode and not self._eastmoney_available:
            return  # 混合模式但东财不可用
        now = time.time()
        if now - self._changes_last_poll < interval:
            return
        self._changes_last_poll = now
        try:
            type_codes = ",".join(str(k) for k in self.CHANGE_TYPE_MAP.keys())
            resp = self.session.get(
                "https://push2ex.eastmoney.com/getAllStockChanges",
                params={
                    'type': type_codes,
                    'pageindex': 0, 'pagesize': 50,
                    'ut': '7eea3edcaed734bea9cbfc24409ed989',
                    'dpt': 'wzchanges', '_': int(now * 1000),
                }, timeout=5)
            data = resp.json()
            if not data.get('data') or not data['data'].get('allstock'):
                return
            # 清理5分钟前的旧记录
            cutoff = now - 300
            for code in list(self._recent_changes.keys()):
                self._recent_changes[code] = [
                    (t, r) for t, r in self._recent_changes[code] if t > cutoff]
                if not self._recent_changes[code]:
                    del self._recent_changes[code]
            # 写入新异动
            for item in data['data']['allstock']:
                code = item.get('c', '')
                t = item.get('t')
                reason = self.CHANGE_TYPE_MAP.get(t)
                if not code or not reason:
                    continue
                self._recent_changes.setdefault(code, [])
                # 去重：相同原因30秒内只记一次
                if any(r == reason and now - ts < 30
                       for ts, r in self._recent_changes[code]):
                    continue
                self._recent_changes[code].append((now, reason))
                # 每只股票最多保留5条
                if len(self._recent_changes[code]) > 5:
                    self._recent_changes[code] = self._recent_changes[code][-5:]
        except Exception as e:
            pass  # 异动流获取失败不影响主流程

    def get_alert_reason(self, stock_code: str, within_seconds: int = 60) -> str:
        """查询股票最近N秒内的异动原因（去重后用逗号拼接）"""
        if stock_code not in self._recent_changes:
            return ""
        now = time.time()
        recent = [r for ts, r in self._recent_changes[stock_code]
                  if now - ts <= within_seconds]
        # 保持顺序去重
        seen, result = set(), []
        for r in recent:
            if r not in seen:
                seen.add(r)
                result.append(r)
        return "、".join(result[-3:])  # 最多3条原因

    def try_recover_eastmoney(self) -> bool:
        """在使用备用源时，定期探测东财是否恢复。恢复则切回。"""
        if not (self.use_sina or self.use_tencent):
            return False
        now = time.time()
        if now - self._last_eastmoney_probe < 90:  # 90秒探测一次
            return False
        self._last_eastmoney_probe = now
        try:
            # 轻量探测：用最小请求测试东财
            resp = self.session.get(
                f"{self.API_HOSTS[0]}/api/qt/clist/get",
                params={'pn': 1, 'pz': 1, 'fltt': 2, 'invt': 2,
                        'fs': 'm:0+t:6', 'fields': 'f12'},
                timeout=4)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                # 东财恢复，切回
                print("[DataSource] 东财已恢复，切回主源")
                self.use_sina = False
                self.use_tencent = False
                self.api_base = self.API_HOSTS[0]
                self.source_just_switched = True
                return True
        except Exception:
            pass
        return False
    
    # ========== 选股宝数据源（个股标签 + 异动板块驱动事件）==========
    
    XGB_BASE = "https://flash-api.xuangubao.cn/api"
    
    def xgb_get_stock_labels(self, codes: List[str]) -> Dict[str, str]:
        """批量获取个股标签（选股宝），返回 {code: label_text}"""
        if not codes:
            return {}
        now = time.time()
        result = {}
        need_fetch = []
        for c in codes:
            cached = self._xgb_label_cache.get(c)
            if cached and now - cached[0] < 120:
                result[c] = cached[1]
            else:
                need_fetch.append(c)
        if not need_fetch:
            return result
        # 构建symbols参数：000559 -> 000559.SZ
        symbols = []
        for c in need_fetch:
            suffix = "SH" if c.startswith(('6', '5')) else "SZ"
            symbols.append(f"{c}.{suffix}")
        try:
            resp = self.session.get(f"{self.XGB_BASE}/stock_label/labels",
                params={'symbols': ','.join(symbols)}, timeout=5,
                headers={'Referer': 'https://xuangubao.cn/'})
            data = resp.json()
            if data.get('code') == 20000 and data.get('data'):
                for sym, labels in data['data'].items():
                    code = sym.split('.')[0]
                    # 取前3个正面/中性标签
                    texts = []
                    for lb in labels[:3]:
                        name = lb.get('label_name', '')
                        desc = lb.get('description', '')
                        if name:
                            texts.append(name)
                    label_str = '、'.join(texts)
                    self._xgb_label_cache[code] = (now, label_str)
                    result[code] = label_str
        except Exception as e:
            print(f"[XGB] 个股标签获取失败: {type(e).__name__}")
        return result
    
    def xgb_get_surge_plates(self) -> List[Dict]:
        """获取异动板块及驱动事件（选股宝），60秒缓存"""
        now = time.time()
        if now - self._xgb_plates_time < 30 and self._xgb_plates:
            return self._xgb_plates
        try:
            resp = self.session.get(f"{self.XGB_BASE}/surge_stock/plates",
                timeout=5, headers={'Referer': 'https://xuangubao.cn/'})
            data = resp.json()
            if data.get('code') == 20000 and data.get('data', {}).get('items'):
                self._xgb_plates = data['data']['items']
                self._xgb_plates_time = now
        except Exception as e:
            print(f"[XGB] 异动板块获取失败: {type(e).__name__}")
        return self._xgb_plates
    
    def xgb_match_plate_event(self, sector_name: str) -> str:
        """根据概念/板块名匹配选股宝的驱动事件描述（单概念）"""
        plates = self.xgb_get_surge_plates()
        for p in plates:
            pname = p.get('name', '')
            desc = p.get('description', '')
            if not desc:
                continue
            # 模糊匹配：板块名互相包含
            if pname in sector_name or sector_name in pname:
                return f"{pname}-{desc}"
        return ''
    
    def xgb_match_plate_events_all(self, sector_str: str, max_n: int = 3) -> List[str]:
        """对一只股票的多个概念（|分隔）全部匹配选股宝驱动事件，返回事件列表。
        商业版"追板精灵"在一只股上同时显示多个概念的最新事件，本方法对齐其效果。
        """
        if not sector_str:
            return []
        sectors = [s.strip() for s in sector_str.split('|') if s.strip()]
        if not sectors:
            return []
        plates = self.xgb_get_surge_plates()
        results = []
        seen = set()
        for sec in sectors:
            for p in plates:
                pname = p.get('name', '')
                desc = p.get('description', '')
                if not desc or not pname:
                    continue
                if pname in seen:
                    continue
                if pname in sec or sec in pname:
                    results.append(f"{pname}-{desc}")
                    seen.add(pname)
                    break
            if len(results) >= max_n:
                break
        return results
    
    # ========== 资讯/公告聚合（选股宝快讯 + 巨潮公告 + 东财公告）==========
    
    def poll_news_flash(self, interval: int = 60):
        """轮询选股宝7x24快讯流，建立 code -> [(time, title, '快讯')] 缓存
        每条快讯包含关联的股票列表，自动归类到对应code
        """
        now = time.time()
        if now - self._news_last_poll < interval:
            return
        self._news_last_poll = now
        try:
            resp = self.session.get(f"{self.XGB_BASE}/pc/free/messages",
                params={'limit': 30}, timeout=5,
                headers={'Referer': 'https://xuangubao.cn/'})
            data = resp.json()
            items = data.get('NewMsgs') or data.get('messages') or []
            cutoff = now - 1800  # 只保留30分钟内
            for msg in items:
                title = msg.get('Title') or msg.get('title') or ''
                summary = msg.get('Summary') or msg.get('summary') or ''
                text = title or summary
                if not text:
                    continue
                # 关联股票
                stocks = msg.get('Stocks') or msg.get('stocks') or []
                ts = msg.get('CreatedAt') or msg.get('created_at') or now
                if isinstance(ts, str):
                    try:
                        from datetime import datetime as _dt
                        ts = _dt.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                    except:
                        ts = now
                # 截取标题前40字
                short = text[:40].replace('\n', ' ')
                for st in stocks:
                    sym = st.get('symbol') or st.get('Symbol') or ''
                    code = sym.split('.')[0] if '.' in sym else sym
                    if not code or len(code) != 6:
                        continue
                    lst = self._news_cache.setdefault(code, [])
                    if not any(x[1] == short for x in lst):
                        lst.append((ts, short, '快讯'))
                # 清理过期
            for code in list(self._news_cache.keys()):
                self._news_cache[code] = [x for x in self._news_cache[code] if x[0] > cutoff]
                if not self._news_cache[code]:
                    del self._news_cache[code]
        except Exception as e:
            print(f"[News] 选股宝快讯获取失败: {type(e).__name__}: {e}")
    
    def poll_announcements(self, interval: int = 120):
        """轮询东财公告流（A股全市场最新公告），建立 code -> [(time, title, '公告')] 缓存"""
        now = time.time()
        if now - self._announce_last_poll < interval:
            return
        self._announce_last_poll = now
        try:
            resp = self.session.get(
                "https://np-anotice-stock.eastmoney.com/api/security/ann",
                params={'sr': -1, 'page_size': 50, 'page_index': 1,
                        'ann_type': 'A', 'client_source': 'web'},
                timeout=8, headers={'Referer': 'https://data.eastmoney.com/'})
            data = resp.json()
            items = (data.get('data') or {}).get('list') or []
            cutoff = now - 7200  # 保留2小时内
            for item in items:
                title = item.get('title', '')
                notice_date = item.get('notice_date', '')  # "2026-04-30 09:30:00"
                try:
                    from datetime import datetime as _dt
                    ts = _dt.strptime(notice_date, '%Y-%m-%d %H:%M:%S').timestamp()
                except:
                    ts = now
                if ts < cutoff:
                    continue
                short = title[:40].replace('\n', ' ')
                for code_obj in (item.get('codes') or []):
                    code = code_obj.get('stock_code', '')
                    if not code or len(code) != 6:
                        continue
                    lst = self._announce_cache.setdefault(code, [])
                    if not any(x[1] == short for x in lst):
                        lst.append((ts, short, '公告'))
            for code in list(self._announce_cache.keys()):
                self._announce_cache[code] = [x for x in self._announce_cache[code] if x[0] > cutoff]
                if not self._announce_cache[code]:
                    del self._announce_cache[code]
        except Exception as e:
            print(f"[Announce] 公告获取失败: {type(e).__name__}: {e}")
    
    def get_stock_news(self, code: str, max_items: int = 1) -> str:
        """获取股票最近资讯/公告摘要（最近30分钟内），格式: "公告:xxx | 快讯:yyy" """
        now = time.time()
        recent_cutoff = now - 1800  # 30分钟内
        items = []
        # 公告优先级最高
        for ts, title, src in self._announce_cache.get(code, []):
            if ts > recent_cutoff:
                items.append((ts, f"公告:{title}", 0))
        for ts, title, src in self._news_cache.get(code, []):
            if ts > recent_cutoff:
                items.append((ts, f"快讯:{title}", 1))
        if not items:
            return ''
        # 公告优先 + 时间倒序
        items.sort(key=lambda x: (x[2], -x[0]))
        return ' | '.join(t for _, t, _ in items[:max_items])
    
    # ========== 腾讯备用数据源 ==========
    
    # 腾讯排行API备用域名
    TENCENT_RANK_HOSTS = [
        "http://stock.gtimg.cn",
        "http://proxy.finance.qq.com",
        "http://web.ifzq.gtimg.cn",
        "http://qt.gtimg.cn",
    ]
    
    def _tencent_rank(self, rank_type='ranka/chr', order=0, page=1, size=80) -> List[Dict]:
        """腾讯股票/板块排行API
        rank_type: ranka/chr(A股涨幅), rankgn/chr(概念板块涨幅)
        order: 0=降序(涨幅最大), 1=升序(跌幅最大)
        """
        import re
        params = {'appn': 'rank', 't': rank_type, 'p': page, 'o': order, 'l': size, 'v': 'list_data'}
        
        # 如果已找到可用的排行API域名，优先使用
        hosts = [self._tencent_rank_host] if hasattr(self, '_tencent_rank_host') else self.TENCENT_RANK_HOSTS
        
        for host in hosts:
            try:
                url = f"{host}/data/index.php"
                resp = self.session.get(url, params=params, timeout=10)
                text = resp.text
                # 验证API响应格式（包含list_data表示API可用）
                if 'list_data' not in text:
                    continue
                # 缓存可用域名
                self._tencent_rank_host = host
                match = re.search(r"data:['\"]([^'\"]*?)['\"]", text)
                if not match or not match.group(1).strip():
                    return []  # API可用但无数据（休市）
                records = match.group(1).split('^')
                result = []
                for record in records:
                    if not record.strip():
                        continue
                    f = record.split('~')
                    if len(f) < 13:
                        continue
                    try:
                        result.append({
                            'code': f[2],
                            'name': f[1],
                            'price': float(f[3]) if f[3] else 0,
                            'prev_close': float(f[4]) if f[4] else 0,
                            'change_pct': float(f[12]) if f[12] else 0,
                        })
                    except (ValueError, IndexError):
                        continue
                return result
            except Exception as e:
                continue
        return []
    
    def _tencent_get_limit_up(self) -> List[Dict]:
        stocks = self._tencent_rank('ranka/chr', order=0, page=1, size=200)
        result = []
        for s in stocks:
            code, pct = s['code'], s['change_pct']
            limit = 19.9 if code.startswith(('300', '301', '688', '689')) else 9.9
            if pct >= limit:
                result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'limit_up'})
        return result
    
    def _tencent_get_limit_down(self) -> List[Dict]:
        stocks = self._tencent_rank('ranka/chr', order=1, page=1, size=200)
        result = []
        for s in stocks:
            code, pct = s['code'], s['change_pct']
            limit = -19.9 if code.startswith(('300', '301', '688', '689')) else -9.9
            if pct <= limit:
                result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'limit_down'})
        return result
    
    def _tencent_get_near_limit(self) -> List[Dict]:
        stocks = self._tencent_rank('ranka/chr', order=0, page=1, size=100)
        result = []
        for s in stocks:
            code, pct = s['code'], s['change_pct']
            if code.startswith(('300', '301', '688', '689')):
                if 18 <= pct < 19.9:
                    result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'near_limit'})
            else:
                if 8 <= pct < 9.9:
                    result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'near_limit'})
        return result
    
    def _tencent_get_surge(self) -> List[Dict]:
        stocks = self._tencent_rank('ranka/chr', order=0, page=1, size=50)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct']}
                for s in stocks if 3 <= s['change_pct'] < 9.8][:10]
    
    def _tencent_get_plunge(self) -> List[Dict]:
        stocks = self._tencent_rank('ranka/chr', order=1, page=1, size=50)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct']}
                for s in stocks if -9.9 < s['change_pct'] <= -5][:10]
    
    def _tencent_get_sectors(self, min_pct=0) -> List[Dict]:
        stocks = self._tencent_rank('rankgn/chr', order=0, page=1, size=20)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct']}
                for s in stocks if s['change_pct'] >= min_pct]
    
    # ========== 新浪备用数据源 ==========
    
    SINA_RANK_URL = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    SINA_CONCEPT_URL = "http://money.finance.sina.com.cn/q/view/newFLJK.php"
    SINA_INDUSTRY_URL = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    
    def _sina_fetch_concept_sectors(self) -> List[Dict]:
        """从新浪获取概念板块数据（含领涨股）
        返回格式: [{'code','name','change_pct','leader_code','leader_name','leader_pct'}, ...]
        """
        import re
        for attempt in range(2):
            try:
                resp = self.session.get(self.SINA_CONCEPT_URL, params={'param': 'class'},
                                       timeout=10, headers={'Referer': 'https://finance.sina.com.cn/'})
                text = resp.text
                # 解析 var S_Finance_bankuai_class = {"gn_xx":"gn_xx,名称,数量,均价,涨跌,涨幅%,...,领涨股名", ...}
                match = re.search(r'=\s*(\{.+\})', text, re.DOTALL)
                if not match:
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    break
                raw = match.group(1)
                items = re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', raw)
                result = []
                for key, val in items:
                    fields = val.split(',')
                    if len(fields) < 13:
                        continue
                    try:
                        pct = float(fields[5]) if fields[5] else 0
                        leader_pct = float(fields[9]) if fields[9] else 0
                        member_count = int(fields[2]) if fields[2] else 0
                        result.append({
                            'code': fields[0],
                            'name': fields[1],
                            'change_pct': pct,
                            'member_count': member_count,
                            'leader_code': fields[8],
                            'leader_name': fields[12],
                            'leader_pct': leader_pct,
                        })
                    except (ValueError, IndexError):
                        continue
                if result:
                    result.sort(key=lambda x: x['change_pct'], reverse=True)
                    self._sina_sector_cache = result
                    self._sina_sector_cache_time = time.time()
                    return result
            except Exception as e:
                print(f"新浪概念板块获取失败(第{attempt+1}次): {e}")
                if attempt == 0:
                    time.sleep(1)
        return getattr(self, '_sina_sector_cache', [])
    
    def _sina_get_cached_sectors(self) -> List[Dict]:
        """获取新浪概念板块缓存（30秒内有效）"""
        cache_time = getattr(self, '_sina_sector_cache_time', 0)
        if time.time() - cache_time < 30 and hasattr(self, '_sina_sector_cache'):
            return self._sina_sector_cache
        return self._sina_fetch_concept_sectors()
    
    def _sina_rank(self, sort='changepercent', asc=0, page=1, size=80, node='hs_a') -> List[Dict]:
        """新浪股票排行API"""
        params = {'page': page, 'num': size, 'sort': sort, 'asc': asc, 'node': node}
        try:
            resp = self.session.get(self.SINA_RANK_URL, params=params, timeout=10,
                                   headers={'Referer': 'https://finance.sina.com.cn/'})
            data = resp.json()
            if not isinstance(data, list):
                return []
            return [{'code': item.get('code', ''), 'name': item.get('name', ''),
                     'price': float(item.get('trade', 0) or 0),
                     'prev_close': float(item.get('settlement', 0) or 0),
                     'change_pct': float(item.get('changepercent', 0) or 0)}
                    for item in data]
        except:
            return []
    
    def _sina_get_limit_up(self) -> List[Dict]:
        stocks = self._sina_rank(sort='changepercent', asc=0, size=200)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct'], 'status': 'limit_up'}
                for s in stocks
                if s['change_pct'] >= (19.9 if s['code'].startswith(('300','301','688','689')) else 9.9)]
    
    def _sina_get_limit_down(self) -> List[Dict]:
        stocks = self._sina_rank(sort='changepercent', asc=1, size=200)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct'], 'status': 'limit_down'}
                for s in stocks
                if s['change_pct'] <= (-19.9 if s['code'].startswith(('300','301','688','689')) else -9.9)]
    
    def _sina_get_near_limit(self) -> List[Dict]:
        # 扩大扫描范围至500，避免漏掉排在中段的逼近涨停股
        stocks = self._sina_rank(sort='changepercent', asc=0, size=500)
        result = []
        for s in stocks:
            code, pct = s['code'], s['change_pct']
            if code.startswith(('300', '301', '688', '689')):
                if 18 <= pct < 19.9:
                    result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'near_limit'})
            else:
                if 8 <= pct < 9.9:
                    result.append({'code': code, 'name': s['name'], 'change_pct': pct, 'status': 'near_limit'})
        return result
    
    def _sina_get_surge(self) -> List[Dict]:
        stocks = self._sina_rank(sort='changepercent', asc=0, size=200)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct']}
                for s in stocks if 3 <= s['change_pct'] < 9.8][:30]
    
    def _sina_get_plunge(self) -> List[Dict]:
        stocks = self._sina_rank(sort='changepercent', asc=1, size=200)
        return [{'code': s['code'], 'name': s['name'], 'change_pct': s['change_pct']}
                for s in stocks if -9.9 < s['change_pct'] <= -5][:30]
    
    def _sina_get_sectors(self, min_pct=0) -> List[Dict]:
        """新浪概念板块排行（含领涨股）"""
        sectors = self._sina_get_cached_sectors()
        if sectors:
            return [s for s in sectors if s['change_pct'] >= min_pct]
        return self._tencent_get_sectors(min_pct)
    
    def detect_sector_new_highs(self, min_pct: float = 2.0) -> List[Dict]:
        """检测板块日内涨幅新高
        返回创新高的板块列表: [{'code','name','change_pct','prev_high','leader_name','leader_pct'}, ...]
        min_pct: 最低涨幅阈值，低于此涨幅不报
        """
        today = datetime.now().strftime('%Y-%m-%d')
        if self._sector_day_high_date != today:
            self._sector_day_high = {}
            self._sector_day_high_date = today
        
        sectors = self._sina_get_cached_sectors()
        if not sectors:
            return []
        
        new_highs = []
        for s in sectors:
            code = s['code']
            pct = s['change_pct']
            if pct < min_pct:
                continue
            prev_high = self._sector_day_high.get(code, 0)
            if pct > prev_high + 0.3:
                new_highs.append({
                    'code': code,
                    'name': s['name'],
                    'change_pct': pct,
                    'prev_high': prev_high,
                    'leader_name': s.get('leader_name', ''),
                    'leader_pct': s.get('leader_pct', 0),
                })
                self._sector_day_high[code] = pct
            elif pct > prev_high:
                self._sector_day_high[code] = pct
        
        new_highs.sort(key=lambda x: x['change_pct'], reverse=True)
        return new_highs
    
    # ========== 连接测试 ==========
    
    def test_connection(self) -> Tuple[bool, str]:
        """测试网络连接，自动选择可用的API域名"""
        global _last_error
        import socket
        
        # 先测试基本网络连接
        try:
            socket.create_connection(("www.baidu.com", 80), timeout=5)
        except Exception as e:
            _last_error = f"基本网络连接失败: {e}"
            return (False, "无法连接互联网，请检查网络连接")
        
        # 混合模式：优先新浪行情，东财仅用于辅助
        if self.hybrid_mode:
            return self._test_hybrid_mode()
        
        # 尝试所有备用域名
        test_params = {'pn': 1, 'pz': 1, 'fs': 'm:0+t:6', 'fields': 'f12,f14'}
        errors = []
        for host in self.API_HOSTS:
            try:
                url = f"{host}/api/qt/clist/get"
                resp = self.session.get(url, params=test_params, timeout=10)
                data = resp.json()
                if data.get('data') and data['data'].get('diff'):
                    self.api_base = host
                    return (True, f"连接成功: {host}")
                else:
                    errors.append(f"{host}: 返回异常")
            except Exception as e:
                errors.append(f"{host}: {type(e).__name__}")
        
        # 东方财富全部失败，尝试新浪行情API
        try:
            resp = self.session.get(self.SINA_RANK_URL, params={
                'page': 1, 'num': 5, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'
            }, timeout=10, headers={'Referer': 'https://finance.sina.com.cn/'})
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                self.use_sina = True
                return (True, "已切换到新浪行情数据源")
        except Exception as e:
            errors.append(f"新浪行情: {type(e).__name__}")
        
        # 新浪也失败，尝试腾讯行情API
        import re
        tencent_errors = []
        for thost in self.TENCENT_RANK_HOSTS:
            try:
                url = f"{thost}/data/index.php"
                resp = self.session.get(url, params={
                    'appn': 'rank', 't': 'ranka/chr', 'p': 1, 'o': 0, 'l': 5, 'v': 'list_data'
                }, timeout=10)
                if resp.status_code == 200 and 'list_data' in resp.text:
                    self.use_tencent = True
                    self._tencent_rank_host = thost
                    return (True, f"已切换到腾讯行情: {thost}")
                else:
                    tencent_errors.append(f"{thost}: 无有效数据")
            except Exception as e:
                tencent_errors.append(f"{thost}: {type(e).__name__}")
        
        all_errors = errors + tencent_errors
        _last_error = "所有数据源均不可用:\n" + "\n".join(all_errors)
        return (False, "所有数据源均无法连接\n\n" + "\n".join(all_errors))
    
    def _test_hybrid_mode(self) -> Tuple[bool, str]:
        """混合模式连接测试：新浪主行情 + 东财辅助（题材/异动）"""
        global _last_error
        msgs = []
        
        # 1. 测试新浪行情（主源）
        sina_ok = False
        try:
            resp = self.session.get(self.SINA_RANK_URL, params={
                'page': 1, 'num': 3, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'
            }, timeout=10, headers={'Referer': 'https://finance.sina.com.cn/'})
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                sina_ok = True
                self.use_sina = True
                msgs.append("新浪行情 ✓")
        except Exception as e:
            msgs.append(f"新浪行情 ✗ {type(e).__name__}")
        
        if not sina_ok:
            # 新浪失败，禁用混合模式，回退普通模式
            print("[DataSource] 新浪不可用，禁用混合模式，尝试普通模式...")
            self.hybrid_mode = False
            return self._test_normal_mode()
        
        # 2. 测试东财辅助（异动流API，低优先级）
        eastmoney_ok = False
        try:
            resp = self.session.get(
                "https://push2ex.eastmoney.com/getAllStockChanges",
                params={'type': '64', 'pageindex': 0, 'pagesize': 1,
                        'ut': '7eea3edcaed734bea9cbfc24409ed989'},
                timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('data') is not None:
                    eastmoney_ok = True
                    self._eastmoney_available = True
                    msgs.append("东财异动流 ✓")
        except:
            pass
        
        if not eastmoney_ok:
            msgs.append("东财异动流 ✗（题材/原因功能受限）")
            self._eastmoney_available = False
        
        status = "混合模式: " + ", ".join(msgs)
        print(f"[DataSource] {status}")
        return (True, status)
    
    def _test_normal_mode(self) -> Tuple[bool, str]:
        """普通模式：按优先级测试 东财→新浪→腾讯"""
        global _last_error
        
        # 尝试东财
        test_params = {'pn': 1, 'pz': 1, 'fs': 'm:0+t:6', 'fields': 'f12,f14'}
        for host in self.API_HOSTS:
            try:
                url = f"{host}/api/qt/clist/get"
                resp = self.session.get(url, params=test_params, timeout=10)
                data = resp.json()
                if data.get('data') and data['data'].get('diff'):
                    self.api_base = host
                    return (True, f"东财连接成功: {host}")
            except:
                pass
        
        # 尝试新浪
        try:
            resp = self.session.get(self.SINA_RANK_URL, params={
                'page': 1, 'num': 5, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'
            }, timeout=10, headers={'Referer': 'https://finance.sina.com.cn/'})
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                self.use_sina = True
                return (True, "已切换到新浪行情")
        except:
            pass
        
        # 尝试腾讯
        import re
        for thost in self.TENCENT_RANK_HOSTS:
            try:
                url = f"{thost}/data/index.php"
                resp = self.session.get(url, params={
                    'appn': 'rank', 't': 'ranka/chr', 'p': 1, 'o': 0, 'l': 5, 'v': 'list_data'
                }, timeout=10)
                if resp.status_code == 200 and 'list_data' in resp.text:
                    self.use_tencent = True
                    self._tencent_rank_host = thost
                    return (True, f"已切换到腾讯行情: {thost}")
            except:
                pass
        
        _last_error = "所有数据源均不可用"
        return (False, "所有数据源均无法连接")
    
    def get_all_stocks(self) -> List[StockInfo]:
        """获取全部A股实时行情"""
        stocks = []
        # 分页获取
        for page in range(1, 50):  # 最多50页
            batch = self._fetch_stock_page(page)
            if not batch:
                break
            stocks.extend(batch)
        return stocks
    
    def _fetch_stock_page(self, page: int, size: int = 100) -> List[StockInfo]:
        """获取一页股票数据"""
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': page,
            'pz': size,
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',  # 按涨幅排序
            'fs': 'b:MK0010,b:MK0001',  # 沪深A股
            'fields': 'f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18,f20,f21,f62,f184'
            # f2现价,f3涨幅,f12代码,f14名称,f15最高,f16最低,f17开盘,f18昨收,f20买一量,f21卖一量,f62主力净流入,f184主力净占比
        }
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                return [self._parse_stock(item) for item in data['data']['diff']]
        except Exception as e:
            print(f"获取行情失败: {e}")
        return []
    
    def _parse_stock(self, item: dict) -> StockInfo:
        """解析股票数据"""
        code = str(item.get('f12', ''))
        name = item.get('f14', '')
        price = float(item.get('f2', 0) or 0)
        change_pct = float(item.get('f3', 0) or 0)
        prev_close = float(item.get('f18', 0) or 0)
        
        # 计算涨跌停价（主板10%，科创板/创业板20%）
        if code.startswith(('300', '301', '688', '689')):
            limit_pct = 0.20
        elif code.startswith(('8', '4')):  # 北交所30%
            limit_pct = 0.30
        else:
            limit_pct = 0.10
        
        limit_up = round(prev_close * (1 + limit_pct), 2)
        limit_down = round(prev_close * (1 - limit_pct), 2)
        
        return StockInfo(
            code=code,
            name=name,
            price=price,
            change_pct=change_pct,
            limit_up_price=limit_up,
            limit_down_price=limit_down,
            prev_close=prev_close,
            volume=float(item.get('f5', 0) or 0),
            amount=float(item.get('f6', 0) or 0),
            buy1_vol=float(item.get('f20', 0) or 0),
            sell1_vol=float(item.get('f21', 0) or 0),
            main_net=float(item.get('f62', 0) or 0),
            main_pct=float(item.get('f184', 0) or 0),
            is_new=self._is_new_stock(code, name)
        )
    
    def _is_new_stock(self, code: str, name: str) -> bool:
        """判断是否新股（上市30天内）"""
        # 简化判断：名称包含N或C开头
        return name.startswith('N') or name.startswith('C-')
    
    def get_stock_quote(self, stock_code: str) -> Dict:
        """查询单只股票实时行情（用新浪hq接口，轻量稳定）
        返回: {'code','name','price','prev_close','change_pct'}; 失败返回空dict
        """
        prefix = 'sh' if stock_code.startswith(('6', '5', '9')) else 'sz'
        try:
            resp = self.session.get(f"http://hq.sinajs.cn/list={prefix}{stock_code}",
                                    timeout=5,
                                    headers={'Referer': 'https://finance.sina.com.cn/'})
            text = resp.text
            # var hq_str_sh600000="股票名,开盘价,昨收,现价,...";
            import re
            m = re.search(r'"([^"]+)"', text)
            if not m:
                return {}
            fields = m.group(1).split(',')
            if len(fields) < 4:
                return {}
            name = fields[0]
            prev_close = float(fields[2] or 0)
            price = float(fields[3] or 0)
            if prev_close <= 0 or price <= 0:
                return {}
            pct = (price - prev_close) / prev_close * 100
            return {'code': stock_code, 'name': name, 'price': price,
                    'prev_close': prev_close, 'change_pct': round(pct, 2)}
        except Exception:
            return {}
    
    def get_limit_up_stocks(self) -> List[Dict]:
        """获取涨停股列表（通过涨幅榜筛选）"""
        if self.use_sina:
            return self._sina_get_limit_up()
        if self.use_tencent:
            return self._tencent_get_limit_up()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 200, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',  # 沪深A股
            'fields': 'f2,f3,f12,f14,f18'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    code = str(item.get('f12', ''))
                    name = item.get('f14', '')
                    
                    # 判断涨停阈值（科创板/创业板20%，主板10%）
                    if code.startswith(('300', '301', '688', '689')):
                        limit_pct = 19.9
                    else:
                        limit_pct = 9.9
                    
                    if pct >= limit_pct:
                        result.append({
                            'code': code,
                            'name': name,
                            'change_pct': pct,
                            'status': 'limit_up'
                        })
                self._record_success()
        except Exception as e:
            global _last_error
            _last_error = f"获取涨停股失败: {e}\n{traceback.format_exc()}"
            print(_last_error)
            self._record_failure_and_maybe_reconnect()
        return result
    
    def get_limit_down_stocks(self) -> List[Dict]:
        """获取跌停股列表（通过跌幅榜筛选）"""
        if self.use_sina:
            return self._sina_get_limit_down()
        if self.use_tencent:
            return self._tencent_get_limit_down()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 200, 'po': 0, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f2,f3,f12,f14,f18'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    code = str(item.get('f12', ''))
                    name = item.get('f14', '')
                    
                    if code.startswith(('300', '301', '688', '689')):
                        limit_pct = -19.9
                    else:
                        limit_pct = -9.9
                    
                    if pct <= limit_pct:
                        result.append({
                            'code': code,
                            'name': name,
                            'change_pct': pct,
                            'status': 'limit_down'
                        })
                self._record_success()
        except Exception as e:
            print(f"获取跌停股失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result
    
    def get_near_limit_stocks(self) -> List[Dict]:
        """获取逼近涨停的股票"""
        if self.use_sina:
            return self._sina_get_near_limit()
        if self.use_tencent:
            return self._tencent_get_near_limit()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 100, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f2,f3,f12,f14,f18'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    code = str(item.get('f12', ''))
                    name = item.get('f14', '')
                    
                    # 逼近涨停：主板8-9.9%，科创板/创业板18-19.9%
                    if code.startswith(('300', '301', '688', '689')):
                        if 18 <= pct < 19.9:
                            result.append({'code': code, 'name': name, 'change_pct': pct, 'status': 'near_limit'})
                    else:
                        if 8 <= pct < 9.9:
                            result.append({'code': code, 'name': name, 'change_pct': pct, 'status': 'near_limit'})
                self._record_success()
        except Exception as e:
            print(f"获取逼近涨停股失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result
    
    def get_surge_stocks(self) -> List[Dict]:
        """获取大幅拉升股票（东方财富异动接口）"""
        if self.use_sina:
            return self._sina_get_surge()
        if self.use_tencent:
            return self._tencent_get_surge()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 50, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f62',  # 按主力净流入
            'fs': 'b:MK0010,b:MK0001',
            'fields': 'f2,f3,f12,f14,f18,f62'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    if 3 <= pct < 9.8:  # 大幅拉升但未涨停
                        result.append({
                            'code': item.get('f12'),
                            'name': item.get('f14'),
                            'change_pct': pct
                        })
                self._record_success()
        except Exception as e:
            print(f"获取大幅拉升股失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result[:10]  # 最多10条
    
    def get_plunge_stocks(self) -> List[Dict]:
        """获取快速跳水股票"""
        if self.use_sina:
            return self._sina_get_plunge()
        if self.use_tencent:
            return self._tencent_get_plunge()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 50, 'po': 0, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fields': 'f2,f3,f12,f14,f18'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    if -9.9 < pct <= -5:  # 快速跳水但未跌停
                        result.append({
                            'code': str(item.get('f12', '')),
                            'name': item.get('f14', ''),
                            'change_pct': pct
                        })
                self._record_success()
        except Exception as e:
            print(f"获取快速跳水股失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result[:10]
    
    def get_sector_alerts(self, min_pct: float = 0.7) -> List[Dict]:
        """获取板块异动（涨幅较大的概念板块）"""
        if self.use_sina:
            return self._sina_get_sectors(min_pct=min_pct)[:5]
        if self.use_tencent:
            return self._tencent_get_sectors(min_pct=min_pct)[:5]
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 20, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:90+t:2',  # 概念板块
            'fields': 'f2,f3,f12,f14'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    pct = float(item.get('f3', 0) or 0)
                    if pct >= min_pct:
                        result.append({
                            'code': item.get('f12', ''),
                            'name': item.get('f14', ''),
                            'change_pct': pct
                        })
                self._record_success()
        except Exception as e:
            print(f"获取板块异动失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result[:5]  # 最多5个板块

    def get_sector_top_stocks(self, sector_code: str, limit: int = 3) -> str:
        """获取板块内涨幅靠前个股摘要"""
        if self.use_sina:
            # 新浪概念板块数据自带领涨股
            sectors = self._sina_get_cached_sectors()
            for s in sectors:
                if s['code'] == sector_code and s.get('leader_name'):
                    return f"{s['leader_name']}{s['leader_pct']:+.1f}%"
            return ''
        if self.use_tencent:
            return ''
        if not sector_code:
            return ''
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1,
            'pz': max(limit, 3),
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',
            'fs': f'b:{sector_code}',
            'fields': 'f12,f14,f3'
        }
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                leaders = []
                for item in data['data']['diff'][:limit]:
                    name = item.get('f14', '')
                    pct = float(item.get('f3', 0) or 0)
                    if name:
                        leaders.append(f"{name}{pct:+.1f}%")
                return ' | '.join(leaders)
        except:
            pass
        return ''
    
    def get_hot_sectors(self) -> List[Dict]:
        """获取热门板块"""
        if self.use_sina:
            return self._sina_get_sectors()
        if self.use_tencent:
            return self._tencent_get_sectors()
        url = self._api_url("/api/qt/clist/get")
        params = {
            'pn': 1, 'pz': 20, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fid': 'f3',
            'fs': 'm:90+t:2',  # 概念板块
            'fields': 'f2,f3,f12,f14'
        }
        result = []
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    result.append({
                        'code': item.get('f12'),
                        'name': item.get('f14'),
                        'change_pct': float(item.get('f3', 0) or 0)
                    })
                self._record_success()
        except Exception as e:
            print(f"获取热门板块失败: {e}")
            self._record_failure_and_maybe_reconnect()
        return result
    
    def _sina_fetch_industry_sectors(self) -> List[Dict]:
        """获取新浪行业板块列表"""
        import re
        try:
            resp = self.session.get(self.SINA_INDUSTRY_URL, timeout=10,
                                   headers={'Referer': 'https://finance.sina.com.cn/'})
            text = resp.text
            match = re.search(r'=\s*(\{.+\})', text, re.DOTALL)
            if not match:
                return []
            items = re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', match.group(1))
            result = []
            for key, val in items:
                fields = val.split(',')
                if len(fields) < 13:
                    continue
                try:
                    result.append({'code': fields[0], 'name': fields[1]})
                except (ValueError, IndexError):
                    continue
            return result
        except Exception as e:
            print(f"新浪行业板块获取失败: {e}")
            return []

    def _sina_build_concept_map(self):
        """构建股票→概念反向索引（概念板块 + 行业板块）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        sectors = self._sina_get_cached_sectors()
        if not sectors:
            return
        # 概念板块：取全部（最大化覆盖率）
        seen_codes = set()
        target_sectors = []
        for s in sectors:
            if s['code'] not in seen_codes:
                seen_codes.add(s['code'])
                target_sectors.append(s)
        # 行业板块：全部（约30个，保证每只股票至少有行业标签）
        industry_sectors = self._sina_fetch_industry_sectors()
        for s in industry_sectors:
            if s['code'] not in seen_codes:
                seen_codes.add(s['code'])
                target_sectors.append(s)

        # 地域板块关键词（这些板块作为概念优先级最低）
        region_keywords = ['陕甘宁', '长株潭', '京津冀', '粤港澳', '长三角', '珠三角',
                           '环渤海', '雄安', '海南', '上海', '北京', '广东', '江苏',
                           '浙江', '山东', '福建', '湖南', '湖北', '四川', '重庆',
                           '安徽', '河南', '河北', '陕西', '辽宁', '吉林', '黑龙江',
                           '云南', '贵州', '广西', '西藏', '新疆', '宁夏', '青海',
                           '甘肃', '内蒙', '山西', '江西', '天津', '自贸区', '本地股',
                           '保险重仓', '基金重仓', '社保重仓', 'QFII重仓']
        
        def is_region(name: str) -> bool:
            return any(kw in name for kw in region_keywords)

        def sector_priority(name: str, is_industry: bool) -> int:
            """优先级：0=行业，1=普通概念，2=地域/重仓"""
            if is_industry:
                return 0
            if is_region(name):
                return 2
            return 1

        # code -> [(priority, abs_pct, name)]，便于后续排序
        concept_map_raw: Dict[str, List[Tuple[int, float, str]]] = {}

        def fetch_members(sector):
            """获取单个板块的成分股"""
            try:
                resp = self.session.get(self.SINA_RANK_URL, params={
                    'page': 1, 'num': 100, 'sort': 'changepercent', 'asc': 0,
                    'node': sector['code'], '_s_r_a': 'page'
                }, timeout=10, headers={'Referer': 'https://finance.sina.com.cn/'})
                data = resp.json()
                if isinstance(data, list):
                    is_industry = sector['code'].startswith('hy_')
                    pri = sector_priority(sector['name'], is_industry)
                    pct = abs(sector.get('change_pct', 0) or 0)
                    return [(item.get('code', ''), sector['name'], pri, pct)
                            for item in data if item.get('code')]
            except:
                pass
            return []

        # 并发获取（概念 + 行业板块的成分股）
        results = []
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(fetch_members, s): s for s in target_sectors}
            for future in as_completed(futures, timeout=120):
                try:
                    results.extend(future.result())
                except:
                    pass

        # 构建反向索引（带优先级）
        for code, concept_name, pri, pct in results:
            if code not in concept_map_raw:
                concept_map_raw[code] = []
            # 去重
            if not any(c[2] == concept_name for c in concept_map_raw[code]):
                concept_map_raw[code].append((pri, pct, concept_name))

        # 补充领涨股（领涨股的所属概念优先级最高）
        for s in sectors:
            leader_code = s.get('leader_code', '')
            if leader_code:
                pure_code = leader_code[2:] if len(leader_code) > 2 else leader_code
                if pure_code not in concept_map_raw:
                    concept_map_raw[pure_code] = []
                if not any(c[2] == s['name'] for c in concept_map_raw[pure_code]):
                    pri = sector_priority(s['name'], False)
                    concept_map_raw[pure_code].append((pri, abs(s.get('change_pct', 0)), s['name']))

        # 排序：优先级升序 + 涨幅绝对值降序
        concept_map: Dict[str, List[str]] = {}
        for code, items in concept_map_raw.items():
            items.sort(key=lambda x: (x[0], -x[1]))
            concept_map[code] = [name for _, _, name in items]

        self._sina_concept_map = concept_map
        self._sina_concept_map_time = time.time()
        n_concept = len([s for s in target_sectors if s['code'].startswith('gn_')])
        n_industry = len(target_sectors) - n_concept
        print(f"[DataSource] 新浪概念索引已建立: {len(concept_map)}只股票, {n_concept}概念+{n_industry}行业板块")

    def _sina_get_stock_concept(self, stock_code: str) -> str:
        """从缓存的概念索引中查询股票所属概念"""
        cache_time = getattr(self, '_sina_concept_map_time', 0)
        if time.time() - cache_time > 300 or not hasattr(self, '_sina_concept_map'):
            try:
                self._sina_build_concept_map()
            except Exception as e:
                print(f"构建概念索引失败: {e}")
                return ''
        concept_map = getattr(self, '_sina_concept_map', {})
        concepts = concept_map.get(stock_code, [])
        return '|'.join(concepts[:2]) if concepts else ''

    def get_stock_concept(self, stock_code: str) -> str:
        """获取股票所属概念（混合模式：优先东财，回退新浪）"""
        # 混合模式：东财可用时优先东财（带限流），否则回退新浪
        if self.hybrid_mode:
            if self._eastmoney_available:
                result = self._eastmoney_get_concept_throttled(stock_code)
                if result:
                    return result
            # 东财不可用或无结果，回退新浪
            return self._sina_get_stock_concept(stock_code)
        
        # 非混合模式：按原逻辑
        if self.use_sina:
            return self._sina_get_stock_concept(stock_code)
        if self.use_tencent:
            return ''
        return self._eastmoney_get_concept(stock_code)
    
    def _eastmoney_get_concept_throttled(self, stock_code: str) -> str:
        """东财题材查询（成功结果永久缓存当日，题材日内不变化）"""
        if not hasattr(self, '_concept_cache'):
            self._concept_cache = {}
        # 已有成功缓存：直接返回（永久缓存，避免重复请求被封）
        cached = self._concept_cache.get(stock_code, '')
        if cached:
            return cached
        # 失败结果限流：30秒内不重复查
        now = time.time()
        last_query = self._last_concept_query.get(stock_code, 0)
        if now - last_query < 30:
            return ''
        self._last_concept_query[stock_code] = now
        result = self._eastmoney_get_concept(stock_code)
        if result:
            self._concept_cache[stock_code] = result
        return result
    
    def _eastmoney_get_concept(self, stock_code: str) -> str:
        """从东财获取股票所属概念"""
        # 根据股票代码获取市场前缀
        if stock_code.startswith(('6', '5')):
            secid = f"1.{stock_code}"
        else:
            secid = f"0.{stock_code}"
        
        url = "http://push2.eastmoney.com/api/qt/slist/get"
        params = {
            'secid': secid,
            'spt': 3,
            'fields': 'f12,f14,f3',
            'fid': 'f3',
            'po': 1,
            'pz': 5,
            'pn': 1,
            'np': 1,
            'invt': 2,
            'fltt': 2
        }
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                concepts = [item.get('f14', '') for item in data['data']['diff'][:2]]
                return '|'.join(concepts) if concepts else ''
        except:
            pass
        return ''
    
    def detect_alerts(self, stocks: List[StockInfo]) -> List[AlertInfo]:
        """检测异动"""
        alerts = []
        now = datetime.now()
        
        current_limit_up = set()
        current_limit_down = set()
        
        for stock in stocks:
            code = stock.code
            
            # 跳过ST和退市股
            if 'ST' in stock.name or '退' in stock.name:
                continue
            
            # 计算是否涨停/跌停
            is_limit_up = abs(stock.price - stock.limit_up_price) < 0.01
            is_limit_down = abs(stock.price - stock.limit_down_price) < 0.01
            near_limit_up = 9.5 <= stock.change_pct < 9.9 and not is_limit_up
            near_limit_down = -9.9 < stock.change_pct <= -9.5 and not is_limit_down
            
            if is_limit_up:
                current_limit_up.add(code)
            if is_limit_down:
                current_limit_down.add(code)
            
            # 检测封涨停（新涨停）
            if is_limit_up and code not in self.limit_up_stocks:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.LIMIT_UP,
                    timestamp=now,
                    message=f"{stock.name} 封涨停"
                ))
            
            # 检测打开涨停
            if code in self.limit_up_stocks and not is_limit_up:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.OPEN_LIMIT_UP,
                    timestamp=now,
                    message=f"{stock.name} 打开涨停 {stock.change_pct:.1f}%"
                ))
            
            # 检测逼近涨停
            if near_limit_up and code not in self.stock_cache:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.NEAR_LIMIT_UP,
                    timestamp=now,
                    message=f"{stock.name} 逼近涨停 {stock.change_pct:.1f}%"
                ))
            
            # 检测封跌停
            if is_limit_down and code not in self.limit_down_stocks:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.LIMIT_DOWN,
                    timestamp=now,
                    message=f"{stock.name} 封跌停"
                ))
            
            # 检测打开跌停
            if code in self.limit_down_stocks and not is_limit_down:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.OPEN_LIMIT_DOWN,
                    timestamp=now,
                    message=f"{stock.name} 打开跌停 {stock.change_pct:.1f}%"
                ))
            
            # 检测逼近跌停
            if near_limit_down and code not in self.stock_cache:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.NEAR_LIMIT_DOWN,
                    timestamp=now,
                    message=f"{stock.name} 逼近跌停 {stock.change_pct:.1f}%"
                ))
            
            # 检测新股开板
            if stock.is_new and code in self.limit_up_stocks and not is_limit_up:
                alerts.append(AlertInfo(
                    stock=stock,
                    alert_type=AlertType.IPO_OPEN,
                    timestamp=now,
                    message=f"新股 {stock.name} 开板"
                ))
            
            # 检测大幅拉升/快速跳水（需要历史价格）
            if code in self.stock_cache:
                old = self.stock_cache[code]
                delta = stock.change_pct - old.change_pct
                if delta >= 3:  # 1分钟涨3%+
                    alerts.append(AlertInfo(
                        stock=stock,
                        alert_type=AlertType.SURGE,
                        timestamp=now,
                        message=f"{stock.name} 大幅拉升 +{delta:.1f}%"
                    ))
                elif delta <= -3:  # 1分钟跌3%+
                    alerts.append(AlertInfo(
                        stock=stock,
                        alert_type=AlertType.PLUNGE,
                        timestamp=now,
                        message=f"{stock.name} 快速跳水 {delta:.1f}%"
                    ))
            
            # 更新缓存
            self.stock_cache[code] = stock
        
        # 更新涨跌停集合
        self.limit_up_stocks = current_limit_up
        self.limit_down_stocks = current_limit_down
        
        return alerts


def is_trading_time() -> bool:
    """判断是否交易时间（9:25集合竞价结束后开始）"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.hour * 100 + now.minute
    # 9:25-11:30, 13:00-15:00（跳过9:15-9:25集合竞价期间）
    return (925 <= t <= 1130) or (1300 <= t <= 1500)


if __name__ == "__main__":
    # 测试
    ds = DataSource()
    print("获取热门板块...")
    sectors = ds.get_hot_sectors()
    for s in sectors[:5]:
        print(f"  {s['name']}: {s['change_pct']:.2f}%")
    
    print("\n获取涨停股...")
    stocks = ds.get_limit_up_stocks()
    for s in stocks[:5]:
        print(f"  {s.name}({s.code}): {s.change_pct:.2f}%")
