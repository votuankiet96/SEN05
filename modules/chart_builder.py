# =============================================================================
# modules/chart_builder.py - Build chart hien thi bang Plotly
# =============================================================================
# Muc tieu file:
# - Chuyen DataFrame da tinh toan thanh chart de quan sat.
# - Chi phuc vu hien thi (visualization), KHONG tao signal giao dich.
#
# Anh huong khi chinh sua:
# - Chinh mau sac/bo cuc/legend/hover: anh huong UI, khong doi ket qua chien luoc.
# - Chinh cot dau vao (Open/open, x_label, BarTime...): de gay vo chart hoac sai canh doc.
#
# Giai thich nhanh cho nguoi quan tri:
# - build_full_chart: dashboard day du indicator (candles + overlay + panel).
# - build_signal_chart: chart scanner SAM (candles + MACD + ATR + marker signal).
# - build_reversal_chart: giong signal_chart, them marker dao chieu.

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .indicators import (
    calc_atr,
    calc_bollinger,
    calc_ema,
    calc_macd,
    calc_obv,
    calc_rsi,
    calc_sma,
    calc_stochastic,
    calc_vwap,
)

# ─────────────────────────────────────────────────────────────────────────────
# Colour palettes
# ─────────────────────────────────────────────────────────────────────────────

# TradingView dark-blue theme  (06_chart_v3)
_TV = dict(
    BG="#131722", BG2="#1e222d", BG3="#2a2e39", BORDER="#363a45",
    TEXT="#d1d4dc", TEXT2="#787b86",
    BLUE="#2962ff", GREEN="#26a69a", RED="#ef5350",
    ORANGE="#ff9800", PURPLE="#ab47bc", CYAN="#00bcd4", LIME="#8bc34a",
)
_MA_COLORS = ["#ff9800", "#2196f3", "#e91e63", "#4caf50", "#9c27b0",
              "#00bcd4", "#ffeb3b", "#ff5722"]

# GitHub dark theme  (10_SAM_dashboard)
_GH = dict(
    BG_PAPER='#0D1117', BG_PLOT='#161B22',
    GRID='#30363D', TEXT='#E6EDF3', MUTED='#8B949E',
)


# =============================================================================
# build_full_chart
# Full candlestick + overlay + panel chart — used by 06_chart_v3
# Expects Title-Case columns: Open, High, Low, Close, Volume, x_label
# =============================================================================

def build_full_chart(df: pd.DataFrame, symbol: str, tf: str,
                     overlay_sel: list, panel_sel: list,
                     params: dict) -> go.Figure:
    """
    Tao chart day du indicator cho dashboard phan tich ky thuat.

    Ham nay chi ve chart, khong can thiep vao scanner/backtest.

    Dau vao:
    - df: phai co BarTime, Open, High, Low, Close, Volume, x_label.
    - overlay_sel: cac indicator ve de len gia (sma, ema, bb, vwap).
    - panel_sel: cac indicator ve o panel rieng (rsi, macd, stoch, atr, obv).
    - params: tham so chu ky indicator.

    Dau ra:
    - Tra ve mot go.Figure da cau hinh bo cuc + trace + mau + hover.

    Tai sao lam vay:
    - Tach logic ve chart ra khoi logic chien luoc de de bao tri.
    - Dung x_label dang category de chart lien mach, khong bi dut do khoang thoi gian.

    Anh huong he thong:
    - Sai ten cot hoac kieu du lieu se lam chart loi ngay.
    - Doi tham so chi thay doi cach nhin chart, khong doi du lieu DB.

    Parameters
    ----------
    df          : DataFrame with BarTime, Open, High, Low, Close, Volume, x_label
    symbol      : symbol name shown in title / legend
    tf          : timeframe code shown in title
    overlay_sel : list subset of 'sma','ema','bb','vwap'
    panel_sel   : list subset of 'rsi','macd','stoch','atr','obv'
    params      : indicator parameter dict (sma_periods, bb_period, …)
    """
    BG, BG2, BG3 = _TV["BG"], _TV["BG2"], _TV["BG3"]
    BORDER, TEXT, TEXT2 = _TV["BORDER"], _TV["TEXT"], _TV["TEXT2"]
    BLUE   = _TV["BLUE"];   GREEN  = _TV["GREEN"];   RED    = _TV["RED"]
    ORANGE = _TV["ORANGE"]; PURPLE = _TV["PURPLE"]
    CYAN   = _TV["CYAN"];   LIME   = _TV["LIME"]

    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            paper_bgcolor=BG, plot_bgcolor=BG, font=dict(color=TEXT),
            annotations=[dict(
                text=f"No data for {symbol} {tf}",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=20, color=TEXT2),
            )],
        )
        return fig

    has_vol    = df["Volume"].notna().any() and df["Volume"].sum() > 0
    panel_keys = [p for p in panel_sel if p in ("rsi", "macd", "stoch", "atr", "obv")]

    row_names   = ["Price"]
    row_heights = [0.55]
    if has_vol and "obv" not in panel_keys:
        row_names.append("Volume"); row_heights.append(0.10)
    for pk in panel_keys:
        lbl = {"rsi": "RSI", "macd": "MACD", "stoch": "Stochastic",
               "atr": "ATR", "obv": "OBV"}[pk]
        row_names.append(lbl); row_heights.append(0.12)

    n_rows      = len(row_names)
    total       = sum(row_heights)
    row_heights = [h / total for h in row_heights]

    fig = make_subplots(rows=n_rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.02, row_heights=row_heights,
                        subplot_titles=row_names)
    x = df["x_label"]

    # ── Candlestick ──────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name=symbol,
        increasing=dict(line=dict(color=GREEN, width=1), fillcolor=GREEN),
        decreasing=dict(line=dict(color=RED,   width=1), fillcolor=RED),
    ), row=1, col=1)

    # ── Overlays ─────────────────────────────────────────────────────────────
    ci = 0

    if "sma" in overlay_sel:
        for ps in str(params.get("sma_periods", "20,50,200")).split(","):
            period = int(ps.strip())
            c = _MA_COLORS[ci % len(_MA_COLORS)]; ci += 1
            fig.add_trace(go.Scatter(
                x=x, y=calc_sma(df["Close"], period),
                mode="lines", name=f"SMA {period}",
                line=dict(color=c, width=1.2),
            ), row=1, col=1)

    if "ema" in overlay_sel:
        for ps in str(params.get("ema_periods", "12,26")).split(","):
            period = int(ps.strip())
            c = _MA_COLORS[ci % len(_MA_COLORS)]; ci += 1
            fig.add_trace(go.Scatter(
                x=x, y=calc_ema(df["Close"], period),
                mode="lines", name=f"EMA {period}",
                line=dict(color=c, width=1.2, dash="dot"),
            ), row=1, col=1)

    if "bb" in overlay_sel:
        bp = int(params.get("bb_period", 20))
        bs = float(params.get("bb_std", 2))
        mid, up, lo = calc_bollinger(df, bp, bs)
        fig.add_trace(go.Scatter(x=x, y=up, mode="lines", name="BB Upper",
            line=dict(color=PURPLE, width=1, dash="dash")), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=mid, mode="lines", name="BB Mid",
            line=dict(color=PURPLE, width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=lo, mode="lines", name="BB Lower",
            line=dict(color=PURPLE, width=1, dash="dash"),
            fill="tonexty", fillcolor="rgba(171,71,188,0.07)"), row=1, col=1)

    if "vwap" in overlay_sel and has_vol:
        fig.add_trace(go.Scatter(
            x=x, y=calc_vwap(df), mode="lines", name="VWAP",
            line=dict(color=CYAN, width=1.5, dash="dashdot"),
        ), row=1, col=1)

    # ── Volume row ───────────────────────────────────────────────────────────
    cr = 2
    if has_vol and "obv" not in panel_keys:
        bc = [GREEN if c >= o else RED for o, c in zip(df["Open"], df["Close"])]
        fig.add_trace(go.Bar(x=x, y=df["Volume"], name="Volume",
            marker_color=bc, opacity=0.6), row=cr, col=1)
        cr += 1

    # ── Panel indicators ─────────────────────────────────────────────────────
    for pk in panel_keys:
        if pk == "rsi":
            rp = int(params.get("rsi_period", 14))
            fig.add_trace(go.Scatter(x=x, y=calc_rsi(df["Close"], rp),
                mode="lines", name=f"RSI({rp})",
                line=dict(color=ORANGE, width=1.3)), row=cr, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color=RED,   line_width=0.8, row=cr, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color=GREEN, line_width=0.8, row=cr, col=1)
            fig.add_hline(y=50, line_dash="dot",  line_color=TEXT2, line_width=0.5, row=cr, col=1)
            fig.update_yaxes(range=[0, 100], row=cr, col=1)

        elif pk == "macd":
            mf = int(params.get("macd_fast",   12))
            ms = int(params.get("macd_slow",   26))
            mg = int(params.get("macd_signal",  9))
            ml, sgl, hi = calc_macd(df["Close"], mf, ms, mg)
            hc = [GREEN if v >= 0 else RED for v in hi]
            fig.add_trace(go.Bar(x=x, y=hi, name="MACD Hist",
                marker_color=hc, opacity=0.5), row=cr, col=1)
            fig.add_trace(go.Scatter(x=x, y=ml,  mode="lines", name="MACD",
                line=dict(color=BLUE,   width=1.3)), row=cr, col=1)
            fig.add_trace(go.Scatter(x=x, y=sgl, mode="lines", name="Signal",
                line=dict(color=ORANGE, width=1.3)), row=cr, col=1)
            fig.add_hline(y=0, line_dash="dot", line_color=TEXT2, line_width=0.5, row=cr, col=1)

        elif pk == "stoch":
            sk, sd = calc_stochastic(
                df,
                int(params.get("stoch_k",      14)),
                int(params.get("stoch_d",        3)),
                int(params.get("stoch_smooth",   3)),
            )
            fig.add_trace(go.Scatter(x=x, y=sk, mode="lines", name="%K",
                line=dict(color=BLUE,   width=1.3)), row=cr, col=1)
            fig.add_trace(go.Scatter(x=x, y=sd, mode="lines", name="%D",
                line=dict(color=ORANGE, width=1.3)), row=cr, col=1)
            fig.add_hline(y=80, line_dash="dash", line_color=RED,   line_width=0.8, row=cr, col=1)
            fig.add_hline(y=20, line_dash="dash", line_color=GREEN, line_width=0.8, row=cr, col=1)
            fig.update_yaxes(range=[0, 100], row=cr, col=1)

        elif pk == "atr":
            ap = int(params.get("atr_period", 14))
            fig.add_trace(go.Scatter(x=x, y=calc_atr(df, ap), mode="lines",
                name=f"ATR({ap})", line=dict(color=LIME, width=1.3)),
                row=cr, col=1)

        elif pk == "obv":
            fig.add_trace(go.Scatter(x=x, y=calc_obv(df), mode="lines",
                name="OBV", line=dict(color=CYAN, width=1.3)),
                row=cr, col=1)

        cr += 1

    # ── Layout ───────────────────────────────────────────────────────────────
    d0  = df["BarTime"].iloc[0].strftime("%Y-%m-%d")
    d1  = df["BarTime"].iloc[-1].strftime("%Y-%m-%d")
    axk = dict(gridcolor=BG2, showgrid=True, linecolor=BORDER,
               tickcolor=BORDER, tickfont=dict(color=TEXT2))
    ck  = dict(type="category", tickangle=-45, nticks=20,
               rangeslider=dict(visible=False))

    fig.update_layout(
        title=dict(
            text=f"  {symbol}  .  {tf}  .  {len(df):,} candles  .  {d0} -> {d1}",
            font=dict(size=14, color=TEXT), x=0,
        ),
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, family="Arial", size=12),
        legend=dict(bgcolor=BG3, bordercolor=BORDER, borderwidth=1, font=dict(size=10)),
        margin=dict(l=0, r=70, t=44, b=40),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=BG3, bordercolor=BORDER, font_color=TEXT),
        height=max(600, 250 + n_rows * 150),
    )
    for r in range(1, n_rows + 1):
        fig.update_xaxes(**axk, row=r, col=1)
        fig.update_yaxes(**axk, side="right", row=r, col=1)
    fig.update_xaxes(**ck, row=n_rows, col=1)
    fig.update_xaxes(**ck, row=1,      col=1)
    for ann in fig.layout.annotations:
        ann.font = dict(size=10, color=TEXT2)

    return fig


# =============================================================================
# build_signal_chart
# SAM signal chart (candlestick + MACD + ATR) — used by 09 / 10
# Expects lowercase columns: open, high, low, close, ma, macd_h, atr, x_label
# Integer RangeIndex as x-axis to prevent Plotly date-type override on shared axes
# =============================================================================

def build_signal_chart(df_plot: pd.DataFrame, signals_df: pd.DataFrame,
                       cfg: dict, sym: str, p: dict,
                       us_filtered_symbols: list = None) -> go.Figure:
    """
        Tao chart scanner SAM: gia + marker signal + MACD + ATR.

        Dau vao:
        - df_plot: du lieu da xu ly, cot lowercase (open/high/low/close...) + x_label.
        - signals_df: ket qua scan signal (co direction, rr, pass_rr, entry/sl/tp...).
        - cfg/sym/p: cau hinh hien thi va tham so scanner.

        Dau ra:
        - Figure de dashboard theo doi chat luong signal theo thoi gian.

        Tai sao lam vay:
        - Dung RangeIndex cho truc x de Plotly khong tu chuyen sang date-type,
            tu do giu canh thang hang giua candlestick, MACD va ATR.
        - Ve tach marker pass/rejected de nguoi van hanh danh gia chat luong loc signal.

        Anh huong he thong:
        - Chinh marker/shape chi anh huong trinh bay.
        - Neu sua sai mapping thoi gian (bar_time -> index), marker se dat sai nen gia.

    Parameters
    ----------
    df_plot             : processed DataFrame (RangeIndex, lowercase cols + x_label)
    signals_df          : output of scan_signals()
    cfg                 : symbol config dict (label, x, …)
    sym                 : symbol key (e.g. 'US30') — used for title note
    p                   : params dict (KTP, MIN_RR, ENTRY_LINE_BARS, …)
    us_filtered_symbols : list of symbol keys using US30 macro filter (for title)
    """
    KTP        = p['KTP']
    MIN_RR     = p['MIN_RR']
    ENTRY_BARS = p['ENTRY_LINE_BARS']
    SHOW_REJ   = p['SHOW_REJECTED']
    SHOW_MA    = p['SHOW_MA']
    SHOW_LINES = p['SHOW_ENTRY_LINES']

    BG_PAPER = _GH['BG_PAPER']; BG_PLOT = _GH['BG_PLOT']
    GRID     = _GH['GRID'];     TEXT    = _GH['TEXT'];  MUTED = _GH['MUTED']

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.60, 0.22, 0.18],
        vertical_spacing=0.03,
        subplot_titles=(
            cfg['label'],
            f'MACD ({p["MACD_FAST"]},{p["MACD_SLOW"]},{p["MACD_SIGNAL"]})',
            f'ATR ({p["ATR_PERIOD"]})',
        ),
    )

    # Integer index as x — avoids Plotly Candlestick forcing type='date'
    # which would break shared_xaxes alignment with MACD/ATR subplots.
    x = df_plot.index

    # ── Candlestick ──────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x,
        open=df_plot['open'], high=df_plot['high'],
        low=df_plot['low'],   close=df_plot['close'],
        name='Price',
        increasing_line_color='#3d9970', increasing_fillcolor='#26a65b',
        decreasing_line_color='#c0392b', decreasing_fillcolor='#e74c3c',
        line_width=0.8,
    ), row=1, col=1)

    # ── MA line ──────────────────────────────────────────────────────────────
    if SHOW_MA:
        fig.add_trace(go.Scatter(
            x=x, y=df_plot['ma'],
            name=f'MA({p["MA_PERIOD"]})',
            line=dict(color='#FFD93D', width=1.5),
            hovertemplate='MA: %{y:.2f}<extra></extra>',
        ), row=1, col=1)

    # ── Signal markers ───────────────────────────────────────────────────────
    if not signals_df.empty:
        _CATS = [
            ('BUY',  True,  '#00FF88', 'triangle-up',   14, 1.0, f'BUY  pass ≥{MIN_RR}'),
            ('SELL', True,  '#FF4444', 'triangle-down',  14, 1.0, f'SELL pass ≥{MIN_RR}'),
            ('BUY',  False, '#335533', 'triangle-up',   10, 0.5, 'BUY  rejected'),
            ('SELL', False, '#553333', 'triangle-down',  10, 0.5, 'SELL rejected'),
        ]
        time_to_pos = dict(zip(df_plot['BarTime'], df_plot.index))

        for direction, passed, color, marker_sym, size, opacity, name in _CATS:
            if not passed and not SHOW_REJ:
                continue
            sub = signals_df[
                (signals_df['direction'] == direction) &
                (signals_df['pass_rr']   == passed)
            ]
            if sub.empty:
                continue
            is_buy   = direction == 'BUY'
            marker_y = (sub['low']  - sub['atr'] * 0.3 if is_buy
                        else sub['high'] + sub['atr'] * 0.3)
            sub_x    = sub['bar_time'].map(time_to_pos)
            fig.add_trace(go.Scatter(
                x=sub_x, y=marker_y,
                mode='markers+text',
                marker=dict(symbol=marker_sym, size=size,
                            color=color, opacity=opacity),
                text=[f'R:{r:.1f}' for r in sub['rr']],
                textposition='bottom center' if is_buy else 'top center',
                textfont=dict(size=9, color=color),
                name=name,
                hovertemplate=(
                    f'<b>{direction}</b>  R:R=%{{text}}<br>'
                    'Time: %{x}<extra></extra>'
                ),
            ), row=1, col=1)

        # ── Entry / SL / TP shapes (pass signals only) ───────────────────────
        if SHOW_LINES:
            n_bars_plot = len(df_plot)
            for _, sig in signals_df[signals_df['pass_rr']].iterrows():
                bt        = sig['bar_time']
                e, sl, tp = sig['entry'], sig['sl'], sig['tp']
                start_i   = time_to_pos.get(bt)
                if start_i is None:
                    continue
                end_i = min(start_i + ENTRY_BARS, n_bars_plot - 1)

                for y_val, col, dash in [
                    (e,  'rgba(255,255,255,0.70)', 'dash'),
                    (sl, 'rgba(255, 68, 68,0.85)', 'dot'),
                    (tp, 'rgba(  0,255,136,0.85)', 'dot'),
                ]:
                    fig.add_shape(type='line',
                        x0=start_i, x1=end_i, y0=y_val, y1=y_val,
                        line=dict(color=col, width=1, dash=dash),
                        row=1, col=1)

                for y0, y1, fill_col in [
                    (min(sl, e),  max(sl, e),  'rgba(255, 68, 68,0.07)'),
                    (min(e,  tp), max(e,  tp), 'rgba(  0,255,136,0.06)'),
                ]:
                    fig.add_shape(type='rect',
                        x0=start_i, x1=end_i, y0=y0, y1=y1,
                        fillcolor=fill_col, line_width=0,
                        row=1, col=1)

    # ── MACD histogram ───────────────────────────────────────────────────────
    macd_vals   = df_plot['macd_h'].values
    macd_colors = ['#26a65b' if v >= 0 else '#e74c3c' for v in macd_vals]
    fig.add_trace(go.Bar(
        x=x, y=macd_vals, marker_color=macd_colors,
        name='MACD', showlegend=False,
        hovertemplate='MACD: %{y:.4f}<extra></extra>',
    ), row=2, col=1)

    # ── ATR area ─────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x, y=df_plot['atr'],
        fill='tozeroy', fillcolor='rgba(132,94,194,0.35)',
        line=dict(color='#845EC2', width=1),
        name='ATR', showlegend=False,
        hovertemplate='ATR: %{y:.2f}<extra></extra>',
    ), row=3, col=1)

    # ── Layout ───────────────────────────────────────────────────────────────
    n_pass  = int(signals_df['pass_rr'].sum()) if not signals_df.empty else 0
    n_rej   = len(signals_df) - n_pass
    us_note = ('  ·  US30 Macro Filter ON'
               if (us_filtered_symbols and sym in us_filtered_symbols) else '')
    title_str = (
        f'{cfg["label"]}  |  {len(df_plot)} bars H4  |  kTP={KTP}  |  '
        f'Signals: <span style="color:#00FF88"><b>{n_pass} pass</b></span>  '
        f'<span style="color:#666">{n_rej} rejected</span>{us_note}'
    )

    fig.update_layout(
        title=dict(text=title_str, font=dict(size=13, color=TEXT), x=0.005),
        height=760,
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        font=dict(color=TEXT, family='monospace', size=11),
        legend=dict(
            orientation='h', yanchor='bottom', y=1.005, x=0,
            font=dict(size=10), bgcolor='rgba(22,27,34,0.85)',
            bordercolor=GRID, borderwidth=1,
        ),
        hovermode='x unified',
        margin=dict(l=60, r=80, t=90, b=40),
    )

    _grid = dict(gridcolor=GRID, zerolinecolor=GRID,
                 color=TEXT, tickfont_color=TEXT, showgrid=True)
    n        = len(df_plot)
    step     = max(1, n // 20)
    tick_vals = list(range(0, n, step))
    tick_text = [df_plot['x_label'].iloc[i] for i in tick_vals]

    fig.update_xaxes(**_grid,
                     tickmode='array', tickvals=tick_vals, ticktext=tick_text,
                     tickangle=-45, rangeslider=dict(visible=False),
                     row=1, col=1)
    fig.update_xaxes(**_grid, row=2, col=1)
    fig.update_xaxes(**_grid, row=3, col=1)
    fig.update_yaxes(**_grid)
    for ann in fig.layout.annotations:
        ann.font.color = MUTED
        ann.font.size  = 10

    return fig


# =============================================================================
# build_reversal_chart
# Same layout as build_signal_chart but with reversal-specific markers (orange)
# =============================================================================

def build_reversal_chart(df_plot: pd.DataFrame, signals_df: pd.DataFrame,
                         cfg: dict, sym: str, p: dict,
                         us_filtered_symbols: list = None) -> go.Figure:
    """
    Tao chart cho tin hieu dao chieu (reversal).

    Giong build_signal_chart nhung bo sung:
    - Marker mau cam de nhin nhanh cac lenh dao chieu.
    - Khung highlight o cay nen dao chieu de de doi chieu context.
    - So luong reversal trong title de theo doi tan suat.

    Anh huong he thong:
    - Ham nay phuc vu quan sat va giai thich signal.
    - Khong thay doi quyet dinh vao lenh, chi thay doi cach trinh bay.
    """
    KTP        = p['KTP']
    MIN_RR     = p['MIN_RR']
    ENTRY_BARS = p['ENTRY_LINE_BARS']
    SHOW_REJ   = p['SHOW_REJECTED']
    SHOW_MA    = p['SHOW_MA']
    SHOW_LINES = p['SHOW_ENTRY_LINES']

    BG_PAPER = _GH['BG_PAPER']; BG_PLOT = _GH['BG_PLOT']
    GRID     = _GH['GRID'];     TEXT    = _GH['TEXT'];  MUTED = _GH['MUTED']

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.60, 0.22, 0.18],
        vertical_spacing=0.03,
        subplot_titles=(
            cfg['label'],
            f'MACD ({p["MACD_FAST"]},{p["MACD_SLOW"]},{p["MACD_SIGNAL"]})',
            f'ATR ({p["ATR_PERIOD"]})',
        ),
    )

    x = df_plot.index

    # ── Candlestick ──────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x,
        open=df_plot['open'], high=df_plot['high'],
        low=df_plot['low'],   close=df_plot['close'],
        name='Price',
        increasing_line_color='#3d9970', increasing_fillcolor='#26a65b',
        decreasing_line_color='#c0392b', decreasing_fillcolor='#e74c3c',
        line_width=0.8,
    ), row=1, col=1)

    # ── MA line ──────────────────────────────────────────────────────────────
    if SHOW_MA:
        fig.add_trace(go.Scatter(
            x=x, y=df_plot['ma'],
            name=f'MA({p["MA_PERIOD"]})',
            line=dict(color='#FFD93D', width=1.5),
            hovertemplate='MA: %{y:.2f}<extra></extra>',
        ), row=1, col=1)

    # ── Signal markers (with reversal support) ───────────────────────────────
    if not signals_df.empty:
        time_to_pos = dict(zip(df_plot['BarTime'], df_plot.index))
        has_reversal = 'is_reversal' in signals_df.columns

        # Categories: reversal pass, normal buy/sell pass, rejected
        _CATS = []
        if has_reversal:
            _CATS.append(('REV', True, '#FFB347', 'diamond', 16, 1.0, 'REVERSAL pass'))
        _CATS.extend([
            ('BUY',  True,  '#00FF88', 'triangle-up',   14, 1.0, f'BUY  pass ≥{MIN_RR}'),
            ('SELL', True,  '#FF4444', 'triangle-down',  14, 1.0, f'SELL pass ≥{MIN_RR}'),
            ('BUY',  False, '#335533', 'triangle-up',   10, 0.5, 'BUY  rejected'),
            ('SELL', False, '#553333', 'triangle-down',  10, 0.5, 'SELL rejected'),
        ])

        for cat_type, passed, color, marker_sym, size, opacity, name in _CATS:
            if not passed and not SHOW_REJ:
                continue

            if cat_type == 'REV':
                sub = signals_df[signals_df['is_reversal'] & signals_df['pass_rr']]
            elif has_reversal:
                sub = signals_df[
                    (signals_df['direction'] == cat_type) &
                    (signals_df['pass_rr']   == passed) &
                    (~signals_df['is_reversal'] if passed else True)
                ]
            else:
                sub = signals_df[
                    (signals_df['direction'] == cat_type) &
                    (signals_df['pass_rr']   == passed)
                ]

            if sub.empty:
                continue

            marker_y = (sub['low']  - sub['atr'] * 0.3 if cat_type != 'SELL'
                        else sub['high'] + sub['atr'] * 0.3)
            # For reversal, position based on direction
            if cat_type == 'REV':
                marker_y = sub.apply(
                    lambda r: r['low'] - r['atr'] * 0.3 if r['direction_int'] == 1
                    else r['high'] + r['atr'] * 0.3, axis=1)

            sub_x = sub['bar_time'].map(time_to_pos)
            text_list = [f'⟳R:{r:.1f}' if cat_type == 'REV' else f'R:{r:.1f}' for r in sub['rr']]
            text_pos = []
            if cat_type == 'REV':
                text_pos = ['bottom center' if d == 1 else 'top center' for d in sub['direction_int']]
            else:
                text_pos = ['bottom center' if cat_type == 'BUY' else 'top center'] * len(sub)

            fig.add_trace(go.Scatter(
                x=sub_x, y=marker_y,
                mode='markers+text',
                marker=dict(symbol=marker_sym, size=size,
                            color=color, opacity=opacity),
                text=text_list,
                textposition=text_pos,
                textfont=dict(size=9, color=color),
                name=name,
                hovertemplate=(
                    f'<b>{name}</b>  R:R=%{{text}}<br>'
                    'Time: %{x}<extra></extra>'
                ),
            ), row=1, col=1)

            # Add orange highlight rectangles for reversal bars
            if cat_type == 'REV':
                for _, sig in sub.iterrows():
                    xi = time_to_pos.get(sig['bar_time'])
                    if xi is not None:
                        fig.add_shape(type='rect',
                            x0=xi - 0.5, x1=xi + 0.5,
                            y0=sig['low'] - sig['atr'] * 0.1,
                            y1=sig['high'] + sig['atr'] * 0.1,
                            fillcolor='rgba(255,179,71,0.12)',
                            line=dict(color='#FFB347', width=0.5),
                            row=1, col=1)

        # ── Entry / SL / TP lines (pass signals only) ────────────────────────
        if SHOW_LINES:
            n_bars_plot = len(df_plot)
            for _, sig in signals_df[signals_df['pass_rr']].iterrows():
                bt        = sig['bar_time']
                e, sl, tp = sig['entry'], sig['sl'], sig['tp']
                start_i   = time_to_pos.get(bt)
                if start_i is None:
                    continue
                end_i = min(start_i + ENTRY_BARS, n_bars_plot - 1)

                is_rev = sig.get('is_reversal', False)
                entry_col = 'rgba(255,179,71,0.80)' if is_rev else 'rgba(255,255,255,0.70)'

                for y_val, col, dash in [
                    (e,  entry_col,                 'dash'),
                    (sl, 'rgba(255, 68, 68,0.85)',  'dot'),
                    (tp, 'rgba(  0,255,136,0.85)',  'dot'),
                ]:
                    fig.add_shape(type='line',
                        x0=start_i, x1=end_i, y0=y_val, y1=y_val,
                        line=dict(color=col, width=1, dash=dash),
                        row=1, col=1)

                for y0, y1, fill_col in [
                    (min(sl, e),  max(sl, e),  'rgba(255, 68, 68,0.07)'),
                    (min(e,  tp), max(e,  tp), 'rgba(  0,255,136,0.06)'),
                ]:
                    fig.add_shape(type='rect',
                        x0=start_i, x1=end_i, y0=y0, y1=y1,
                        fillcolor=fill_col, line_width=0,
                        row=1, col=1)

    # ── MACD histogram ───────────────────────────────────────────────────────
    macd_vals   = df_plot['macd_h'].values
    macd_colors = ['#26a65b' if v >= 0 else '#e74c3c' for v in macd_vals]
    fig.add_trace(go.Bar(
        x=x, y=macd_vals, marker_color=macd_colors,
        name='MACD', showlegend=False,
        hovertemplate='MACD: %{y:.4f}<extra></extra>',
    ), row=2, col=1)

    # ── ATR area ─────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x, y=df_plot['atr'],
        fill='tozeroy', fillcolor='rgba(132,94,194,0.35)',
        line=dict(color='#845EC2', width=1),
        name='ATR', showlegend=False,
        hovertemplate='ATR: %{y:.2f}<extra></extra>',
    ), row=3, col=1)

    # ── Layout ───────────────────────────────────────────────────────────────
    n_pass = int(signals_df['pass_rr'].sum()) if not signals_df.empty else 0
    n_rej  = len(signals_df) - n_pass
    n_rev  = int(signals_df['is_reversal'].sum()) if (not signals_df.empty and 'is_reversal' in signals_df.columns) else 0
    us_note = ('  ·  US30 Macro Filter ON'
               if (us_filtered_symbols and sym in us_filtered_symbols) else '')
    title_str = (
        f'{cfg["label"]}  |  {len(df_plot)} bars H4  |  kTP={KTP}  |  '
        f'Signals: <span style="color:#00FF88"><b>{n_pass} pass</b></span>  '
        f'<span style="color:#FFB347"><b>{n_rev} reversal</b></span>  '
        f'<span style="color:#666">{n_rej} rejected</span>{us_note}'
    )

    fig.update_layout(
        title=dict(text=title_str, font=dict(size=13, color=TEXT), x=0.005),
        height=760,
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        font=dict(color=TEXT, family='monospace', size=11),
        legend=dict(
            orientation='h', yanchor='bottom', y=1.005, x=0,
            font=dict(size=10), bgcolor='rgba(22,27,34,0.85)',
            bordercolor=GRID, borderwidth=1,
        ),
        hovermode='x unified',
        margin=dict(l=60, r=80, t=90, b=40),
    )

    _grid = dict(gridcolor=GRID, zerolinecolor=GRID,
                 color=TEXT, tickfont_color=TEXT, showgrid=True)
    n        = len(df_plot)
    step     = max(1, n // 20)
    tick_vals = list(range(0, n, step))
    tick_text = [df_plot['x_label'].iloc[i] for i in tick_vals]

    fig.update_xaxes(**_grid,
                     tickmode='array', tickvals=tick_vals, ticktext=tick_text,
                     tickangle=-45, rangeslider=dict(visible=False),
                     row=1, col=1)
    fig.update_xaxes(**_grid, row=2, col=1)
    fig.update_xaxes(**_grid, row=3, col=1)
    fig.update_yaxes(**_grid)
    for ann in fig.layout.annotations:
        ann.font.color = MUTED
        ann.font.size  = 10

    return fig
