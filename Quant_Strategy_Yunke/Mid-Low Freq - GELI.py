import os
import pandas as pd
import datetime as dt
import warnings
import yfinance as yf
import backtrader as bt
import matplotlib.pyplot as plt
import akshare as ak  # 新增 A 股数据源

# ==========================================
# 【1. 全局配置开关】
# ==========================================
# 切换为 AKSHARE 或 YAHOO
DATA_SOURCE = 'AKSHARE'
STRATEGY_CHOICE = 'MOM'

SHOW_TRADE_LOG = False
SHOW_FINAL_REPORT = True

START_CASH = 100000.0
# A股佣金通常在万分之三左右，印花税卖出时千分之一
# 这里简化设定一个综合双边费率，或者在下方具体设置
COMMISSION = 0.001

# --- 时间与标的 ---
START_DATE = '20200101'  # AKShare 常用格式 YYYYMMDD
END_DATE = '20251231'

if DATA_SOURCE == 'AKSHARE':
    TARGET_SYMBOL = '000651'  # 格力电器
    MARKET_SYMBOL = '000300'  # 沪深300 (如果用MOM策略需用到)
else:
    TARGET_SYMBOL = 'TSLA'
    MARKET_SYMBOL = 'SPY'

warnings.filterwarnings("ignore")


# ==========================================
# 【2. 数据引擎】
# ==========================================

def clean_dataframe(df):
    """
    统一清洗数据格式，确保符合 backtrader 的要求
    """
    df.columns = [str(col).lower() for col in df.columns]
    # 映射 AKShare 字段到标准字段
    rename_dict = {'开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume', '日期': 'date'}
    df = df.rename(columns=rename_dict)

    if 'date' in df.columns:
        df.index = pd.to_datetime(df['date'])
    else:
        df.index = pd.to_datetime(df.index)

    keep_cols = ['open', 'high', 'low', 'close', 'volume']
    df = df[keep_cols]
    df = df.astype(float)
    return df


def fetch_akshare_data(symbol, start_str, end_str):
    """
    爬取 A 股历史行情（后复权）
    """
    print(f"正在从 AKShare 获取 {symbol} 数据...")
    try:
        # adjust="qfq" 表示前复权，这对回测至关重要（处理除权除息造成的股价跳空）
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                start_date=start_str, end_date=end_str,
                                adjust="qfq")
        return df
    except Exception as e:
        print(f"获取数据失败: {e}")
        return None


def load_data_unified(symbol):
    if not symbol: return None
    cache_file = f"{DATA_SOURCE}_{symbol}_{START_DATE}_{END_DATE}.csv"

    if os.path.exists(cache_file):
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)

    df = None
    if DATA_SOURCE == 'AKSHARE':
        df = fetch_akshare_data(symbol, START_DATE, END_DATE)
    elif DATA_SOURCE == 'YAHOO':
        # Yahoo Finance A股需加后缀 .SZ (深证) 或 .SS (上证)
        yf_symbol = symbol + (".SZ" if symbol.startswith("0") or symbol.startswith("3") else ".SS")
        df = yf.download(yf_symbol, start=START_DATE, end=END_DATE, auto_adjust=True)

    if df is not None and not df.empty:
        df = clean_dataframe(df)
        df.to_csv(cache_file)
        return df
    return None


# ==========================================
# 【3. 策略定义】 (保持原有逻辑不变)
# ==========================================

class BaseStrategy(bt.Strategy):
    def log(self, txt, dname=None):
        if SHOW_TRADE_LOG:
            dt_val = self.datas[0].datetime.date(0)
            print(f"{dt_val.isoformat()} [{dname or ''}] {txt}")


class MaCrossStrategy(BaseStrategy):
    params = (('fast', 5), ('slow', 30))

    def __init__(self):
        self.inds = {d: bt.ind.CrossOver(bt.ind.SMA(d, period=self.params.fast),
                                         bt.ind.SMA(d, period=self.params.slow)) for d in self.datas}

    def next(self):
        for d in self.datas:
            pos = self.getposition(d).size
            if not pos and self.inds[d] > 0:
                self.buy(data=d)
            elif pos and self.inds[d] < 0:
                self.close(data=d)


# ... (UltimateStrategy 和 MomentumStrategy 保持不变) ...
class UltimateStrategy(BaseStrategy):
    params = (('p1', 7), ('p2', 14), ('p3', 28), ('low', 30), ('high', 70))

    def __init__(self):
        self.ult = {d: bt.ind.UltimateOscillator(d, p1=self.params.p1, p2=self.params.p2, p3=self.params.p3) for d in
                    self.datas}

    def next(self):
        for d in self.datas:
            pos = self.getposition(d).size
            if not pos and self.ult[d] < self.params.low:
                self.buy(data=d)
            elif pos and self.ult[d] > self.params.high:
                self.close(data=d)


# ==========================================
# 【4. 主运行程序】
# ==========================================
if __name__ == '__main__':
    target_data = load_data_unified(TARGET_SYMBOL)

    if target_data is not None:
        cerebro = bt.Cerebro()

        # 加载数据
        data_feed = bt.feeds.PandasData(dataname=target_data)
        cerebro.adddata(data_feed, name=TARGET_SYMBOL)

        # A股交易规则：最少买100股 (1手)
        # 这里的 Sizer 设置为 50% 仓位，Backtrader 默认不强制百股整数
        # 如需强制百股，可自定义 Sizer，这里演示先保持 PercentSizer
        cerebro.addsizer(bt.sizers.PercentSizer, percents=50)

        # 策略加载
        cerebro.addstrategy(MaCrossStrategy)  # 默认使用均线交叉

        # 账户与费用
        cerebro.broker.setcash(START_CASH)
        # 模拟 A 股印花税和佣金：这里设置 0.1% 综合费率
        cerebro.broker.setcommission(commission=COMMISSION)

        # 分析器
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe',
                            riskfreerate=0.03, annualize=True, timeframe=bt.TimeFrame.Days)

        print(f"--- 启动回测 | 策略: {STRATEGY_CHOICE} | 目标: {TARGET_SYMBOL} (格力电器) ---")
        results = cerebro.run()
        strat = results[0]

        if SHOW_FINAL_REPORT:
            final_v = cerebro.broker.getvalue()
            total_ret = (final_v - START_CASH) / START_CASH

            dd_stats = strat.analyzers.dd.get_analysis()
            sharpe_stats = strat.analyzers.sharpe.get_analysis()

            print('\n' + '█' * 50)
            print(f'   【 A 股市场回测总结报告 】')
            print('█' * 50)
            print(f' • 交易标的     :  {TARGET_SYMBOL} (格力电器)')
            print(f' • 初始资产     :  {START_CASH:,.2f}')
            print(f' • 最终资产     :  {final_v:,.2f}')
            print(f' • 累计收益率   :  {total_ret * 100:.2f}%')
            print(f' • 最大资金回撤 :  {dd_stats.max.drawdown:.2f}%')
            print(f' • 夏普比率     :  {sharpe_stats.get("sharperatio", 0):.2f}')
            print('█' * 50 + '\n')

        # 解决 macOS/Windows 绘图可能报错的问题
        try:
            cerebro.plot(style='candle', iplot=False)
        except:
            print("绘图失败，请检查图形库配置。")
    else:
        print("错误：数据加载失败。")