# ─────────────────────────────────────────────────────────────────────────────
# signal_dashboard.py  —  Combo Signal Scanner  |  Streamlit + Plotly
# Run :  streamlit run strategies/combo/deploy/signal_dashboard.py
# ─────────────────────────────────────────────────────────────────────────────
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Đây là giao diện tương tác để scan và xem xét tín hiệu.
#
# Có thể chỉnh an toàn:
# - Giá trị mặc định trên sidebar
# - Nhãn hiển thị và các tùy chọn hiển thị
# - Trình bày chart/table
#
# Kết quả chiến lược hiển thị ở đây đến từ scan_pipeline + reversal_scanner.
# Thay đổi UI không ảnh hưởng dữ liệu DB hay logic backtest.

import sys
import warnings

warnings.filterwarnings('ignore')
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# Bootstrap: thêm project root vào path (harmless khi đã pip install -e .)
_ROOT = Path(__file__).resolve().parents[3]   # deploy/ → combo/ → strategies/ → root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.chart_builder import build_reversal_chart
from strategies.combo.core.scan_pipeline import calc_reversal_stats, run_multi_reversal_scan
from strategies.combo.core.theme import METRIC_CSS, NUM_FMT, style_combined_row, style_reversal_row

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title='SAM Signal Scanner',
    page_icon='📡',
    layout='wide',
    initial_sidebar_state='expanded',
)

st.markdown(METRIC_CSS, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL CONFIG
# ─────────────────────────────────────────────────────────────────────────────
from strategies.combo.core.strategy_config import (
    STRATEGY,
    get_indicator_params,
)
from strategies.combo.core.strategy_config import (
    SYMBOLS as TARGET_SYMBOLS,
)
from strategies.combo.core.strategy_config import (
    US_FILTERED as US_FILTERED_SYMBOLS,
)

_DEFAULT_P = get_indicator_params()   # default params từ strategy_config

# ─────────────────────────────────────────────────────────────────────────────
# DATA + CHART  (scan_pipeline handles caching internally via modules/data_loader)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('## 📡 SAM Dashboard')
    st.caption(f"{STRATEGY['name']} {STRATEGY['version']}  |  kTP={STRATEGY['ktp']}  |  Min R:R={STRATEGY['min_rr']}")
    st.markdown('---')

    st.markdown('#### Symbols')
    selected_syms = st.multiselect(
        label='Symbols to scan',
        options=list(TARGET_SYMBOLS.keys()),
        default=list(TARGET_SYMBOLS.keys()),
    )

    st.markdown('#### Scan window')
    n_bars = st.slider('N Bars (H4)', min_value=50, max_value=500,
                       value=100, step=10)

    st.markdown('#### Indicators')
    c1, c2 = st.columns(2)
    with c1:
        ma_period   = st.number_input('MA',         min_value=5,  max_value=200, value=_DEFAULT_P["MA_PERIOD"], step=1)
        atr_period  = st.number_input('ATR',        min_value=2,  max_value=30,  value=_DEFAULT_P["ATR_PERIOD"], step=1)
        macd_fast   = st.number_input('MACD Fast',  min_value=2,  max_value=50,  value=_DEFAULT_P["MACD_FAST"], step=1)
    with c2:
        macd_slow   = st.number_input('MACD Slow',  min_value=5,  max_value=200, value=_DEFAULT_P["MACD_SLOW"], step=1)
        macd_sig    = st.number_input('MACD Sig',   min_value=2,  max_value=30,  value=_DEFAULT_P["MACD_SIGNAL"], step=1)

    st.markdown('#### Strategy')
    ktp    = st.number_input('kTP (TP multiplier)', min_value=0.5, max_value=10.0, value=_DEFAULT_P["KTP"], step=0.1, format='%.1f')
    min_rr = st.number_input('Min R:R',             min_value=0.5, max_value=5.0,  value=_DEFAULT_P["MIN_RR"], step=0.1, format='%.1f')

    st.markdown('#### Chart options')
    entry_bars    = st.slider('Entry/SL/TP line width (bars)', 1, 30, 8)
    show_rejected = st.checkbox('Show rejected signals', value=True)
    show_ma       = st.checkbox('Show MA line',          value=True)
    show_lines    = st.checkbox('Show Entry/SL/TP lines', value=True)

    st.markdown('---')
    run_btn = st.button('🔄  Run Scan', type='primary', width='stretch')

# Bundle all params into one dict
P = {
    'MA_PERIOD':    int(ma_period),
    'ATR_PERIOD':   int(atr_period),
    'MACD_FAST':    int(macd_fast),
    'MACD_SLOW':    int(macd_slow),
    'MACD_SIGNAL':  int(macd_sig),
    'KTP':          float(ktp),
    'MIN_RR':       float(min_rr),
    'ENTRY_LINE_BARS': int(entry_bars),
    'SHOW_REJECTED':   show_rejected,
    'SHOW_MA':         show_ma,
    'SHOW_ENTRY_LINES': show_lines,
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────────────────────
st.markdown('# 📡 SAM — Signal Scanner Dashboard')

# ── Session state init ────────────────────────────────────────────────────────
if 'results'    not in st.session_state:
    st.session_state.results    = None
if 'scan_n_bars' not in st.session_state:
    st.session_state.scan_n_bars = None

# ── Run scan ──────────────────────────────────────────────────────────────────
if run_btn:
    if not selected_syms:
        st.warning('Vui lòng chọn ít nhất 1 symbol.')
        st.stop()

    prog_bar = st.progress(0, text='Đang load dữ liệu...')
    try:
        def _progress(i, total, sym):
            prog_bar.progress((i + 1) / total,
                              text=f'Scanning {sym}  ({i+1}/{total})...')

        results = run_multi_reversal_scan(selected_syms, n_bars, P, progress_cb=_progress)

        st.session_state.results     = results
        st.session_state.scan_n_bars = n_bars
        prog_bar.empty()

    except Exception as exc:
        prog_bar.empty()
        st.error(f'Lỗi khi scan: {exc}')
        st.stop()

# ── Guard: no results yet ─────────────────────────────────────────────────────
if st.session_state.results is None:
    st.info('Chọn symbols và params ở sidebar, rồi nhấn **🔄 Run Scan**.')
    st.stop()

results  = st.session_state.results
scan_nb  = st.session_state.scan_n_bars

# ─────────────────────────────────────────────────────────────────────────────
# METRICS ROW
# ─────────────────────────────────────────────────────────────────────────────
st.subheader('Summary')
metric_cols = st.columns(len(results))
for idx, (sym, res) in enumerate(results.items()):
    stats = calc_reversal_stats(res['signals_df'])
    with metric_cols[idx]:
        win_str = f'  TP {stats["win_pct"]:.0f}%' if stats['n_tp'] + stats['n_sl'] > 0 else ''
        st.metric(
            label=sym,
            value=f'{stats["n_pass"]} / {stats["n_total"]}',
            delta=f'avg R:R {stats["avg_rr"]:.2f}{win_str}' if stats['n_pass'] > 0 else '—',
            delta_color='normal' if stats['n_pass'] > 0 else 'off',
            help=f'{res["cfg"]["label"]}  |  {scan_nb} bars H4  |  TP:{stats["n_tp"]}  SL:{stats["n_sl"]}',
        )

st.markdown('---')

# ─────────────────────────────────────────────────────────────────────────────
# TABS:  All Signals  +  per-symbol charts
# ─────────────────────────────────────────────────────────────────────────────
tab_labels = ['📋 All Signals'] + [f'📈 {sym}' for sym in results]
tabs       = st.tabs(tab_labels)

# ── Tab 0: Combined signal table ─────────────────────────────────────────────
with tabs[0]:
    pass_rows = []
    for sym, res in results.items():
        sub = res['signals_df']
        if not sub.empty:
            p_sub = sub[sub['pass_rr']].copy()
            if not p_sub.empty:
                p_sub.insert(0, 'symbol', sym)
                pass_rows.append(p_sub)

    if pass_rows:
        combined  = pd.concat(pass_rows, ignore_index=True)
        combined  = combined.sort_values('bar_time', ascending=False)

        st.markdown(
            f'**{len(combined)} tín hiệu pass** trên {len(results)} symbols  '
            f'  |  Min R:R ≥ {P["MIN_RR"]}  |  {scan_nb} bars H4'
        )

        show_cols = ['symbol', 'bar_time', 'direction', 'outcome',
                     'entry', 'sl', 'tp', 'rr', 'atr', 'sl_dist', 'tp_dist']
        tbl = combined[show_cols].copy()
        tbl['bar_time'] = tbl['bar_time'].dt.strftime('%Y-%m-%d %H:%M')

        st.dataframe(
            tbl.style
               .apply(style_combined_row, axis=1)
               .format(NUM_FMT),
            width='stretch',
            height=460,
        )

        csv_bytes = combined[show_cols].to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            label='⬇️  Download CSV',
            data=csv_bytes,
            file_name=f'SAM_signals_{pd.Timestamp.now().strftime("%Y%m%d_%H%M")}.csv',
            mime='text/csv',
        )
    else:
        st.info(f'Không có tín hiệu pass R:R ≥ {P["MIN_RR"]} trong {scan_nb} bars gần nhất.')

# ── Tabs 1..N: per-symbol charts ─────────────────────────────────────────────
for tab_idx, (sym, res) in enumerate(results.items(), start=1):
    with tabs[tab_idx]:
        sdf   = res['signals_df']
        stats = calc_reversal_stats(sdf)

        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.caption(
                f'Tổng: **{stats["n_total"]}**  |  Pass: **{stats["n_pass"]}**  |  '
                f'Rejected: **{stats["n_rejected"]}**  |  '
                f'Avg R:R (pass): **{stats["avg_rr"]:.2f}**'
                if stats['n_pass'] > 0 else
                f'Tổng: **{stats["n_total"]}**  |  Pass: **0**  |  Không có tín hiệu đạt R:R'
            )

        # Plotly chart
        fig = build_reversal_chart(res['df_scan'], sdf, res['cfg'], sym, P, US_FILTERED_SYMBOLS)
        st.plotly_chart(fig, width='stretch')

        # Signal detail table in expander
        if not sdf.empty:
            with st.expander('📄 Chi tiết tín hiệu', expanded=False):
                detail_cols = ['bar_time', 'direction', 'outcome', 'entry', 'sl', 'tp',
                               'rr', 'pass_rr', 'atr', 'sl_dist', 'tp_dist',
                               'ma', 'macd_h']
                dtbl = sdf[detail_cols].copy()
                dtbl['bar_time'] = dtbl['bar_time'].dt.strftime('%Y-%m-%d %H:%M')
                st.dataframe(
                    dtbl.style
                        .apply(style_reversal_row, axis=1)
                        .format(NUM_FMT),
                    width='stretch',
                    height=350,
                )
        else:
            st.info('Không có tín hiệu nào trong khoảng này.')

# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    '<br><center style="color:#444">SAM Signal Scanner  ·  Combo v2  ·  '
    'Data cached 5 min</center>',
    unsafe_allow_html=True,
)
