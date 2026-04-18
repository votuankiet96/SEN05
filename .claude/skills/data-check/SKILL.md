---
name: data-check
description: Kiểm tra chất lượng dữ liệu trong SQL Server trước khi backtest — kết nối DB, độ tươi mới, lỗ hổng data, OHLC hợp lệ, duplicate, completeness. TRIGGER khi user nói: "check data", "kiểm tra data", "kiểm tra dữ liệu", "data có ổn không", "dữ liệu có vấn đề", "data bị thiếu", "gap data", "dữ liệu cũ", "DB có kết nối không", "data freshness", "data quality", "trước khi backtest", "data sạch không", "check database", "data missing".
---

# Skill: /data-check

Kiểm tra chất lượng dữ liệu trước khi backtest.

## Khi nào dùng

Trước khi chạy backtest hoặc khi nghi ngờ dữ liệu có vấn đề.

## Các bước thực hiện

### Bước 1: Kiểm tra kết nối Database

Chạy Python script để test connection đến SQL Server:

```python
import sys
sys.path.insert(0, '/d/Project/SEN05')
from modules.db_connector import get_connection

try:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA IN ('SEN','DWH','MART')")
    table_count = cursor.fetchone()[0]
    print(f"✅ Kết nối DB OK — {table_count} tables")
    conn.close()
except Exception as e:
    print(f"❌ Không kết nối được DB: {e}")
```

Nếu không kết nối được → dừng lại, báo lỗi, gợi ý kiểm tra `.env` và SQL Server service.

### Bước 2: Kiểm tra Data Freshness (dữ liệu mới nhất)

Query bar mới nhất cho mỗi symbol và timeframe chính (D1, H4, H1):

```sql
SELECT 
    s.SymbolCode,
    t.TFCode,
    MAX(f.BarTime) AS LatestBar,
    DATEDIFF(HOUR, MAX(f.BarTime), GETUTCDATE()) AS HoursAgo
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
WHERE t.TFCode IN ('D1', 'H4', 'H1')
GROUP BY s.SymbolCode, t.TFCode
ORDER BY HoursAgo DESC
```

- Nếu HoursAgo > 48 cho D1, > 8 cho H4, > 2 cho H1 → đánh dấu ⚠️ STALE
- Liệt kê symbols bị stale

### Bước 3: Tìm lỗ hổng dữ liệu (Gaps)

Query tìm gaps trong 30 ngày gần nhất cho timeframe D1 và H4:

```sql
WITH BarSequence AS (
    SELECT 
        s.SymbolCode, t.TFCode, f.BarTime,
        LAG(f.BarTime) OVER (PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime) AS PrevBar,
        DATEDIFF(MINUTE, LAG(f.BarTime) OVER (PARTITION BY f.SymbolID, f.TimeframeID ORDER BY f.BarTime), f.BarTime) AS GapMinutes
    FROM DWH.Fact_OHLCV f
    JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
    JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
    WHERE f.BarTime >= DATEADD(DAY, -30, GETUTCDATE())
      AND t.TFCode IN ('D1', 'H4')
)
SELECT SymbolCode, TFCode, COUNT(*) AS GapCount
FROM BarSequence
WHERE GapMinutes > CASE TFCode 
    WHEN 'D1' THEN 2880    -- > 2 ngày (tính cả weekend)
    WHEN 'H4' THEN 480     -- > 8 tiếng
    ELSE 999999 END
GROUP BY SymbolCode, TFCode
HAVING COUNT(*) > 0
ORDER BY GapCount DESC
```

- Lưu ý: bỏ qua gaps qua weekend (Thứ 7 - Chủ nhật) cho Forex/Indices
- Crypto (BTCUSD) trade 24/7 nên mọi gap đều là vấn đề

### Bước 4: Kiểm tra OHLC Consistency

```sql
SELECT 
    s.SymbolCode, t.TFCode, COUNT(*) AS BadBars
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
WHERE f.BarTime >= DATEADD(DAY, -30, GETUTCDATE())
  AND (f.[High] < f.[Low] OR f.Volume < 0)
GROUP BY s.SymbolCode, t.TFCode
```

- High < Low → dữ liệu sai
- Volume < 0 → dữ liệu sai

### Bước 5: Kiểm tra Duplicate Bars

```sql
SELECT 
    s.SymbolCode, t.TFCode, f.BarTime, COUNT(*) AS Duplicates
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
WHERE f.BarTime >= DATEADD(DAY, -30, GETUTCDATE())
GROUP BY s.SymbolCode, t.TFCode, f.BarTime
HAVING COUNT(*) > 1
```

### Bước 6: Tính Data Completeness

```sql
SELECT 
    s.SymbolCode, t.TFCode,
    COUNT(*) AS ActualBars,
    DATEDIFF(MINUTE, MIN(f.BarTime), MAX(f.BarTime)) / 
        CASE t.TFCode 
            WHEN 'D1' THEN 1440 WHEN 'H4' THEN 240 WHEN 'H1' THEN 60 
            ELSE 1 END AS ExpectedBars
FROM DWH.Fact_OHLCV f
JOIN DWH.Dim_Symbol s ON f.SymbolID = s.SymbolID
JOIN DWH.Dim_Timeframe t ON f.TimeframeID = t.TimeframeID
WHERE f.BarTime >= DATEADD(DAY, -30, GETUTCDATE())
  AND t.TFCode IN ('D1', 'H4', 'H1')
GROUP BY s.SymbolCode, t.TFCode
```

- Tính % completeness = ActualBars / ExpectedBars * 100
- < 90% → ⚠️ cần kiểm tra

### Bước 7: Báo cáo kết quả

Trình bày kết quả bằng tiếng Việt:

```
=== KIỂM TRA DỮ LIỆU ===

1. Kết nối DB:     ✅/❌
2. Dữ liệu mới:   ✅/⚠️ (liệt kê symbols stale nếu có)
3. Lỗ hổng data:   ✅/⚠️ (X gaps tìm thấy)
4. OHLC hợp lệ:    ✅/❌ (X bars sai)
5. Trùng lặp:      ✅/❌ (X bars trùng)
6. Đầy đủ:         ✅/⚠️ (XX.X% đầy đủ)

KẾT LUẬN: 
- Dữ liệu SẠCH → sẵn sàng backtest
HOẶC
- Dữ liệu CÓ VẤN ĐỀ → cần xử lý trước (liệt kê cụ thể)
```

## Lưu ý

- Skill này CHỈ ĐỌC dữ liệu, không thay đổi gì trong DB
- Nếu tìm thấy vấn đề → gợi ý chạy `python data_provider/02_gap_fill.py` để lấp gaps
- Nếu DB không kết nối được → gợi ý kiểm tra SQL Server service và file `.env`
