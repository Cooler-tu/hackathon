#!/usr/bin/env python3
"""
kz_bot.py - 全功能量化交易机器人模板
功能：
 - 读取配置(.env / 环境变量)
 - 拉取历史/实时行情(ccxt / REST)
 - 简单策略(SMA 短/长均线交叉)
 - 回测(backtrader)
 - 模拟/实盘下单(ccxt,支持 dry-run)
 - 日志 (loguru) 与简单调度(schedule)
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from typing import Optional

import ccxt
import pandas as pd
import numpy as np
from loguru import logger
from dotenv import load_dotenv
import schedule
import backtrader as bt

# ========== 配置 ==========

load_dotenv()

API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binance")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
BASE_CURRENCY = os.getenv("BASE_CURRENCY", "USDT")

DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "BTC/USDT")
DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "1h")
DEFAULT_SINCE_DAYS = int(os.getenv("DEFAULT_SINCE_DAYS", "90"))
INITIAL_CASH = float(os.getenv("INITIAL_CASH", "10000.0"))

logger.add("bot.log", rotation="10 MB", retention="7 days", level="INFO")

# ========== 工具函数 ==========

def now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ========== 交易所封装 ==========

class ExchangeClient:
    def __init__(self, exchange_id: str = EXCHANGE_ID, api_key: str = API_KEY, api_secret: str = API_SECRET):
        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True
        })
        logger.info(f"[{now_ts()}] 初始化交易所客户端：{exchange_id}, DRY_RUN={DRY_RUN}")

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("datetime", inplace=True)
        return df

    def create_order(self, symbol, side, amount, price=None, order_type="market"):
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
                return self.exchange.create_market_order(symbol, side, amount)
            else:
                return self.exchange.create_limit_order(symbol, side, amount, price)
        except Exception:
            logger.exception("下单失败：")
            raise

# ========== 策略(SMA交叉) ==========

class SmaCross:
    def __init__(self, short_window=10, long_window=30):
        if short_window >= long_window:
            raise ValueError("short_window must be < long_window")
        self.short = short_window
        self.long = long_window

    def generate_signals(self, df):
        close = df["close"].astype(float)
        sma_short = close.rolling(self.short).mean()
        sma_long = close.rolling(self.long).mean()
        signal = pd.Series(0, index=df.index)
        cross_up = (sma_short.shift(1) <= sma_long.shift(1)) & (sma_short > sma_long)
        cross_down = (sma_short.shift(1) >= sma_long.shift(1)) & (sma_short < sma_long)
        signal[cross_up] = 1
        signal[cross_down] = -1
        return signal

# ========== 回测 ==========
class SmaCrossBT(bt.Strategy):
    params = dict(short=10, long=30, stake=0.001)
    def __init__(self):
        self.sma_short = bt.indicators.SimpleMovingAverage(self.datas[0], period=self.p.short)
        self.sma_long = bt.indicators.SimpleMovingAverage(self.datas[0], period=self.p.long)
        self.crossover = bt.indicators.CrossOver(self.sma_short, self.sma_long)
    def next(self):
        if not self.position and self.crossover > 0:
            self.buy(size=self.p.stake)
        elif self.position and self.crossover < 0:
            self.close()

def run_backtest(df, cash=INITIAL_CASH, short=10, long=30, stake=0.001):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    data = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(data)
    cerebro.addstrategy(SmaCrossBT, short=short, long=long, stake=stake)
    start_val = cerebro.broker.getvalue()
    logger.info(f"[{now_ts()}] 回测开始: 初始资金 {start_val}")
    cerebro.run()
    end_val = cerebro.broker.getvalue()
    logger.info(f"[{now_ts()}] 回测结束: 最终资金 {end_val}, 收益 {end_val - start_val}")
    return cerebro

# ========== 交易主循环 ==========

class TradingBot:
    def __init__(self, client, symbol=DEFAULT_SYMBOL, timeframe=DEFAULT_TIMEFRAME, strategy=None):
        self.client = client
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy or SmaCross()
        self.position = 0.0
        logger.info(f"[{now_ts()}] TradingBot 初始化: {symbol}")

    def fetch_recent(self, since_minutes=1000):
        since_dt = datetime.utcnow() - timedelta(minutes=since_minutes)
        since = int(since_dt.timestamp() * 1000)
        return self.client.fetch_ohlcv(self.symbol, self.timeframe, since=since)

    def step(self):
        try:
            df = self.fetch_recent(60 * 24)
            signals = self.strategy.generate_signals(df)
            signal = int(signals.dropna().iloc[-1])
            last_close = float(df["close"].iloc[-1])
            logger.info(f"[{now_ts()}] 最新价 {last_close} 信号 {signal}")

            if signal == 1 and self.position == 0:
                amount = float(os.getenv("TRADE_AMOUNT", "0.001"))
                order = self.client.create_order(self.symbol, "buy", amount)
                if order:
                    self.position += amount
                    logger.info("买入成功")
            elif signal == -1 and self.position > 0:
                amount = self.position
                order = self.client.create_order(self.symbol, "sell", amount)
                if order:
                    self.position = 0.0
                    logger.info("卖出成功")
            else:
                logger.info("无交易动作")
        except Exception:
            logger.exception("step 执行出错：")

    def run_loop(self, interval_seconds=60):
        logger.info(f"[{now_ts()}] 开始运行循环，每 {interval_seconds}s 执行一次，DRY_RUN={DRY_RUN}")
        try:
            while True:
                self.step()
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("循环停止")

# ========== 主程序入口 ==========

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["backtest", "live", "paper", "fetch"], default="backtest")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL)
    p.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    p.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    p.add_argument("--short", type=int, default=10)
    p.add_argument("--long", type=int, default=30)
    p.add_argument("--interval", type=int, default=60)
    return p.parse_args()

def main():
    global DRY_RUN  # ✅ 移到函数最顶部
    args = parse_args()
    client = ExchangeClient()

    if args.mode == "fetch":
        since_dt = datetime.utcnow() - timedelta(days=args.since_days)
        since = int(since_dt.timestamp() * 1000)
        df = client.fetch_ohlcv(args.symbol, args.timeframe, since=since)
        print(df.tail())
        return

    if args.mode == "backtest":
        since_dt = datetime.utcnow() - timedelta(days=args.since_days)
        since = int(since_dt.timestamp() * 1000)
        df = client.fetch_ohlcv(args.symbol, args.timeframe, since=since)
        run_backtest(df, cash=INITIAL_CASH, short=args.short, long=args.long)
        return

    bot = TradingBot(client, symbol=args.symbol, timeframe=args.timeframe, strategy=SmaCross(args.short, args.long))

    if args.mode == "live":
        if DRY_RUN:
            logger.warning("当前为 DRY_RUN 模式，未进行真实下单")
        bot.run_loop(interval_seconds=args.interval)
    elif args.mode == "paper":
        DRY_RUN = True  # ✅ 不再触发语法错误
        logger.info("进入 PAPER 模式(强制模拟下单)")
        bot.run_loop(interval_seconds=args.interval)

if __name__ == "__main__":
    main()
