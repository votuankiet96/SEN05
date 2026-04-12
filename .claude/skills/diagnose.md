# Skill: /diagnose

Chẩn đoán lỗi khi script hoặc hệ thống gặp vấn đề. Giải thích nguyên nhân bằng tiếng Việt đơn giản và hướng dẫn cách sửa.

## Khi nào dùng

Khi chạy script bị lỗi, dashboard không hoạt động, hoặc dữ liệu không cập nhật.

## Bước đầu tiên: Hỏi user vấn đề gì

Dùng AskUserQuestion hỏi user:

"Bạn đang gặp vấn đề gì?"
- Dữ liệu không cập nhật
- Backtest bị lỗi
- Dashboard không chạy
- Lỗi khác (paste error message)

Hoặc nếu user đã paste error message → phân tích ngay.

---

## Nhánh 1: Dữ liệu không cập nhật

### Bước 1.1: Kiểm tra kết nối DB

```python
import sys
sys.path.insert(0, '/d/Project/SEN05')
from modules.db_connector import get_connection

try:
    conn = get_connection()
    conn.execute("SELECT 1")
    print("✅ DB OK")
    conn.close()
except Exception as e:
    print(f"❌ DB lỗi: {e}")
```

Nếu DB lỗi → kiểm tra:
- SQL Server service có đang chạy không?
- File `.env` có tồn tại và có đúng `SQL_PWD` không?
- Firewall có chặn port 1433 không?

### Bước 1.2: Kiểm tra dữ liệu mới nhất

```sql
SELECT TOP 10
    s.SymbolCode, t.TFCode,
    MAX(f.BarTime) AS LatestBar,
    DATEDIFF(HOUR, MAX(f.BarTime), GETUTCDATE()) AS HoursAgo
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
GROUP BY s.SymbolCode, t.TFCode
ORDER BY HoursAgo DESC
```

Nếu data quá cũ → pipeline không chạy hoặc TV auth hết hạn.

### Bước 1.3: Kiểm tra log files

Đọc log files gần nhất:
- Kiểm tra `logs/` directory ở project root
- Tìm các dòng ERROR hoặc WARNING gần nhất
- Tìm keyword: `ConnectionError`, `AuthError`, `timeout`, `expired`, `401`, `403`

### Bước 1.4: Kiểm tra TradingView credentials

```python
import sys
sys.path.insert(0, '/d/Project/SEN05')
import config

# Check TV auth
if not config.TV_AUTH_TOKEN or config.TV_AUTH_TOKEN.strip() == '':
    print("❌ TV_AUTH_TOKEN trống — cần cập nhật trong .env")
else:
    print(f"✅ TV_AUTH_TOKEN có giá trị (length={len(config.TV_AUTH_TOKEN)})")
```

Nếu token trống hoặc hết hạn → hướng dẫn:
1. Mở TradingView trên trình duyệt, đăng nhập
2. Nhấn F12 → Application → Cookies → tìm `sessionid` hoặc `auth_token`
3. Copy giá trị, dán vào file `.env`: `TV_AUTH_TOKEN=<giá_trị_mới>`

---

## Nhánh 2: Backtest bị lỗi

### Bước 2.1: Phân tích error message

Nếu user paste traceback → phân tích:
- Xác định file và line number gây lỗi
- Xác định loại exception (ImportError, KeyError, ValueError, etc.)
- Giải thích nguyên nhân bằng tiếng Việt

### Bước 2.2: Kiểm tra imports

```python
import sys
sys.path.insert(0, '/d/Project/SEN05')

errors = []
try:
    import config
except Exception as e:
    errors.append(f"config: {e}")

try:
    from modules import db_connector, data_loader, indicators
except Exception as e:
    errors.append(f"modules: {e}")

try:
    from core_python.strategies.combo.core import execution, metrics, backtest_engine
except Exception as e:
    errors.append(f"strategy core: {e}")

if errors:
    print("❌ Import lỗi:")
    for e in errors:
        print(f"  - {e}")
else:
    print("✅ Tất cả modules import OK")
```

Nếu import lỗi → kiểm tra:
- Đã chạy `pip install -e .` chưa?
- Dependencies trong `requirements.txt` đã install chưa?

### Bước 2.3: Kiểm tra data có tồn tại

```sql
SELECT t.TFCode, COUNT(*) AS BarCount
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
WHERE s.SymbolCode = '<SYMBOL_ĐANG_TEST>'
GROUP BY t.TFCode
ORDER BY t.TFCode
```

Nếu không có data → cần chạy pipeline trước: `python data_provider/01_data_pipeline.py --mode full`

### Bước 2.4: Kiểm tra NaN/Inf trong data

Nếu lỗi liên quan đến NaN/infinity → kiểm tra data quality:

```python
import sys
sys.path.insert(0, '/d/Project/SEN05')
from modules.data_loader import load_ohlcv

df = load_ohlcv('<SYMBOL>', '<TIMEFRAME>')
print(f"NaN count: {df.isna().sum().sum()}")
print(f"Inf count: {(df == float('inf')).sum().sum()}")
print(f"Total rows: {len(df)}")
```

---

## Nhánh 3: Dashboard không chạy

### Bước 3.1: Kiểm tra Streamlit installed

```bash
python -m streamlit --version
```

Nếu không có → `pip install streamlit`

### Bước 3.2: Kiểm tra DB connection

Giống Bước 1.1 ở Nhánh 1.

### Bước 3.3: Kiểm tra port conflict

```bash
# Windows
netstat -ano | findstr :8501
```

Nếu port đã dùng → thử port khác: `streamlit run ... --server.port 8502`

### Bước 3.4: Thử chạy trực tiếp

```bash
cd /d/Project/SEN05
streamlit run core_python/strategies/combo/deploy/signal_dashboard.py
```

Xem error output và phân tích.

---

## Nhánh 4: Lỗi khác (user paste error message)

### Bước 4.1: Phân tích traceback

Đọc error message từ dưới lên:
1. **Dòng cuối cùng**: Loại exception + message
2. **Dòng trước đó**: File, line number, function gây lỗi
3. **Các dòng trên**: Call stack (thứ tự gọi hàm)

### Bước 4.2: Xác định nguyên nhân

Các lỗi thường gặp:
- `ModuleNotFoundError` → chưa install package hoặc chưa `pip install -e .`
- `pyodbc.OperationalError` → DB không kết nối được
- `KeyError` → column/key không tồn tại trong data
- `ValueError` → giá trị không hợp lệ (NaN, wrong type)
- `FileNotFoundError` → file .env hoặc file data không tồn tại
- `PermissionError` → không có quyền truy cập file/DB

### Bước 4.3: Đề xuất cách sửa

Với mỗi lỗi, đưa ra:
1. Nguyên nhân (giải thích đơn giản)
2. Cách sửa (từng bước cụ thể)
3. Cách kiểm tra đã sửa xong (lệnh chạy lại)

---

## Format báo cáo

```
=== CHẨN ĐOÁN ===

Vấn đề: [mô tả ngắn gọn]

Nguyên nhân tìm thấy:
→ [giải thích đơn giản bằng tiếng Việt]

Cách sửa:
1. [bước 1]
2. [bước 2]
3. [bước 3]

Kiểm tra lại:
→ [lệnh chạy để verify đã fix xong]
```

## Lưu ý

- Luôn giải thích bằng tiếng Việt đơn giản
- Không dùng thuật ngữ kỹ thuật phức tạp
- Đưa ra lệnh cụ thể để user copy-paste chạy
- Nếu vấn đề phức tạp → đề xuất từng bước, không làm hết một lúc
- Nếu không xác định được nguyên nhân → hỏi thêm thông tin từ user
