#!/usr/bin/env python3
"""
bot.py - 全功能量化交易机器人模板
功能：
 - 读取配置(.env / 环境变量)
 - 拉取历史/实时行情(ccxt / REST)
 - 简单策略(SMA 短/长均线交叉)
 - 回测(backtrader)
 - 模拟/实盘下单(ccxt,支持 dry-run)
 - 日志 (loguru) 与简单调度(schedule)
使用方法示例：
  python3 bot.py --mode backtest --symbol BTC/USDT --timeframe 1h --since-days 90
  python3 bot.py --mode live --symbol BTC/USDT
  python3 bot.py --mode paper --symbol BTC/USDT
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import ccxt
import pandas as pd
import numpy as np
from loguru import logger
from dotenv import load_dotenv
import schedule

# backtrader 仅用于回测
import backtrader as bt

# ========== 配置 ==========

load_dotenv()  # 从 .env 加载变量(如果存在)

API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")    # 默认 binance
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
BASE_CURRENCY = os.getenv("BASE_CURRENCY", "USDT")

# 交易参数
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "BTC/USDT")
DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "1h")
DEFAULT_SINCE_DAYS = int(os.getenv("DEFAULT_SINCE_DAYS", "90"))

# 回测参数
INITIAL_CASH = float(os.getenv("INITIAL_CASH", "10000.0"))

# 日志
logger.add("bot.log", rotation="10 MB", retention="7 days", level="INFO")

# ========== 工具函数 ==========

def now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ========== 交易所客户端封装 ==========

class ExchangeClient:
    def __init__(self, exchange_id: str = EXCHANGE_ID, api_key: str = API_KEY, api_secret: str = API_SECRET):
        exchange_cls = getattr(ccxt, exchange_id)
        opts = {"enableRateLimit": True}
        # 有些交易所需要 sandbox; 若需要可在 .env 设置 SANDBOX=true 并处理
        self.exchange = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            **opts,
        })
        logger.info(f"[{now_ts()}] 初始化交易所客户端：{exchange_id}, DRY_RUN={DRY_RUN}")

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int] = None, limit: Optional[int] = None):
        """
        返回 DataFrame: columns = [timestamp, open, high, low, close, volume]
        timestamp 为毫秒
        """
        logger.debug(f"fetch_ohlcv {symbol} {timeframe} since={since} limit={limit}")
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("datetime", inplace=True)
        return df

    def create_order(self, symbol: str, side: str, amount: float, price: Optional[float] = None, order_type: str = "market"):
        """
        下单封装。若 DRY_RUN 为 True，则不会发送到交易所，而是记录模拟订单。
        order_type 支持 "market" 或 "limit"
        """
        logger.info(f"[{now_ts()}] 下单请求: {side} {amount} {symbol} @ {order_type} {price}")
        if DRY_RUN:
            logger.info("[DRY_RUN] 模拟下单，不会提交真实订单。")
            return {
                "id": f"sim-{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "status": "simulated",
                "timestamp": int(time.time()*1000)
            }
        try:
            if order_type == "market":
                order = self.exchange.create_market_order(symbol, side, amount)
            else:
                order = self.exchange.create_limit_order(symbol, side, amount, price)
            logger.info(f"下单结果: {order}")
            return order
        except Exception as e:
            logger.exception("下单失败：")
            raise

# ========== 策略示例(SMA 交叉) ==========

class SmaCross:
    """
    简单均线交叉策略(示例)
    - 快线(short_window)上穿慢线(long_window) -> 买入
    - 快线下穿慢线 -> 卖出
    """

    def __init__(self, short_window: int = 10, long_window: int = 30):
        if short_window >= long_window:
            raise ValueError("short_window must be < long_window")
        self.short = short_window
        self.long = long_window

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        df: DataFrame 包含 close 列
        返回信号序列(index 与 df 对齐)： 1 买入信号， -1 卖出信号， 0 无操作
        """
        close = df["close"].astype(float)
        sma_short = close.rolling(self.short).mean()
        sma_long = close.rolling(self.long).mean()
        signal = pd.Series(0, index=df.index)
        cross_up = (sma_short.shift(1) <= sma_long.shift(1)) & (sma_short > sma_long)
        cross_down = (sma_short.shift(1) >= sma_long.shift(1)) & (sma_short < sma_long)
        signal[cross_up] = 1
        signal[cross_down] = -1
        return signal

# ========== 回测：Backtrader 集成 ==========

class SmaCrossBT(bt.Strategy):
    params = dict(short=10, long=30, stake=0.001)

    def __init__(self):
        self.dataclose = self.datas[0].close
        self.sma_short = bt.indicators.SimpleMovingAverage(self.datas[0], period=self.p.short)
        self.sma_long = bt.indicators.SimpleMovingAverage(self.datas[0], period=self.p.long)
        self.crossover = bt.indicators.CrossOver(self.sma_short, self.sma_long)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                # 买入：以全部资金的某个比例或固定手数
                size = self.p.stake
                self.buy(size=size)
        else:
            if self.crossover < 0:
                self.close()

def run_backtest(df: pd.DataFrame, cash: float = INITIAL_CASH, short: int = 10, long: int = 30, stake: float = 0.001):
    """
    使用 backtrader 运行回测(df 必须包含 datetime index，open/high/low/close/volume)
    """
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    # 将 pandas DataFrame 转为 backtrader feed
    data = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(data)
    cerebro.addstrategy(SmaCrossBT, short=short, long=long, stake=stake)
    cerebro.addsizer(bt.sizers.FixedSize, stake=1)
    logger.info(f"[{now_ts()}] 回测开始，初始资金: {cash}")
    start_val = cerebro.broker.getvalue()
    cerebro.run()
    end_val = cerebro.broker.getvalue()
    logger.info(f"[{now_ts()}] 回测结束，结束资金: {end_val:.2f}，收益: {end_val - start_val:.2f}")
    # 返回 cerebro 以便画图或进一步处理
    return cerebro

# ========== 模拟/实盘交易循环 ==========

class TradingBot:
    def __init__(self, client: ExchangeClient, symbol: str = DEFAULT_SYMBOL, timeframe: str = DEFAULT_TIMEFRAME, strategy: Optional[SmaCross] = None):
        self.client = client
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy or SmaCross(short_window=10, long_window=30)
        self.position = 0.0  # 持仓数量(仅本地记录)
        logger.info(f"[{now_ts()}] TradingBot 初始化: {symbol} {timeframe}")

    def fetch_recent(self, since_minutes: int = 1000) -> pd.DataFrame:
        """
        获取最近 K 线。since_minutes 表示回溯分钟数，用于计算 since 时间。
        """
        limit = None
        since = None
        if since_minutes:
            since_dt = datetime.utcnow() - timedelta(minutes=since_minutes)
            since = int(since_dt.timestamp() * 1000)
        df = self.client.fetch_ohlcv(self.symbol, self.timeframe, since=since, limit=limit)
        return df

    def step(self):
        """
        单次决策与下单流程(可以被 schedule 定时调用)
        """
        try:
            df = self.fetch_recent(since_minutes=60 * 24)  # 拉取足够历史供指标计算
            signals = self.strategy.generate_signals(df)
            latest_signal = int(signals.dropna().iloc[-1])  # 1, -1, or 0
            last_close = float(df["close"].iloc[-1])
            logger.info(f"[{now_ts()}] 最新收盘价: {last_close} 信号: {latest_signal}")

            # 简单仓位逻辑示例：持仓为 0 且买信号 -> 买入固定数量；持仓 > 0 且卖信号 -> 卖出全部
            if latest_signal == 1 and self.position == 0:
                amount = float(os.getenv("TRADE_AMOUNT", "0.001"))  # 可用 env 配置
                order = self.client.create_order(self.symbol, "buy", amount)
                if order and order.get("status") != "rejected":
                    self.position += amount
                    logger.info(f"买入成功(本地更新持仓): {amount} {self.symbol}")
            elif latest_signal == -1 and self.position > 0:
                amount = self.position
                order = self.client.create_order(self.symbol, "sell", amount)
                if order and order.get("status") != "rejected":
                    self.position = 0.0
                    logger.info("卖出成功(本地更新持仓)")
            else:
                logger.info("无交易动作")
        except Exception as e:
            logger.exception("step 执行出错：")

    def run_loop(self, interval_seconds: int = 60):
        """
        简单循环：每 interval_seconds 执行一次 step。生产环境建议用更稳健的调度/worker。
        """
        logger.info(f"[{now_ts()}] 进入运行循环，间隔 {interval_seconds} 秒。DRY_RUN={DRY_RUN}")
        try:
            while True:
                self.step()
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("已停止运行循环(KeyboardInterrupt)")

# ========== CLI / 主入口 ==========

def parse_args():
    p = argparse.ArgumentParser(description="Quant Bot - CLI")
    p.add_argument("--mode", choices=["backtest", "live", "paper", "fetch"], default="backtest",
                   help="运行模式：backtest / live / paper (模拟实盘) / fetch (仅拉数据)")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL)
    p.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    p.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    p.add_argument("--short", type=int, default=10)
    p.add_argument("--long", type=int, default=30)
    p.add_argument("--interval", type=int, default=60, help="live/paper 模式下循环间隔(秒)")
    return p.parse_args()

def main():
    args = parse_args()
    client = ExchangeClient()
    if args.mode == "fetch":
        since_dt = datetime.utcnow() - timedelta(days=args.since_days)
        since = int(since_dt.timestamp() * 1000)
        df = client.fetch_ohlcv(args.symbol, args.timeframe, since=since)
        print(df.tail())
        return

    if args.mode == "backtest":
        logger.info("开始回测模式")
        since_dt = datetime.utcnow() - timedelta(days=args.since_days)
        since = int(since_dt.timestamp() * 1000)
        df = client.fetch_ohlcv(args.symbol, args.timeframe, since=since)
        # backtrader 需要列名 open/high/low/close/volume 和 datetime index
        cerebro = run_backtest(df, cash=INITIAL_CASH, short=args.short, long=args.long, stake=1)
        # 默认不绘图，若需要绘图：cerebro.plot()
        return

    # live / paper 模式
    bot = TradingBot(client, symbol=args.symbol, timeframe=args.timeframe, strategy=SmaCross(short_window=args.short, long_window=args.long))
    if args.mode == "live":
        if DRY_RUN:
            logger.warning("你当前处于 DRY_RUN 模式。若要开启真实下单，请在环境变量中设置 DRY_RUN=false 并谨慎操作。")
        logger.info("进入 LIVE 模式(循环执行)")
        bot.run_loop(interval_seconds=args.interval)
    elif args.mode == "paper":
        # paper 模式：强制模拟下单(覆盖 DRY_RUN)
        global DRY_RUN
        DRY_RUN = True
        logger.info("进入 PAPER 模式(强制模拟下单)")
        bot.run_loop(interval_seconds=args.interval)

if __name__ == "__main__":
    main()
