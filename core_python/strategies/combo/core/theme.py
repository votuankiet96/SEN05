# =============================================================================
# strategy/theme.py  —  Shared visual theme for Combo v2
# Used by: 09_scanner_research.ipynb, 10_signal_dashboard.py,
#          07_backtest.ipynb, 08_wf_optimizer.ipynb
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Chỉ là giao diện hiển thị (màu sắc, định dạng số, kiểu bảng).
#
# Có thể chỉnh an toàn:
# - Bảng màu (color palette)
# - Hàm hỗ trợ style
# - Định dạng số hiển thị
#
# File này không ảnh hưởng đến logic chiến lược hay hành vi ghi dữ liệu DB.

"""
Kho cấu hình giao diện dùng chung cho dashboard/notebook.

Mục tiêu quản trị:
- Màu sắc, format số, style bảng, style chart nằm cùng một nơi.
- Khi muốn đổi giao diện, sửa tại file này để toàn hệ thống đồng bộ.

Lưu ý:
- File này không đổi logic vào lệnh hay kết quả backtest.
"""

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLOUR PALETTES
# ─────────────────────────────────────────────────────────────────────────────

# Màu tín hiệu và nền bảng tín hiệu
SIGNAL = {
    'buy':           '#6BCB77',
    'sell':          '#FF6B6B',
    'tp':            '#00FF88',
    'sl':            '#FF4444',
    'bg_buy':        '#1a3a1a',
    'bg_sell':       '#3a1a1a',
    'bg_tp':         '#0d2b1a',
    'bg_sl':         '#2b0d0d',
    'bg_rejected':   '#2a2a2a',
    'text_rejected': '#666',
    'ma_line':       '#FFD93D',
    'atr_fill':      'rgba(132,94,194,0.35)',
    'atr_line':      '#845EC2',
}

# Theme tối cho chart/bảng (Plotly, Streamlit, Matplotlib)
DARK = {
    'bg':     '#0D1117',
    'panel':  '#161B22',
    'border': '#30363D',
    'text':   '#E6EDF3',
    'muted':  '#8B949E',
}

# Màu đường equity theo symbol
EQUITY_COLORS = [
    '#00D4FF', '#FF6B6B', '#FFD93D', '#6BCB77',
    '#845EC2', '#FF9671', '#4D8076',
]

# CSS card metric trên Streamlit
METRIC_CSS = """
<style>
[data-testid="stMetric"] {
    background-color: #161B22;
    border: 1px solid #30363D;
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="stMetricLabel"] { color: #8B949E !important; font-size: 13px; }
[data-testid="stMetricValue"] { color: #E6EDF3 !important; font-size: 22px; font-weight: 700; }
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# 2. FORMAT SỐ (dùng cho pd.Styler.format)
# ─────────────────────────────────────────────────────────────────────────────

NUM_FMT = {
    'entry': '{:.2f}', 'sl': '{:.2f}', 'tp': '{:.2f}',
    'rr': '{:.2f}', 'atr': '{:.2f}',
    'sl_dist': '{:.2f}', 'tp_dist': '{:.2f}',
    'ma': '{:.2f}', 'macd_h': '{:.6f}',
}

METRICS_FMT = {
    'WR%': '{:.1f}', 'PF': '{:.2f}', 'PnL $': '{:,.0f}',
    'Return%': '{:+.1f}', 'MaxDD%': '{:.1f}', 'Sharpe': '{:.2f}',
    'Avg R': '{:.3f}', 'W/L': '{:.2f}',
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. DATAFRAME STYLE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def style_signal_row(row) -> list[str]:
    """Tô màu một dòng trong bảng tín hiệu theo outcome/direction/pass_rr."""
    outcome   = row.get('outcome', '')
    direction = row.get('direction', '')
    pass_rr   = row.get('pass_rr', False)

    if outcome == 'TP':
        c = f'background-color:{SIGNAL["bg_tp"]};color:{SIGNAL["tp"]}'
    elif outcome == 'SL':
        c = f'background-color:{SIGNAL["bg_sl"]};color:{SIGNAL["sl"]}'
    elif direction == 'BUY' and pass_rr:
        c = f'background-color:{SIGNAL["bg_buy"]};color:{SIGNAL["buy"]}'
    elif direction == 'SELL' and pass_rr:
        c = f'background-color:{SIGNAL["bg_sell"]};color:{SIGNAL["sell"]}'
    else:
        c = f'background-color:{SIGNAL["bg_rejected"]};color:{SIGNAL["text_rejected"]}'
    return [c] * len(row)


def style_reversal_row(row) -> list[str]:
    """Tô màu bảng reversal, có thêm màu riêng cho trạng thái Reversed."""
    outcome   = row.get('outcome', '')
    direction = row.get('direction', '')
    pass_rr   = row.get('pass_rr', False)

    if outcome == 'TP':
        c = f'background-color:{SIGNAL["bg_tp"]};color:{SIGNAL["tp"]}'
    elif outcome == 'SL':
        c = f'background-color:{SIGNAL["bg_sl"]};color:{SIGNAL["sl"]}'
    elif outcome == 'Reversed':
        c = 'background-color:#3a2800;color:#FFB347'
    elif direction == 'BUY' and pass_rr:
        c = f'background-color:{SIGNAL["bg_buy"]};color:{SIGNAL["buy"]}'
    elif direction == 'SELL' and pass_rr:
        c = f'background-color:{SIGNAL["bg_sell"]};color:{SIGNAL["sell"]}'
    else:
        c = f'background-color:{SIGNAL["bg_rejected"]};color:{SIGNAL["text_rejected"]}'
    return [c] * len(row)


def style_combined_row(row) -> list[str]:
    """Tô màu bảng tổng hợp nhiều symbol theo hướng BUY/SELL."""
    if row.get('direction') == 'BUY':
        c = f'background-color:{SIGNAL["bg_buy"]};color:{SIGNAL["buy"]}'
    elif row.get('direction') == 'SELL':
        c = f'background-color:{SIGNAL["bg_sell"]};color:{SIGNAL["sell"]}'
    else:
        c = ''
    return [c] * len(row)


def dark_table_props() -> dict:
    """Trả về thuộc tính nền/chữ/viền cho bảng dark mode."""
    return {
        'background-color': DARK['panel'],
        'color': DARK['text'],
        'border': f'1px solid {DARK["border"]}',
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. HÀM HỖ TRỢ MATPLOTLIB
# ─────────────────────────────────────────────────────────────────────────────

def setup_dark_axes(ax) -> None:
    """Áp dark theme cho một trục Matplotlib."""
    ax.set_facecolor(DARK['panel'])
    ax.tick_params(colors=DARK['text'], labelsize=8)
    ax.spines[:].set_color(DARK['border'])


def setup_dark_figure(nrows=1, ncols=1, figsize=(16, 6), **subplot_kw):
    """Tạo figure + axes đã gắn dark theme sẵn để dùng ngay."""
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize,
                             facecolor=DARK['bg'], **subplot_kw)
    if hasattr(axes, '__iter__'):
        for ax in (axes.flat if hasattr(axes, 'flat') else axes):
            setup_dark_axes(ax)
    else:
        setup_dark_axes(axes)
    return fig, axes


def color_pf(v) -> str:
    """Map màu cho ô Profit Factor: tốt/trung tính/xấu theo ngưỡng PF."""
    if isinstance(v, (int, float)):
        if v >= 1.5:
            return f'color:{SIGNAL["buy"]};font-weight:bold'
        if v >= 1.0:
            return f'color:{SIGNAL["ma_line"]}'
        return f'color:{SIGNAL["sell"]}'
    return ''
