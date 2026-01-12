import os
import requests
import pandas as pd
import numpy as np
import datetime as dt
import backtrader as bt
import warnings

# 忽略警告
warnings.filterwarnings("ignore")

# ==========================================
# 【1. 全局配置】
# ==========================================
# 建议使用 SOL vs ETH，波动率更高，空间更大
SYMBOL_A = 'SOLUSDT'
SYMBOL_B = 'ETHUSDT'
INTERVAL = '1m'
START_DATE = '2026-01-01'
END_DATE = '2026-01-10'

# 手续费设置：模拟合约费率 (0.04%)。
# 注意：在散户现货费率(0.1%)下，几乎所有高频策略都会亏损。
COMMISSION = 0.0004
START_CASH = 100000.0
PORTFOLIO_USE_PERCENT = 0.2  # 每次动用 20% 资金


# ==========================================
# 【2. 数据引擎：抓取与相似度预检】
# ==========================================
def fetch_binance_1m(symbol, start_str, end_str):
    cache_file = f"binance_{symbol}_{INTERVAL}_{start_str}_{end_str}.csv"
    if os.path.exists(cache_file):
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)

    url = 'https://api.binance.com/api/v3/klines'
    start_dt = dt.datetime.strptime(start_str, '%Y-%m-%d')
    end_dt = dt.datetime.strptime(end_str, '%Y-%m-%d')
    all_dfs = []
    curr_start = start_dt

    print(f"正在从币安抓取 {symbol} 1分钟数据...")
    while curr_start < end_dt:
        params = {'symbol': symbol, 'interval': INTERVAL,
                  'startTime': int(curr_start.timestamp() * 1000), 'limit': 1000}
        try:
            res = requests.get(url, params=params).json()
            if not res or 'code' in res: break
            df = pd.DataFrame(res).iloc[:, :6]
            df.columns = ['time', 'open', 'high', 'low', 'close', 'volume']
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            all_dfs.append(df)
            curr_start = df.index[-1] + dt.timedelta(minutes=1)
        except:
            break

    if not all_dfs: return None
    full_df = pd.concat(all_dfs).astype(float)
    full_df.to_csv(cache_file)
    return full_df


def analyze_similarity(df_a, df_b):
    correlation = df_a['close'].corr(df_b['close'])
    print('\n' + '═' * 55)
    print(f" 🔍 [数据预检] {SYMBOL_A} / {SYMBOL_B}")
    print(f" • 相关系数 (Correlation): {correlation:.4f}")
    print('═' * 55 + '\n')
    return correlation


# ==========================================
# 【3. 策略定义：利润感知的动态分位数策略】
# ==========================================
class FeeAwareDynamicStrategy(bt.Strategy):
    params = (
        ('lookback', 1000),  # 滚动窗口
        ('q_entry', 0.98),  # 极值入场：只看最极端的 2%
        ('min_profit_pct', 0.0025),  # 利润门槛：必须赚够 0.25% 才走（约 3 倍手续费）
    )

    def __init__(self):
        # 基础比例指标
        self.ratio = self.datas[0].close / self.datas[1].close
        self.mean = bt.ind.SMA(self.ratio, period=200)
        self.std = bt.ind.StdDev(self.ratio, period=200)
        self.zscore = (self.ratio - self.mean) / self.std

        self.z_history = []
        self.current_level = 0
        self.entry_ratio = 0
        self.side = 0

    def next(self):
        z = self.zscore[0]
        self.z_history.append(z)

        # 确保历史数据足够
        if len(self.z_history) < self.params.lookback:
            return

        recent_z = self.z_history[-self.params.lookback:]

        # 动态计算入场阈值
        upper_threshold = np.percentile(recent_z, self.params.q_entry * 100)
        lower_threshold = np.percentile(recent_z, (1 - self.params.q_entry) * 100)
        median_z = np.percentile(recent_z, 50)

        # 仓位大小计算
        cash = self.broker.getvalue()
        size_a = (cash * PORTFOLIO_USE_PERCENT) / self.datas[0].close[0]
        size_b = (cash * PORTFOLIO_USE_PERCENT) / self.datas[1].close[0]

        # --- 1. 入场逻辑 ---
        if self.current_level == 0:
            if z < lower_threshold:
                # 比例太低 -> 买 A 卖 B
                self.buy(data=self.datas[0], size=size_a)
                self.sell(data=self.datas[1], size=size_b)
                self.current_level = 1
                self.entry_ratio = self.ratio[0]
                self.side = 1
            elif z > upper_threshold:
                # 比例太高 -> 卖 A 买 B
                self.sell(data=self.datas[0], size=size_a)
                self.buy(data=self.datas[1], size=size_b)
                self.current_level = 1
                self.entry_ratio = self.ratio[0]
                self.side = -1

        # --- 2. 出场逻辑 ---
        else:
            # 计算当前比例变动带来的利润百分比 (不计手续费的毛利)
            current_profit_pct = (self.ratio[0] / self.entry_ratio - 1) * self.side

            # 条件 A：均值回归 且 利润覆盖了门槛
            regression_signal = (self.side == 1 and z >= median_z) or (self.side == -1 and z <= median_z)

            if regression_signal and current_profit_pct > self.params.min_profit_pct:
                self.close(data=self.datas[0])
                self.close(data=self.datas[1])
                self.current_level = 0
                self.side = 0

            # 条件 B：硬性止损 (发生异常脱钩)
            elif abs(z) > 5.0:
                self.close(data=self.datas[0])
                self.close(data=self.datas[1])
                self.current_level = 0
                self.side = 0


# ==========================================
# 【4. 运行回测与报告】
# ==========================================
if __name__ == '__main__':
    # 数据加载
    df_a = fetch_binance_1m(SYMBOL_A, START_DATE, END_DATE)
    df_b = fetch_binance_1m(SYMBOL_B, START_DATE, END_DATE)

    if df_a is not None and df_b is not None:
        common = df_a.index.intersection(df_b.index)
        df_a, df_b = df_a.loc[common], df_b.loc[common]
        analyze_similarity(df_a, df_b)

        cerebro = bt.Cerebro()
        cerebro.adddata(bt.feeds.PandasData(dataname=df_a), name=SYMBOL_A)
        cerebro.adddata(bt.feeds.PandasData(dataname=df_b), name=SYMBOL_B)

        cerebro.addstrategy(FeeAwareDynamicStrategy)
        cerebro.broker.setcash(START_CASH)
        cerebro.broker.setcommission(commission=COMMISSION)

        # 分析器
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='ta')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', annualize=True)
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')

        print("🚀 正在执行优化后的高频回测...")
        results = cerebro.run()
        strat = results[0]

        # 输出结果
        ta = strat.analyzers.ta.get_analysis()
        total_trades = ta.total.closed if 'total' in ta else 0
        final_v = cerebro.broker.getvalue()
        sharpe = strat.analyzers.sharpe.get_analysis().get('sharperatio', 0)
        max_dd = strat.analyzers.dd.get_analysis().max.drawdown

        # 计算 CAGR
        start_dt = dt.datetime.strptime(START_DATE, '%Y-%m-%d')
        end_dt = dt.datetime.strptime(END_DATE, '%Y-%m-%d')
        years = (end_dt - start_dt).days / 365.25
        cagr = (pow(final_v / START_CASH, 1 / years) - 1) * 100 if years > 0 else 0

        print('\n' + '█' * 55)
        print(f'   【 优化版高频套利报告: {SYMBOL_A} / {SYMBOL_B} 】')
        print('█' * 55)
        print(f' • 策略逻辑     :  动态分位数入场 + 利润覆盖门槛')
        print(f' • 累计交易次数 :  {total_trades} 次 (频率显著降低，质量提升)')
        print(f' • 初始资产     :  {START_CASH:,.2f}')
        print(f' • 最终资产     :  {final_v:,.2f}')
        print(f' • 累计收益率   :  {(final_v - START_CASH) / START_CASH * 100:.2f}%')
        print(f' • 年化收益率   :  {cagr:.2f}%')
        print(f' • 夏普比率     :  {sharpe:.2f}')
        print(f' • 最大回撤     :  {max_dd:.2f}%')
        print('█' * 55 + '\n')

        # 绘图
        try:
            cerebro.plot(style='candle', lookback=1000)
        except:
            print("绘图失败，请检查环境。")
    else:
        print("数据获取失败。")