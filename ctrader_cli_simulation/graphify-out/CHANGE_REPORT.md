# BO20 Robots — Graphify refresh 2026-10-10

Phạm vi duy nhất: `C:\Users\Administrator\Documents\cAlgo\Sources\Robots`.

## File-level delta

So với manifest Graphify BO20 ngày 2026-10-06, corpus hiện có 78 file: 1 file mới, 12 file thay đổi và không có file bị xóa.

File mới:

- `bo_workflow/tests/test_optimize_backend.py`

File thay đổi:

- `CLAUDE.md`
- `bo_workflow/config.example.yaml`
- `bo_workflow/core_engine/cli_runner.py`
- `bo_workflow/core_engine/configuration.py`
- `bo_workflow/core_engine/facilitator.py`
- `bo_workflow/core_engine/models.py`
- `bo_workflow/core_engine/output_util/evidence.py`
- `bo_workflow/core_engine/planner.py`
- `bo_workflow/core_engine/signal_trans.py`
- `bo_workflow/core_engine/store.py`
- `bo_workflow/tests/test_cli_engine.py`
- `bo_workflow/tests/test_core_engine.py`

## Thay đổi kiến trúc chính

Code bổ sung backend chạy grid bằng `ctrader-cli optimize`, được điều khiển bởi `optimize_chunk_size`. Giá trị mặc định `0` giữ nguyên đường chạy từng `backtest`; chỉ CLI với chunk size lớn hơn 1 mới sử dụng optimize boxes.

`planner.plan_optimize_boxes()` nhóm các `RunSpec` có cùng bối cảnh, kiểm tra tích Descartes và chia thành các hộp không vượt quá chunk size. `facilitator.run_experiment()` gửi hộp đầy đủ sang `cli_runner.run_optimize_box()`; spec lẻ hoặc hộp bị thủng do kết quả đã cache tiếp tục dùng backtest thường.

CLI runner tạo params file, kiểm tra capability của lệnh `optimize`, chạy một process cho cả hộp, đọc `.optres` và pass directories, rồi ghép kết quả về từng spec bằng giá trị tham số thay vì thứ tự. Lỗi cấp hộp áp dụng cho mọi spec; lỗi cấp pass chỉ áp dụng cho spec tương ứng. Kết quả cuối vẫn đi qua `RunResult`, evidence normalization và `ExperimentStore` hiện có.

`test_optimize_backend.py` bao phủ chia hộp, arithmetic/irregular dimensions, CLI arguments, matching pass, cancellation, timeout/cores, retry, budget, cache holes, failure isolation và experiment-lock compatibility.

## Graph delta

- Corpus: 78 file — 59 code, 19 document.
- Graph mới: 1.783 node, 4.006 edge, 121 community.
- Baseline 2026-10-06: 1.649 node, 3.587 edge, 97 community.

Chênh lệch topology không đồng nghĩa hoàn toàn với source delta vì phiên này dựng lại toàn bộ AST và semantic graph.

## Giới hạn sức khỏe graph

Diagnostic ghi nhận 459 dangling-endpoint edge, 5 self-loop và 223 same-endpoint relationship bị collapse trong graph vô hướng. Graph phù hợp để định hướng kiến trúc và truy nguồn file/line, nhưng không thay thế việc đọc code trước khi sửa.

Không chạy bot, backtest, optimize, test suite, dashboard hoặc dịch vụ runtime trong phiên Graphify này.
