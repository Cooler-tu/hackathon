# kz_bot.py
#!/usr/bin/env python3
"""
kz_bot.py - 全功能量化交易机器人（适配 Roostoo Mock API）
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np
from loguru import logger
from dotenv import load_dotenv
import schedule
import backtrader as bt

from roostoo_client import RoostooClient

# ========== 配置 ==========
load_dotenv()

API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
EXCHANGE_ID = os.getenv("EXCHANGE_ID", "roostoo")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
BASE_CURRENCY = os.getenv("BASE_CURRENCY", "USD")

DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "BTC/USD")
DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "1h")
DEFAULT_SINCE_DAYS = int(os.getenv("DEFAULT_SINCE_DAYS", "90"))
INITIAL_CASH = float(os.getenv("INITIAL_CASH", "1000000.0"))
TRADE_AMOUNT = int(float(os.getenv("TRADE_AMOUNT", "10000")))

logger.add("bot.log", rotation="10 MB", retention="7 days", level="INFO", enqueue=True, backtrace=True)

# ========== 工具函数 ==========
def now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ========== 交易所封装 ==========
class ExchangeClient:
    def __init__(self):
        self.client = RoostooClient()
        logger.info(f"[{now_ts()}] 初始化 Roostoo Mock 客户端, DRY_RUN={DRY_RUN}")

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=200):
        logger.info("生成模拟 K 线数据（Mock API 无 OHLCV 接口，强制触发买卖）")
        np.random.seed(int(datetime.utcnow().timestamp()) % 10000)

        dates = pd.date_range(end=datetime.utcnow(), periods=limit, freq='5min')

        # --- 1️⃣ 明显的先涨后跌趋势 ---
        half = limit // 2
        up_trend = np.linspace(0, 3000, half)
        down_trend = np.linspace(3000, -2000, limit - half)
        trend = np.concatenate([up_trend, down_trend])

        # --- 2️⃣ 加噪声制造局部波动 ---
        noise = np.random.randn(limit) * 150
        close = 29000 + trend + noise
        close = np.maximum(close, 10000)

        # --- 3️⃣ 生成K线 ---
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        high = np.maximum(open_, close) + np.abs(np.random.randn(limit) * 50)
        low = np.minimum(open_, close) - np.abs(np.random.randn(limit) * 50)
        volume = np.random.randint(500, 1500, limit)

        df = pd.DataFrame({
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        }, index=dates)

        return df.tail(limit)


    def create_order(self, symbol, side, amount, price=None, order_type="market"):
        logger.info(f"[{now_ts()}] 下单请求: {side} {amount} {symbol} @ {order_type}")
        if DRY_RUN:
            logger.info("[DRY_RUN] 模拟下单")
            return {"id": f"sim-{int(time.time()*1000)}", "status": "filled"}
        try:
            pair = symbol  # BTC/USD
            quantity = float(amount)
            return self.client.place_order(pair, side, quantity, price)
        except Exception:
            logger.exception("下单失败")
            raise

    def get_balance(self):
        try:
            data = self.client.get_balance()
            logger.debug(f"原始余额数据: {data}")
            
            spot = data.get("SpotWallet", {})
            balances = {}
            # 正确遍历所有币种
            for currency, info in spot.items():
                free = info.get("Free", 0)
                lock = info.get("Lock", 0)
                # 确保是数字
                balances[currency] = float(free or 0) + float(lock or 0)
            return balances
        except Exception as e:
            logger.warning(f"获取余额失败: {e}, 使用默认值")
            return {"USD": INITIAL_CASH}

# ========== 策略 ==========
class SmaCross:
    def __init__(self, short_window=10, long_window=30):
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
    params = dict(short=10, long=30, stake=10000)
    def __init__(self):
        self.sma_short = bt.indicators.SMA(self.datas[0], period=self.p.short)
        self.sma_long = bt.indicators.SMA(self.datas[0], period=self.p.long)
        self.crossover = bt.indicators.CrossOver(self.sma_short, self.sma_long)
    def next(self):
        if not self.position and self.crossover > 0:
            self.buy(size=self.p.stake)
        elif self.position and self.crossover < 0:
            self.close()

def run_backtest(df, cash=INITIAL_CASH, short=10, long=30, stake=10000):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    data = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(data)
    cerebro.addstrategy(SmaCrossBT, short=short, long=long, stake=stake)
    start_val = cerebro.broker.getvalue()
    logger.info(f"[{now_ts()}] 回测开始: 初始资金 {start_val}")
    cerebro.run()
    end_val = cerebro.broker.getvalue()
    logger.info(f"[{now_ts()}] 回测结束: 最终资金 {end_val}, 收益 {end_val - start_val:.2f}")
    return cerebro

# ========== 主循环 ==========
class TradingBot:
    def __init__(self, client, symbol=DEFAULT_SYMBOL, strategy=None):
        self.client = client
        self.symbol = symbol
        self.strategy = strategy or SmaCross()
        self.position = 0.0

        self.sim_usd = 50000.0
        self.sim_btc = 0.0

        logger.info(f"[{now_ts()}] Bot 初始化: {symbol}")

    def step(self):
        try:
            df = self.client.fetch_ohlcv(self.symbol, DEFAULT_TIMEFRAME, limit=200)
            close = df['close']

            # 计算短期、长期均线
            short_window = 20
            long_window = 50
            short_ma = close.rolling(window=short_window).mean()
            long_ma = close.rolling(window=long_window).mean()

            # 计算信号（均线交叉）
            signal = 0
            if short_ma.iloc[-2] < long_ma.iloc[-2] and short_ma.iloc[-1] > long_ma.iloc[-1]:
                signal = 1  # 金叉 → 买入
            elif short_ma.iloc[-2] > long_ma.iloc[-2] and short_ma.iloc[-1] < long_ma.iloc[-1]:
                signal = -1  # 死叉 → 卖出

            price = float(close.iloc[-1])
            # 获取余额
            if DRY_RUN:
                usd_balance = self.sim_usd
                btc_balance = self.sim_btc
            else:
                balance = self.client.get_balance()
                usd_balance = balance.get("USD", 0)
                btc_balance = balance.get("BTC", 0)

            # 初始化仓位追踪
            if not hasattr(self, 'entry_price'):
                self.entry_price = 0.0

            # 实时盈亏计算
            pnl = 0.0
            if btc_balance > 0:
                pnl = (price - self.entry_price) / self.entry_price * 100

            # 输出详细调试信息
            logger.debug(f"短均线={short_ma.iloc[-1]:.2f}, 长均线={long_ma.iloc[-1]:.2f}")
            logger.info(
                f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"价格: {price:.2f} | 信号: {signal} | 持仓: {btc_balance:.4f} BTC | 现金: {usd_balance:.2f} USD"
            )

            # 执行交易逻辑
            if signal == 1 and usd_balance > 10:
                # 买入信号
                amount = usd_balance / price
                order = self.client.create_order(self.symbol, 'buy', amount, price)
                if order and order.get("status") == "filled":
                    cost = amount * price
                    if DRY_RUN:
                        self.sim_usd -= cost
                        self.sim_btc += amount
                    self.entry_price = price
                    logger.info(f"买入成功 | 数量: {amount:.6f} BTC | 成本: ${cost:.2f}")
                else:
                    logger.warning(f"买入失败: {order}")
            elif signal == -1 and btc_balance > 0:
                # 卖出信号
                self.client.place_order(self.symbol, 'sell', btc_balance, price)
                logger.info(f"💰 触发【卖出】信号 → 价格: {price:.2f} USD | 平仓收益: {pnl:.2f}%")
                self.entry_price = 0.0
            else:
                logger.info("无信号")

            # 保存信号历史（用于分析）
            if not hasattr(self, 'signals'):
                self.signals = []
            self.signals.append(signal)
            logger.info(f"最近60个信号: {self.signals.tail(60)}")

        except Exception as e:
            logger.error("step 出错", exc_info=True)


    def run_loop(self, interval_seconds=60):
        logger.info(f"[{now_ts()}] 启动循环，每 {interval_seconds}s 执行一次")
        try:
            while True:
                self.step()
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("停止")

# ========== 主程序 ==========
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
    global DRY_RUN
    args = parse_args()
    client = ExchangeClient()

    if args.mode == "fetch":
        df = client.fetch_ohlcv(args.symbol, args.timeframe)
        print(df.tail())
        return

    if args.mode == "backtest":
        df = client.fetch_ohlcv(args.symbol, args.timeframe)
        run_backtest(df, cash=INITIAL_CASH, short=args.short, long=args.long, stake=TRADE_AMOUNT)
        return

    bot = TradingBot(client, symbol=args.symbol, strategy=SmaCross(args.short, args.long))

    if args.mode in ["live", "paper"]:
        if args.mode == "paper":
            DRY_RUN = True
        bot.run_loop(interval_seconds=args.interval)

if __name__ == "__main__":
    main()