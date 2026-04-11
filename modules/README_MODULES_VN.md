# MODULES - Huong dan quan tri va toi uu (cho nguoi khong chuyen code)

## 1) Tong quan luong he thong

Folder modules la lop dung chung cho toan bo he thong:

1. db_connector.py
- Lo ket noi SQL va cac thao tac ghi/xoa/upsert.
- Day la lop rui ro cao nhat.

2. data_loader.py
- Doc du lieu tu DB ra DataFrame dung schema chuan.
- Cung cap dau vao cho chart/scanner/backtest.

3. indicators.py
- Cong thuc indicator dung chung.
- Sai o day se sai toan he thong.

4. chart_builder.py
- Chi ve chart de quan sat.
- Khong tao signal, khong ghi DB.

## 2) Cach doc tung file de hieu nhanh

### db_connector.py (quan trong nhat)

Nhom ham ket noi:
- _build_conn_str: tao chuoi ket noi.
- get_connection: ket noi co retry.
- test_connection: kiem tra song truoc khi chay pipeline.

Nhom ham nap du lieu:
- insert_staging_batch: ghi loat vao staging qua temp + MERGE.
- run_etl_direct: goi SP nap 1:1 staging -> Fact.
- run_etl_aggregate: goi SP tong hop timeframe phai sinh.
- aggregate_from_fact: tong hop timeframe phai sinh truc tiep tu Fact.

Nhom ham quan tri du lieu:
- purge_staging: don dep staging da xu ly.
- get_latest_bars: lay moc du lieu moi nhat moi cap symbol-tf.
- get_internal_gaps: tim lo hong thoi gian trong du lieu.
- find_price_spikes: tim nhay gia bat thuong giua 2 bar lien tiep.

Nhom ham sua/chuan hoa du lieu:
- upsert_ohlcv_bar: chen/cap nhat 1 bar theo tolerance.
- delete_fact_bars, delete_staging_bars, delete_ohlcv_bars: xoa du lieu co chu dich.

Rui ro neu sua sai:
- Mat du lieu lich su.
- Trung lap du lieu.
- Sai ket qua backtest ma kho phat hien.

### data_loader.py

Muc tieu:
- Doc dung schema cho tung use-case.

Ham chinh:
- load_symbols: danh muc symbol theo nhom hien thi.
- load_timeframes: danh sach timeframe cho UI.
- load_ohlcv: du lieu lowercase cho scanner/signal.
- load_candles: du lieu Title-Case + Volume cho full chart.

Diem can luu y:
- Khong doi ten cot neu chua sua dong bo ben goi ham.
- Luon sap xep oldest -> newest de indicator dung.

### indicators.py

Muc tieu:
- Mot noi duy nhat cho cong thuc indicator.

Ham low-level:
- calc_sma, calc_ema, calc_bollinger, calc_vwap
- calc_rsi, calc_macd, calc_stochastic, calc_atr, calc_obv

Ham bundle:
- add_indicators: them ma, macd_h, atr cho scanner/backtest.

Luu y van hanh:
- Neu muon bot nhieu signal, uu tien doi tham so truoc.
- Chi sua cong thuc khi thay doi thiet ke chien luoc.

### chart_builder.py

Muc tieu:
- Trinh bay du lieu bang Plotly.

Ham chinh:
- build_full_chart: chart day du indicator.
- build_signal_chart: chart SAM signal + MACD + ATR.
- build_reversal_chart: them marker dao chieu.

Luu y van hanh:
- Day la lop hien thi, sua o day khong doi logic vao lenh.
- Sai mapping thoi gian (bar_time -> x) se dat marker sai vi tri.

## 3) Checklist quan tri truoc khi toi uu

1. Xac dinh muc tieu toi uu
- Muon nhanh hon: xem query/data volume.
- Muon dung hon: xem indicator formula va schema.
- Muon de doc hon: xem chart label/layout/comment.

2. Khoanh vung file can sua
- DB integrity: db_connector.py
- Schema/data output: data_loader.py
- Signal math: indicators.py
- Visualization: chart_builder.py

3. Danh gia tac dong
- Tac dong UI
- Tac dong scanner/backtest
- Tac dong du lieu DB (cao nhat)

4. Kiem tra sau sua
- Co loi syntax/type khong.
- Co thay doi schema cot khong.
- Ket qua chart/signal co hop ly khong.

## 4) Nguyen tac toi uu an toan

- Uu tien thay doi tham so truoc khi thay doi cong thuc.
- Khong doi ten cot dau ra neu chua cap nhat ben su dung.
- Bat ky thay doi SQL ghi/xoa phai duoc test tren du lieu mau.
- Tach biet ro: thay doi hien thi va thay doi logic giao dich.

## 5) Ban do luong chay end-to-end (de theo doi he thong)

Luot chay tong quat:

1. Nguon du lieu thi truong -> staging
- Pipeline goi db_connector.insert_staging_batch.
- Du lieu moi duoc MERGE vao staging, tranh trung lap theo (SymbolID, BarTime).

2. Staging -> DWH.Fact_OHLCV
- Truc tiep 1:1 bang run_etl_direct.
- Hoac tong hop timeframe phai sinh bang run_etl_aggregate/aggregate_from_fact.

3. Fact_OHLCV -> DataFrame
- data_loader.load_ohlcv/load_candles doc du lieu theo schema can cho tung use-case.

4. DataFrame -> Indicator columns
- indicators.add_indicators (hoac cac ham calc_*) them cot indicator.

5. DataFrame + signal result -> Chart
- chart_builder.build_signal_chart/build_reversal_chart/build_full_chart de hien thi.

Noi de gay loi nhat trong luong:
- Khong dong nhat schema cot giua data_loader va chart_builder.
- Du lieu khong sap xep dung thu tu thoi gian.
- Chinh sua cong thuc indicator ma khong test lai scanner/backtest.

## 6) Ma tran toi uu: sua gi, vi sao, tac dong gi

### A. Toi uu hieu nang

1. db_connector.py
- Diem toi uu: insert_staging_batch, aggregate_from_fact, purge_staging.
- Dau hieu can toi uu: pipeline chay cham, lock lau, bang staging phinh to nhanh.
- Cach toi uu an toan:
	- Giam khoi luong xu ly moi lan (chia batch).
	- Don dep staging dinh ky deu.
	- Tranh query full-table neu chi can lookback.

2. data_loader.py
- Diem toi uu: TOP n_bars, lookback warmup.
- Dau hieu can toi uu: dashboard mo cham, scanner mat nhieu giay de nap data.
- Cach toi uu an toan:
	- Chi lay dung so bars can thiet.
	- Warmup vua du (khong qua it, khong qua nhieu).

3. chart_builder.py
- Diem toi uu: so trace, so marker, so shape.
- Dau hieu can toi uu: chart lag khi zoom/hover.
- Cach toi uu an toan:
	- Han che marker/rejected qua nhieu.
	- Giam so line/shape hien thi trong mot view.

### B. Toi uu do chinh xac

1. indicators.py
- Diem toi uu: chu ky MA/MACD/ATR, cong thuc xu ly bien dong.
- Dau hieu can toi uu: signal qua nhieu/qua it, backtest khong on dinh giua giai doan.
- Cach toi uu an toan:
	- Chinh tham so trong strategy_config truoc.
	- Neu sua cong thuc, phai backtest lai nhieu giai doan.

2. db_connector.py
- Diem toi uu: upsert_ohlcv_bar, gap/spike detection.
- Dau hieu can toi uu: phat hien du lieu lech giua DB va nguon, jump gia bat thuong.
- Cach toi uu an toan:
	- Theo doi ket qua get_internal_gaps/find_price_spikes.
	- Ghi log ro truoc/sau khi xoa va pull lai du lieu.

## 7) Checklist van hanh dinh ky (SOP)

### Daily checklist (moi ngay)

1. Kiem tra ket noi DB
- Dam bao test_connection pass.

2. Kiem tra do moi du lieu
- Dung get_latest_bars de xem cap symbol-tf nao dang tre.

3. Kiem tra chat luong du lieu
- Quet nhanh gap/spike trong lookback ngan.

4. Kiem tra man hinh scanner/chart
- Co symbol nao khong len chart, marker dat sai vi tri, hoac signal dem bat thuong khong.

### Weekly checklist (moi tuan)

1. Don dep staging
- Chay purge_staging va ghi nhan tong so dong da xoa.

2. Soat tan suat loi
- Tong hop log loi DB connect, ETL fail, upsert error.

3. Danh gia tham so chien luoc
- So sanh so luong signal pass/rejected theo tuan de phat hien drift.

### Monthly checklist (moi thang)

1. Rasoat schema phu thuoc
- Kiem tra data_loader output co con dong nhat voi scanner/chart/backtest.

2. Rasoat hieu nang tong the
- Thoi gian pipeline trung binh, thoi gian mo dashboard, kich thuoc staging.

3. Kich ban su co
- Dien tap quy trinh full re-pull cho 1 symbol/tf mau (xoa -> nap lai -> doi chieu).

## 8) Playbook xu ly su co nhanh

Tinh huong A: Chart khong hien thi hoac hien thi sai

1. Xac minh data_loader tra dung ten cot cho use-case.
2. Kiem tra df co rong khong va x_label co duoc tao khong.
3. Kiem tra mapping bar_time -> x trong chart_builder.

Tinh huong B: Signal bat thuong (qua nhieu/qua it)

1. Kiem tra thay doi gan day trong indicators.py va strategy_config.
2. So sanh so luong pass/rejected voi baseline tuan truoc.
3. Neu vua sua cong thuc, rollback cong thuc va test lai tren cung tap du lieu.

Tinh huong C: Nghi ngo du lieu DB hu/lech

1. Chay get_internal_gaps + find_price_spikes de khoanh vung symbol/tf loi.
2. Neu can, delete_fact_bars + delete_staging_bars cho dung cap loi.
3. Pull/ETL lai, sau do doi chieu so luong bar va moc BarTime moi nhat.
