# =============================================================================
# data_provider/04_chart.py  —  Interactive candlestick chart dashboard
# Version     : 3.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# File này là dashboard giám sát (Dash/Plotly), không phải engine giao dịch.
#
# Có thể chỉnh an toàn:
# - Symbol/timeframe/số bar mặc định khi mở chart
# - Chu kỳ tự động refresh
# - Các tùy chỉnh hiển thị
#
# Các thay đổi ở đây chỉ ảnh hưởng đến giao diện chart, không ảnh hưởng
# đến dữ liệu lưu trong DB hay logic thực thi chiến lược.

# Features:
#   - Offline dashboard: reads from local SQL Server (SEN05_AutoTrading)
#   - Categorical x-axis: no weekend/holiday gaps
#   - Auto-refresh every 60 seconds
#   - Flexible technical indicators (toggle on/off):
#       Overlay : SMA, EMA, Bollinger Bands, VWAP
#       Panel   : RSI, MACD, Stochastic, ATR, OBV
#   - TradingView-style dark theme
#
# Usage:
#   python data_provider/04_chart.py
#   Open: http://127.0.0.1:8050
#   Stop: Ctrl+C
# =============================================================================

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import dash
    import plotly.graph_objects as go
    from dash import ALL, MATCH, Input, Output, State, ctx, dcc, html
    from plotly.subplots import make_subplots
except ImportError:
    print("[ERROR] Missing libraries. Install with:")
    print("  pip install plotly dash pandas numpy")
    sys.exit(1)

# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
_ROOT = Path(__file__).parent.parent   # data_provider/ → project root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.chart_builder import build_full_chart
from modules.data_loader import load_candles, load_symbols, load_timeframes
from modules.db_connector import get_connection, test_connection

# =============================================================================
# Configuration  —  loaded from DB at startup (no hard-coding)
# =============================================================================
SYMBOLS    = {}   # populated after DB connection is confirmed
TIMEFRAMES = []   # populated after DB connection is confirmed

N_BARS_OPTIONS = [
    {"label": "100",   "value": 100},
    {"label": "200",   "value": 200},
    {"label": "500",   "value": 500},
    {"label": "1 000", "value": 1000},
    {"label": "2 000", "value": 2000},
]

BG     = "#131722"
BG2    = "#1e222d"
BG3    = "#2a2e39"
BORDER = "#363a45"
TEXT   = "#d1d4dc"
TEXT2  = "#787b86"
BLUE   = "#2962ff"
GREEN  = "#26a69a"
RED    = "#ef5350"
ORANGE = "#ff9800"
PURPLE = "#ab47bc"
CYAN   = "#00bcd4"
LIME   = "#8bc34a"

DEFAULT_SYMBOL = "EURUSD"
DEFAULT_TF     = "H1"
DEFAULT_BARS   = 500
REFRESH_MS     = 60_000

MA_COLORS = ["#ff9800","#2196f3","#e91e63","#4caf50","#9c27b0",
             "#00bcd4","#ffeb3b","#ff5722"]

# =============================================================================
# Technical indicators, data loading, and chart building are provided by:
#   modules/indicators.py, modules/data_loader.py, modules/chart_builder.py
# =============================================================================

# Chart building → modules/chart_builder.build_full_chart()

# =============================================================================
# UI helpers
# =============================================================================
BTN = {"border":f"1px solid {BORDER}","borderRadius":"4px","cursor":"pointer",
       "fontFamily":"Arial, sans-serif","transition":"all 0.15s"}

def sym_btn(sym, selected=False):
    return html.Button(sym, id={"type":"sym-btn","index":sym}, n_clicks=0,
        style={**BTN,"backgroundColor":BLUE if selected else BG3,
               "color":"#fff" if selected else TEXT2,
               "border":f"1px solid {BLUE if selected else BORDER}",
               "padding":"5px 9px","margin":"2px","fontSize":"12px",
               "fontWeight":"600" if selected else "400","minWidth":"76px"})

def tf_btn(tf, selected=False):
    return html.Button(tf, id={"type":"tf-btn","index":tf}, n_clicks=0,
        style={**BTN,"backgroundColor":BLUE if selected else BG3,
               "color":"#fff" if selected else TEXT2,
               "border":f"1px solid {BLUE if selected else BORDER}",
               "padding":"7px 16px","margin":"3px","fontSize":"13px",
               "fontWeight":"700" if selected else "400"})

def make_symbol_panel(selected):
    groups = []
    for gname, syms in SYMBOLS.items():
        groups.append(html.Div([
            html.Div(gname, style={"color":TEXT2,"fontSize":"10px",
                "textTransform":"uppercase","letterSpacing":"1.2px","margin":"10px 3px 4px"}),
            html.Div([sym_btn(s, s==selected) for s in syms],
                     style={"display":"flex","flexWrap":"wrap"}),
        ]))
    return groups

def make_tf_panel(selected):
    return [tf_btn(t, t==selected) for t in TIMEFRAMES]

def stitle(text):
    return html.Div(text, style={"color":TEXT2,"fontSize":"10px",
        "textTransform":"uppercase","letterSpacing":"1.2px",
        "marginBottom":"5px","marginTop":"12px"})

inp_style = {"width":"100%","backgroundColor":BG3,"color":TEXT,
    "border":f"1px solid {BORDER}","borderRadius":"3px",
    "padding":"4px 6px","fontSize":"12px","marginBottom":"4px"}
inp_sm = {"width":"33%","backgroundColor":BG3,"color":TEXT,
    "border":f"1px solid {BORDER}","borderRadius":"3px",
    "padding":"4px","fontSize":"12px"}

# =============================================================================
# Dash app
# =============================================================================
app = dash.Dash(__name__, title="AutoTrading Chart")
app.config.suppress_callback_exceptions = True

app.layout = html.Div(
    style={"backgroundColor":BG,"height":"100vh","display":"flex",
           "flexDirection":"column","fontFamily":"Arial, sans-serif","overflow":"hidden"},
    children=[
        # Header
        html.Div(style={"backgroundColor":BG2,"borderBottom":f"1px solid {BORDER}",
            "padding":"10px 20px","display":"flex","alignItems":"center","gap":"16px","flexShrink":0},
        children=[
            html.Span("\U0001f4c8 AutoTrading Chart", style={"color":TEXT,"fontSize":"16px","fontWeight":"700"}),
            html.Span("SEN05_AutoTrading", style={"color":TEXT2,"fontSize":"12px"}),
            html.Span(id="refresh-badge", style={"color":TEXT2,"fontSize":"11px",
                "backgroundColor":BG3,"padding":"2px 8px","borderRadius":"10px",
                "border":f"1px solid {BORDER}"}),
            html.Div(id="ohlc-bar", style={"marginLeft":"auto","color":TEXT2,
                "fontSize":"12px","fontFamily":"monospace"}),
        ]),
        # Body
        html.Div(style={"display":"flex","flex":1,"overflow":"hidden"}, children=[
            # Sidebar
            html.Div(style={"width":"300px","minWidth":"300px","backgroundColor":BG2,
                "borderRight":f"1px solid {BORDER}","padding":"12px 10px",
                "overflowY":"auto","flexShrink":0},
            children=[
                stitle("Timeframe"),
                html.Div(id="tf-panel", children=make_tf_panel(DEFAULT_TF),
                    style={"display":"flex","flexWrap":"wrap","marginBottom":"10px"}),
                stitle("Candles"),
                dcc.Dropdown(id="bars-dd",options=N_BARS_OPTIONS,value=DEFAULT_BARS,
                    clearable=False,style={"marginBottom":"10px","fontSize":"13px"}),

                stitle("Overlay Indicators"),
                dcc.Checklist(id="overlay-chk",
                    options=[{"label":"SMA","value":"sma"},{"label":"EMA","value":"ema"},
                             {"label":"Bollinger Bands","value":"bb"},{"label":"VWAP","value":"vwap"}],
                    value=[], inline=False, style={"fontSize":"12px"},
                    labelStyle={"display":"flex","alignItems":"center","gap":"6px",
                        "color":TEXT,"padding":"3px 0","cursor":"pointer"},
                    inputStyle={"accentColor":BLUE}),

                html.Div(id="sma-params",style={"display":"none"},children=[
                    html.Label("SMA periods:",style={"color":TEXT2,"fontSize":"11px"}),
                    dcc.Input(id="sma-periods",type="text",value="20,50,200",debounce=True,style=inp_style)]),
                html.Div(id="ema-params",style={"display":"none"},children=[
                    html.Label("EMA periods:",style={"color":TEXT2,"fontSize":"11px"}),
                    dcc.Input(id="ema-periods",type="text",value="12,26",debounce=True,style=inp_style)]),
                html.Div(id="bb-params",style={"display":"none"},children=[
                    html.Label("BB period / std:",style={"color":TEXT2,"fontSize":"11px"}),
                    html.Div(style={"display":"flex","gap":"6px"},children=[
                        dcc.Input(id="bb-period",type="number",value=20,min=2,debounce=True,
                            style={**inp_sm,"width":"50%"}),
                        dcc.Input(id="bb-std",type="number",value=2,min=0.5,step=0.5,debounce=True,
                            style={**inp_sm,"width":"50%"})])]),

                stitle("Panel Indicators"),
                dcc.Checklist(id="panel-chk",
                    options=[{"label":"RSI","value":"rsi"},{"label":"MACD","value":"macd"},
                             {"label":"Stochastic","value":"stoch"},{"label":"ATR","value":"atr"},
                             {"label":"OBV","value":"obv"}],
                    value=[], inline=False, style={"fontSize":"12px"},
                    labelStyle={"display":"flex","alignItems":"center","gap":"6px",
                        "color":TEXT,"padding":"3px 0","cursor":"pointer"},
                    inputStyle={"accentColor":BLUE}),

                html.Div(id="rsi-params",style={"display":"none"},children=[
                    html.Label("RSI period:",style={"color":TEXT2,"fontSize":"11px"}),
                    dcc.Input(id="rsi-period",type="number",value=14,min=2,debounce=True,style=inp_style)]),
                html.Div(id="macd-params",style={"display":"none"},children=[
                    html.Label("MACD fast / slow / signal:",style={"color":TEXT2,"fontSize":"11px"}),
                    html.Div(style={"display":"flex","gap":"4px"},children=[
                        dcc.Input(id="macd-fast",type="number",value=12,min=1,debounce=True,style=inp_sm),
                        dcc.Input(id="macd-slow",type="number",value=26,min=1,debounce=True,style=inp_sm),
                        dcc.Input(id="macd-signal",type="number",value=9,min=1,debounce=True,style=inp_sm)])]),
                html.Div(id="stoch-params",style={"display":"none"},children=[
                    html.Label("Stoch K / D / smooth:",style={"color":TEXT2,"fontSize":"11px"}),
                    html.Div(style={"display":"flex","gap":"4px"},children=[
                        dcc.Input(id="stoch-k",type="number",value=14,min=1,debounce=True,style=inp_sm),
                        dcc.Input(id="stoch-d",type="number",value=3,min=1,debounce=True,style=inp_sm),
                        dcc.Input(id="stoch-smooth",type="number",value=3,min=1,debounce=True,style=inp_sm)])]),
                html.Div(id="atr-params",style={"display":"none"},children=[
                    html.Label("ATR period:",style={"color":TEXT2,"fontSize":"11px"}),
                    dcc.Input(id="atr-period",type="number",value=14,min=2,debounce=True,style=inp_style)]),

                html.Hr(style={"borderColor":BORDER,"margin":"12px 0"}),
                stitle("Symbol"),
                html.Div(id="sym-panel",children=make_symbol_panel(DEFAULT_SYMBOL)),
            ]),
            # Chart
            html.Div(style={"flex":1,"display":"flex","flexDirection":"column","overflow":"hidden"},
            children=[
                dcc.Loading(type="circle",color=BLUE,children=[
                    dcc.Graph(id="chart",style={"flex":1,"height":"100%"},
                        config={"scrollZoom":True,"displayModeBar":True,
                            "modeBarButtonsToRemove":["lasso2d","select2d","autoScale2d"],
                            "displaylogo":False})]),
            ]),
        ]),
        dcc.Store(id="store-sym",data=DEFAULT_SYMBOL),
        dcc.Store(id="store-tf", data=DEFAULT_TF),
        dcc.Interval(id="auto-refresh",interval=REFRESH_MS,n_intervals=0),
    ])

# =============================================================================
# Callbacks
# =============================================================================
@app.callback(Output("store-sym","data"),
    Input({"type":"sym-btn","index":ALL},"n_clicks"), prevent_initial_call=True)
def on_symbol_click(clicks):
    if not ctx.triggered or not any(clicks): return dash.no_update
    return json.loads(ctx.triggered[0]["prop_id"].split(".")[0])["index"]

@app.callback(Output("store-tf","data"),
    Input({"type":"tf-btn","index":ALL},"n_clicks"), prevent_initial_call=True)
def on_tf_click(clicks):
    if not ctx.triggered or not any(clicks): return dash.no_update
    return json.loads(ctx.triggered[0]["prop_id"].split(".")[0])["index"]

@app.callback(Output("sym-panel","children"), Input("store-sym","data"))
def update_sym_panel(sym): return make_symbol_panel(sym)

@app.callback(Output("tf-panel","children"), Input("store-tf","data"))
def update_tf_panel(tf): return make_tf_panel(tf)

@app.callback(
    Output("sma-params","style"), Output("ema-params","style"), Output("bb-params","style"),
    Input("overlay-chk","value"))
def toggle_overlay_params(sel):
    sel = sel or []
    show = {"display":"block","marginTop":"4px","marginBottom":"6px"}
    hide = {"display":"none"}
    return (show if "sma" in sel else hide,
            show if "ema" in sel else hide,
            show if "bb"  in sel else hide)

@app.callback(
    Output("rsi-params","style"), Output("macd-params","style"),
    Output("stoch-params","style"), Output("atr-params","style"),
    Input("panel-chk","value"))
def toggle_panel_params(sel):
    sel = sel or []
    show = {"display":"block","marginTop":"4px","marginBottom":"6px"}
    hide = {"display":"none"}
    return (show if "rsi"   in sel else hide, show if "macd"  in sel else hide,
            show if "stoch" in sel else hide, show if "atr"   in sel else hide)

@app.callback(
    Output("chart","figure"), Output("ohlc-bar","children"),
    Input("store-sym","data"),    Input("store-tf","data"),
    Input("bars-dd","value"),     Input("overlay-chk","value"),
    Input("panel-chk","value"),   Input("sma-periods","value"),
    Input("ema-periods","value"), Input("bb-period","value"),
    Input("bb-std","value"),      Input("rsi-period","value"),
    Input("macd-fast","value"),   Input("macd-slow","value"),
    Input("macd-signal","value"), Input("stoch-k","value"),
    Input("stoch-d","value"),     Input("stoch-smooth","value"),
    Input("atr-period","value"),  Input("auto-refresh","n_intervals"))
def update_chart(symbol, tf, n_bars, overlay_sel, panel_sel,
                 sma_per, ema_per, bb_per, bb_s,
                 rsi_per, macd_f, macd_sl, macd_sig,
                 stoch_k, stoch_d, stoch_sm, atr_per, _n):
    overlay_sel = overlay_sel or []; panel_sel = panel_sel or []
    params = {"sma_periods":sma_per or "20,50,200","ema_periods":ema_per or "12,26",
        "bb_period":bb_per or 20,"bb_std":bb_s or 2,"rsi_period":rsi_per or 14,
        "macd_fast":macd_f or 12,"macd_slow":macd_sl or 26,"macd_signal":macd_sig or 9,
        "stoch_k":stoch_k or 14,"stoch_d":stoch_d or 3,"stoch_smooth":stoch_sm or 3,
        "atr_period":atr_per or 14}
    df  = load_candles(symbol, tf, n_bars or DEFAULT_BARS)
    fig = build_full_chart(df, symbol, tf, overlay_sel, panel_sel, params)
    if df.empty:
        bar = html.Span(f"No data for {symbol} {tf}", style={"color":RED})
    else:
        last = df.iloc[-1]
        prev = df.iloc[-2]["Close"] if len(df)>1 else last["Close"]
        chg  = last["Close"] - prev
        pct  = (chg/prev*100) if prev else 0
        sign = "+" if chg>=0 else ""
        col  = GREEN if chg>=0 else RED
        bar  = html.Span([
            html.Span(f"O {last['Open']:.5f}  ",  style={"color":TEXT2}),
            html.Span(f"H {last['High']:.5f}  ",  style={"color":GREEN}),
            html.Span(f"L {last['Low']:.5f}  ",   style={"color":RED}),
            html.Span(f"C {last['Close']:.5f}  ", style={"color":TEXT}),
            html.Span(f"{sign}{chg:.5f} ({sign}{pct:.2f}%)",
                style={"color":col,"fontWeight":"600"})])
    return fig, bar

@app.callback(Output("refresh-badge","children"), Input("auto-refresh","n_intervals"))
def update_refresh_badge(n):
    return f"~  {datetime.now().strftime('%H:%M:%S')}"

# =============================================================================
# Entry point
# =============================================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  AUTO TRADING - CHART DASHBOARD  (v3.0)")
    print(f"  Auto-refresh : every {REFRESH_MS//1000}s")
    print("  Indicators   : SMA, EMA, BB, VWAP, RSI, MACD, Stoch, ATR, OBV")
    print("  Gap fix      : categorical x-axis (no weekend gaps)")
    print("="*60)
    print("\n[Checking SQL Server connection...]")
    if not test_connection():
        sys.exit(1)

    # Load symbol list and timeframes dynamically from DB
    SYMBOLS.update(load_symbols())
    TIMEFRAMES.extend(load_timeframes())

    all_syms = [s for syms in SYMBOLS.values() for s in syms]
    print(f"  Symbols    : {len(all_syms)} loaded from Dim_Symbol")
    print(f"  Timeframes : {TIMEFRAMES}")

    print("\nReady!")
    print("  -> Open browser: http://127.0.0.1:8050")
    print("  -> Stop server : Ctrl+C\n")
    app.run(debug=False, host="127.0.0.1", port=8050)
