import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime

# === 日志配置 ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class MockTradingBot:
    def __init__(self, symbol="BTC/USD", balance_usd=50000, interval=5):
        self.symbol = symbol
        self.interval = interval  # 每隔多少秒执行一次
        self.balance_usd = balance_usd
        self.position_btc = 0.0
        self.last_signal = 0
        logger.info(f"初始化虚拟交易机器人: {symbol}, 初始余额 {balance_usd} USD")

    # === 模拟生成价格数据 ===
    def fetch_ohlcv(self, limit=200):
        """生成有波动趋势的虚拟 K 线"""
        np.random.seed(int(time.time()) % 10000)
        dates = pd.date_range(end=datetime.utcnow(), periods=limit, freq='1min')
        base = 30000 + np.sin(np.linspace(0, 6, limit)) * 1500
        noise = np.random.randn(limit) * 200
        close = base + noise
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        high = np.maximum(open_, close) + abs(np.random.randn(limit) * 100)
        low = np.minimum(open_, close) - abs(np.random.randn(limit) * 100)
        return pd.DataFrame({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.randint(500, 5000, limit)
        }, index=dates)

    # === 简单移动均线策略 ===
    def compute_signal(self, df, short_window=10, long_window=30):
        short_ma = df["close"].rolling(short_window).mean()
        long_ma = df["close"].rolling(long_window).mean()
        if len(df) < long_window:
            return 0  # 数据不够，不交易

        if short_ma.iloc[-2] < long_ma.iloc[-2] and short_ma.iloc[-1] > long_ma.iloc[-1]:
            return 1  # 金叉买入信号
        elif short_ma.iloc[-2] > long_ma.iloc[-2] and short_ma.iloc[-1] < long_ma.iloc[-1]:
            return -1  # 死叉卖出信号
        else:
            return 0  # 无信号

    # === 模拟下单逻辑 ===
    def execute_trade(self, signal, price):
        trade_amount = 0.05  # 固定每次交易 0.05 BTC
        if signal == 1 and self.balance_usd >= trade_amount * price:
            self.balance_usd -= trade_amount * price
            self.position_btc += trade_amount
            logger.info(f"🚀 买入 {trade_amount:.4f} BTC at {price:.2f} USD | "
                        f"现金余额: {self.balance_usd:.2f} USD | 持仓: {self.position_btc:.4f} BTC")

        elif signal == -1 and self.position_btc >= trade_amount:
            self.balance_usd += trade_amount * price
            self.position_btc -= trade_amount
            logger.info(f"💥 卖出 {trade_amount:.4f} BTC at {price:.2f} USD | "
                        f"现金余额: {self.balance_usd:.2f} USD | 持仓: {self.position_btc:.4f} BTC")

    # === 主循环 ===
    def run(self):
        logger.info("开始运行，按 Ctrl+C 停止。")
        while True:
            df = self.fetch_ohlcv()
            price = df["close"].iloc[-1]
            signal = self.compute_signal(df)
            logger.info(f"当前价格: {price:.2f} | 信号: {signal} | 持仓: {self.position_btc:.4f} BTC")

            if signal != 0 and signal != self.last_signal:
                self.execute_trade(signal, price)
                self.last_signal = signal
            else:
                logger.info("无交易执行。")

            time.sleep(self.interval)


if __name__ == "__main__":
    bot = MockTradingBot(symbol="BTC/USD", interval=5)
    bot.run()
