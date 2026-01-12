import os
import requests
import pandas as pd
import numpy as np
import datetime as dt
import backtrader as bt
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 【1. 全局配置】
# ==========================================
SYMBOL = 'BTCUSDT'
INTERVAL = '1h'
START_DATE = '2024-01-01'
END_DATE = '2026-01-01'
START_CASH = 100000.0
COMMISSION = 0.0004


# ==========================================
# 【2. 策略定义：科学调参版】
# ==========================================
class ScientificMultiFactor(bt.Strategy):
    params = (
        ('ema_long', 200),  # 增加到200，只看大趋势
        ('adx_period', 14),
        ('adx_min', 25),  # 只有ADX > 25才进场，过滤震荡磨损
        ('atr_period', 14),
        ('risk_percent', 0.01),  # 单笔风险 1%
    )

    def __init__(self):
        # 1. 核心过滤：长期均线
        self.ema = bt.ind.EMA(period=self.params.ema_long)

        # 2. 核心过滤：趋势强度
        self.adx = bt.ind.ADX(period=self.params.adx_period)

        # 3. 辅助动量：RSI
        self.rsi = bt.ind.RSI(period=14)

        # 4. 仓位控制：ATR
        self.atr = bt.ind.ATR(period=self.params.atr_period)

        self.stop_price = 0
        self.highest_price = 0

    def next(self):
        pos = self.getposition().size

        # --- 因子逻辑 ---
        c1 = self.data.close[0] > self.ema[0]  # 价格在200均线上方
        c2 = self.adx[0] > self.params.adx_min  # 趋势足够强
        c3 = self.rsi[0] > 50  # 处于多头动量

        if not pos:
            # 只有当大趋势、强度、动量三者共振时入场
            if c1 and c2 and c3:
                # 动态计算仓位
                stop_dist = self.atr[0] * 3.0  # 放宽止损到 3倍 ATR，减少被震仓
                self.stop_price = self.data.close[0] - stop_dist

                risk_amt = self.broker.get_value() * self.params.risk_percent
                size = risk_amt / stop_dist

                max_size = (self.broker.get_cash() * 0.95) / self.data.close[0]
                self.buy(size=min(size, max_size))
                self.highest_price = self.data.close[0]
        else:
            # --- 持仓管理 ---
            # 1. 追踪止损：如果价格创收盘新高，移动止损位向上
            self.highest_price = max(self.highest_price, self.data.close[0])
            # 只要价格回调超过 2.5倍 ATR，就离场
            trailing_stop = self.highest_price - (self.atr[0] * 2.5)

            if self.data.close[0] < max(self.stop_price, trailing_stop):
                self.close()

            # 2. 趋势反转离场
            elif self.data.close[0] < self.ema[0]:
                self.close()


# ==========================================
# 【3. 运行逻辑】 (fetch_binance_data 保持不变)
# ==========================================
def fetch_binance_data(symbol, interval, start_str, end_str):
    cache_file = f"binance_{symbol}_{interval}_{start_str}_{end_str}.csv"
    if os.path.exists(cache_file): return pd.read_csv(cache_file, index_col=0, parse_dates=True)
    url = 'https://api.binance.com/api/v3/klines'
    start_dt = dt.datetime.strptime(start_str, '%Y-%m-%d')
    end_dt = dt.datetime.strptime(end_str, '%Y-%m-%d')
    all_dfs = []
    curr_start = start_dt
    while curr_start < end_dt:
        params = {'symbol': symbol, 'interval': interval, 'startTime': int(curr_start.timestamp() * 1000),
                  'limit': 1000}
        res = requests.get(url, params=params).json()
        if not res or 'code' in res: break
        df = pd.DataFrame(res).iloc[:, :6]
        df.columns = ['time', 'open', 'high', 'low', 'close', 'volume']
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)
        all_dfs.append(df)
        curr_start = df.index[-1] + dt.timedelta(hours=1)
    full_df = pd.concat(all_dfs).astype(float)
    full_df.to_csv(cache_file)
    return full_df


if __name__ == '__main__':
    df_btc = fetch_binance_data(SYMBOL, INTERVAL, START_DATE, END_DATE)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_btc), name=SYMBOL)
    cerebro.addstrategy(ScientificMultiFactor)
    cerebro.broker.setcash(START_CASH)
    cerebro.broker.setcommission(commission=COMMISSION)

    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='ta')
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')

    results = cerebro.run()
    strat = results[0]

    # --- 报告 ---
    ta = strat.analyzers.ta.get_analysis()
    final_v = cerebro.broker.getvalue()
    total_closed = ta.total.closed if 'total' in ta else 0
    sharpe = strat.analyzers.sharpe.get_analysis().get('sharperatio', 0)
    max_dd = strat.analyzers.dd.get_analysis().max.drawdown

    print('\n' + '█' * 60)
    print(f'   【 科学调参版多因子策略报告 】')
    print('█' * 60)
    print(f' • 最终资产      :  {final_v:,.2f} ({((final_v - START_CASH) / START_CASH) * 100:.2f}%)')
    print(f' • 夏普比率      :  {sharpe:.2f}')
    print(f' • 最大回撤      :  {max_dd:.2f}%')
    print('------------------------------------------------------------')
    print(f' • 交易次数      :  {total_closed} 次 (目标：降低频率，单次重质)')

    total_comm = abs(ta.pnl.net.total - ta.pnl.gross.total) if total_closed > 0 else 0
    print(f' • 总手续费支出  :  {total_comm:,.2f}')
    print('█' * 60 + '\n')