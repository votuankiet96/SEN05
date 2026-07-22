# dp_program — Ghi chú trao đổi chuẩn bị Refactor lần 2

Tài liệu này ghi lại từng vòng hỏi-đáp giữa Kiệt và Sonnet về vai trò thực sự
của từng file/folder trong dp_program, để cuối cùng tổng hợp thành một phân
tích/nhận định/đánh giá cuối cùng phục vụ refactor lần 2.

Bối cảnh: đã có bản đồ hệ thống + bảng phân loại độ phức tạp (10 cơ chế) +
7 ứng viên streamline + 5 câu hỏi nghiệp vụ cần Kiệt quyết định, hoàn thành
trong phiên đọc code toàn diện trước đó (xem tóm tắt cuối file này).

Quy ước ghi chép mỗi mục:
- **Chủ đề / file-folder**: đường dẫn cụ thể
- **Câu hỏi của Kiệt**:
- **Trả lời/phát hiện của Sonnet**:
- **Kết luận tạm thời** (giữ / bỏ / đơn giản hoá / cần thêm thông tin):
- **Ghi chú liên quan** (liên kết tới cơ chế nào trong bảng Phần 2 cũ):

---

## Log trao đổi

### 1. core_engine/ — có phải nền tảng của toàn hệ thống? Phân tầng trọng yếu/hỗ trợ
**Câu hỏi của Kiệt**: core_engine có phải folder nền tảng của toàn hệ thống? Giải thích ngắn gọn: dp_program có những chức năng trọng yếu nào, và những chức năng hỗ trợ nào trong quá trình xử lý tiến trình?

**Trả lời/phát hiện của Sonnet**:
- Xác nhận: core_engine là package Python DUY NHẤT trong repo (`pip install -e .`, `python -m core_engine` là entrypoint duy nhất). `scripts/`, `config/`, `test/`, `runtime/` chỉ bao quanh nó, không phải logic nghiệp vụ.
- Phân 3 tầng theo tiêu chí "bỏ đi thì mất chức năng cốt lõi không":
  - **Lõi nghiệp vụ (trọng yếu)**: `tradingview/` (nguồn dữ liệu) + `historical/` + `live/` (2 luồng lấy dữ liệu) + `warehouse/` (đích ghi/đọc SQL — hệ thống ghi nhận thật sự) + `settings/instruments.py`+`system.py` (định nghĩa domain 37 symbol/15 TF).
  - **Hạ tầng hỗ trợ vận hành**: `supervisor/` (vòng đời, lịch, restart) + `coordination/locks.py` (tránh đụng độ ghi) + `logkit/` (log + cảnh báo) + `reporting/` (Discord) + `health.py`+`cli.py` (tự kiểm tra, giao diện lệnh) + `settings/operational.py` (đọc config).
  - **Ngoại vi tuỳ chọn**: `redis_io/` (handoff OG, best-effort) + `dashboard/` (xem dữ liệu, không được supervisor gọi) + `tls.py`/`exit_codes.py` (tiện ích nhỏ).

**Kết luận tạm thời**: Xác nhận, không có bất đồng. Khung phân tầng này (lõi/hỗ trợ/ngoại vi) sẽ dùng làm lăng kính chung cho các câu hỏi tiếp theo về từng file/folder cụ thể.

**Ghi chú liên quan**: Khung này khớp với nhận định cũ ở Phần 2 — các cơ chế #7 (release-directory), #8 (Redis), #9 (dashboard) đều rơi vào nhóm "hỗ trợ/ngoại vi", tức bản chất không phải lõi nghiệp vụ, nên là nhóm ưu tiên xem xét đơn giản hoá trước khi động đến lõi.

### 2. Quyết định cấu trúc: gộp "hỗ trợ + ngoại vi" thành folder `util/`
**Câu hỏi/đề xuất của Kiệt**: Muốn gộp nhóm hạ tầng hỗ trợ và nhóm ngoại vi vào chung 1 folder tên `util` (utility). Nhóm ngoại vi `redis_io` tạm thời cũng lưu ở đó.

**Trao đổi làm rõ ranh giới (Sonnet hỏi lại)**: `settings/` trước đó được xếp vào nhóm "hỗ trợ" trong bảng phân tầng ban đầu, nhưng thực tế cả nhóm lõi (tradingview/historical/live/warehouse) lẫn nhóm hỗ trợ đều import `settings/` trực tiếp — không thuộc riêng nhóm nào. Đặt câu hỏi: settings/ có gộp vào util/ không, hay đứng riêng?

**Quyết định của Kiệt**: **settings/ giữ đứng riêng**, KHÔNG gộp vào util/ — vì là nền tảng dùng chung cho cả lõi lẫn util, gộp vào sẽ khiến nhóm lõi phải phụ thuộc ngược vào util, phá vỡ ranh giới lõi/hỗ trợ.

**KẾT LUẬN CẤU TRÚC ĐÃ CHỐT (đề xuất cho refactor v2, chưa thực thi)**:
- **`util/`** (folder mới, gộp toàn bộ hỗ trợ + ngoại vi):
  - Từ nhóm hỗ trợ vận hành: `supervisor/`, `coordination/`, `logkit/`, `reporting/`, `health.py`, `cli.py`
  - Từ nhóm ngoại vi: `redis_io/` (tạm lưu ở đây), `dashboard/`, `tls.py`, `exit_codes.py`
- **`settings/`**: đứng riêng ở cấp cao nhất, song song với `util/` và nhóm lõi — vì cả hai bên đều phụ thuộc vào nó.
- **Nhóm lõi nghiệp vụ**: giữ nguyên vị trí hiện tại — `tradingview/`, `historical/`, `live/`, `warehouse/`.

**Trạng thái**: Đây là quyết định cấu trúc thư mục cho refactor v2, mới dừng ở mức ghi nhận/thảo luận — CHƯA thực thi di chuyển file nào.

### 3. `coordination/` — vai trò, file, chức năng
**Câu hỏi của Kiệt**: Đi vào folder `Z:\dp_program\src\core_engine\coordination` trước — đây là folder gì, có những file nào, chức năng ra sao?

**Trả lời của Sonnet**:
- Chỉ 2 file: `__init__.py` (1 dòng, trống) + `locks.py` (1.107 dòng, chứa toàn bộ logic).
- Toàn bộ dựa trên 1 bảng SQL Server duy nhất `SEN.ActiveTask` — điểm thống nhất chung vì live/historical/supervisor là 3 process riêng biệt, không thể dùng OS mutex/file lock.
- 4 nhóm cơ chế con trong locks.py:
  1. Advisory lock chung có TTL (acquire/renew/release/fetch/is_locked) — nguyên thuỷ cơ bản
  2. Lock fencing OwnerId+Fence — tự dò migration 09 đã chạy chưa, feature-detect
  3. Historical job singleton + heartbeat thread (renew mỗi 60s, TTL 240 phút, historical_lease_lost() cờ dừng an toàn)
  4. Live runtime lock + graceful handoff (request_live_shutdown/is_live_shutdown_requested)
  5. Live batch window (tv_live_batch, TTL ngắn, historical tự nhường 75s trước mỗi batch live)
- 5 tên khoá cố định: dp_program_supervisor, ws_live_runtime, tv_historical_job, tv_live_batch, warehouse_maintenance.
- Ai dùng: historical/, live/, supervisor/, health.py, cli.py — hầu hết module khác đều import.

**Kết luận tạm thời**: `coordination/` xác nhận thuộc nhóm **hỗ trợ vận hành** (→ sẽ vào `util/` theo cấu trúc đã chốt ở mục 2) — không tự lấy/ghi dữ liệu OHLCV, chỉ đảm bảo các module lõi không giẫm chân nhau. Không có đề xuất thay đổi nào khác ngoài việc di chuyển vào `util/`.

**Ghi chú liên quan**: Đây chính là nơi triển khai cơ chế #2 trong bảng Phần 2 cũ (Lock fencing OwnerId+Fence, 🔴 bắt buộc — có real incident VM-DP6 reboot).

**Bổ sung — phản hồi của Kiệt**: "Chưa hiểu, hãy diễn giải đơn giản và dễ hiểu hơn". Đã giải thích lại bằng ẩn dụ "tấm bảng đăng ký giữ chỗ chung" (3 tiến trình = 3 nhân viên dùng chung 1 cuốn sổ; locks.py = bảng treo giữa phòng để báo ai đang bận việc gì; "mã vé" = OwnerId+Fence, chống trường hợp 1 người mất kết nối lâu rồi quay lại tưởng mình còn giữ chỗ). Không dùng thuật ngữ TTL/heartbeat/fencing trong bản giải thích lại.

**Ghi chú phong cách trao đổi (áp dụng cho các mục sau)**: Kiệt cần giải thích ở mức đơn giản/dễ hiểu trước, có thể dùng ẩn dụ đời thường thay vì thuật ngữ kỹ thuật (TTL, heartbeat, fencing, singleton...) ngay từ lần giải thích đầu tiên cho mỗi folder/file mới, rồi mới đi sâu kỹ thuật nếu được hỏi thêm.

**Câu hỏi xác nhận của Kiệt**: "Bản thân chức năng coordination này là buộc phải có, đảm bảo cho tiến trình an toàn ghi nhận và sắp xếp việc ghi nhận của 2 tiến trình historical và live?"

**Trả lời/chỉnh lại của Sonnet (2 điểm bổ sung)**:
1. Không chỉ 2 tiến trình (historical+live) mà là 3 — `supervisor` cũng dùng chung cơ chế này (tự ngăn 2 bản supervisor chạy song song; quản lý khoá `warehouse_maintenance` dùng khi dọn dẹp/reset dữ liệu, lúc đó cả live lẫn historical đều phải tạm dừng).
2. Không chỉ "sắp xếp ghi nhận giữa 2 bên", còn thêm: (a) ngăn CHÍNH một tiến trình chạy trùng với chính nó (vd 2 bản live cùng chạy), (b) tự dọn "chỗ giữ" bị bỏ quên khi 1 tiến trình chết đột ngột, tránh các tiến trình khác chờ vô thời hạn.

**KẾT LUẬN ĐÃ XÁC NHẬN (dùng cho tổng hợp cuối)**: coordination/ là **bắt buộc phải có**, không phải ứng viên đơn giản hoá/loại bỏ. Lý do chốt: 3 tiến trình độc lập chạy 24/7 cùng ghi 1 database chung; đã có sự cố thật (VM-DP6 reboot); rủi ro nếu bỏ là ghi chồng vào chính bảng `Fact_OHLCV` mà SEN05 AutoTrading dùng để giao dịch thật — không phải rủi ro có thể đánh đổi lấy sự đơn giản.

### 4. `historical/` — vai trò, file, chức năng
**Câu hỏi của Kiệt**: "folder historical nhé" (tiếp tục sang nhóm lõi nghiệp vụ, dùng phong cách giải thích đơn giản trước đã thống nhất ở mục 3).

**Trả lời của Sonnet (đã dùng ẩn dụ đơn giản trước)**:
- Ẩn dụ: Fact_OHLCV = tủ hồ sơ khổng lồ lưu giá theo ngày/giờ; `historical/` = đội đi kiểm kê và vá lại hồ sơ cũ bị thiếu (khác `live/` = đội đứng canh nhận tin mới liên tục).
- 3 file: `engine.py` (604d, "người tiếp nhận yêu cầu" — CLI entry, kiểm tra an toàn trước khi chạy) / `pipeline.py` (788d, "người làm việc thật" — fetch→validate→ghi→retry, chứa 3 mode runner) / `runtime_support.py` (617d, "người dò bản đồ thiếu sót" — phân biệt lỗ hổng thật vs đóng cửa thị trường bình thường).
- 3 chế độ: `full` (lấy lại toàn bộ, chỉ dùng lúc cài đặt lần đầu) / `gap` (mặc định, tự chạy theo lịch 11:00 và 22:00 UTC mỗi ngày, chỉ vá chỗ thiếu) / `reset` (xoá dữ liệu phạm vi chỉ định, có 2 lớp xác nhận dry-run rồi --reset --yes).
- **Điểm quan trọng**: historical xử lý CẢ 37 symbol (không chỉ 11 symbol live theo dõi) → 26 symbol FOREX hoàn toàn phụ thuộc vào historical (không có real-time), chỉ được cập nhật theo lịch 2 lần/ngày — đây là quyết định nghiệp vụ có chủ đích từ đầu, không phải lỗi.

**Kết luận tạm thời**: `historical/` thuộc nhóm **lõi nghiệp vụ** — cùng `live/` là 1 trong 2 con đường duy nhất đưa dữ liệu giá vào hệ thống. Không phải ứng viên đơn giản hoá/gộp vào util/.

**Ghi chú liên quan**: Không có phản hồi/điều chỉnh từ Kiệt sau câu trả lời này (đang chờ phản hồi tiếp theo).

### 5. `historical/` — 3 file có chồng chéo/dư thừa không, tại sao tên runtime_support
**Câu hỏi của Kiệt**: 3 file (engine.py/pipeline.py/runtime_support.py) có chồng chéo chức năng gì không? Có code dư thừa gì không? Tại sao lại gọi là runtime_support?

**Trả lời của Sonnet**:
1. **Chồng chéo**: Không chồng chéo vai trò — ranh giới rõ, engine.py chỉ import và ráp lại logic từ 2 file kia, không viết lại. Điểm đáng lưu ý (không phải chồng chéo nhưng là "khớp nối lỏng lẻo"): engine.py ghi thẳng vào biến toàn cục của pipeline.py từ bên ngoài (`_pipeline._TF_FILTER = set(scope.timeframe_filter)`) thay vì truyền tham số — khó theo dõi khi đọc code sau này.
2. **Dư thừa cụ thể tìm thấy**: `run_full_load` và `run_backfill` trong `pipeline.py` có khối logic đếm lỗi liên tiếp (`consecutive_fail`) gần như y hệt nhau, kèm comment giải thích lặp lại 2 lần. Đây là ứng viên gộp thành 1 hàm dùng chung — rủi ro thấp, dễ làm. Ngoài chỗ này không phát hiện duplicate nào khác giữa 3 file.
3. **Tên "runtime_support"**: Là quy ước đặt tên LẶP LẠI trong toàn hệ thống — `live/` cũng có `runtime_support.py` (504d) cùng vai trò tương tự. Nghĩa là "mọi thứ engine chính cần nhưng không thuộc luồng xử lý dữ liệu chính, không phải điểm vào CLI". Trong historical/runtime_support.py cụ thể gộp 4 nhóm việc khác bản chất: CLI parser, phân loại lỗ hổng thật/đóng cửa thị trường (phần lớn nhất ~250d, giá trị nhất), cache lỗ hổng đã xác minh, tv_probe. Nhận định: tên khá chung chung, là kiểu "module thùng rác gọn gàng" khi tách file lớn — không phản ánh phần giá trị nhất (logic phân loại lỗ hổng) nằm ở đây.

**Kết luận tạm thời**:
- Ứng viên streamline mới #8: gộp khối `consecutive_fail` trùng lặp trong `run_full_load`/`run_backfill` (pipeline.py) thành 1 hàm dùng chung. Rủi ro thấp, dễ làm.
- Ứng viên streamline mới #9: cân nhắc đổi tên hoặc tách nhỏ `runtime_support.py` (cả ở historical/ lẫn live/) để tên phản ánh đúng nội dung — ví dụ tách riêng phần phân loại lỗ hổng (historical) thành module tên rõ nghĩa hơn.
- Điểm cần theo dõi khi refactor: pattern "ghi thẳng vào biến toàn cục module khác" (`_pipeline._TF_FILTER`, và tương tự `replay_runtime` — cả 2 đều được engine.py mutate trực tiếp qua module reference) — không phải lỗi nhưng là điểm dễ gây khó hiểu, nên cân nhắc đổi thành truyền tham số tường minh nếu refactor v2 động vào các file này.

### 6. `historical/` — đào sâu luồng chạy chi tiết từng file + kết hợp + đánh giá
**Câu hỏi của Kiệt**: Đào sâu vào từng file, cho biết luồng triển khai của từng file, khi kết hợp thì chạy thế nào, đánh giá luồng đã ổn thoả chưa, hệ thống file có quá kỹ thuật hay dư thừa không.

**Trả lời của Sonnet — luồng từng file**:
- `runtime_support.py`: không tự chạy, là hộp công cụ gọi theo điểm quyết định (build_parser 1 lần, resolve_scope 1 lần, tv_probe 1 lần trước vòng lặp, find_stale_pairs+find_hole_pairs chỉ trong run_backfill, raise_if_cancelled gọi liên tục mọi vòng lặp).
- `pipeline.py`: 3 luồng con — run_full_load (2 vòng lặp lồng TF×symbol, mỗi cặp: cancel-check→yield-to-live→pull_with_retry retry 3 lần→đếm lỗi liên tiếp→sleep lịch sự), run_backfill (1 query latest bars→find_stale+find_hole→lặp theo mức nghiêm trọng→pull→re-verify gap→lưu cache 24h), run_reset_scope (safety check không ai đang chạy→preview→2 lớp xác nhận→xoá thật→xoá cache lỗ hổng).
- `engine.py` main(): guard storage mode→parse+scope+probe (runtime_support)→gán trực tiếp vào biến pipeline._TF_FILTER/replay_runtime→fail-fast DB/contract check→xác định mode→nhánh reset (giữ khoá riêng) vs nhánh full/gap (tv_probe→auth→giữ khoá→try chạy pipeline→except/finally nhả khoá+báo cáo).

**Đánh giá luồng — 4 điểm cụ thể tìm được**:
1. Trùng lặp cấu trúc khoá (acquire→atexit→try/finally) giữa nhánh `reset` và nhánh `full/gap` trong `engine.py main()` — trùng lặp thật, gộp được.
2. `detect_mode()` gọi `get_latest_bars()` để quyết định mode, rồi `run_backfill()` gọi lại `get_latest_bars()` lần nữa ngay dòng đầu — 1 truy vấn SQL thừa mỗi lần chạy mode=auto→gap.
3. **[CẦN XÁC NHẬN QUA LOG THẬT, chưa khẳng định]**: ngưỡng "8 lỗi liên tiếp → thử refresh đăng nhập" không phân biệt nguyên nhân — nếu lỗi thật sự do khoá warehouse_maintenance bị giữ lâu (không phải do auth), hệ thống vẫn cứ thử refresh (sẽ "thành công" vì auth không hỏng), reset bộ đếm, lặp lại chu kỳ này suốt cả lần chạy mà không bao giờ dừng cứng để báo đúng vấn đề gốc.
4. `wait_for_historical_slot()` gọi trước MỌI cặp symbol/TF (tới 555 cặp/full load), mỗi lần cố ý bỏ qua cache 30s để nhường live kịp thời — đánh đổi có chủ đích (đúng > nhanh), là chi phí thật đáng ghi nhận, không phải lỗi.

**Đánh giá kiến trúc file — quá kỹ thuật/dư thừa?**:
- Tách 3 file (604+788+617d) hợp lý, không file nào nên xoá/gộp.
- Phần "quá kỹ thuật hơn cần thiết": `ReplayRuntimeOptions` — object mutable dùng chung bị engine.py chỉnh từ xa thay vì truyền tham số tường minh (bản thân code tự thừa nhận đây là hệ quả của việc tách file, không phải thiết kế tối ưu).
- Phần KHÔNG nên coi là dư thừa: logic phân loại lỗ hổng thật vs đóng cửa thị trường — phức tạp vì bài toán lịch giao dịch vốn phức tạp (nhiều sàn, DST), không phải do code làm phức tạp hoá việc đơn giản.

**Kết luận tạm thời — cập nhật danh sách ứng viên streamline cho historical/**:
- #8 (đã có): gộp khối `consecutive_fail` trùng lặp trong run_full_load/run_backfill
- #9 (đã có): cân nhắc đổi tên/tách runtime_support.py
- **#10 (mới)**: gộp cấu trúc giữ-khoá/nhả-khoá trùng lặp giữa nhánh reset và nhánh full/gap trong engine.py main() — dễ, rủi ro thấp
- **#11 (mới)**: tránh gọi `get_latest_bars()` 2 lần khi mode=auto→gap (truyền kết quả từ detect_mode() xuống thay vì gọi lại) — dễ, rủi ro thấp, lợi ích nhỏ (tối ưu hiệu năng, không phải bug)
- **#12 (mới, CẦN DỮ LIỆU VẬN HÀNH THẬT trước khi kết luận)**: xác nhận qua `historical_pulling.log` xem ngưỡng consecutive_fail=8 có từng bị kích hoạt lặp lại do nguyên nhân không phải auth (vd warehouse lock stuck) hay không — nếu có, cần tách biệt xử lý "lỗi do auth" khỏi "lỗi do nguyên nhân khác" thay vì luôn thử refresh đăng nhập.

### 7. `live/` — mở đầu (tổng quan, chưa đào sâu)
**Câu hỏi của Kiệt**: "Ok, hãy đi qua folder live nhé" (chuyển sang live/ sau khi hoàn tất historical/).

**Trả lời của Sonnet (mức tổng quan, dùng ẩn dụ trước theo phong cách đã thống nhất)**:
- Ẩn dụ: historical = đội kiểm kê/vá hồ sơ cũ (chạy xong nghỉ); live = đội đứng canh nhận tin liên tục 24/7 (không rời vị trí, mỗi 5 phút nhận giá mới).
- 3 file: `engine.py` (**4.450d** — bộ não: mở kết nối, nhận dữ liệu, ghi kho, tự phát hiện treo và tự khởi động lại) / `state.py` (210d — bảng trạng thái dùng chung, nhiều phần engine.py cùng đọc/ghi) / `runtime_support.py` (504d — cùng kiểu tên với historical nhưng nội dung khác: ghi file trạng thái, khoá cục bộ 1-tiến-trình-1-máy, kiểm tra kết nối TV, ngưỡng "bao lâu là cũ").
- **Điểm nhấn quan trọng**: riêng engine.py (4.450d) LỚN HƠN CẢ gói historical/ cộng lại (2.009d, 3 file). Là file lớn nhất toàn hệ thống. Lý do: live phải tự chứa toàn bộ cơ chế chịu lỗi (spool, wedge-detection, deferred ETL) trong 1 file, chưa được tách ra như historical đã tách pipeline.py khỏi engine.py.

**Kết luận tạm thời**: Đang chờ Kiệt chọn hướng tiếp theo — đào sâu luồng chạy giống historical/, hay so sánh trực tiếp 2 folder trước.

**Ghi chú liên quan**: Độ lệch kích thước quá lớn giữa live/engine.py và historical/ toàn bộ là dữ liệu định lượng hỗ trợ mạnh cho ứng viên streamline #9 (tách nhỏ live/engine.py theo mẫu historical đã làm — chưa đề xuất chính thức, cần thảo luận thêm khi đào sâu).

### 8. `live/engine.py` — phân tích sâu: tại sao lớn, có nên tách, luồng có quá mức cần thiết không
**Câu hỏi của Kiệt**: Phân tích sâu file engine.py — tại sao lớn đến vậy, có nên tách ra không, luồng chạy có đang quá phức tạp hay quá mức cần thiết không.

**Phương pháp**: Grep toàn bộ `def`/`class` trong file (không chỉ dựa phần đã đọc trước đó) để có bản đồ đầy đủ, tính tỉ lệ dòng theo nhóm chức năng thực tế.

**Trả lời của Sonnet — bảng phân bổ dòng (tổng 4.450d)**:
- main() ~753d (17%) — 1 hàm duy nhất cho toàn bộ khởi động+vòng đời+tắt máy
- class BatchFetcher ~1.054d (24%) — gộp 3 việc: vòng đời worker thread + giao thức WS + xử lý timeout/treo
- _db_worker() ~440d (10%) — 1 hàm duy nhất: dequeue→ghi staging→ghi Fact→xử lý hoãn
- _run_batch()+phân loại treo ~368d (8%)
- lịch chạy+báo cáo giờ ~430d (10%) — _batch_loop, _status_reporter, _on_batch_complete
- điều phối spool/hàng đợi ~306d (7%)
- log/định dạng bảng nến ~213d (5%) — thuần trình bày
- đo lường batch ~206d (5%)
- nạp watermark ~159d (4%)
- phân loại lỗi WS ~99d (2%)
→ 4 khối lớn nhất (main+BatchFetcher+db_worker+run_batch/lịch) chiếm 67% (~3.000/4.450d).

**Phát hiện quan trọng nhất — so sánh với historical/**: `historical/` đã TỪNG ở tình trạng y hệt và ĐÃ ĐƯỢC TÁCH (bằng chứng: docstring đầu pipeline.py giải thích lý do tách khỏi engine.py cũ). `live/` CHƯA được tách theo cách đó — chỉ mới có state.py (dữ liệu dùng chung) và runtime_support.py (hàm phụ nhỏ) tách ra, còn toàn bộ phần "làm việc thật" (BatchFetcher, _db_worker, lịch chạy, main()) vẫn dồn hết 1 file. So sánh trực tiếp: historical/engine.py's main() chỉ 604d vì GIAO VIỆC cho pipeline.py; live/engine.py's main() dài hơn (753d) vì TỰ LÀM LUÔN TẠI CHỖ (viết thẳng vòng lặp retry đăng nhập, thread heartbeat, toàn bộ trình tự tắt máy ngay trong hàm).

**4 đường tách tự nhiên (rủi ro thấp→cao)**:
1. Log/định dạng bảng nến (~213d) → `live/logging_support.py`, gần như không rủi ro
2. Đo lường batch (~206d) → `live/batch_metrics.py`, rủi ro thấp (đã tự cô lập qua khoá riêng)
3. Điều phối spool/hàng đợi (~306d) + _db_worker (~440d) → gộp thành `live/db_worker.py`, rủi ro trung bình (đụng cơ chế spool)
4. BatchFetcher (~1.054d) → khó nhất, gộp 3 việc khác bản chất. Phần (b) nói giao thức WS về lý thuyết giống việc tradingview/history_client.py đã làm cho historical/, nhưng live/ KHÔNG dùng lại module đó — tự viết bộ kết nối WS riêng (chỉ dùng chung phần "định dạng khung tin" ở tradingview/protocol.py). Trùng lặp có lý do chính đáng (1 lần vs sống lâu dài) nhưng đáng xem lại phần dùng chung được.

**Kết luận về "quá phức tạp/quá mức cần thiết"**: KHÔNG quá mức cần thiết về NĂNG LỰC — mỗi lớp phức tạp (spool, wedge-detect, deferred ETL) đều bắt nguồn từ vấn đề có thật/đã xác nhận qua test được duyệt trên production (đã đánh giá ở Phần 2 cũ, mục #6 🔴 bắt buộc). Vấn đề thật là TỔ CHỨC FILE — nhồi hết vào 1 nơi khiến người đọc sau phải tải toàn bộ 4.450 dòng vào đầu mới hiểu 1 luồng, thay vì tách theo ranh giới trách nhiệm như historical/ đã chứng minh hiệu quả.

**KẾT LUẬN DÙNG CHO TỔNG HỢP CUỐI**: KHÔNG đề xuất cắt giảm logic của live/engine.py — mà đề xuất **tách file theo khuôn mẫu historical/ đã áp dụng thành công trong cùng hệ thống**. Đây là ứng viên streamline mới, mức độ ưu tiên cao vì có bằng chứng nội bộ (historical/) chứng minh cách làm này khả thi và đã hoạt động tốt.

**Ứng viên streamline mới #13**: Tách `live/engine.py` (4.450d) theo mẫu historical/ đã làm — tối thiểu tách `logging_support.py` (213d, rủi ro ~0) và `batch_metrics.py` (206d, rủi ro thấp) trước; sau đó cân nhắc tách `db_worker.py` (~746d gộp spool-dispatch+db_worker, rủi ro trung bình); `BatchFetcher` (~1.054d) là khó nhất, cần bàn riêng có nên tách phần giao thức WS ra khỏi phần vòng đời worker/wedge-detect hay không.

### 9. Cấu trúc `src/` tổng thể — 4 nhóm core/shared/util/other + vị trí settings/
**Câu hỏi của Kiệt**: Nếu tách live/engine.py thì tách thế nào? Phần nào đưa qua runtime_support được? Có phần nào là "dùng chung" (giống cả historical) nên tách thành module `shared/`? Đánh giá lại nhóm `util/` đã nêu trước. Muốn cấu trúc src/ đơn giản, dễ dùng: nhóm **core** (live và historical), **shared**, **util**, **other**.

**Trả lời của Sonnet**:

**(A) Quy tắc tách live/engine.py → cái gì qua runtime_support.py**: Dựa trên khuôn mẫu ĐÃ có sẵn trong chính file (vd `_is_market_expected_live()`/`_freshness_threshold_minutes()` trong engine.py đã là lớp bọc mỏng gọi hàm thật trong runtime_support.py) → quy tắc: **hàm thuần tuý (nhận tham số, trả kết quả, không đụng trạng thái sống) → runtime_support.py; hàm phải đọc/ghi trạng thái sống (hàng đợi, khoá, số đếm batch) → ở lại engine.py**.
- Qua runtime_support được: `_fmt_bar_time_utc`, `_classify_ws_error` (đã là lớp bọc mỏng quanh tradingview/protocol.py), nhóm log/định dạng bảng nến (~213d), đo lường batch (~206d, có thể đóng gói thành 1 class nhỏ độc lập).
- Phải ở lại engine.py: `_db_worker()`, `BatchFetcher`, `_run_batch()`, `main()`.

**(B) Phát hiện cụ thể — công thức bị viết trùng giữa live/historical (bằng chứng cho shared/ thật sự)**: `historical/runtime_support.gap_threshold_minutes()` và `live/runtime_support.freshness_threshold_minutes()` cùng tính công thức `max(tf_phút×3, phút_qua_đêm+tf_phút)` — VIẾT 2 LẦN ĐỘC LẬP, cú pháp khác nhưng cho cùng kết quả toán học. Đây là quy tắc nghiệp vụ ("thế nào là dữ liệu cũ, có tính giờ đóng cửa qua đêm") đang trùng lặp thật — rủi ro nếu sửa 1 chỗ quên chỗ kia.

**(C) Đánh giá lại util/ đã nêu trước, trong bối cảnh có thêm shared/**:
- Ranh giới đề xuất: `shared/` = thứ live/historical KHÔNG THỂ hoạt động nếu thiếu (chất liệu của việc lấy-lưu dữ liệu). `util/` = thứ giúp chạy an toàn/có giám sát, không phải bản thân việc lấy-lưu.
- `tradingview/` (nguồn) + `warehouse/` (đích) → đã đúng vị trí từ trước, chỉ đổi NHÃN từ "lõi nghiệp vụ" sang `shared/`, KHÔNG cần di chuyển code.
- `util/` giữ nguyên như đã chốt: supervisor/, coordination/, logkit/, reporting/, health.py, cli.py, redis_io/, dashboard/ — không có gì cần chuyển ra.
- **Điều chỉnh mới**: `tls.py` và `exit_codes.py` — soát lại thấy dùng bởi CẢ util/ lẫn shared/ (tls.py: discord.py (util) + tradingview/auth (shared) đều gọi; exit_codes.py: cli.py (util) + live/historical/engine.py (core) đều dùng). Không phải "mối quan tâm vận hành" thật sự (không trạng thái, không logic nghiệp vụ, chỉ hằng số/hàm tiện ích 1 dòng) → đề xuất chuyển sang **`other/`** thay vì `util/`.

**(D) Câu hỏi làm rõ của Sonnet**: settings/ dùng RỘNG hơn cả "shared giữa 2 core engine" (util cũng cần: BACKEND cho supervisor, NOTIFICATION cho reporting, LOGGING cho logkit) — không khớp gọn vào shared/ (quá hẹp) hay util/ (không phải mối quan tâm vận hành) hay other/ (quá quan trọng). Đặt câu hỏi 3 lựa chọn cho Kiệt.

**QUYẾT ĐỊNH CỦA KIỆT**: **settings/ đứng riêng, là nhóm thứ 5 ngoài 4 nhóm** — nền cấu hình cho TOÀN BỘ hệ thống, không thuộc core/shared/util/other.

**CẤU TRÚC `src/core_engine/` CHỐT CHO REFACTOR V2 (dùng cho tổng hợp cuối, đã cập nhật theo quyết định mục 13, 2026-07-22)**:
```
core_engine/
├── core/
│   ├── live/       (+ spool.py chuyển từ warehouse/, + reporter.py chuyển từ reporting/live_reporter.py)
│   └── historical/ (+ reporter.py chuyển từ reporting/historical_reporter.py)
├── shared/         tradingview/, warehouse/ (spool.py đã chuyển đi, xem trên) (+ module mới cho công thức "độ cũ dữ liệu" hợp nhất từ live+historical)
├── util/
│   ├── logkit/     (giữ nguyên — cơ chế logging thuần: factory/handlers/formatters/tables/jsonl/activity)
│   ├── notify/     MỚI — gộp discord.py (từ reporting/) + critical_outbox.py (từ logkit/) — kênh cảnh báo ra ngoài
│   ├── supervisor/, coordination/, health.py, cli.py, redis_io/, dashboard/
├── other/          tls.py, exit_codes.py
└── settings/       đứng riêng, nhóm thứ 5 — nền cấu hình toàn hệ thống
```
(`reporting/` biến mất hoàn toàn sau tái tổ chức này — 3 file phân về 3 nơi khác nhau theo domain/vai trò thật của chúng.)

**Ứng viên streamline mới #14**: Tạo module `shared/` mới (tên đề xuất: `shared/freshness.py` hoặc gộp vào `shared/` chung) chứa DUY NHẤT công thức "ngưỡng dữ liệu cũ" hiện đang viết trùng ở `historical/runtime_support.gap_threshold_minutes()` và `live/runtime_support.freshness_threshold_minutes()` — hợp nhất thành 1 hàm dùng chung. Rủi ro thấp (đã xác minh 2 công thức cho cùng kết quả toán học), lợi ích: tránh sửa 1 nơi quên nơi kia trong tương lai.

**Trạng thái**: Đây là quyết định cấu trúc thư mục lớn nhất từ đầu buổi, đã chốt đầy đủ 4+1 nhóm — CHƯA thực thi di chuyển file nào, chỉ ghi nhận cho refactor v2.

### 10. `warehouse/` — mở đầu (tổng quan) + phát hiện quan trọng về spool.py
**Câu hỏi của Kiệt**: "Okie, hãy vào folder warehouse" (tiếp tục sang shared/warehouse sau khi chốt cấu trúc 5 nhóm).

**Trả lời của Sonnet (dùng ẩn dụ trước theo phong cách đã thống nhất)**:
- Ẩn dụ: warehouse/ = phòng kho + nhân viên kho của toàn hệ thống — không quyết định "lấy gì lúc nào" (việc của live/historical), chỉ lo "cất vào đâu, đọc lại ra sao".
- 8 file, nhóm theo 4 việc khác nhau:
  1. Đường ống ghi/đọc chính (dùng chung thật): `connection.py`(171d)/`writer.py`(199d)/`reader.py`(358d)/`validation.py`(136d)/`operation_log.py`(74d)
  2. Bảo trì/dọn dẹp: `maintenance.py`(447d)
  3. **Chỉ riêng live/ dùng**: `spool.py`(545d, file lớn thứ 2 trong warehouse/)
  4. Công cụ vận hành viên (ngoài luồng chính): `reconcile.py`(302d)

**PHÁT HIỆN QUAN TRỌNG (ảnh hưởng cấu trúc shared/ vừa chốt ở mục 9)**: `spool.py` (545d) thực ra KHÔNG "dùng chung" đúng nghĩa — chỉ `live/` gọi tới (qua `live/state.py`: `from core_engine.warehouse.spool import LiveSpool`). `historical/` KHÔNG dùng spool — ghi thẳng qua `insert_staging_batch`/`run_etl_direct` vì chạy 1 lần rồi thoát, không cần phục hồi sau crash như tiến trình 24/7. Nếu xếp `warehouse/` nguyên khối vào `shared/`, bên trong `shared/` sẽ có 1 phần (spool.py) thực chất chỉ phục vụ riêng `core/live/` — lệch với định nghĩa "shared = cả 2 core engine đều cần" đã thống nhất.

**Câu hỏi đang chờ Kiệt quyết định**: (a) giữ nguyên spool.py trong warehouse/shared/ (chấp nhận ngoại lệ nhỏ), hay (b) tách spool.py ra khỏi warehouse/, đặt gần core/live/ cho đúng bản chất "chỉ live dùng"?

**Ghi chú liên quan**: Đây là phát hiện thứ 2 (sau công thức freshness trùng lặp ở mục 9) cho thấy ranh giới shared/core không phải lúc nào cũng khớp 100% với ranh giới thư mục hiện tại — cần rà thêm khi đào sâu các module khác.

### 11. `warehouse/reader.py` và `operation_log.py` — xác minh giả thuyết của Kiệt bằng grep
**Câu hỏi của Kiệt**: reader.py nếu chỉ dùng cho nhu cầu Redis thì bỏ vào redis_io/? operation_log.py là log thì bỏ vào logkit/?

**Phương pháp**: Grep toàn bộ codebase (src/+test/+scripts/) để xác minh, không suy đoán từ trí nhớ — vì đây là quyết định cấu trúc.

**Trả lời của Sonnet — reader.py KHÔNG chỉ dùng cho Redis (đã sửa giả thuyết của Kiệt bằng bằng chứng)**:
- 6 hàm trong reader.py, grep xác nhận người gọi thật:
  - `get_latest_bars()` → health.py, historical/pipeline.py, historical/engine.py (KHÔNG phải Redis)
  - `get_internal_gaps()` → health.py, historical/runtime_support.py (KHÔNG phải Redis)
  - `fact_covers_window()` → historical/pipeline.py (KHÔNG phải Redis)
  - `get_latest_ohlcv_snapshot()` → CHỈ redis_io/candle_snapshot.py (ĐÚNG, đây là hàm Redis thật)
  - `get_staging_bar_window()` → **KHÔNG AI GỌI, ở bất kỳ đâu trong repo** (kể cả test/, scripts/)
  - `get_fact_bar_window_context()` → **KHÔNG AI GỌI, ở bất kỳ đâu trong repo**
- Kết luận: chỉ 1/6 hàm phục vụ Redis thật. Nếu chuyển cả reader.py vào redis_io/ (util/), historical/ (core/) sẽ phải import ngược từ util/ — phá vỡ ranh giới core→shared→util. **reader.py nên ở lại warehouse/ (shared/)**.

**PHÁT HIỆN NGOÀI DỰ KIẾN — CODE CHẾT THẬT SỰ**: `get_staging_bar_window()` và `get_fact_bar_window_context()` trong `warehouse/reader.py` — grep toàn repo xác nhận **không có nơi nào gọi tới 2 hàm này**, kể cả test. Đây không phải "ít dùng" mà là dead code thật sự.

**Trả lời của Sonnet — operation_log.py KHÔNG nên chuyển vào logkit/**:
- Grep xác nhận: chỉ dùng nội bộ trong warehouse/ (bởi writer.py, reader.py, maintenance.py) — không ai bên ngoài warehouse/ gọi.
- Lý do không nên chuyển: không phải hạ tầng log chung (logkit/ đã có get_logger/rotating handler/critical outbox) — chỉ là lớp định dạng riêng cho domain "warehouse" (biết về staging_table/symbol_id/tf_code, in tiền tố "WAREHOUSE | ..."). Đây là KHUÔN MẪU LẶP LẠI trong toàn hệ thống: historical/pipeline.py có `_hlog()` riêng, live/engine.py có `_llog()` riêng — cả 2 cũng không đặt trong logkit/. operation_log.py chỉ là bản to hơn của cùng khuôn mẫu (dùng chung cho 3 file thay vì 1). → Giữ nguyên trong warehouse/ để nhất quán.

**KẾT LUẬN DÙNG CHO TỔNG HỢP CUỐI**:
- reader.py: giữ nguyên trong warehouse/(shared/), KHÔNG chuyển vào redis_io/.
- operation_log.py: giữ nguyên trong warehouse/(shared/), KHÔNG chuyển vào logkit/(util/) — vì đây là pattern nhất quán toàn hệ thống (mỗi domain tự có lớp format log riêng, không tập trung vào logkit/).
- **Ứng viên streamline mới #15 (CODE CHẾT, ưu tiên cao vì bằng chứng chắc chắn)**: Xoá `get_staging_bar_window()` và `get_fact_bar_window_context()` khỏi `warehouse/reader.py` — xác nhận 100% không có caller nào trong toàn repo (src/test/scripts). Rủi ro gần như 0.
- **Câu hỏi spool.py từ mục 10 vẫn đang chờ Kiệt quyết định** (chưa trả lời trong lượt này).

---

## Tóm tắt nền tảng đã có trước khi bắt đầu Q&A (từ phiên đọc code toàn diện)

### Bảng 10 cơ chế đã phân loại (mức độ: 🔴 bắt buộc / 🟡 nên giữ / 🟠 cân nhắc bỏ / ⚪ có thể thừa)
1. Spool write-ahead outbox (live) — 🔴 bắt buộc
2. Lock fencing OwnerId+Fence — 🔴 bắt buộc (có real incident VM-DP6 reboot)
3. CRITICAL alert SQLite outbox riêng — 🟡 nên giữ
4. Semantic health (batch_completed_at tách heartbeat) — 🟡 nên giữ, chi phí ~0
5. Reconcile-fact CLI + contract versioning SP — 🔴 phần contract-check bắt buộc; 🟠 toggle count-unsupported-as-missing đáng hỏi
6. Fixed-worker-per-group + wedge-detection + self-recycle (live) — 🔴 bắt buộc (có approved recovery test chạy thật trên production)
7. Release-directory-theo-commit + atomic switch — 🟠 đáng cân nhắc — **XÁC NHẬN: KHÔNG active trong production hiện tại** (runbook)
8. Redis integration (candle_snapshot) — 🟡 bản thân module không thừa (đã tối giản đúng mức best-effort); câu hỏi mở là OG có thực sự dùng không
9. Dashboard module (chart_datacheck) — 🟡 đã kiến trúc tách biệt sẵn, không được supervisor gọi
10. Playwright/Chromium headless fallback — 🟠 đáng cân nhắc đơn giản hoá — auth/core.py 2.376 dòng, cần dữ liệu tần suất thực tế

### Phát hiện thêm (ngoài 10 mục gốc)
- `reporting/discord.py`: ~500/1.178 dòng là engine "dịch" ngôn ngữ kỹ thuật→vận hành viên (regex heuristic đoán feature/result/impact/action) — không phải fault-tolerance, là UX polish dễ vỡ.
- Trùng lặp nhẹ giữa `live/engine.py` và `historical/pipeline.py` cho logic "refresh auth giữa chừng khi liên tiếp lỗi".

### 7 ứng viên streamline cụ thể đã liệt kê
1. Xoá nhánh `DP_STORAGE_MODE=redis` (settings/operational.py, chưa từng wire) — dễ
2. NSSM Windows Service script (scripts/windows_service/, runbook cấm dùng) — dễ
3. Release-directory deployment (~1.000 dòng script, chưa dùng lần nào) — quyết định phụ thuộc lộ trình vận hành
4. `reconcile-fact --count-unsupported-as-missing` toggle — dễ
5. Hợp nhất logic "extract token từ page Playwright" trùng lặp giữa `_headless_refresh`/`_headless_login_fresh` (auth/core.py) — dễ
6. Đơn giản hoá text-inference engine trong discord.py — trung bình-khó
7. Timeframe/instrument review (15 TF × 37 symbol, 11 live) — quyết định nghiệp vụ, không tự trả lời

### 5 câu hỏi cần Kiệt quyết định (không phải kỹ thuật thuần)
1. Release-directory: giữ hay bỏ?
2. NSSM: có dự định dùng Windows Service thật không?
3. Redis/OG: CANDLE_SNAPSHOT_ENABLED có đang bật thật, OG có tiêu thụ không?
4. Headless auth: có log auth.log thực tế cho biết tần suất kích hoạt lớp 3/4 không?
5. 15 TF × 37 symbol: TF nào SEN05 AutoTrading thực sự đọc?
6. **(mới, phát sinh trong Q&A)** `spool.py` (545d) — giữ trong `warehouse/`(shared/) hay tách ra đặt gần `core/live/`?

---

## TỔNG HỢP CUỐI (sau vòng Q&A file/folder, 2026-07-22)

### Cấu trúc `src/core_engine/` chốt cho refactor v2 (5 nhóm, CHƯA THỰC THI)

```
core_engine/
├── core/       live/, historical/          → lõi nghiệp vụ, 2 con đường duy nhất đưa giá vào hệ thống
├── shared/     tradingview/, warehouse/     → cả core lẫn util đều cần, dùng chung
│               (+ module mới hợp nhất công thức "ngưỡng dữ liệu cũ" đang bị viết trùng)
├── util/       supervisor/, coordination/, logkit/, reporting/, health.py, cli.py, redis_io/, dashboard/
├── other/      tls.py, exit_codes.py       → tiện ích 1 dòng, dùng bởi cả util lẫn core, không phải mối quan tâm vận hành
└── settings/   đứng riêng, nhóm thứ 5      → nền cấu hình cho TOÀN BỘ hệ thống, không thuộc 4 nhóm kia
```

### Quyết định kiến trúc cụ thể đã chốt

| # | Quyết định | Lý do |
|---|---|---|
| 1 | Gộp "hỗ trợ vận hành" + "ngoại vi" thành 1 folder `util/` | Đơn giản hoá, cùng bản chất "giúp chạy an toàn, không trực tiếp lấy/lưu dữ liệu" |
| 2 | `settings/` đứng riêng, không vào `util/` | Cả nhóm lõi lẫn `util/` đều import trực tiếp — gộp vào sẽ khiến lõi phụ thuộc ngược vào util |
| 3 | `coordination/` (locks.py) là bắt buộc phải có, không đơn giản hoá | 3 tiến trình độc lập cùng ghi 1 DB; có sự cố thật (VM-DP6 reboot); rủi ro nếu bỏ là ghi chồng vào bảng SEN05 AutoTrading dùng để giao dịch thật |
| 4 | `tls.py`, `exit_codes.py` → `other/`, không phải `util/` | Không có trạng thái/logic nghiệp vụ, dùng bởi cả core lẫn util |
| 5 | `tradingview/`, `warehouse/` đổi nhãn "lõi nghiệp vụ" → `shared/` | Không cần di chuyển code, chỉ đổi cách gọi tên nhóm |
| ⏳ | `spool.py` — CHƯA QUYẾT (câu hỏi #6 ở trên) | Grep xác nhận chỉ `live/` dùng, không phải "shared" đúng nghĩa |

### Toàn bộ ứng viên streamline (gộp 7 cũ + 8 mới từ Q&A = 15)

1. Xoá nhánh `DP_STORAGE_MODE=redis` chưa từng wire — dễ
2. Xoá script NSSM Windows Service (runbook cấm dùng) — dễ
3. Release-directory deployment (~1.000 dòng, chưa dùng lần nào) — phụ thuộc lộ trình vận hành
4. Bỏ toggle `reconcile-fact --count-unsupported-as-missing` — dễ
5. Hợp nhất logic "extract token Playwright" trùng lặp trong `auth/core.py` — dễ
6. Đơn giản hoá text-inference engine trong `discord.py` — trung bình-khó
7. Rà lại 15 TF × 37 symbol (chỉ 11 live) — quyết định nghiệp vụ
8. Gộp khối `consecutive_fail` trùng lặp trong `historical/pipeline.py` — dễ
9. Đổi tên/tách `runtime_support.py` (cả historical/ lẫn live/) — tên không phản ánh đúng nội dung
10. Gộp cấu trúc giữ/nhả khoá trùng lặp giữa nhánh reset và full/gap trong `historical/engine.py main()` — dễ
11. Tránh gọi `get_latest_bars()` 2 lần khi mode=auto→gap — dễ, lợi ích nhỏ
12. [cần dữ liệu log thật] Ngưỡng `consecutive_fail=8` không phân biệt lỗi do auth hay do khoá warehouse bị giữ lâu
13. Tách `live/engine.py` (4.450 dòng, file lớn nhất hệ thống) theo mẫu `historical/` đã làm thành công — ưu tiên cao
14. Hợp nhất công thức "ngưỡng dữ liệu cũ" viết trùng 2 nơi (historical/live runtime_support) — đã xác minh cùng công thức toán học
15. Xoá code chết xác nhận 100%: `get_staging_bar_window()`, `get_fact_bar_window_context()` trong `warehouse/reader.py` — rủi ro gần 0

**Trạng thái chung**: thuần thảo luận/ghi nhận — chưa có dòng code nào bị di chuyển hay xoá.

### Đề xuất của Sonnet cho 6 câu hỏi nghiệp vụ + QUYẾT ĐỊNH CUỐI CÙNG của Kiệt (2026-07-22)

| # | Câu hỏi | Đề xuất của Sonnet | **Quyết định của Kiệt** |
|---|---|---|---|
| 1 | Release-directory: giữ hay bỏ? | Bỏ — không active production, chưa dùng lần nào | **Bỏ** (đồng ý đề xuất) |
| 2 | NSSM: có dự định dùng Windows Service thật không? | Bỏ | **Giữ đó, chưa cần** (Kiệt không theo đề xuất — chưa muốn xoá) |
| 3 | Redis/OG: CANDLE_SNAPSHOT_ENABLED bật thật, OG tiêu thụ? | Chưa đủ dữ liệu, cần xác minh config/OG thật | **Tắt flag (CANDLE_SNAPSHOT_ENABLED) nhưng GIỮ code/module `redis_io/`** — sẽ dùng lại làm nền tảng khi xây dựng OG sau |
| 4 | Headless auth: log auth.log cho biết tần suất fallback 3/4? | Chưa đủ dữ liệu, cần đọc auth.log thật | **Tạm giữ nguyên**, chưa cần đơn giản hoá |
| 5 | 15 TF × 37 symbol: TF nào SEN05 AutoTrading thực sự đọc? | Chưa đủ dữ liệu, cần xem repo SEN05 AutoTrading | **Bỏ qua — không liên quan**, loại khỏi phạm vi refactor v2 |
| 6 | spool.py: giữ warehouse/ hay tách core/live/? | Tách sang `core/live/` (grep xác nhận chỉ live dùng) | **Tách sang `core/live/`** (đồng ý đề xuất) |

**Đã THỰC THI (2026-07-22, theo yêu cầu "tắt bây giờ luôn")**: `config/dp_provider.env` dòng 90 đổi `CANDLE_SNAPSHOT_ENABLED=1` → `CANDLE_SNAPSHOT_ENABLED=0`. Kiểm tra `runtime/run/ws_live_runtime.pid` (4764) tại thời điểm đổi → **không có tiến trình nào đang chạy với PID đó** (tasklist xác nhận), nên không cần restart gì — thay đổi sạch, có hiệu lực ngay từ lần `live` khởi động kế tiếp. Code `redis_io/` giữ nguyên, không xoá gì (theo quyết định #3 giữ code làm nền cho OG sau).

### 12. `tradingview/` — vai trò, xác nhận thuộc `shared/`, audit dead-code đầy đủ
**Câu hỏi của Kiệt**: "Ok, tới tradingview nhé" → sau đó "Ok, hãy làm luôn nhé, tôi muốn audit luôn chính chức năng này" (yêu cầu audit dead-code như đã làm với warehouse/reader.py).

**Trả lời của Sonnet (dùng ẩn dụ trước theo phong cách đã thống nhất)**:
- Ẩn dụ: `tradingview/` = "cánh cổng ra vào + thẻ ra vào" dùng chung cho `historical/` và `live/` — không tự lấy hàng, chỉ lo (1) giao thức WS và (2) giữ token/cookie còn hiệu lực.
- 6 file, 2 nhóm: giao tiếp+lấy dữ liệu (`protocol.py` 217d, `history_client.py` 684d) và auth (`auth/core.py` 2.376d, `auth/jwt_utils.py` 123d, `auth/captcha.py` 121d).
- **Chuỗi fallback 8 lớp trong `auth/core.py`** (chính là cái câu hỏi nghiệp vụ #4 nhắc tới): Lớp 0 cache → Lớp 1 token .env → Lớp 1.5 refresh qua cookie HTTP → Lớp 2 browser profile → Lớp 2.5 username/password HTTP POST → **Lớp 3 headless Chromium+cookie** → **Lớp 4 headless Chromium đăng nhập mới** → Lớp cuối guest token. Lớp 3/4 chỉ kích hoạt khi mọi lớp nhẹ hơn thất bại.
- **Xác nhận `shared/` bằng grep**: 4 nơi dùng — `historical/`+`live/` (lõi, gọi hàm vận hành thật) và `cli.py`+`health.py` (util, **chỉ gọi hàm đọc trạng thái** `browser_profile_status()`/`auth_refresh_lock_status()`/`diagnose_connectivity()`) — cùng khuôn mẫu đã thấy ở `coordination/`. Không cần điều chỉnh gì.
- Việc "trùng lặp refresh-auth khi liên tiếp lỗi" ghi nhận sơ bộ trước đó: xác nhận **không phải duplicate code thật** — `historical/pipeline.py` dùng `refresh_mid_run()` (phản ứng theo consecutive_fail), `live/engine.py` dùng `renew()`+`check_and_refresh()` (chủ động theo lịch) — 2 điểm vào khác nhau, hợp lý theo mô hình lỗi khác nhau của mỗi bên.

**AUDIT DEAD-CODE (grep xác nhận độc lập bằng Python script + ripgrep, quét toàn bộ ~85 tên hàm/class đối chiếu caller trên `src/`+`test/`+`scripts/`)**:

- **Ứng viên streamline #16 (CODE CHẾT, ưu tiên cao)**: `_renew_auth_token()` (`auth/core.py:1155-1213`, ~59 dòng) — 0 caller toàn repo. Hàm public `renew()` gọi `_renew_auth_token_coordinated()` (bản có phối hợp lock) thay vì hàm này — code cũ bị bỏ lại sau khi nâng cấp, quên xoá.
- **Ứng viên streamline #17 (CODE CHẾT)**: `TradingViewWsHistoryError` (`history_client.py:93-94`) — exception class định nghĩa nhưng không nơi nào raise/catch.
- **Ứng viên streamline #18 (CODE CHẾT)**: `_fmt_ts()` (`history_client.py:163-169`) — hàm format timestamp, 0 caller.
- **Ứng viên streamline #19 (TRÙNG LẶP, không phải chết)**: Vòng lặp "nhận gói tin WS + echo heartbeat + parse" bị viết trùng 2 lần trong `history_client.py` — `drain_until_complete()` (trong `fetch_history()`, dòng ~249) và `drain()` (trong `fetch_replay_window()`, dòng ~416). Cả 2 có y hệt 1 khối comment 10 dòng giải thích lý do echo heartbeat (copy-paste) — rủi ro sửa quy tắc echo ở 1 nơi mà quên nơi kia (đúng như comment tự cảnh báo). Đề xuất tách thành 1 helper dùng chung trong `protocol.py`.

**Kết luận tạm thời**: `tradingview/` xác nhận đúng vị trí `shared/`, không có vấn đề cấu trúc. 4 ứng viên streamline mới (#16-19), trong đó #16-18 rủi ro gần 0 (xoá code chết xác nhận), #19 rủi ro thấp (tách helper, không đổi hành vi).

### 13. `logkit/` + `reporting/` — vai trò, audit dead-code, đề xuất tổ chức lại
**Câu hỏi của Kiệt**: "Đi tiếp tới folder logkit và reporting nhé, 2 folder này đóng vai trò gì, chức năng thế nào, có trùng lặp hay dư thừa ko" → sau đó "Có lẽ 2 folder này cũng cần refactor lại để gộp lại chung, thành logkit thôi, tôi muốn có 1 proposal sao cho chuẩn loging của một chương trình hơn".

**`logkit/` (8 file, 868d) — hạ tầng logging thuần, dùng bởi MỌI component**: `factory.py` (`get_logger()` — entry point duy nhất, mọi logger đều tự động có thêm 2 handler: aggregate WARNING+ vào `errors.log`, và CRITICAL→Discord), `handlers.py` (rotating file handler chịu khoá file Windows, + 2 handler dùng chung nói trên), `formatters.py` (format dòng log 1-dòng cho operator, sanitize mojibake console), `tables.py` (helper `cell`/`kv` dùng chung — **đã tự sửa 1 trùng lặp trước đó**: docstring ghi rõ `live_reporter.py`/`historical_reporter.py` từng tự viết y hệt 2 hàm này, đã hợp nhất), `jsonl.py` (JSONL append có cap dung lượng, tránh phình vô hạn), `activity.py` (nhật ký hoạt động vận hành — **xác nhận dùng bởi 5 nơi**: cli.py, supervisor/engine.py, supervisor/process_control.py, dashboard/server.py + chính nó — cross-cutting thật, không thuộc riêng 1 domain), `critical_outbox.py` (đường gửi CRITICAL alert bền vững: persist SQLite trước, gửi đồng bộ, ack khi 200/204, retry nếu fail).

**`reporting/` (3 file, 2.273d) — KHÔNG đồng chất, grep xác nhận 2 bản chất khác nhau**:
- `discord.py` (1.178d) — dùng bởi **5 nơi khắp core+util+shared** (`live/engine.py`, `historical/engine.py`, `supervisor/engine.py`, `supervisor/process_control.py`, `tradingview/auth/core.py`) → cross-cutting thật, không mang domain knowledge riêng.
- `historical_reporter.py` (435d) — grep xác nhận **CHỈ `historical/` import** (pipeline.py, engine.py, runtime_support.py).
- `live_reporter.py` (659d) — grep xác nhận **CHỈ `live/engine.py` import**.

**AUDIT DEAD-CODE (cùng phương pháp #16-19, grep xác nhận độc lập)**:
- **Ứng viên streamline #20 (CODE CHẾT)**: `_headline_status()` (`live_reporter.py:360`, static method trong `LiveReporter`) — 0 caller kể cả nội bộ class.
- **Ứng viên streamline #21 (CODE CHẾT)**: `_description_from_text()` (`discord.py:429`) — 0 caller.
- **Ứng viên streamline #22 (CODE CHẾT)**: `notify_database_event()` (`discord.py:1177`) — 0 caller; đáng chú ý vì 4 hàm anh em cùng mẫu (`notify_backend_event`, `notify_live_event`, `notify_historical_event`, `notify_auth_event`) đều có người gọi thật (10-19 lần) — có vẻ được thêm "cho đủ bộ" nhưng chưa dùng.

**PHÁT HIỆN QUAN TRỌNG — 2 đường gửi Discord song song nằm khác thư mục**: `logkit/critical_outbox.py` (đồng bộ, bền vững, chỉ CRITICAL) và `reporting/discord.py` (bất đồng bộ fire-and-forget, mọi alert khác) — comment trong chính `critical_outbox.py` tự thừa nhận "deliberately NOT routed through reporting.discord.send_alert()". Tách 2 CƠ CHẾ là đúng (lý do chính đáng: CRITICAL cần đảm bảo gửi được), nhưng 2 module cùng "nói chuyện với Discord webhook" lại nằm 2 THƯ MỤC khác nhau — đây là chỗ đáng tổ chức lại hơn là câu hỏi "gộp reporting vào logkit" ban đầu.

**ĐỀ XUẤT CỦA SONNET cho việc tổ chức lại (chưa thực thi, chờ Kiệt quyết định)**:
```
util/
├── logkit/                  giữ nguyên — factory, handlers, formatters, tables, jsonl, activity (hạ tầng logging thuần)
├── notify/                  MỚI — gộp cả 2 đường Discord vào 1 chỗ, không đổi nội dung/hành vi
│   ├── discord.py           (chuyển từ reporting/)
│   └── critical_outbox.py   (chuyển từ logkit/)
└── ... (coordination/, supervisor/, cli.py, health.py, redis_io/, dashboard/ không đổi)

core/historical/reporter.py  ← chuyển từ reporting/historical_reporter.py (single-consumer, đi theo domain, cùng nguyên tắc đã chốt cho spool.py ở mục 10)
core/live/reporter.py        ← chuyển từ reporting/live_reporter.py (single-consumer, đi theo domain)
```
Lý do KHÔNG gộp nguyên khối `reporting/` vào `logkit/`: `historical_reporter.py`/`live_reporter.py` là domain formatter đơn-người-dùng, đúng pattern đã chốt ở mục 11 (`warehouse/operation_log.py`) — mỗi domain tự có lớp format riêng, không tập trung vào util. Gộp sẽ làm `logkit/` mang domain knowledge, phá vỡ chính lý do logkit/ đang sạch (0 domain logic hiện tại).

**QUYẾT ĐỊNH CỦA KIỆT (2026-07-22)**: "Ok, ghi nhận đề xuất này nhé" — **đồng ý** đề xuất tổ chức lại 4 nhóm (logkit=cơ chế logging, notify=kênh cảnh báo ngoài gộp discord.py+critical_outbox.py, reporter theo domain=đi cùng core engine). Đây là quyết định cấu trúc chính thức cho refactor v2, bổ sung vào cấu trúc 5 nhóm đã chốt ở mục 9 (làm rõ nội bộ nhóm `util/` + tách thêm `reporter.py` vào `core/historical/` và `core/live/`). **Trạng thái: CHƯA THỰC THI** — chỉ mới ghi nhận quyết định, chưa di chuyển file nào.

### 14. `supervisor/` + `health.py` — vai trò, cần thiết không, trùng lặp gì
**Câu hỏi của Kiệt**: "folder supervisor, file health là dùng để làm gì, có đang trùng lặp ko" → sau đó "File health này có cần thiết ko" → sau đó "file supervisor có bị trùng lặp code hoạc chức năng gì với các file đã qua ko?"

**Vai trò (ẩn dụ trước)**: `health.py` = "bảng đồng hồ đo" — thuần đọc trạng thái, không tự sửa gì. `supervisor/` = "người quản lý ca trực 24/7" — nhìn bảng đồng hồ rồi quyết định hành động (restart/dừng/chờ), tự tay bật/tắt tiến trình con.

- `supervisor/engine.py` (1.547d): class `BackendSupervisor` — vòng lặp 24/7, spawn `live`/`historical` làm subprocess, backoff/retry, quyết định restart.
- `supervisor/process_control.py` (527d): hạ tầng cấp thấp `engine.py` dựa vào (nhận diện tiến trình cùng máy, phối hợp dừng qua 3 khoá, hàng đợi historical job, ghi log quyết định). Docstring xác nhận phụ thuộc 1 chiều: "Nothing here imports BackendSupervisor - engine.py imports from this module, never the other way around."
- `health.py` (1.255d): `collect_health()` gọi ~15 hàm `_*_check()` (live_state, historical_state, locks, auth, discord, process_inventory quét PowerShell, redis_snapshot...).

**`health.py` có cần thiết không? — CÓ, xác nhận bằng grep 2 người dùng thật**:
1. `cli.py` — lệnh `doctor`/`status`/`data-health` gọi trực tiếp `health.collect_health()`, `health.print_json()`, `health.print_human()`, `health.cleanup_old_runtime_files()`, `health.collect_data_health()`, `health.print_data_health()`. Đây là tính năng CLI thật cho vận hành viên, không phải code thừa.
2. `supervisor/engine.py` — gọi `collect_health()` 3 lần trong vòng lặp chính để quyết định restart (kiểm tra db_contract, staleness...). Xoá `health.py` sẽ làm mất luôn logic quyết định restart của supervisor.
→ Cùng loại lý do đã dùng để xác nhận `coordination/` "bắt buộc phải có" (mục 3): nhiều consumer quan trọng phụ thuộc trực tiếp, xoá là mất chức năng thật.
- Ghi chú phụ: có 1 file khác cũng tên liên quan "health" — `dashboard/health_queries.py` — nhưng grep xác nhận đây là 2 thứ khác hẳn nhau (chỉ trùng tên): `dashboard/health_queries.py` thuần query SQL cho biểu đồ xem dữ liệu (OHLCV), không dính gì tới process/lock/auth. Không có quan hệ code, chỉ trùng tên ngẫu nhiên.

**`supervisor/` có trùng lặp với các file đã audit trước không? — Tổng thể KHÔNG, nhưng tìm thêm 1 trùng lặp cụ thể (ngoài #23 đã ghi nhận lượt trước)**:
- Không trùng với `coordination/locks.py`, `tradingview/auth`, `reporting/discord.py`, `logkit/activity.py` — `supervisor/` gọi đúng các hàm dùng chung này (`fetch_lock`, `notify_*_event`, `log_activity`...) chứ không viết lại logic của chúng.
- **Ứng viên streamline #23 (đã ghi lượt trước)**: parse timestamp ISO/"Z" trùng giữa `health.py::_parse_time()` (dòng 73-87) và `supervisor/engine.py::_live_state_age_seconds()` (dòng 891-897) — cùng 4 dòng logic y hệt, engine.py có thêm 2 guard riêng (khớp PID tiến trình mình spawn, bỏ qua timestamp cũ hơn lúc khởi động) nên không xoá được thẳng, nhưng phần parse nên dùng chung.
- **Ứng viên streamline #24 (MỚI, trùng lặp + có khả năng là lỗi nhẹ)**: Lấy "tên host cục bộ" để so khớp lock cùng máy — trùng giữa `supervisor/process_control.py::_local_host_names()`/`_same_local_host()` (dùng `os.environ["COMPUTERNAME"]` + `socket.gethostname()`) và `health.py::_locks_check()` viết inline (dùng `os.environ["COMPUTERNAME"]` + `os.uname().nodename if hasattr(os,"uname") else ""`). Đáng chú ý: **bản trong `health.py` yếu hơn thật sự trên Windows** — `os.uname` không tồn tại trên Windows nên nhánh đó luôn ra chuỗi rỗng, chỉ còn lại đúng 1 tên host (`COMPUTERNAME`) thay vì 2 tên như bản `process_control.py` (`COMPUTERNAME` + `socket.gethostname()`, cách chuẩn/cross-platform hơn). Đề xuất: `health.py::_locks_check()` gọi thẳng `_local_host_names()`/`_same_local_host()` từ `process_control.py` thay vì viết lại.
- Ghi chú phụ (không phải ứng viên bắt buộc, độ ưu tiên thấp): `_active_lock_detail()`/`_stop_target_snapshot()` (process_control.py) và `_locks_check()` (health.py) đều đọc lock record rồi trích cùng bộ field (pid/host/owner/started/heartbeat/expires_at) — không phải copy-paste y hệt, mà là 2 hình chiếu khác nhau của cùng 1 record cho 2 mục đích hiển thị khác nhau (CLI stop-target vs health diagnostic) — cùng bản chất "mỗi consumer tự format" đã thấy ở historical_reporter/live_reporter, không đề xuất gộp.

**Kết luận tạm thời**: `health.py` bắt buộc phải có (2 consumer thật phụ thuộc). `supervisor/` không trùng lặp về mặt kiến trúc (đúng vai trò actor/quyết định, dùng lại hạ tầng chung đúng cách), chỉ có 2 chỗ trùng lặp code cụ thể (#23, #24) đáng gộp — cả 2 đều rủi ro thấp.

