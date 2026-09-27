---
title: Kiến trúc pipeline_lock (cache content-addressable) + dashboard Dash — đã code
date: 2026-09-22
status: CHỐT + đã triển khai, verify (66/66 test, Dash server khởi động + serve thật thành công)
---

# Bối cảnh

Sau khi chạy full pipeline US30/H1/Combo, người dùng nhận xét kết quả lưu
"rời rạc, chưa hệ thống" (3 experiment tách rời, tên tự đặt tay, DSR/PBO/MC
không lưu lại) — và muốn hướng tới dashboard trực quan. Yêu cầu rõ: dựa trên
RESEARCH/EVIDENCE, không tự suy luận.

# 1. Bằng chứng cho thiết kế lưu trữ (`pipeline_lock.py`)

- **[MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/)** — mô
  hình `Experiment → Run → {Params, Metrics, Artifacts}`, tách 2 kho: backend
  store (params/metrics nhỏ, tra nhanh) + artifact store (file lớn). Hệ
  thống ĐÃ ĐÚNG mô hình này từ trước (`index.sqlite` = backend store,
  `report.json.gz` = artifact store) — chỉ thiếu chỗ lưu DSR/PBO/Monte Carlo.
- **[DVC Pipelines](https://doc.dvc.org/user-guide/pipelines)** — DAG các
  tầng, mỗi tầng có `deps`/`outs`, hash TOÀN BỘ deps (kể cả code) ghi vào 1
  file `dvc.lock`, hash trùng → dùng cache, không chạy lại. `store.param_hash()`
  ĐÃ hash cả code (`algo_sha`/`cli_sha`/`signal_sha`) đúng nguyên tắc này ở
  cấp "1 tổ hợp" — `pipeline_lock.py` nâng nguyên tắc lên cấp "1 tầng".

# 2. Bằng chứng cho công nghệ dashboard (Dash, không phải Streamlit)

Yêu cầu: nhanh tương tác + đẹp + đồng bộ (job chạy nền không đứng hình).

- **[So sánh Streamlit/Dash/NiceGUI/Reflex 2026]** — Streamlit "reruns the
  entire script... creates noticeable lag as apps grow", "customizing look
  and feel is difficult". Dash: "partial property updates that only patch
  changed components", xây trên Plotly+React nên tuỳ biến đẹp sâu hơn.
- **[Streamlit chính thức](https://docs.streamlit.io/develop/concepts/design/multithreading)**
  — *"Streamlit does not officially support multithreading in app code"* —
  đề xuất threading ban đầu SAI, đã tự sửa trước khi code.
- **[Dash background callbacks](https://dash.plotly.com/background-callbacks)**
  — `background=True` + `DiskcacheManager` — CHẠY TÁCH TIẾN TRÌNH THẬT, có
  `set_progress`/`running` sẵn, không cần Redis/Celery cho 1 người dùng cục
  bộ (`pip install dash[diskcache]`). Xác nhận qua đọc thẳng tài liệu +
  introspect API thật đã cài (`inspect.signature(callback)`,
  `DiskcacheManager.__init__`), không chỉ tin tóm tắt search.

# 3. Kiến trúc cuối — đã code, 7 file

```
core_engine/output_util/pipeline_lock.py   MỚI — sổ ghi content-addressable
core_engine/facilitator.py                 SỬA — run_grid() tự tra/ghi lock
core_engine/optimize/walkforward.py        SỬA — run_walkforward() tự tra/ghi lock
core_engine/output_util/readout.py         SỬA — run_dsr/run_pbo/run_montecarlo tự tra/ghi lock

dashboard/data_access.py   MỚI — CHỈ ĐỌC (CQRS query-side), không import facilitator/walkforward,
                            ExperimentStore chỉ .rows()/.flat_rows()
dashboard/actions.py       MỚI — CHỈ TRIGGER (CQRS command-side) — map đúng 5 tầng CLI/tính
                            toán, luôn truyền pipeline=<tên> để mọi tầng qua pipeline_lock
dashboard/app.py           MỚI — giao diện Dash thật, 4 nút chạy nền (background callback,
                            KHÔNG threading) + 1 dcc.Interval (15s) tự đọc lại qua data_access.py
```

**Nguyên tắc ranh giới quan trọng nhất (tự phát hiện + sửa giữa chừng)**:
`evidence.py`/`selection.py` KHÔNG chuyển sang `dashboard/` được — dù cùng
nhóm "output_util" — vì `facilitator.py`/`walkforward.py` (core_engine, bên
GHI) đang import ngược chúng để dùng NGAY TRONG lúc thực thi (không phải chỉ
đọc lại sau). Chuyển đi sẽ tạo phụ thuộc NGƯỢC (core_engine phụ thuộc
dashboard) — vi phạm chính nguyên tắc CQRS đang xây. `readout.py` cũng ở lại
core_engine (không chuyển) vì giờ nó là nơi TÍNH+GHI lock cho DSR/PBO/MC —
hành động GHI, không phải ĐỌC thuần.

# 4. Quyết định Stage [4] KHÔNG tự động hoá

`actions.run_final_stage()` nhận `ksl_level`/`ktp_level` làm THAM SỐ BẮT
BUỘC do người dùng chọn trên giao diện (sau khi xem heatmap Grid + bảng walk-
forward) — không có luật tự động "chọn tổ hợp tốt nhất" nào trong code, đúng
kết luận đã thống nhất trước đó: quyết định này là PHÁN ĐOÁN, không nên tự
động hoá cứng.

# 5. Verify thật đã làm (không chỉ chạy test đơn vị)

- 66/66 test pass (`PipelineLockTests` + `PipelineCachingIntegrationTests` —
  xác nhận cache HIT thật sự bỏ qua công việc thật, không chỉ mock toàn bộ).
- Cài thật `dash`/`dash-mantine-components`/`plotly`/`diskcache`, introspect
  API thật (không đoán) trước khi dùng.
- Khởi động THẬT `python dashboard/app.py`, xác nhận qua `curl`: trang chủ
  trả về đúng title, `/_dash-layout` parse JSON hợp lệ (`MantineProvider` ở
  gốc), `/_dash-dependencies` đăng ký đúng 5 callback (khớp code) — Dash tự
  validate mọi ID callback tham chiếu tới component tồn tại lúc khởi động,
  nên khởi động thành công = cấu trúc callback/layout nhất quán.
- **Giới hạn đã biết, CHƯA verify được**: tương tác THẬT qua trình duyệt
  (bấm nút, xem biểu đồ vẽ đúng dữ liệu thật, job chạy nền nhiều giờ có ổn
  định không) — môi trường này không có trình duyệt để tự thao tác, cần
  người dùng tự mở `http://127.0.0.1:8050` kiểm tra bằng mắt.

# Nguồn

- [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/)
- [DVC — Defining/Running Pipelines](https://doc.dvc.org/user-guide/pipelines)
- [Streamlit — Multithreading (chính thức, có cảnh báo không hỗ trợ)](https://docs.streamlit.io/develop/concepts/design/multithreading)
- [Dash — Background Callbacks (chính thức)](https://dash.plotly.com/background-callbacks)
- Reflex blog, usedatabrain.com, bitdoze.com — so sánh Streamlit/Dash/NiceGUI/Reflex 2026 (khảo sát chung, không phải nguồn học thuật)
