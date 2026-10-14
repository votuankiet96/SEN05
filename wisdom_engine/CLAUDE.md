# wisdom_engine (WE) — Kiến trúc & Quy chuẩn hệ thống

Tài liệu kiến trúc và quy chuẩn duy nhất của `wisdom_engine` (viết tắt **WE**; hệ
con mô phỏng là `wes` = **WE Simulator**). Hệ thống nội bộ (SEN05), không có
`requirements.txt`/`pyproject.toml`.

**Đổi tên 2026-10-08:** dự án `core_python` (CP) → `wisdom_engine` (WE); hệ con `ogs`
→ `wes`. Tên cũ → mới: thư mục `core_python/` → `wisdom_engine/`, package
`core_python.src.*` → `wisdom_engine.src.*`, `ogs/` → `wes/` (`core_python.ogs.src.*` →
`wisdom_engine.wes.src.*`), `cp_config(.example).yaml` → `we_config(.example).yaml`,
`run_cp.sh` → `run_we.sh`, `runtime/logs/cp_run.log` → `we_run.log`, `ogs_run.log` →
`wes_run.log`, unit `core-python-worker.service` → `we-worker.service`,
`ogs-signal.service` → `wes-signal.service`, env `CP_LAUNCH_MODE` → `WE_LAUNCH_MODE`,
hàm launcher `cp_worker_*` → `we_worker_*`, profile Chrome của dashboard →
`~/.cache/we-dashboard-chrome` (đã chuyển nguyên profile cũ sang). **KHÔNG đổi** (hợp đồng với hệ khác):
key Redis `L_PastSignal*`, kênh `L_CHANNEL_SIM_*`, schema CSV (và lúc đó cả file config `wes/og_config.yaml`
— đã đổi thành `wes/wes_config.yaml` ngày 2026-10-14, xem ghi chú đó). Đường dẫn cũ `~/Desktop/core_python` **không còn tồn tại** (symlink
tương thích đã gỡ 2026-10-08 theo yêu cầu): mọi nơi ngoài repo từng dùng nó — nhất là mount
SSHFS `runtime/exports` của VM-BO20 (mục 9) — phải trỏ sang `…/wisdom_engine/…`. Remote
`origin/main` (monorepo `votuankiet96/SEN05`): thư mục `core_python/` cũ ĐÃ được thay bằng `wisdom_engine/`
ngày 2026-10-14 (commit `bd9b64d`; xem ghi chú "Đồng bộ lên main" bên dưới).

**Đổi tên phía datafeed 2026-10-08 (do bên DF làm, không thuộc repo này):** Data Provider
**DP → DF** (datafeed), DP Simulator **DPS → DFS** (Datafeed Simulator); thư mục trên DP6
`dp_program\` → `datafeed\` (`core_program\` = mã DF, `run_df\` = bản chạy thật, `dfs\` = DFS).
Kênh Pub/Sub báo backfill **`dp:events:backfill` → `df:events:backfill`**; WE đã đổi theo cùng
ngày (`redis.backfill_event.channel`). Mục 5 và 10 đã đổi theo (DFS, `L_DFS_*`, `L_WES_*` từ 2026-10-09); chữ "DP"/"DPS" còn sót chỗ nào khác là
DF/DFS. Nhớ: Pub/Sub không lưu tin — bên publish đổi tên kênh mà WE không đổi theo thì worker
**im lặng mất trigger** (heartbeat vẫn báo sống); từng xảy ra 08:50 UTC 08/10 trước khi sửa.

**Tái cấu trúc thư mục 2026-10-09:** gom mã WE vào `we_core/` — `src/` → `we_core/src/`, `tests/` → `we_core/tests/`,
`we_config(.example).yaml` → `we_core/`; `runtime/` → `deploy/runtime/` (exports, logs, charts; lúc đó `deploy/` còn ở gốc repo, xem ghi chú 2026-10-10). Package
`wisdom_engine.src.*` → **`wisdom_engine.we_core.src.*`** (unit `we-worker.service`, `run_we.sh` đã đổi theo); `wes/`
giữ nguyên chỗ, log của nó lúc đó cũng ghi vào `deploy/runtime/logs/` (đã đổi 2026-10-10, xem dưới). Lệnh test/lint: `pytest wisdom_engine/we_core/tests`,
`vulture we_core/src we_core/tests wes/src wes/tests`. Chữ `wisdom_engine.src`, `src/`, `tests/`, `runtime/` ở các đoạn
lịch sử phía trên (ghi chú đổi tên 08/10) là đường dẫn cũ.

**Đổi tên 2026-10-10:** thư mục `we/` → **`we_core/`** (package `wisdom_engine.we.*` → `wisdom_engine.we_core.*`), nội dung
không đổi; mọi đường dẫn/lệnh trong file này đã dùng tên mới (kể cả đoạn 2026-10-09 ở trên: lúc đó thư mục còn tên `we/`).
Hệ con `wes/` và package `wisdom_engine.wes.*` KHÔNG đổi.

**Dời `deploy/` 2026-10-10:** thư mục `deploy/` (unit `we-worker.service` + `runtime/`) dời từ gốc repo vào **`we_core/deploy/`**
(`git mv`, nội dung không đổi: 176 file exports, 30 file log, cùng mã băm). Output vận hành của WE nằm ở
`we_core/deploy/runtime/{exports,logs,charts}`; mỗi hệ giữ output riêng — log của wes ghi vào `wes/deploy/runtime/logs/`
(cạnh `wes/deploy/wes-signal.service`), KHÔNG ghi vào `we_core/`. **Đã xong 2026-10-11 02:53 UTC:** lối đi tạm (symlink `deploy -> we_core/deploy`
ở gốc repo, giữ cho tiến trình wes cũ vì nó nhớ đường dẫn log cũ) đã bị xoá cùng dòng `/deploy` trong `.git/info/exclude`, và `wes_run.log*`
đã chuyển sang `wes/deploy/runtime/logs/` khi restart wes (db15 bị xoá theo cơ chế của wes; DFS không dừng). Gốc repo không còn thư mục
`deploy/` nào. Đường dẫn này KHÔNG phải đường dẫn cho BO20 (xem mục 9).

**Dọn tồn đọng 2026-10-10** (sau các đợt đổi tên; chỉ đụng comment/docstring, config mẫu, fixture test — AST của mã nguồn
`we_core/src` + `wes/src` không đổi sau khi bỏ docstring, YAML giữ nguyên giá trị, 106 + 91 test vẫn qua): (1) bỏ các con trỏ
"xem memory …" (trỏ tới ghi chú không nằm trong repo) khỏi code, config và `run_we.sh`; (2) "DP/DPS" còn sót trong comment → DF/DFS
(KHÔNG đụng `DP6` — tên máy); key trong comment/test/`wes/wes_config.example.yaml` → `L_DFS`/`L_WES` (bản mẫu từng ghi
`L_CANDLE`/`L_OG`, lệch file thật — chạy theo bản mẫu sẽ không nhận được nến); (3) sửa 2 docstring sai trong wes:
`configuration.py` ("wes KHÔNG có trend filter" — thật ra có, mặc định tắt) và `indicator.py` (trỏ tới hàm không còn);
(4) `wes/tests/test_order_gateway.py` → `test_wes_engine.py`; (5) `.gitignore` bỏ `/config.yaml`, `/research/reports/`; xoá thư mục
`.claude/` rỗng; `we_core/we_config.yaml` từ 664 → 600 (có mật khẩu); (6) `.venv` gỡ 51 gói không code nào import (Jupyter/IPython,
paramiko, py-spy…; còn 22 gói), sửa shebang/`activate`/`pyvenv.cfg` còn trỏ `…/og_program/.venv` (đã không tồn tại). **Chủ ý chưa
đụng:** đoạn comment trong `wes/src/redis_io/event_listener.py` và `candle_reader.py` mô tả hành vi OG live gốc (Core/AEN, DB0/DB1) —
đúng với hệ gốc nên giữ như lịch sử; tên hàm/tham số kế thừa của `wes` (`og_list_key`...) cũng giữ nguyên (package `og_signal` của `we_core` lúc đó cũng giữ, nhưng ĐÃ đổi thành `we_signal` ngày 2026-10-14, xem ghi chú đó).
`research_notes/scripts/t001_sideway_regime_test.py` không chạy được trên mã hiện tại (cần `breakout_atr` ở commit 64a2a18) — đã
ghi chú ở đầu file, không xoá vì INDEX còn dẫn tới nó.

**Xoá Dow khỏi `we_core` 2026-10-11** (người dùng quyết định, làm giống `wes`): bỏ `DowStructureParams`,
`add_dow_structure_indicators` và các hàm phụ khỏi `indicator.py`, `_copy_sl_dow_reference` + cột `sl_dow` khỏi `levels.py`,
trường `sl_dow` khỏi bảng tín hiệu của `signal_display/payload.py`, và 2 test Dow. Lý do: Dow chỉ là tham chiếu phụ
(không phải SL chính thức) thừa kế từ chiến lược `ai_trend` cũ (git 1b985d8: KNN trend H1–H4/H3 + EMA 13/34, dùng
`sl_dow` làm SL thật), không đi vào CSV/Redis, chỉ có `ma_cross` còn nối, mà lại chiếm ~100% thời gian
`add_ma_cross_indicators` (150.000 bar: 11,98 s → 0,058 s) và 83% thời gian bootstrap. Đối chiếu trên dữ liệu thật
(9 ca: ma_cross/combo/macd_break, cả đường có trend H4; 3.000–8.000 bar mỗi ca): mọi cột còn lại và `signal_frame`
(phần đi ra CSV/Redis) giống hệt từng bit, chỉ mất đúng 4 cột Dow (`dow_swing_low`, `sl_dow_buy`, `sl_dow_sell`,
`sl_dow`). Test: `we_core` 106 → 105. `we-worker` đang chạy vẫn dùng mã cũ trong bộ nhớ cho tới lần restart kế —
đầu ra không khác nên không cần restart ngay.

**Khôi phục lọc lặp cùng chiều của `combo` 2026-10-11** (người dùng quyết định, cả `we_core` lẫn `wes`): dựng lại
`_alternating_signals` — chỉ bắn tín hiệu đầu tiên khi hướng đổi chiều. Hàm này có từ 2026-08-26 (git 50a2643), bị bỏ 2026-09-11
(git c74ffa9, commit lớn về EMA Cross/KNN, thông điệp không nhắc; lý do còn lại chỉ ở docstring cũ: hệ không theo dõi lệnh thật nên
giả định "còn ở trạng thái BUY tới khi có SELL" có thể bỏ lỡ tín hiệu thật sau khi lệnh cũ đã dính SL). Giữ nguyên chuỗi
`raw_signal_reason` (wes ghi nó vào field `comment`, OF đọc nguyên văn). **Ảnh hưởng đo thật:** DB0 trước khi đổi có 53.777 tín hiệu
combo, 33,5% cùng chiều với tín hiệu liền trước (trung vị cách nhau 5 nến); trên 5 cặp × 4.000–6.000 nến thật bộ lọc mới chặn
32–37% (tổng 4.168 → 2.716), khớp một công thức viết độc lập ở cả 11 ca, `we_core` == `wes`, `signal ⊆ raw_signal`, các cột khác
(kể cả entry của tín hiệu còn lại) giống hệt. Hai điều cần biết: (1) bộ lọc chạy TRƯỚC trend filter nên một setup bị trend loại vẫn
chiếm "lượt đầu" của hướng đó (DB1 mất các tín hiệu cùng trend đến sau setup lệch trend khi chưa có setup ngược giữa chừng);
(2) trạng thái chỉ nhớ trong cửa sổ đang tính (bar có setup đầu cửa sổ luôn bắn): khoảng cách lớn nhất giữa hai setup liên tiếp trên
DB0 là 345 nến, chỉ 6/53.733 khoảng (0,01%) vượt 300 nến warm-up của cửa sổ làm mới worker, 0 khoảng vượt 500 nến của cửa sổ wes.
**Đã có hiệu lực từ 2026-10-11 15:49 UTC (restart `we-worker` và `wes` theo yêu cầu người dùng):** bootstrap 367 s (352/352 lần ghi, 0 lỗi;
nhanh hơn lần trước 960 s vì đã bỏ Dow). DB0 combo 53.777 → 35.744 dòng (−33,5%), DB1 combo 12.642 → 8.710 (−31,1%); `ma_cross` và
`macd_break` không đổi (103.789 / 396.071 ở DB0, 61.254 / 320.034 ở DB1); DB0 combo không còn tín hiệu nào cùng chiều với tín hiệu liền
trước, mọi tín hiệu combo của DB1 đều có trong DB0 với cùng hướng. `wes` restart xoá 1.024 key db15 (đã chép tạm ra scratchpad). Server
chart (chart_simul) giữ mã cũ trong bộ nhớ nên phải restart sau khi đổi mã wes: bản chạy từ 10/10 báo `MISSED` giả cho tín hiệu lặp mà wes
đúng khi bỏ qua và trả 404 cho `/mtf`; đã restart 15:56 UTC; `run_we.sh` mục 5 giờ báo rõ khi server đang chạy chưa có trang `/mtf`.
**CSV đã xuất lại 2026-10-12 07:32–07:35 UTC** (theo yêu cầu người dùng): 176 file `*_full_history_nt.csv` (44 combo, 44 ma_cross, 88 macd_break,
đúng bộ tên cũ) xuất vào thư mục tạm rồi đổi chỗ với bộ cũ trong 0,08 s để `exports/` không bao giờ trống; đã kiểm: đúng cấu trúc, 0 lỗi,
combo −35,6% dòng (97.739 → 62.970) và khớp tuyệt đối Redis DB0 từ 2022, không còn tín hiệu liên tiếp cùng chiều; cả 176 file khớp DB0 trên
535.818 dòng (chỉ 3 file ma_cross US500 lệch 1 dòng ở nến cũ — nghi DF vừa backfill, chưa kiểm). 34 file ma_cross/macd_break có phần
cũ khác bản xuất cũ (24/9 và 6/10) vì dữ liệu SQL đổi kể từ đó (nhiều dòng M10 hơn hẳn, FR40 đổi `atr`), không phải do mã chiến lược. Bản
cũ (176 file, 22 MB) đã xoá hẳn theo yêu cầu người dùng, không còn bản sao. Việc báo VM-BO20 về nội dung DB0/DB1/CSV do người dùng tự quyết (mục 7). Test: `we_core` 105 → 109, `wes` 109 → 112 (4 test golden/thống kê tính lại bằng pipeline, thêm test lọc xen kẽ).

**Lọc lặp cùng chiều cho `macd_break` 2026-10-13** (người dùng quyết định, lúc đó chỉ `we_core`: `wes` chưa có `macd_break`, xem ghi chú 2026-10-14 bên dưới): thêm
`_alternating_signals` (bản sao của combo) vào `strategies/macd_break.py`, đứng trước trend filter. Lý do: chiến lược không
trạng thái nên khi giá chạy một pha nến nào cũng phá đỉnh/đáy mới và bắn tín hiệu — đo thật trước khi đổi (88 cặp, từ 2022,
tái dựng khớp Redis 88/88 cặp): DB0 396.646 dòng, 92,5% cùng chiều với tín hiệu liền trước, 49,3% nằm ngay sau nến kề cùng chiều,
chuỗi nến liền nhau dài nhất 15 (FR40 M20). Đối chiếu mã mới trên dữ liệu thật (88/88 cặp đạt): mọi cột khác giống hệt,
`raw_signal` mới == lọc xen kẽ viết độc lập áp lên `raw_signal` cũ, DB1 ⊆ DB0, entry = close; DB0 396.646 → 29.751 (−92,5%), DB1
320.506 → 14.158 (xen kẽ trước, trend sau). Rủi ro "quên trạng thái" của cửa sổ làm mới worker (warm-up 300 nến) = 0: khoảng cách
lớn nhất giữa hai setup liên tiếp 288 nến. Test: `we_core` 109 → 114 (golden tính lại bằng pipeline: dữ liệu mẫu 39 → 7 tín hiệu, tín
hiệu đầu tiên giữ nguyên; thêm test lọc xen kẽ, lock thứ tự với trend, bản sao khớp combo).
**Đã có hiệu lực từ 2026-10-13 11:17 UTC** (restart `we-worker` theo yêu cầu người dùng, keyspace DB0/DB1 chỉ có tiền tố của ta): bootstrap
322 s (352/352 lần ghi, 0 lỗi). `macd_break` DB0 396.646 → 29.756 dòng (−92,5%), DB1 320.506 → 14.159; combo (DB0 35.786, DB1 8.716) và
ma_cross (103.903 / 61.315) chỉ tăng nhẹ so với lần restart 11/10 vì có dữ liệu mới, không do mã. DB0 `macd_break` không còn cặp
liên tiếp cùng chiều; DB1 ⊆ DB0 nhưng còn 4.437 cặp liền nhau cùng chiều (có chủ đích, do thứ tự lọc). Đường **delta** của worker (cửa
sổ + 300 nến warm-up) kiểm trên 352 cửa sổ 3 ngày (88 cặp × 4 cửa sổ, seed cố định): 0 lệch so với bản full trong Redis ở cả DB0 lẫn
DB1 (dòng tham chiếu ít: 176/81, nhưng ~92% cửa sổ có setup đầu cửa sổ cùng chiều setup trước nên đúng chỗ dễ sai nhất). **CSV đã
xuất lại 2026-10-13** cho phần `macd_break` (thay phần cùng tên của đợt 12/10): 88 file `macd_break_*_full_history_nt.csv` xuất vào thư mục
tạm, kiểm: header/mã signal/định dạng đúng, 0 cặp liên tiếp cùng chiều, mọi dòng từ 2022-01-01 khớp tuyệt đối DB0 (29.756 dòng, 0/88 cặp
lệch); rồi thay từng file bằng sao chép sang tên tạm + đổi tên nguyên tử trong `exports/` (hash khớp, không sót file tạm, combo/ma_cross
không đụng). Cả bộ: 49.130 dòng (trước 660.813, −92,6%); bản `macd_break` cũ KHÔNG giữ bản sao. Việc báo VM-BO20 (mục 7) do người dùng tự quyết: lượng tín
hiệu DB0/DB1/CSV của `macd_break` giảm ~92% (CSV ~93%).

**Tách bộ lọc lặp cùng chiều thành module dùng chung + cờ config 2026-10-14** (người dùng quyết định, cả `we_core` lẫn `wes`): hàm lọc
trước đó có hai bản sao riêng (`combo.py`, `macd_break.py`) và một bản thứ ba trong `wes`; nay chuyển thành `strategies/alternating.py`
(`alternating_filter_enabled`, `alternating_signals`, `apply_alternating_filter`; `wes` có bản sao nguyên văn riêng vì không import `we_core`)
và bật/tắt bằng cờ `alternating_filter_enabled` của từng chiến lược trong `we_config.yaml` / `wes/wes_config.yaml` (mục 5). Mặc định = true cho
`combo`, `macd_break` (và `combo` của `wes`) nên hành vi KHÔNG đổi; `ma_cross` không có cờ (chưa dùng bộ lọc — bật cho nó là hành vi mới, phải
quyết định cách phối hợp với cơ chế "chờ trend" của nó). **Chứng minh không đổi** (SQL SELECT, dữ liệu cố định [2022-01-01, 2026-10-12), băm từng
dòng mọi cột của toàn bộ khung kết quả; chụp bằng mã CŨ rồi so với mã MỚI): 422/422 mục khớp từng bit — cờ bật giống hệt mã cũ, cờ tắt giống
hệt chế độ "bar nào có setup cũng bắn" của mã cũ — gồm `we_core` combo 44 cặp, macd_break 44 cặp (M10/M45/H2/H4), ma_cross 22 cặp (M10/M45), mỗi
cặp cả đường DB0 lẫn DB1 (trend), và `wes` combo 5 ca + ma_cross 3 ca. Test khóa thêm: tắt cờ ra đúng golden cũ (combo 269/15/16 = 31 tín hiệu;
macd_break 561/29/10 = 39 tín hiệu, 11 cặp nến kề nhau; combo `wes` 2 BUY/3 SELL, digest `1fb2c847…`). Test: `we_core` 114 → 119, `wes` 112 → 117.
Kèm theo: `bootstrap_start` của worker và log khởi động của `wes` ghi cờ đang áp dụng. **Không restart gì**: `we-worker`, `wes-signal` và server
chart vẫn chạy mã cũ trong bộ nhớ (kết quả giống hệt, như lần xoá Dow); mã mới có hiệu lực ở lần restart kế — config thật của cả hai đã có sẵn
khoá mới nên restart không gặp `KeyError`. Dashboard: ô chữ "Alternating Filter" (placeholder `true`, gõ `false` để xem chart không lọc).

**Đổi tên file config của `wes` 2026-10-14** (người dùng yêu cầu: hai file còn mang tên `og_config`): `wes/og_config.yaml` →
**`wes/wes_config.yaml`** (file thật, gitignored, quyền 600 giữ nguyên) và `wes/og_config.example.yaml` → **`wes/wes_config.example.yaml`**
(bản mẫu, có trong git). Sửa cùng lượt: `CONFIG_PATH` trong `wes/src/configuration.py`, mọi comment/docstring/thông báo lỗi nhắc tên cũ trong
`wes/src` + `wes/tests` (15 file), 2 comment trong chính hai file yaml (giá trị YAML giữ nguyên, đã đối chiếu trước/sau), và `.gitignore`
(`og_config.yaml` → `wes_config.yaml` — BẮT BUỘC cùng lượt vì file thật chứa mật khẩu; đã kiểm bằng `git check-ignore`: file thật bị bỏ qua, file
mẫu thì không). Trước đó CLAUDE.md liệt kê tên cũ trong mục "KHÔNG đổi (hợp đồng với hệ khác)"; đã kiểm trên máy này: unit systemd (cả bản trong
repo lẫn bản đã cài) không nhắc tới tên file, không script/tiến trình nào khác đọc nó — các nơi còn nhắc `og_config` chỉ là lịch sử/báo cáo cũ ngoài
repo (`~/Documents/GraphifyResults/VM-OG8_core_python`, `.bash_history`, backup `og8_order_gateway_backup`). Cái tên KHÔNG đổi lúc đó: package `og_signal` (về sau ĐÃ đổi thành `we_signal`, ghi chú 2026-10-14),
hàm/tham số kế thừa `og_list_key`... của `wes` (chủ ý giữ, xem ghi chú 2026-10-10). Test: `wes` 117 qua, `we_core` 119 qua, ruff/vulture sạch. **Không restart
gì**: `wes-signal` và server chart đã nạp config vào bộ nhớ nên không bị ảnh hưởng; ở lần restart kế chúng đọc `wes_config.yaml`.

**Đăng ký `macd_break` vào `wes` 2026-10-14** (người dùng yêu cầu; chọn mặc định: 8 khung M10–M45 + H1–H4 như `we_core`, lệnh `MARKET`, lọc lặp
cùng chiều bật, lọc trend tắt, chart vẽ thêm hai đường đỉnh/đáy close): chép chiến lược sang `wes/src/strategies/macd_break.py`, thêm
`add_macd_break_indicators` (`indicator.py`), `add_macd_break_levels` (`levels.py`: entry = close), `normalize_macd_break_params` + đăng ký
`StrategySpec` (`configuration.py`), `macd_break: MARKET` (`signal_publisher.py`), mục `strategies.macd_break` + `live.enabled_strategies` trong
`wes_config.yaml` và bản mẫu, nhãn/đường vẽ/giá trị luật trong `chart_simul` (danh sách cặp của chart tự lấy từ config: 88 → 176). Mọi hàm/lớp cũ của
các file sửa giữ nguyên AST (script sửa kiểm trước khi ghi). **Kiểm chứng** (SQL SELECT + Redis đọc; giá làm tròn 2 số như DFS): (1) `wes` ==
`we_core` từng cột (`prior_high_close`, `prior_low_close`, `macd_h`, `atr`, `raw_signal`, `signal`, `signal_reason`, `entry_price`...) trên khung nến
SQL toàn lịch sử của 88 cặp VÀ trên cửa sổ 500 nến Redis thật của 88 cặp: 0 cặp lệch cột nào; (2) cửa sổ 500 nến (cái `wes` thấy) cho cùng tín hiệu ở
nến cuối như tính trên toàn lịch sử, vùng DFS đã phát (2022-01-01 → 2023-03-02): 12.477 phép so (7.197 tín hiệu, 3.520 setup bị lọc xen kẽ chặn, 1.760
nến không setup): 0 lệch; (3) khoảng cách giữa hai setup liên tiếp (độ dài "trí nhớ" cần của bộ lọc xen kẽ) tối đa 281 nến (p99 111, trung vị 2;
94.906 setup) — không khoảng nào ≥ 300 nên cửa sổ 500 an toàn; ngược lại cửa sổ 100 sẽ lệch 805 lần và cửa sổ 200 lệch 8 lần (đừng giảm
`redis.input.snapshot_bars` xuống dưới ~300). Test: `wes` 117 → 145 (thêm bộ nến dao động không trend `range_bars()` vì `synthetic_bars()` có trend tăng nên
macd_break chỉ ra BUY; golden 3 BUY / 3 SELL, digest `87809d7c…`), `we_core` 119 giữ nguyên, ruff/vulture sạch; một test cũ lấy `macd_break` làm ví dụ "chiến
lược wes không có" đã đổi sang `sma_trend`. **Tải của `wes`** (đo chi phí thật trên nến Redis, MÔ HÌNH hàng đợi trên 61.070 mốc đầu của lịch DFS, CHƯA kiểm
chứng bằng lần chạy thật): `macd_break` ~12 ms mỗi lần tính (combo ~13, ma_cross ~13, đọc 500 nến ~15 ms, đọc 1 lần cho cả hai chiến lược trên cùng nến);
mốc cần xử lý lâu hơn `delay_seconds` (2 s): 249 → 2.111 (0,4% → 3,5%), mốc nặng nhất 2,2 → 3,2 s (hạn chót của nến M10 cùng cặp ~4 s nên còn
~0,8 s, trước đó ~1,8 s); reconcile 30 phút/lần dự kiến ~2,9 s → ~4 s. Nếu thấy `wes` lỡ tín hiệu M10 thì xử lý theo thứ tự: tăng `delay_seconds` của DFS (phía họ),
tính MACD/ATR một lần cho cả hai chiến lược của cùng nến, hoặc bỏ macd_break khỏi các khung M10/M20. **Đã có hiệu lực từ 2026-10-14 09:22:53 UTC**
(restart `wes-signal` + server chart theo yêu cầu người dùng; `we-worker` KHÔNG restart): wes xoá 32.830 key db15 (≈ 32.739 tín hiệu của mốc tới
2023-03-02 11:45, đã chép tạm ra scratchpad), tắt khoảng 0,9 s, DFS nhích đúng 1 mốc (121.132 → 121.133). **Sau restart** (đối chiếu db15 với tín hiệu tính lại
từ SQL bằng chính mã chiến lược của `wes`, mốc đóng nến trong (2023-03-02 11:45, 2023-03-04 11:25], ~2 ngày ảo gồm các mốc 00:00/12:00 nặng của thứ Sáu): macd_break
48/48, combo 32/32, ma_cross 98/98 — 0 lỡ, 0 thừa; log 0 WARNING/ERROR; chart 176 cặp: 180 `OK`, 0 thừa/sai chiều/lệch giá trị. CPU `wes` đo 90 s: ~24% một lõi
(trước macd_break ~14,5% trung bình). Rà lại 30 phút đầu tiên (09:52:54 UTC): `reconcile_end pairs=88 published=0 duration_s=4.1` (trước ~2,9 s; đúng dự kiến ~4 s), 0 tín hiệu bị lỡ. CHƯA đo ngày giao dịch
đầy đủ (thứ Hai–Năm có nhiều mốc nặng hơn, mới đo qua thứ Sáu/thứ Bảy) — cần so lại db15 với SQL sau vài ngày ảo. **Chart báo `MISSED` giả sau
restart:** 9 dòng ma_cross (M10/M45), cả 9 nến đóng 10:45–11:40 giờ ảo, TRƯỚC khi `wes` mới chạy (11:50) — do `source.first_signal_stamp` lấy giờ MỞ nến của tín hiệu
sớm nhất làm mốc wes bắt đầu (10:00, nến H2 mở 10:00 đóng 12:00); chưa sửa, tự hết khi các nến đó trôi khỏi cửa sổ hiển thị.

**Đổi tên package `og_signal` → `we_signal` trong `we_core` 2026-10-14** (người dùng yêu cầu; trước đó tôi ghi "chủ ý giữ" nhưng không có ràng buộc kỹ thuật, chỉ là
tên kế thừa của dự án `order_gateway` cũ): `we_core/src/og_signal/` → **`we_core/src/we_signal/`** (`git mv`: `export_cli.py` + `redis_io/{client,listener,logging_setup,publisher,worker}.py`).
Sửa cùng lượt 19 chỗ trỏ tên package của dự án này: import ở `signal_display/server.py` và trong `we_signal/*`, tên logger của worker (`wisdom_engine.we_core.src.we_signal.redis_io.worker`),
`ExecStart` của `we-worker.service` (bản trong repo VÀ bản đã cài ở `~/.config/systemd/user/`, đã `daemon-reload`), 2 lệnh `python -m` trong `run_we.sh`, 4 import trong test.
KHÔNG đổi 5 chỗ nhắc `order_gateway/og_signal/...` (đường dẫn của dự án CŨ, lịch sử) và `og_list_key`... của `wes` (OG là tên engine mà `wes` mô phỏng). Đã kiểm: AST của mọi file
sửa chỉ khác đúng chuỗi tên package (kiểm bằng script, bỏ docstring); ruff/vulture sạch (1 lỗi thứ tự import ở `signal_display/server.py` đã sắp lại); `we_core` 119 + `wes` 145 test qua; import thử
40 module không lỗi; `export_cli --help` chạy; `og_signal` cũ không còn import được. **`we-worker` CHƯA restart** (theo yêu cầu): tiến trình đang chạy (PID 384599, từ 2026-10-13 11:17) đã nạp mã bằng tên cũ trong
bộ nhớ nên vẫn chạy bình thường (mã sản xuất không có import chạy muộn trỏ vào package này); nó sẽ dùng tên mới ở lần restart kế, nếu nó tự sập trước đó systemd khởi động lại bằng unit mới (đúng đường dẫn).
Dashboard `signal_display.server` (cổng 8517, nếu đang chạy) cũng giữ mã cũ trong bộ nhớ tới lần restart kế. Đường dẫn cũ `python -m ...og_signal...` không còn tồn tại: script/lệnh ngoài repo gọi tên cũ phải đổi (đã kiểm: không có).

**Đưa `trend.py` và `alternating.py` ra `src/` 2026-10-14** (người dùng yêu cầu, cả `we_core` lẫn `wes`): `strategies/{trend,alternating}.py` → **`src/{trend,alternating}.py`** của từng hệ
(`git mv` với file đã vào git, `mv` với file chưa theo dõi — `we_core/src/alternating.py` và toàn bộ `wes/`). Lý do kiến trúc: hai module chỉ import `pandas`/`numpy`/`typing`, không import gì
trong `src/`, nên thuộc tầng đáy cùng hàng `indicator.py`/`levels.py`; `strategies/` giờ chỉ còn file chiến lược (we_core: `combo`, `ma_cross`, `macd_break`, `sma_trend`; wes: `combo`, `ma_cross`, `macd_break`).
Import đổi `…src.strategies.trend` → `…src.trend`, `…src.strategies.alternating` → `…src.alternating`; sửa 45 dòng trong 15 file (mã, test, comment/docstring) + sơ đồ và quy tắc phụ thuộc ở mục 2/3 của file này.
Đã kiểm: AST của mọi file sửa chỉ khác chuỗi tên module (script, bỏ docstring); ruff/vulture sạch; `we_core` 119 + `wes` 145 test qua; import thử 40 module không lỗi; module đường cũ không còn import được.
**Không restart gì**: không có import chạy muộn trong mã sản xuất (chỉ 7 chỗ trong test), nên `we-worker`, `wes-signal`, server chart và dashboard đang chạy vẫn dùng mã đã nạp trong bộ nhớ; lần restart kế dùng cấu trúc mới.
Các ghi chú ngày phía trên nhắc `strategies/alternating.py`, `strategies/trend.py` là đường dẫn lúc đó.

**Đồng bộ lên `main` của SEN05 2026-10-14** (người dùng yêu cầu: kiểm tra `wisdom_engine/` đã có trên main chưa, chưa có/cũ thì đẩy bản mới nhất): remote `origin` = `https://github.com/votuankiet96/SEN05.git`
là MONOREPO (thư mục gốc: `core_python`, `ctrader_cli_simulation`, `datafeed`, `dp_program`, `og_program`, `order_following`, `order_gateway`, `tick_program`); `wisdom_engine/` CHƯA có trên main, chỗ của nó là bản cũ `core_python/`
(48 file, commit cuối 2026-10-06). Làm bằng worktree riêng dựng từ `origin/main` (KHÔNG đụng nhánh `og_program` đang làm việc): gỡ `core_python/`, thêm `wisdom_engine/` = 76 file (mọi file đã theo dõi + chưa theo dõi nhưng
không bị `.gitignore`, trừ `graphify-out/` và `.graphifyignore`), commit `bd9b64d` (git thấy 45 file là đổi tên từ `core_python/`), push THƯỜNG (fast-forward `a03d2de..bd9b64d`, không force). Đã kiểm trước khi đẩy:
quét bí mật bằng cách so mọi file với giá trị thật của `sql.pwd`/`redis.password` trong 2 file config thật (0 trùng, không in giá trị) + mẫu token/webhook/khoá riêng + mật khẩu DP6 (0); config thật và `runtime/` không có trong bản đẩy;
sau khi đẩy: cây băm của 7 thư mục còn lại giữ nguyên so với trước, `core_python/` không còn, `wisdom_engine/` có 76 file. Lịch sử cũ của `core_python/` vẫn nằm trong git history của main. Thư mục khác trên main còn nhắc `core_python`
chỉ trong tài liệu/test của bản `og_program` cũ (`og_program/CLAUDE.md`, `og_program/tests/*`, `ctrader_cli_simulation/AGENT.md`...) — không sửa vì không thuộc phạm vi. Repo cục bộ (`og_program`, gốc = thư mục `wisdom_engine/`) KHÔNG commit gì,
lịch sử cục bộ khác lịch sử main (main chỉ có 1 commit đồng bộ cho thư mục này). Lần đồng bộ kế: dựng lại worktree từ `origin/main` mới nhất, thay `wisdom_engine/` bằng danh sách file hiện tại, đẩy thường.

**Mọi khẳng định trong file này phải khớp code thật.** Bản này viết lại ngày
2026-10-05 bằng cách đọc lại từng file trong `src/`, `tests/`, `run_we.sh`,
`deploy/` và đối chiếu với trạng thái chạy thật (Redis, systemd). Bản cũ mô tả
hệ thống trước 3 lần tái cấu trúc (tách `order_gateway`/`strategy_lab`, xoá
live, flatten thư mục) nên không còn dùng được.

## 1. Hệ thống này làm gì (và không làm gì)

`wisdom_engine` = hệ **backtest / past-signal**. Việc làm:

```text
SQL Server (DP6) OHLCV -> chỉ báo -> tín hiệu BUY/SELL + entry -> CSV / Redis DB0+DB1 / dashboard
```

1. Đọc OHLCV lịch sử từ SQL Server DP6 — **chỉ đọc** (SELECT), không ghi.
2. Chạy chiến lược kỹ thuật: chỉ báo → tín hiệu → điểm vào lệnh.
3. Xuất tín hiệu ra 4 đường: CSV, Redis (worker 24/7), dashboard sống, chart
   HTML tĩnh (mục 7).

**Không làm trong `wisdom_engine`**: không đọc nến từ Redis, không đặt lệnh,
không Discord, không tính SL/TP/risk-reward. Luồng live (đọc Redis DB0 của
DP → tín hiệu → OF) đã chuyển sang hệ khác (OG18), repo này không còn code
đó. Ngoại lệ có chủ đích: thư mục `wes/` (mục 10) là **hệ con mô phỏng** engine OG
đó, đọc nến do DFS phát lên db14, không dùng chung code với `we_core/src/`. `levels.py` chỉ điền `entry_price`/`entry_time`; `sl_price`, `tp_price`,
`risk_reward` vẫn được tạo nhưng **luôn NaN** để payload/dashboard không gãy.

**4 chiến lược đăng ký** (`configuration.STRATEGIES`):

| Key | Nhãn | Timeframe | Giới hạn cứng? | Trend filter KNN | Worker Redis chạy? |
|---|---|---|---|---|---|
| `combo` | Combo | khuyến nghị H1–H4 | Không | Có | Có (H1–H4) |
| `ma_cross` | MA Cross | M10/M20/M30/M45 | Có (`run_strategy` raise) | Có (cơ chế "chờ", mục 6) | Có (M10–M45) |
| `macd_break` | MACD Break | M10–M45, H1–H4 | Có | Có (kiểu Combo: khớp đúng 1 bar) | Có (M10–M45 + H1–H4) |
| `sma_trend` | SMA Trend (research) | M10–M45, H1–H4 | Có | **Không** (bật sẽ raise) | Không |

`macd_break` (thêm 2026-10-06) là bản biến tấu của mẫu breakout trong
research_notes S005 (xem git history của `breakout_atr.py` đã đổi tên), **chưa có
căn cứ backtest** — các số liệu T001/F057–F059 trong research_notes đo cho
`breakout_atr` cũ (có trailing stop, không có MACD), KHÔNG áp dụng cho nó.
`sma_trend` là chiến lược nghiên cứu (S005), tham số do người dùng duyệt để xem
chart/export, **chưa có căn cứ backtest**. `trend.py` (ở `src/`) **không phải** chiến lược — là module lọc
trend KNN dùng chung.

## 2. Nguyên tắc thiết kế — bắt buộc tuân thủ khi sửa/thêm code

- **Chỉ code khi đã được chốt/đồng thuận triển khai rõ ràng — áp dụng cho MỌI
  session.** Không tự ý sửa/thêm/xoá code hay file ngoài đúng phạm vi vừa
  được yêu cầu, kể cả khi thấy hợp lý hay "hiển nhiên". Bàn bạc, đề xuất,
  phân tích — làm tự do. Chỉ thực thi (sửa file, chạy lệnh đổi trạng thái)
  khi người dùng xác nhận rõ ràng (vd "triển khai đi", "làm đi", "chốt vậy").
  Không tự suy diễn đồng ý từ ngữ cảnh.
- **Hành động phá huỷ trên Redis → kiểm tra đối tượng trước khi làm.** Từ
  2026-10-05 Redis trên OG8 chỉ dành cho `wisdom_engine` (db0/db1) và hệ mô phỏng
  DFS/wes (db14/db15, mục 5 và 10), nhưng vẫn phải xem `keyspace` trước khi
  `FLUSH*`: nếu thấy key lạ ở db0/db1 (không phải `L_PastSignal*`)
  thì dừng và hỏi — có thể hệ khác đã dùng lại server này.
- **Mỗi file một trách nhiệm duy nhất** (bảng mục 3).
- **Phụ thuộc một chiều, không vòng lặp.** `db_connector.py`/`indicator.py`/`levels.py`/
  `trend.py`/`alternating.py` là tầng đáy (không import gì trong `we_core/src/`; từ 2026-10-14
  `trend.py` và `alternating.py` nằm ở `src/`, không còn trong `strategies/`). `strategies/*.py`
  chỉ import tầng đáy này — `trend` (`combo.py`, `ma_cross.py`, `macd_break.py`) và `alternating`
  (`combo.py`, `macd_break.py`) — không import lẫn nhau; `sma_trend` không import gì.
  `configuration.py` nối indicator + strategies + levels thành pipeline (và đọc `trend.py` để
  dựng chỉ báo trend). `we_signal/*`, `signal_display/*` là tầng wiring.
- **Không dư thừa, không phức tạp hoá.** Không tạo abstraction/setting cho
  trường hợp chưa xảy ra. Code chưa nối vào pipeline là orphan — xoá (đã có
  git history), không để "dự phòng".
- **Không ghi cột DataFrame mà không ai đọc.** Cột mới phải có nơi đọc thật
  (strategy, levels, payload, export, test).
- **Không có `__init__.py`** (namespace package, PEP 420). Ngoại lệ duy nhất:
  `we_core/tests/__init__.py` (pytest cần để resolve `from tests.fixtures import ...`).
- **Package import là `wisdom_engine.we_core.src.*`** nên tên thư mục repo PHẢI là
  `wisdom_engine` và mọi lệnh Python chạy từ **thư mục cha** (`~/Desktop`), không
  phải từ trong repo. `run_we.sh` và unit systemd đã làm đúng (`WorkingDirectory=
  /home/administrator/Desktop`). Đổi tên thư mục repo là làm hỏng toàn bộ import
  và đường dẫn trong unit.
- **Tham số vận hành → `we_config.yaml`. Cấu trúc/logic → code.** SQL, Redis
  (DB, prefix, kênh), symbol/bars mặc định, tham số chiến lược, timeframe hỗ
  trợ, công tắc trend... nằm ở `we_config.yaml`. `configuration.py` và
  `redis_io/client.py` dùng `_require()` — thiếu key bắt buộc thì raise
  `KeyError` rõ ràng ngay lúc import/nạp config. 1 giá trị chỉ tồn tại ở 1 nơi.
  Ngoại lệ có chủ đích (default kỹ thuật thuần, nằm trong code):
  - Tham số KNN (`KNN_TREND_DEFAULT_PARAMS`) ở `trend.py` (cạnh `indicator.py`).
  - `REDIS_DEFAULTS` (port 6379, các timeout, healthcheck, reconnect) và
    `reconcile_interval_seconds` (1800) ở `redis_io/client.py` — operator vẫn
    đè được qua `redis:`/`redis.backfill_event:`.
  - **Lệch chuẩn đã biết:** `db_connector.py` KHÔNG dùng `_require()` — các hằng
    `SQL_*` có fallback im lặng (`str(_SQL.get("server") or "localhost")`...), và
    nếu thiếu file config thì `_load_config()` trả `{}` thay vì lỗi.
- **Dữ liệu khớp schema ngoài (DP6) → không hardcode.** Danh sách
  symbol/timeframe hợp lệ lấy từ `DWH.Dim_Symbol`/`DWH.Dim_Timeframe`, cache
  một lần mỗi tiến trình.
- **`strategies/*.py` CHỈ có logic quyết định BUY/SELL** — nhận `params` đã
  hoàn chỉnh từ `configuration.py`, không tính chỉ báo, không tính entry.
- **`levels.py` CHỈ có logic entry. `indicator.py` chỉ tính chỉ báo thuần.**
- **Test phải hermetic** — không test nào đụng SQL Server/Redis thật (mục 8).
- **Dọn artifact sau khi chạy/test thật**: `__pycache__/`, `.pytest_cache/`,
  `.ruff_cache/` (mục 9). KHÔNG tự xoá `we_core/deploy/runtime/exports/`.
- **Sửa code xong luôn chạy `ruff check .` + `vulture we_core/src we_core/tests wes/src wes/tests
  --min-confidence 80` + `pytest -q`** (từ thư mục cha cho pytest, mục 8).
  Cả 3 phải sạch.

## 3. Kiến trúc — bảng trách nhiệm từng file

```text
wisdom_engine/                       (tên thư mục BẮT BUỘC, xem mục 2)
  run_we.sh                        Launcher Linux: export CSV, mở dashboard, điều khiển worker, mở WES Chart.
  we_core/                         Hệ WE (backtest / past-signal). Package `wisdom_engine.we_core.*`.
    we_config.yaml                 Config THẬT (gitignored — chứa mật khẩu SQL/Redis).
    we_config.example.yaml         Bản mẫu không secret (có trong git).
    deploy/
      we-worker.service            Unit systemd user cho worker Redis.
      runtime/                     Output vận hành của WE, gitignored: exports/ (CSV), logs/ (we_run.log), charts/ (mục 9).
    src/
      db_connector.py                Kết nối + MỌI dữ liệu từ DP6 (tầng đáy):
                                      get_connection() (retry), load(symbol, tf, n_bars,
                                      before=None), load_range(symbol, tf, from, to),
                                      load_range_with_warmup(symbol, tf, from, to,
                                      warmup_bars), symbols()/tf_minutes()/get_symbol().
      indicator.py                   Chỉ báo thuần (tầng đáy): sma/ema/wma/rma/
                                      macd_hist/atr (Wilder seed SMA, mục 6),
                                      calc_ai_trend_navigator
                                      (KNN), merge_trend_reference, timeframe_minutes,
                                      add_{combo,ma_cross,knn_trend,macd_break,sma_trend}_indicators.
      levels.py                      Entry: combo = high+X / low−X; ma_cross/macd_break/
                                      sma_trend = close.
      trend.py                       Trend KNN dùng chung (tầng đáy): trend_filter_enabled,
                                      apply_trend_filter (combo/macd_break dùng), knn_trend_indicator_params.
      alternating.py                 Lọc lặp cùng chiều dùng chung (tầng đáy; combo, macd_break): alternating_filter_enabled,
                                      alternating_signals, apply_alternating_filter. Cờ trong config (mục 5).
      strategies/
        combo.py ma_cross.py         detect_<tên>_signals (CHỈ tín hiệu).
        macd_break.py                CHỈ tín hiệu, chỉ có lọc lặp cùng chiều (như Combo, cờ trong config), có trend filter kiểu Combo.
        sma_trend.py                 Chiến lược nghiên cứu có TRẠNG THÁI (position, exit_*).
      configuration.py               Tham số từng chiến lược (đọc we_config.yaml) +
                                      PARAM_FIELDS + normalize_*_params + StrategySpec/
                                      STRATEGIES/get_strategy + run_strategy() +
                                      run_strategy_with_trend_reference() + trim_to_requested_range().
      we_signal/
        export_cli.py                CLI xuất CSV + signal_frame() (nguồn DUY NHẤT của
                                      schema CSV lẫn schema Redis — 2 đường không lệch nhau).
        redis_io/
          client.py                  Nạp we_config.yaml cho worker, normalize_redis_config, create_client.
          listener.py                SUBSCRIBE kênh backfill, parse message, heartbeat.
          publisher.py               Ghi/đọc/xoá past-signal (LIST+HASH) trên Redis.
          worker.py                  Vòng đời worker: clear → bootstrap → subscribe → delta refresh.
          logging_setup.py           Log có cấu trúc (console INFO+, file DEBUG+, xoay vòng).
      signal_display/
        payload.py                   DataFrame -> candles/markers/overlays/panels/table/stats (thuần).
        renderer.py                  candles/markers -> 1 file HTML tĩnh tự chứa.
        cli.py                       Chart HTML tĩnh: load → run_strategy → payload → renderer.
                                      Giữ BARS_MIN/BARS_MAX (50/20000) + clamp_bars.
        live_page.py                 HTML dashboard sống.
        server.py                    Flask: /, /api/scan, /api/export, /health.
        vendor/lightweight-charts.js Vendor sẵn (không cần internet).
    tests/                         119 test, hermetic (mục 8).
  wes/                             Hệ con: mô phỏng engine OG trên nến do DFS phát (mục 10). Package riêng
                                    `wisdom_engine.wes.src.*` (KHÔNG import `wisdom_engine.we_core.src`), 145 test riêng.
    deploy/                        wes-signal.service + runtime/logs/ (wes_run.log, gitignored) — TÁCH RIÊNG khỏi we_core.
    src/chart_simul/               Trang chart sống CHỈ ĐỌC cho wes: nến db14 + tín hiệu db15 + chỉ báo, đối
                                    chiếu kỳ vọng/đã publish + trang /mtf nhiều timeframe (mục 10, "chart_simul"). 6 file, mỗi file một vai trò.
  research_notes/                  Ghi chú nghiên cứu + scripts/t001_* (không phải code hệ thống).
```

**Luồng dữ liệu** — 2 đường, đều ở `configuration.py`:

```text
[A] 1 timeframe — run_strategy()
load() | load_range() | load_range_with_warmup()
  -> normalize_params -> add_indicators -> detect_signals -> add_levels

[B] 2 timeframe — run_strategy_with_trend_reference()
entry_bars + trend_bars (khung lớn, mặc định H4)
  -> add_indicators(entry) + add_knn_trend_indicators(trend)
  -> merge_trend_reference (chỉ trend bar ĐÃ ĐÓNG, không lookahead)
  -> detect_signals (lọc theo trend_bias) -> add_levels
```

`StrategySpec` ghim chết thứ tự [A]; mỗi bước nhận output bước trước.
`run_strategy_with_trend_reference` luôn ép `TREND_FILTER_ENABLED=True`
(cờ trong config chỉ ảnh hưởng đường [A]).

## 4. Hợp đồng với DP6 (SQL Server)

`db_connector.py` là file DUY NHẤT kết nối DB `SEN05_AutoTrading` trên DP6.
Schema kỳ vọng:

```text
DWH.Fact_OHLCV   SymbolID, TimeframeID, BarTime (UTC-naive, giờ MỞ bar),
                 [Open], High, Low, [Close], Volume
DWH.Dim_Timeframe  TimeframeID, Code ("M5","H1"...), Minutes
DWH.Dim_Symbol     SymbolID, Symbol, AssetType, IsActive (chỉ IsActive=1 được nạp)
```

Giả định (vi phạm sẽ tính sai): BarTime tăng dần/không trùng theo (Symbol,
TF); bar cuối có thể là bar đang mở (không có cột `is_closed`); BarTime nhất
quán UTC. `_validate()` loại dòng NaT/NaN OHLC, bỏ trùng (giữ dòng cuối), sort.

Từ Linux phải dùng **SQL Authentication** (`uid`/`pwd`) với `driver: freetds`
(Windows Integrated Auth lỗi `GSS_S_FAILURE` trên máy không join domain).

## 5. `we_config.yaml` và Redis

`we_config.yaml` nằm ở `we_core/` (cạnh `src/`), **gitignored**. Cấu trúc (xem
`we_config.example.yaml`):

```yaml
sql:   {server, database, uid, pwd, ...}   # driver/port/tds_version/retry... có default trong db_connector.py
redis:
  host: "127.0.0.1"
  password: ...
  backfill_event: {channel: "df:events:backfill"}      # bắt buộc; reconcile_interval_seconds tuỳ chọn
  past_signal:        {db: 0, key_prefix: "L_PastSignal"}        # bắt buộc — không lọc trend
  past_signal_trend:  {db: 1, key_prefix: "L_PastSignal_Trend"}  # bắt buộc — có lọc trend
defaults:
  symbol: BTCUSD
  bars: 500                     # chart/dashboard (export_cli KHÔNG dùng — export mặc định full-history)
  warmup_bars: 300
  signal_start_date: "2022-01-01"   # mốc bắt đầu của worker Redis; KHÔNG áp dụng cho export_cli
strategies:
  combo / ma_cross / macd_break / sma_trend: {...}  # tham số, timeframe, trend, symbol_x (chỉ combo)
  # combo và macd_break còn có alternating_filter_enabled (true/false) — xem 'Cờ lọc lặp cùng chiều' bên dưới
```

Thiếu `defaults`/`strategies.*` hoặc key con → `KeyError` lúc import. Thiếu
`redis.backfill_event.channel`, `redis.past_signal.{db,key_prefix}`,
`redis.past_signal_trend.{db,key_prefix}` → `KeyError` lúc nạp config worker.
Sửa `strategies:` có hiệu lực ở lần chạy kế (worker cần restart).
**Cờ lọc lặp cùng chiều** `strategies.{combo,macd_break}.alternating_filter_enabled` (2026-10-14; `wes`: `strategies.combo` trong
`wes_config.yaml`): true = chỉ bắn tín hiệu đầu tiên khi hướng đổi chiều (`alternating.py`); false = bar nào thoả điều kiện
cũng bắn (số liệu trước khi có bộ lọc). Cờ là một công tắc true/false trong config, cùng khuôn với `trend_filter_enabled`; bắt buộc có
(thiếu → `KeyError` lúc import), mặc định HIỆN TẠI = true cho cả hai chiến lược, `ma_cross`/`sma_trend` không có cờ vì không dùng bộ
lọc. Đè tạm được bằng `--param ALTERNATING_FILTER_ENABLED=false` (CLI export, cho ra bản không lọc vào `--output-dir` riêng) hoặc ô
"Alternating Filter" của form dashboard. **Đổi giá trị trong config làm đổi NỘI DUNG DB0/DB1/CSV mà tên key/tên file không đổi**, nên
quy trình là: sửa config → restart `we-worker` (bootstrap ~5,5 phút, DB0/DB1 trống trong lúc đó) → xuất lại CSV nếu cần dùng bản CSV mới (việc báo VM-BO20 do người dùng tự quyết).
Worker ghi cờ đang áp dụng vào log `bootstrap_start` (`alternating_combo=… alternating_macd_break=…`), `wes` ghi `event=alternating_filter`
lúc khởi động — dùng để tra dữ liệu hiện tại được tạo với cấu hình nào.
`PARAM_FIELDS` (min/max/label cho form dashboard/`--param`) là schema validate
đầu vào, không nằm trong config. `symbol_x`/`session_hours_utc` chỉ có ở
`combo` (X là buffer entry theo symbol; symbol thiếu → X=0.0).

**`signal_start_date` đổi 2026-10-11: 2024-01-01 → 2022-01-01** (người dùng quyết định, để BO20 chạy backtest
bằng CLI từ 2022 trên dữ liệu có sẵn trong Redis). Nguồn SQL DP6 đủ cho mốc này (đo 2026-10-11, 88 cặp
symbol × khung): bar sớm nhất 2017-04/05 với đa số cặp, riêng H2 từ 2019-01 (FR40 H2 2018-01, BTCUSD H2
2020-12-31, BTCUSD H3 2018-12-31), HK50 mọi khung từ 2020-06-29; số bar 2022–2023 bằng 98,5% số bar 2024–2025,
không cặp nào lệch quá 10% (chỉ đo độ dày, chưa dò từng khoảng trống). Sau khi đổi và restart `we-worker`
(clear + bootstrap, 960 s, 352/352 lần ghi, 0 lỗi): mọi 176 key của DB0 và DB1 có tín hiệu đầu tiên trong
năm 2022 (sớm nhất 2022-01-01 00:00); DB0 553.631 dòng (trước đó 324.257), DB1 393.926 dòng (trước đó
230.251); bộ nhớ Redis 162,8 → 244,8 MB. Trong lúc bootstrap DB0/DB1 trống/dở dang ~16 phút.

### Redis trên OG8 — `wisdom_engine` (db0/db1) và hệ mô phỏng DFS/wes (db14/db15)

**Chốt 2026-10-05 (người dùng quyết định):** Redis server trên OG8
(`127.0.0.1:6379`) từ nay chỉ phục vụ phần chiến lược của `wisdom_engine`. Cùng
ngày đã `FLUSHALL` toàn bộ server (trước đó DB0 chứa `L_CANDLE_*` của DP, DB1 chứa
`L_SIGNAL_*`/`L_CHANNEL_*`/`L_OG_*` của OG18) rồi cho worker nạp lại.

| DB | Key (tiền tố) | Nội dung |
|---|---|---|
| **DB0** | `L_PastSignal_*` | past-signal không lọc trend (`past_signal`) |
| **DB1** | `L_PastSignal_Trend_*` | past-signal có lọc trend (`past_signal_trend`) |
| DB2, DB3 | — | không dùng (DB cũ của `wisdom_engine`, đã xoá) |
| **DB14** | `L_DFS_*` + `dfs:*` | nến do DFS (DP6) phát lên, `wes` đọc (mục 10) |
| **DB15** | `L_WES_*` | tín hiệu `wes` ghi ra (mục 10) |

- Hệ khác (DP live, OG18, OF) **không còn ghi vào db0/db1**. `L_DFS_*` chỉ hợp lệ
  ở db14 (do DFS ghi; đổi từ `L_CANDLE_*` ngày 2026-10-09); thấy nó (hay `L_SIGNAL_*`...) ở db0/db1 thì có hệ khác đang
  dùng chung — kiểm tra trước khi `FLUSH*` hay `clear_all_signals`. `wisdom_engine`
  không bao giờ ghi/xoá ở db14/db15.
- `clear_all_signals` quét đúng `{key_prefix}_*`, an toàn khi worker restart.
- Hai prefix không đè nhau vì ở 2 DB khác nhau; nếu đặt cả hai vào cùng 1 DB thì
  `L_PastSignal_*` sẽ khớp cả `L_PastSignal_Trend_*` — phải đổi prefix trước.
- Worker chỉ SUBSCRIBE `df:events:backfill` — kênh này do DF (trước là DP) publish lên server
  Redis; xác nhận DF vẫn publish lên đúng server này sau khi dọn. Chỉ nhánh **backfill** của DF
  publish (sau mỗi cặp ghi SQL có `affected>0`, payload `{symbol, timeframe, candle_count?, from?, to?}`);
  live của DF không publish (và đang tắt từ 05/10). Kiểm tra bằng `PUBSUB NUMSUB df:events:backfill`
  (phải ≥ 1, chính là worker).
- **Redis OG8 KHÔNG sao lưu (chốt 2026-10-08):** `save ""`, `appendonly no`. Lý do: luật
  cũ `save 3600 1 300 100 60 10000` khiến Redis ghi lại toàn bộ dump (~97 MB) 42–48
  lần/giờ khi DFS chạy (07/10 ghi 19,2 GB đĩa trong ngày; từ fstrim 05/10 đến lúc OG8 sập
  12:45 UTC 07/10 dồn ~32 GB chưa trả về host) — nghi làm phình VHDX trên host. Không cần
  sao lưu vì worker luôn clear + bootstrap DB0/DB1 từ SQL; db14/db15 là mô phỏng — OG8/Redis
  khởi động lại thì mất, phải chạy lại DFS từ đầu (không `resume` được vì checkpoint DFS
  nằm trong db14). Muốn giữ db15 thì chủ động `BGSAVE`/`redis-cli --rdb <file>`. Không bật
  lại luật `save` khi chưa xử lý được lượng ghi này.
  **Điều kiện "worker tự nạp lại" CHỈ đúng khi worker nhận được `RedisError`.** Sự cố thật
  2026-10-08 06:31 UTC: `unattended-upgrades` nâng gói `redis-server` (5:8.0.5-1 →
  5:8.0.5-1ubuntu0.1) và restart Redis trong ~1 giây; redis-py nối lại âm thầm, không ném
  `RedisError`, nên worker KHÔNG bootstrap lại: db0/db1 trống ~45 phút (heartbeat vẫn báo
  `pairs_tracked=176`) cho đến khi restart tay `we-worker`. Mỗi lần Redis restart hoặc OG8 reboot
  (hiện còn `/var/run/reboot-required`) đều phải kiểm tra rồi restart `we-worker`. Chưa có cơ chế
  tự phát hiện (vd so `run_id`/`dbsize` ở heartbeat) — chưa làm, chờ quyết định.

### Warm-up chỉ báo (`defaults.warmup_bars`)

`warmup_bars` = số bar tải thêm NGAY TRƯỚC mốc `from` để chỉ báo kịp ổn định.
Đo thật (khi còn Dow, trước 2026-10-11): SMA chỉ cần đúng `period` bar; MACD
(5/25/5) hội tụ gần như tức thì; điểm nghẽn lúc đó là **ATR period=14 của Dow
structure** (lệch ~1% ở 30 bar, ~0,007% ở 100, 0% ở 150) — Dow đã xoá khỏi
`we_core` nên điểm nghẽn này không còn (chưa đo lại). 300 là biên an toàn do
operator chọn. Dùng chung cho:
`load_range_with_warmup()`, `export_cli._load_entry_range()` (có `--from`),
`signal_display/server.py` (chế độ khoảng ngày), `redis_io/worker.refresh_pair()`.
Sau khi chạy strategy, **caller phải cắt về đúng `bartime >= date_from`**
(`configuration.trim_to_requested_range`) — không thì tín hiệu trong đoạn
warmup lọt ra output.

## 6. Chiến lược

Logic "khi nào/hướng nào" luôn nằm trong khối banner comment ở
`strategies/<tên>.py`. `ma_cross` **không có state machine giữa các bar** — mỗi bar thoả
điều kiện là bắn tín hiệu (OG không theo dõi lệnh thật; có nhớ "tín hiệu đầu tiên"/đang giữ
lệnh hay không là việc của hệ thống đặt lệnh). `combo` và `macd_break` chỉ có MỘT trạng thái
duy nhất: lọc lặp cùng chiều (`alternating.py` ở `src/`, dùng chung, bật/tắt bằng cờ
`alternating_filter_enabled` trong config — mục 5; `macd_break` từ 2026-10-13; `wes` có bản sao riêng
`wes/src/alternating.py` vì không import `we_core`, sửa một bên phải sửa cả bên kia).
`sma_trend` thì **có trạng thái** (`position`, `exit_signal`).

- **Combo**: BUY = `close>open` & `close>ma` & `prev_close<ma` & `macd_h>0`;
  SELL đối xứng. Lọc session `session_hours_utc` (rỗng = mọi giờ). **Lọc lặp cùng chiều**
  (`alternating.py`, khôi phục 2026-10-11, cả WE lẫn wes; cờ config): chỉ bar setup ĐẦU TIÊN khi hướng
  đổi chiều bắn tín hiệu; các setup cùng chiều sau đó bị chặn cho tới khi có setup ngược (bar
  không có setup không đổi trạng thái). Entry = `high+X` (BUY) / `low−X` (SELL). Trend filter
  dùng `apply_trend_filter`: `signal` chỉ giữ khi `raw_signal == trend_bias` đúng bar đó; lọc
  xen kẽ chạy TRƯỚC trend filter (`raw_signal` đã xen kẽ; DB1 vẫn ⊆ DB0).
- **MA Cross**: BUY = `prev_fast<=prev_slow` & `fast>slow` & `macd_h>0`; cần
  `atr` không NaN; SELL đối xứng. Entry = `close`. Trend filter
  (từ 2026-09-25) dùng `_apply_trend_wait`, **riêng của ma_cross**: bản chất
  là sự kiện CẮT nên nếu trend chưa khớp đúng bar cắt thì tiếp tục chờ (miễn
  xu hướng MA chưa đảo), nổ ở bar đầu tiên MACD và trend cùng khớp; giữ
  55–75% tín hiệu gốc (cách khớp-đúng-1-bar chỉ giữ 30–47%). **Tắt trend
  filter ⇒ `signal = raw_signal` bit-for-bit.** Cột `trend_filter_status`:
  `disabled|fired|pending|no_regime`.
- **MACD Break** (chốt 2026-10-06): BUY = `close >= prior_high_close` & `macd_h > 0`;
  SELL = `close <= prior_low_close` & `macd_h < 0`. `prior_high/low_close` = đỉnh/đáy
  **close** của `LOOKBACK_BARS` (100) nến TRƯỚC, không gồm nến hiện tại (nên bar hợp lệ
  đầu tiên là bar thứ 101); bằng đỉnh/đáy cũng tính; `macd_h == 0` thì không có tín
  hiệu. MACD 5/25/5, ATR chu kỳ 5. **ATR không tham gia điều kiện tín hiệu** — chỉ
  xuất ra cột `atr` cho hệ đặt lệnh tính SL/TP (KSL/KTP × ATR). **Không có** trailing
  stop, tín hiệu thoát hay `ALLOW_SHORT`: quản lý lệnh (thoát, SL/TP, vào lại sau khi dính
  SL) thuộc hệ thống đặt lệnh, không thuộc wisdom_engine. **Lọc lặp cùng chiều**
  (`alternating.py`, thêm 2026-10-13, cùng cơ chế Combo; cờ config): chỉ nến phá đỉnh/đáy ĐẦU
  TIÊN khi hướng đổi chiều bắn tín hiệu; các nến cùng chiều sau đó bị chặn cho tới khi có
  setup ngược. Trước đó (không trạng thái) khi giá chạy mạnh nến nào cũng bắn: đo 2026-10-13
  trên 88 cặp từ 2022, 92,5% tín hiệu cùng chiều với tín hiệu liền trước, 74% nằm trong
  chuỗi ≥2 nến liền nhau, chuỗi dài nhất 15 nến. Entry tham chiếu = `close` nến tín hiệu
  (hệ đặt lệnh vào market ở nến kế tiếp). Trend filter dùng `apply_trend_filter` như Combo
  (khớp đúng 1 bar), chạy SAU lọc xen kẽ (cùng hệ quả như Combo: DB1 ⊆ DB0 nhưng DB1 vẫn
  có thể có hai tín hiệu liền nhau cùng chiều).
  `LOOKBACK_BARS` bị kẹp tối đa = `defaults.warmup_bars` (300) để không mất tín hiệu đầu
  khoảng sau khi cắt warmup. Biến tấu từ `breakout_atr` cũ (ATR10, trailing stop, có trạng thái).
- **SMA Trend** (nghiên cứu): giữ mua khi close > SMA, `ALLOW_SHORT` thì giữ bán
  khi close < SMA; BUY/SELL khi trạng thái chuyển, `exit_signal` khi trạng thái
  đổi. `StrategySpec.state_warmup_start` (2025-01-01) làm dashboard nạp dữ liệu
  từ mốc đó rồi mới cắt cửa sổ hiển thị, để tín hiệu không đổi theo cửa sổ xem.
- **Trend KNN** (`trend.py`): indicator deterministic (không phải model ML),
  chạy trên trend timeframe; `merge_trend_reference` gắn trend bar đã đóng
  (`trend_close_time <= entry bartime`). Mặc định tắt trong config.

### Công thức ATR/RMA — khớp TradingView

`atr()`/`rma()` dùng `_wilder_smooth()`: bar hợp lệ đầu seed bằng **SMA(period)**
rồi mới đệ quy `alpha=1/period`. **KHÔNG thay bằng `ewm(alpha=1/period,
adjust=False)` thuần** (seed bằng 1 điểm, lệch 2,5–4% ở bar đầu, cần +24..+81
bar mới hội tụ). `we_core/tests/test_indicators.py` khoá hành vi này.

### Thêm chiến lược mới — checklist

1. `indicator.py`: `add_<tên>_indicators`.
2. `strategies/<tên>.py`: `detect_<tên>_signals`.
3. `levels.py`: `add_<tên>_levels`.
4. `configuration.py`: `_<TÊN>_CFG` + hằng timeframe + `<TÊN>_DEFAULT_PARAMS` +
   `<TÊN>_PARAM_FIELDS` + `normalize_<tên>_params` + đăng ký `StrategySpec`.
5. `we_config.yaml` **và** `we_config.example.yaml`: mục `strategies.<tên>` (nếu chiến lược dùng lọc lặp cùng chiều: thêm
   `alternating_filter_enabled`, đọc bằng `_strategy_alternating_default` + `_normalize_alternating_param` + dòng trong
   `PARAM_FIELDS`, gọi `apply_alternating_filter` trong `detect_<tên>_signals`).
6. `we_signal/export_cli.py`: `csv_columns()`/`signal_frame()` nếu schema CSV
   khác mặc định. **Cảnh báo:** `csv_columns()` trả schema mặc định
   (`bartime, atr, signal`) cho key lạ thay vì raise; chiến lược không có cột
   `atr` (vd `sma_trend`) vẫn ra CSV nhưng cột `atr` toàn rỗng.
7. `signal_display/server.py`: `_STRATEGY_OVERLAYS` (đường overlay trên chart).
8. `run_we.sh`: dòng nhắc `Strategy (...)`.
9. Muốn worker Redis chạy chiến lược này: thêm vào `_STRATEGY_TIMEFRAMES`
   (`redis_io/worker.py`). Từ 2026-10-06 các tập timeframe CÓ THỂ giao nhau
   (`_strategies_for_timeframe()` trả list; macd_break chồng cả combo lẫn ma_cross);
   mỗi chiến lược ghi LIST riêng vì key có STRATEGY. Mọi chiến lược dùng chung tập
   symbol = key của `strategies.combo.symbol_x`.

## 7. Input → Processing → Output

**Input**: (1) SQL Server DP6 — OHLCV; (2) `we_config.yaml`; (3) trigger Pub/Sub
`df:events:backfill` — JSON `{symbol, timeframe, from?, to?}` (`from`/`to` dạng
`YYYY-MM-DD HH:MM:SS` UTC-naive, tuỳ chọn); (4) tham số CLI/HTTP.

**Processing**: mục 3 (luồng [A]/[B]).

**Output** (4 đường):

1. **CSV** (`export_cli`, mặc định `we_core/deploy/runtime/exports/`):

   ```text
   {strategy}_{symbol}_{tf}_{label}_{mode}.csv      (bản trend nằm ở trend_filter/)
   label: full_history | from_YYYYMMDD | to_YYYYMMDD | YYYYMMDD_YYYYMMDD
   mode : nt (no_trend) | tf_<trend_type>_<TREND_TF>   vd tf_knn_H4
   ```

   Cột: `combo` → `bartime, atr, entry, signal`; còn lại → `bartime, atr, signal`.
   `signal` dùng mã **`ProtoOATradeSide` của cTrader: BUY=1, SELL=2** (không phải
   1/−1 nội bộ; quy đổi đúng 1 chỗ ở `signal_frame()`). `atr`/`entry` làm tròn 2
   chữ số; `bartime` dạng `YYYY-MM-DD HH:MM:SS`. Mặc định (không `--from/--to`)
   = toàn bộ lịch sử. Có `--from` thì tải kèm warmup rồi cắt lại.
   Dashboard `/api/export` dùng cùng `signal_frame()` nhưng tên file khác
   (`..._signals_{nt|tf_*}.csv`, theo `latest_{N}bars` hoặc khoảng giờ).
2. **Redis past-signal** (worker 24/7, mục 5):
   - `LIST {key_prefix}_{SYMBOL}_{TF}_{STRATEGY}` + `HASH <list>:{bartime}`
     (1 phép nối, bartime `YYYY-MM-DD HH:MM:SS`), field = đúng cột CSV
     (giá trị chuỗi). Vd `L_PastSignal_US30_H1_COMBO`,
     `L_PastSignal_Trend_US30_H1_COMBO`.
   - DB0 = không lọc trend (`past_signal`); DB1 = có lọc trend khung H4
     (`past_signal_trend`). Cùng schema, trend chỉ đổi dòng nào có signal.
   - **Không TTL, không LTRIM** — giữ toàn bộ lịch sử từ `signal_start_date`.
     Ghi bằng pipeline transaction `DELETE + HSET + RPUSH` (ghi đè idempotent).
   - Không còn kênh Pub/Sub báo ngược (bỏ 2026-09-26: 0 subscriber).
   - VM-BO20 (ctrader-cli) đọc Redis DB0/DB1 này (trước 2026-10-14 tài liệu ghi đọc cả CSV; người dùng nói
     hiện chỉ đọc Redis db0/db1 — chưa đối chiếu với mount SSHFS ở mục 9) — **đổi schema, tên key, mã
     `signal` hay DB vẫn phải cân nhắc báo bên đó**. Thay đổi chỉ về NỘI DUNG (số tín hiệu, bật/tắt bộ
     lọc...) thì KHÔNG cần báo: người dùng khẳng định (2026-10-14) BO20 không cần biết, người dùng tự báo
     khi cần — không nhắc việc này như việc tồn đọng.
3. **Dashboard sống** (`signal_display.server`, cổng **8517**, mặc định
   `127.0.0.1`): form strategy/symbol/tf/bars/override, chart giá + MACD +
   chart trend khung lớn đồng bộ, bảng tín hiệu, nút Export CSV. Chế độ `latest`
   (N bar) hoặc `range` (`from_time`/`to_time`). Một `_DB_LOCK` tuần tự hoá truy
   vấn SQL.
4. **Chart HTML tĩnh** (`signal_display.cli`, mặc định `we_core/deploy/runtime/charts/`).

**Phạm vi worker Redis**: symbol = key của `strategies.combo.symbol_x` (11
symbol) × timeframe riêng từng chiến lược (combo: H1–H4; ma_cross: M10/M20/M30/
M45; macd_break: M10–M45 + H1–H4) = 44 + 44 + 88 = **176 cặp**, từ `signal_start_date`. Trigger ngoài phạm vi bị bỏ qua (DP
backfill cả universe rộng hơn).

**Vòng đời worker** (`redis_io/worker.py`):

1. Khởi động tiến trình → `clear_all_signals()` **đúng 1 lần** cho DB0 và DB1
   (chỉ tiền tố của ta; cờ `_CLEARED_THIS_PROCESS` phân biệt process restart
   với reconnect) → Redis sạch trước khi ghi.
2. `bootstrap_all()` full-recompute 176 cặp (từ `signal_start_date` 2022-01-01: đo 960 s ngày 2026-10-11 khi còn
   Dow — trong 756 s ghi ở các dòng `pair_refreshed`: ma_cross 627 s, macd_break 105 s, combo 24 s; sau khi
   xoá Dow chỉ còn 367 s (11/10) và 322 s (13/10); trước đó ~9–10 phút từ 2024-01-01; lúc này DB0/DB1 chưa đủ dữ
   liệu của ta) — chạy lại ở mỗi lần reconnect Redis, không clear.
3. Subscribe `df:events:backfill`. Mỗi trigger hợp lệ → `refresh_pair`:
   - có `from`+`to` → chế độ **delta**: tải `[from,to]`+warmup, tính lại, ghép vào
     dữ liệu hiện có (`_merge_delta_frame`: giữ dòng ngoài cửa sổ, thay hẳn dòng
     trong cửa sổ — kể cả khi frame rỗng, tín hiệu cũ phải biến mất);
     `from` bị kẹp không sớm hơn `signal_start_date`.
   - thiếu một trong hai → **full**: tính lại từ `signal_start_date`.
   - Mỗi pair publish 2 nơi (DB0 + DB1); DB1 tải thêm `trend_bars`.
   - Lỗi 1 pair (không phải `RedisError`) chỉ log + bỏ qua pair đó;
     `RedisError` propagate → tự reconnect sau `reconnect_delay_seconds`.
4. Heartbeat log mỗi `reconcile_interval_seconds` (1800s) — chỉ log "còn sống",
   **không quét lại dữ liệu**. Chưa có reconcile định kỳ: mất 1 message Pub/Sub
   thì chờ trigger kế hoặc lần bootstrap kế (restart/reconnect) mới bù.
5. SIGTERM chỉ set cờ `_SHUTDOWN` (không log/IO trong handler); vòng lặp tự
   thoát. Hạn chế đã biết: 1–2 dòng log cuối đôi khi không kịp ghi.
6. **Hạn chế đã kiểm chứng (2026-10-10): DB1 có thể "cũ" so với tính lại từ đầu.** Với mỗi symbol, DF phát trigger
   theo thứ tự khung nhỏ → lớn (M10, M20, M30, M45, H1, H2, H3, H4 — đo trong log, H4 đến sau M10 khoảng 8–9 giây) và chỉ
   phát sau khi nến của khung đó đã vào SQL. Chiến lược lọc trend dùng nến H4 (`TREND_TF`), nên khi một nến H4 đóng giữa
   hai đợt backfill, lần làm mới DB1 của khung nhỏ trong đợt đó đọc SQL TRƯỚC khi nến H4 mới được ghi → các nến vào
   sau giờ đóng H4 được tính với trend của nến H4 CŨ hơn. Trigger H4 chỉ tính lại `combo,macd_break` của chính khung H4
   (không tính lại `ma_cross`, và không tính lại DB1 của khung nhỏ), nên dòng sai nằm lại đến lần bootstrap kế (restart).
   DB0 (không lọc trend) KHÔNG bị. Đo thật: so toàn bộ Redis trước/sau một lần restart (2026-10-10, sau ~36 giờ và 8 đợt
   backfill): DB0 giống hệt từng byte (324.243 dòng); DB1 chỉ 1/176 cặp lệch (`FR40 M10 ma_cross`, 2 dòng trên 230.242: thiếu
   BUY 08:00, thừa SELL 10:30 ngày 09/10), và tính lại từ SQL khi thiếu/đủ nến H4 mở 04:00 tái hiện đúng cả hai trạng
   thái. Hiếm vì chỉ sai khi nến H4 mới đổi trend_bias và làm đổi quyết định tín hiệu. Cách khắc phục (chưa làm, chờ quyết
   định): khi nhận trigger H4 của một symbol thì tính lại DB1 của các khung nhỏ dùng trend H4 cho đoạn sau giờ đóng nến
   H4 đó; hoặc dùng nhịp reconcile 30 phút để tính lại DB1 của 1–2 ngày gần nhất.

**Log**: `we_core/deploy/runtime/logs/we_run.log` (DEBUG+, xoay vòng 700KB × 30 file ≈ 20MB, dọn file
>30 ngày) + journald (INFO+). Mỗi dòng: `<ts UTC> <level> component=<module.hàm>
event=<tên> pid=<pid> result=<ok|fail|skip> key=value...`.

## 8. Vận hành, test, kiểm tra

**Cài đặt** (không cài `wisdom_engine` như package):

```bash
python3 -m venv .venv
./.venv/bin/pip install pandas numpy pyyaml pyodbc flask redis        # chạy hệ thống
./.venv/bin/pip install pytest ruff vulture                           # phát triển/test
```

Từ 2026-10-10 `.venv` chỉ giữ đúng các gói trên + phụ thuộc của chúng (22 gói). Cần thêm công cụ khác (Jupyter, paramiko...)
thì tự `pip install` — không code nào của WE import chúng.

**Chạy** — tất cả từ **thư mục cha** của repo (`cd ~/Desktop`), hoặc dùng
`./wisdom_engine/run_we.sh` (menu: 1 Export CSV, 2 Open Dashboard, 3 Manage Past-Signal Worker,
4 Open WES Chart, 5 Open WES Multi-timeframe chart):

```bash
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.export_cli --strategy combo --symbol US30 --tf H1
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.export_cli --strategy ma_cross --symbol US30 --tf M30 --from 2024-01-01 --to 2024-06-01 --trend-mode trend_filter --trend-tf H4
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.signal_display.cli --strategy combo --symbol US30 --tf H1
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.signal_display.server --port 8517
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.redis_io.worker      # foreground
```

**Worker qua systemd user service** (không cần sudo; submenu 3 của `run_we.sh`
bọc sẵn, và luôn `install` lại unit + `daemon-reload` trước khi start):

```bash
install -D -m 0644 wisdom_engine/we_core/deploy/we-worker.service ~/.config/systemd/user/we-worker.service
systemctl --user daemon-reload
systemctl --user enable --now we-worker.service      # start
systemctl --user restart we-worker.service
systemctl --user status we-worker.service
journalctl --user -u we-worker.service -f
```

`Restart=on-failure`, `RestartSec=5`, `StartLimitBurst=20`/300s. Bản unit đã
cài trong `~/.config/systemd/user/` có thể lệch bản trong `we_core/deploy/` (vd sau đổi
đường dẫn repo) — luôn cài lại trước khi start. **Mỗi lần worker start là clear
+ bootstrap lại DB0/DB1 phần của ta** (mục 7) — chấp nhận được có chủ đích vì
đây là dữ liệu backtest.

**Test/kiểm tra** (pytest chạy từ thư mục cha vì import là `wisdom_engine.we_core.src.*`):

```bash
cd ~/Desktop
PYTHONDONTWRITEBYTECODE=1 wisdom_engine/.venv/bin/python -m pytest wisdom_engine/we_core/tests -q -p no:cacheprovider   # 119 test
PYTHONDONTWRITEBYTECODE=1 wisdom_engine/.venv/bin/python -m pytest wisdom_engine/wes/tests -q -p no:cacheprovider   # 145 test (cần wes/wes_config.yaml)
cd wisdom_engine
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m ruff check . --no-cache
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m vulture we_core/src we_core/tests wes/src wes/tests --min-confidence 80
```

Test **không được đụng SQL Server/Redis thật**: `we_core/tests/conftest.py` có fixture
`autouse` nạp sẵn cache `db_connector.symbols()`/`tf_minutes()` bằng dữ liệu
tổng hợp. Cần `we_config.yaml` tồn tại (import `configuration` đòi). File test:
`test_dashboard_server`, `test_db_connector`, `test_export_cli`,
`test_indicators`, `test_signal_display`, `test_strategies`. Phần lớn là
characterization/golden test — sửa logic có chủ đích thì chạy lại pipeline để
tính giá trị mới rồi cập nhật test, không đoán số. Cột không có reader
production nhưng test đang khoá giá trị (vd `entry_time`,
`trend_filter_status`) thì muốn xoá phải sửa test trước.

Vulture ở confidence 60 báo false positive: route Flask (`api_scan`,
`api_export`, `health`) và fixture autouse `_no_live_reference_data` — không
phải orphan.

## 9. Dọn dẹp

Sau mỗi lần chạy/verify bằng dữ liệu thật:

```bash
cd ~/Desktop/wisdom_engine
rm -rf .ruff_cache .pytest_cache
find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
```

**Không tự xoá `we_core/deploy/runtime/exports/`.** Đó là sản phẩm đầu ra, VM-BO20 mount qua
SSHFS và phụ thuộc trực tiếp (sự cố thật 2026-09-04: xoá nhầm 88 file CSV làm hỏng
1 lượt chạy backtest đang dở). Đường dẫn mà BO20 mount đã đổi ba lần: đổi tên repo (2026-10-08, `…/core_python/runtime/exports` → `…/wisdom_engine/runtime/exports`) rồi tái cấu trúc thư mục (2026-10-09, → `…/wisdom_engine/deploy/runtime/exports`) và dời `deploy/` vào `we_core/` (2026-10-10, → `…/wisdom_engine/we_core/deploy/runtime/exports`); KHÔNG có symlink tương thích cho BO20 (symlink `deploy` ở gốc từng là lối đi tạm cho wes, đã xoá 2026-10-11 — đừng trỏ BO20 vào `wisdom_engine/deploy`), nên mount cũ trên BO20 sẽ lỗi cho đến khi BO20 đổi sang đường dẫn mới (user sẽ yêu cầu hiệu chỉnh sau). Muốn dọn phải hỏi trước. `we_core/deploy/runtime/logs/` đã tự xoay vòng
(không dọn tay, trừ khi được yêu cầu). Redis: chỉ xoá tiền tố `L_PastSignal*`
(mục 5); chỉ `FLUSH*` khi người dùng yêu cầu rõ và đã kiểm tra `keyspace`.


## 10. `wes` (WE Simulator, trước là `ogs`) — mô phỏng engine OG trên nến do DFS phát

`wes/` (thêm 2026-10-07) là bản **mô phỏng** của engine OG live (OG18): nhận nến do DFS
phát lên Redis OG8 **db14**, chạy đúng luồng OG (combo / ma_cross / macd_break), ghi tín hiệu ra **db15**.
Hệ con tách biệt: package `wisdom_engine.wes.src.*` (không import `wisdom_engine.we_core.src`), config
riêng `wes/wes_config.yaml`.

```text
DFS (DP6, 10.11.12.6) --ghi nến--> Redis OG8 db14 --keyspace event hset--> wes --> Redis OG8 db15  L_WES_*
                                                                                  \--> Pub/Sub L_CHANNEL_SIM_*
```

### DFS — đọc từ mã nguồn DFS ngày 2026-10-07 (KHÔNG phải code của repo này)

Nằm ở `C:\Users\Administrator\Desktop\datafeed\dfs` trên DP6 (Windows), chạy bằng `dfs.bat`
(`plan`/`run`/`resume`/`status`/`stop`/`clean`). Bên khác sở hữu, không sửa từ repo này.

- Phát lại 11 symbol × 15 khung (M5, M10, M15, M20, M30, M45, H1, M90, H2, H3, H4, H6, H8, D1, W)
  = 165 cặp, giờ phát trong (2022-01-01, 2026-10-07]. SQL chỉ `SELECT`.
- **Seed** trước (không chờ): tối đa 1.200 nến đã đóng mỗi cặp. Sau đó phát từng **mốc**: nến phát đúng
  lúc đóng (giờ phát = giờ mở + độ dài khung, không có độ trễ); mọi nến cùng giờ phát = 1 mốc, sắp khung
  nhỏ → lớn rồi theo SymbolID, ghi trong MỘT MULTI/EXEC cùng `dfs:clock` và checkpoint `dfs:state`.
- Điều tốc `delay`: mốc thứ k vào lúc `t0 + k × delay_seconds` (hiện **2,0 s mỗi mốc**, ≈ 150× thời gian
  thật; đổi từ 1,0 s ngày 2026-10-09 vì ở 1,0 s wes lỡ ~3,5% tín hiệu M10). Ước tính từ SQL: 493.134 mốc ≈ 11,4 ngày
  ở 2 s (5,7 ngày ở 1 s; `dfs.bat plan` cho số chính xác). **DFS không chờ bên đọc.**
- Cấu trúc ghi: LIST `L_DFS_{SYMBOL}_{TF}` **mới nhất ở đầu**, tối đa 1.200; HASH `...:{YYYY-MM-DD HH:MM:SS}`
  (giờ MỞ nến) 6 field `timestamp/open/high/low/close/time_update` (**không có `volume`**, giá làm tròn 2 số
  lẻ, `time_update` = giờ ảo). Lua giống live ⇒ mỗi nến mới đúng 1 event `hset`; nến bị đẩy ra khỏi LIST sinh
  event `del` (wes bỏ qua).
- Guard: chỉ ghi db trong `allowed_dbs` (hiện `[14]`) và db rỗng/của DFS; `clean` chỉ `FLUSHDB` db14.
  `stop` + `resume` được (mã băm lịch phát chỉ phụ thuộc dữ liệu/khoảng thời gian, không phụ thuộc tốc độ).

### wes

- Chạy: `cd ~/Desktop && wisdom_engine/.venv/bin/python -m wisdom_engine.wes.src.redis_io.live_worker`, hoặc service
  `wes/deploy/wes-signal.service` (đã cài vào `~/.config/systemd/user/`, **chưa enable** — start tay bằng
  `systemctl --user start wes-signal`). Config thật `wes/wes_config.yaml` (gitignored; mẫu
  `wes_config.example.yaml`). Log: `wes/deploy/runtime/logs/wes_run.log` (RotatingFileHandler 5 MB × 3; từ lần khởi động 2026-10-11 02:53 UTC;
  log của các lần chạy trước nằm cùng thư mục, cũ nhất là `wes_run.log.1` ghi 2026-10-09 20:47).
- **Hai khác biệt HÀNH VI so với OG live.** (1) **Không hết hạn** (chốt 2026-10-07): bỏ cửa sổ hiệu lực
  (`signal_validity`, `valid_until`), bỏ TTL của HASH, bỏ `LTRIM` của LIST và bỏ field `expirationTimestamp`.
  Giữ `valid_from` (giờ nến đóng) và cổng "nến chưa đóng". Mọi thứ khác giữ nguyên: đọc 500 nến, chỉ xét nến
  cuối, combo = lệnh STOP / ma_cross = MARKET, chống trùng bằng "HASH đã tồn tại", Pub/Sub.
  (2) **Mỗi lần START tiến trình là xoá sạch DB tín hiệu** (chốt 2026-10-08): trước khi subscribe,
  `clear_signals()` xoá mọi key `L_WES_*` ở db15 (theo tiền tố, KHÔNG FLUSHDB) và log `event=signals_cleared`.
  Làm đúng 1 lần mỗi tiến trình — reconnect Redis trong cùng tiến trình thì không xoá lại; `validate` đã cấm
  input/output cùng DB nên không thể đụng db14. Hệ quả cần biết: mọi lần wes start lại — kể cả systemd tự
  restart sau lỗi (`Restart=on-failure`) — là mất toàn bộ kết quả đã chạy và bỏ lỡ các mốc DFS phát trong lúc
  wes chết; muốn giữ kết quả thì chép db15 ra trước khi start lại (script đọc LIST/HASH `L_WES_*`, hoặc `BGSAVE`
  cả instance — Redis OG8 đang tắt sao lưu nên phải chủ động). Các key `L_OG_*` của lần chạy trước khi đổi tên
  prefix (2026-10-09: ~19.500 key) KHÔNG bị xoá tự động vì prefix mới là `L_WES_*`; xoá tay theo tiền tố (`scan_iter`) khi
  không cần nữa.
- Phần còn lại chỉ là cách đặt lên máy: đổi đường dẫn import, `wes_config.yaml` cho OG8 (vào db14, ra db15,
  `process_on_startup: false`, tắt Discord, `channel_prefix: L_CHANNEL_SIM` để không lọt vào kênh của OG live,
  `symbol_x` đủ 11 **tên SQL**), log vào `wes/deploy/runtime/logs/`. Symbol thiếu trong `symbol_x` ⇒ combo dùng X=0 im lặng.
- **Cờ lọc lặp cùng chiều của combo và macd_break** (`strategies.<tên>.alternating_filter_enabled` trong `wes_config.yaml`, mặc định true; thêm 2026-10-14):
  bật = chỉ bắn tín hiệu đầu tiên khi hướng đổi chiều; tắt = bar nào có setup cũng bắn. `ma_cross` của `wes` không có cờ. Lúc khởi động `wes` ghi
  `event=alternating_filter strategy=combo enabled=…` và `strategy=macd_break` (cạnh `event=trend_filter`). Đổi cờ làm đổi số tín hiệu mà tên key không đổi, và restart
  `wes` xoá db15 — nên chỉ đổi khi chạy lại từ đầu. Mã: `wes/src/alternating.py` (bản sao của bản `we_core`).
- Tên key: `L_WES_{STRATEGY}_{SYMBOL}_{TF}`, STRATEGY **bỏ gạch dưới** (`COMBO`, `MACROSS`, `MACDBREAK`) — khác quy ước
  `MA_CROSS`/`MACD_BREAK` của `L_PastSignal_*`.
- **`macd_break` trong wes (đăng ký 2026-10-14):** lệnh `MARKET` (entry = close nến tín hiệu, không có `stopPrice`, giống ma_cross), chạy trên
  M10–M45 và H1–H4 (8 khung — chồng lên ma_cross và combo nên MỘT nến kích hoạt 2 chiến lược), `comment` =
  `close broke above prior highest close, MACD histogram > 0` / `close broke below prior lowest close, MACD histogram < 0`. Luật là bản sao của
  `we_core/src/strategies/macd_break.py` (sửa một bên phải sửa cả bên kia). `lookback_bars` tối đa 300 vì nến cuối chỉ có tín hiệu khi
  cửa sổ có ít nhất `lookback_bars + 1` nến (wes đọc 500). db15 vì thế có 176 LIST `L_WES_*` (88 cặp nến × 2 chiến lược).
- Hợp đồng wes cần từ DFS: key/LIST/HASH đúng cấu trúc trên (LIST mới nhất ở đầu, có OHLC), mỗi nến 1 event
  `hset`, ghi nguyên tử theo mốc. wes chỉ xử lý nến của 88/165 cặp (symbol × khung): H1–H4 (combo, macd_break) và
  M10–M45 (ma_cross, macd_break); 77 cặp còn lại bị bỏ qua gần như tức thì.
- **Giới hạn đã biết và cách xử lý (đo thật 2026-10-09):** wes xử lý tuần tự trong 1 luồng và chỉ xét **nến
  cuối** của cặp, nên nến bị "đè" nếu nến kế tiếp của cùng cặp được ghi trước khi wes kịp đọc. Chi phí đo thật mỗi
  sự kiện cần tính: đọc 500 nến ~14 ms + tính combo ~13 ms (≈27 ms) hoặc ma_cross ~13 ms (≈28 ms). Trước
  2026-10-09 ma_cross còn tính cấu trúc Dow (~40 ms, 75% thời gian tính; chỉ để dòng "SL Dow" trong tin Discord đã
  tắt, không ảnh hưởng tín hiệu/HASH — đã kiểm chứng trên 22.000 dòng, 582 tín hiệu, 0 khác biệt) nên cả sự kiện là
  ~69 ms; Dow đã bị xoá khỏi wes (và khỏi `we_core/src/` ngày 2026-10-11). Mốc nặng nhất (mọi cặp đóng cùng lúc) cần vài giây.
  Lần chạy 1,0 s/mốc (08/10, 60.000 mốc đầu) so với baseline tính lại từ SQL bằng chính mã chiến lược của wes: khớp
  98,88%, lỡ 208/18.592 tín hiệu — **203 ở ma_cross M10 (3,48%)**, 5 ở M20; combo H1–H4, M30, M45 lỡ 0; 0 sai chiều.
  Nguyên nhân: mốc `:00` là mốc nặng nhất trong giờ, hàng đợi của nó chưa xong khi M10 của mốc `:10` (2 s sau) tới
  nên bị nến M10 `:20` đè (185/203 lỡ ở phút `:10`). Cách xử lý: DFS chạy `delay_seconds: 2` và bỏ Dow; mô hình
  hàng đợi (khớp thực tế ở chi phí ×1,0–1,05) dự đoán lỡ 0, chịu được chi phí tăng ~3× — chỉ là mô hình, CHƯA kiểm
  chứng bằng lần chạy thật: sau khi chạy xong phải so db15 với tín hiệu tính lại từ SQL. Tín hiệu do reconcile
  (30 phút/lần, chặn luồng ~4,7 s) publish thường chỉ là sự kiện đang xếp hàng, không phải bị lỡ.
  **Đồng hồ OG8 nhanh hơn DP6 ~0,673 s** (cụm sự kiện DFS đến OG8 lệch hằng số, độ lệch chuẩn 5 ms): mọi độ trễ
  tính bằng cách so log hai máy phải trừ số này.
- **Trình tự chạy:** `dfs run` (seed xong thì DFS tự chạy từng mốc) → bật wes càng sớm càng tốt (mỗi giây chờ
  là 1 mốc wes không thấy; bật SAU khi seed xong để tránh bão event làm Redis ngắt subscriber). Không cần dọn
  db15 tay nữa — wes tự xoá lúc start. Tốt nhất: `dfs run` rồi ngay `dfs stop` (dừng ở mốc 0), bật wes, rồi
  `dfs resume` đúng tham số cũ thì wes thấy đủ mọi mốc. Không restart `we-worker` trong lúc chạy.
### chart_simul — trang chart sống để kiểm tra wes (thêm 2026-10-10)

`wes/src/chart_simul/` là một trang web nhỏ **chỉ đọc Redis** để tận mắt xem wes có làm đúng và kịp không:
nến từ db14 (`L_DFS_*`), tín hiệu wes từ db15 (`L_WES_*`), chỉ báo của chiến lược, cập nhật mỗi ~1 s theo nhịp
DFS. Chạy tay, KHÔNG phải service: dùng `run_we.sh` mục **4 Open WES Chart** (bật server + mở Chrome với profile riêng
`~/.cache/wes-chart-chrome`; đổi cổng bằng biến môi trường `CHART_URL`, mặc định `http://127.0.0.1:8518`), hoặc từ thư
mục cha của repo: `wisdom_engine/.venv/bin/python -m wisdom_engine.wes.src.chart_simul.server --port 8518` (mặc định
chỉ nghe 127.0.0.1). URL nhận `?strategy=&symbol=&tf=&bars=`. **Toàn bộ chữ trên chart là tiếng Anh** (yêu cầu của người
dùng 2026-10-10; comment/docstring trong code vẫn tiếng Việt) — có test khoá (`test_giao_dien_chart_hoan_toan_tieng_anh`).

- **Phạm vi chọn:** đúng các tổ hợp wes xử lý (176 = combo H1–H4 + ma_cross M10–M45 + macd_break M10–M45 và H1–H4, × 11 symbol), lấy từ
  `wes_config.yaml` (`live.enabled_strategies`, timeframe của từng chiến lược, `symbol_x`); prefix/db đọc từ cùng
  file nên đổi prefix không phải sửa code. Chỉ báo vẽ: combo = MA(`ma_period`); ma_cross = Fast/Slow; macd_break = hai đường
  `Prior high close` / `Prior low close` (đỉnh/đáy close của `lookback_bars` nến trước); cả ba kèm panel
  MACD histogram; ATR và các đầu vào của luật nằm trong khung chi tiết khi bấm vào tín hiệu.
- **6 file, một vai trò mỗi file, không kéo chéo (test kiến trúc khoá):** `source.py` (đọc Redis, chỉ lệnh đọc),
  `verify.py` (đối chiếu thuần), `payload.py` (dựng JSON thuần; chỉ import kiểu `Check` của verify), `live_page.py`
  và `mtf_page.py` (HTML/JS, lá), `server.py` (Flask, nơi DUY NHẤT nối các tầng + gọi `run_strategy` của wes). `vendor/` là bản SAO
  của lightweight-charts v4.2.1. **Không import gì từ `we_core/`** (test `test_chart_simul_khong_keo_cheo_sang_we`).
- **Trang `/mtf` — nhiều timeframe của một symbol, CHỈ nến (thêm 2026-10-11):** để kiểm tra DFS có phát nến đều và
  đúng không. Chọn 1 symbol và tối đa 4 trong **15 khung DFS phát** (M5, M10, M15, M20, M30, M45, H1, M90, H2, H3, H4,
  H6, H8, D1, W); xếp dọc từ khung lớn xuống nhỏ, mỗi chart chỉ có nến (không tín hiệu, không chỉ báo, không nến "đang
  hình thành" dựng thêm), thanh trạng thái hiện đồng hồ ảo/tick/trạng thái/run_id của DFS, mỗi nến mới thì khung đó
  chớp nhãn `NEW`. Mở bằng `run_we.sh` mục 5 hoặc liên kết "Multi-timeframe" ở trang chính; URL nhận
  `?symbol=&tfs=H4,H1,M10&bars=`. API `/api/mtf` cùng cơ chế dấu vân tay như `/api/view` (mỗi nhịp: đồng hồ + 1 lệnh
  `LINDEX` mỗi khung; chỉ khung có nến mới hoặc run_id đổi mới đọc lại cửa sổ nến). Đo trên Redis thật (4 khung × 300
  nến): lần nạp đầu 96,7 KB / 0,24 s; mỗi nhịp hỏi nhẹ 203 byte / 5,6 ms. Các chart **đồng bộ theo thời gian** (không
  theo chỉ số nến): trình duyệt giữ một khoảng thời gian chung, đổi sang khoảng chỉ số logic của từng chart; mặc định
  = 120 nến của khung nhỏ nhất, tự trượt theo nến mới khi mép phải đang ở nến mới nhất, rời mép phải thì đứng yên;
  crosshair đặt ở nến chứa thời điểm đang trỏ trên các chart kia. DFS chỉ phát nến ĐÃ ĐÓNG nên khung lớn chỉ có nến mới
  khi nến lớn đóng (đứng yên giữa hai lần đóng là đúng). **Giới hạn đã biết:** trục Lightweight Charts tính theo chỉ
  số nên khoảng nghỉ (cuối tuần, giờ nghỉ của chỉ số) làm vị trí ở GIỮA lệch nhẹ giữa các chart (hai đầu khoảng nhìn
  vẫn khớp thời gian); khung lớn có nến cuối cũ hơn mép phải chung có thể bị thư viện kẹp lại, không hiển thị nổi vùng
  trống bên phải; mỗi khung chỉ có tối đa 1200 nến (M10 ≈ 8 ngày, H4 ≈ 200 ngày). Chưa đo độ trễ nến → hiển thị theo
  mili-giây (cần luồng nghe keyspace, như ghi ở "Giới hạn" bên dưới). Đã kiểm bằng Chrome thật (CDP): kéo, zoom, crosshair,
  bám/không bám nến mới, không lỗi JS, không thanh cuộn; chưa thử trên màn hình có độ phân giải khác.
- **Nhẹ với Redis và wes:** mỗi nhịp trình duyệt gửi "dấu vân tay" (nến mới nhất, số tín hiệu, run DFS, cờ chờ);
  không đổi thì server chỉ hỏi 3–4 lệnh nhỏ (~3 ms, 171 byte); có đổi mới đọc đủ cửa sổ (≤1200 nến, ~60–100 ms).
  Server không giữ trạng thái giữa các yêu cầu.
- **Trạng thái đối chiếu** (chart tự chạy chiến lược của wes trên nến db14 rồi so với db15; chỉ xét từ khi wes bắt
  đầu ghi — suy từ tín hiệu sớm nhất trong db15 — và khi đã đủ 500 nến lịch sử như wes). Nhãn trên chart:
  `OK` khớp · `wait BUY/SELL` (bảng: "waiting for wes…") nến mới nhất có tín hiệu kỳ vọng nhưng wes chưa ghi — trình
  duyệt đổi thành `MISSED` sau 5 giây thật nếu vẫn chưa có · `MISSED` nến đã cũ hơn nến mới nhất mà wes không ghi
  (wes chỉ xét nến cuối) · `EXTRA` wes ghi mà chart không tính ra · `WRONG SIDE` ngược hướng · `unchecked` (ngoài
  vùng đối chiếu). Mọi khác biệt được kiểm lại bằng đúng cửa sổ 500 nến của wes trước khi kết luận (nhiễu hoà SMA
  ~1/12.400 không bị báo nhầm). "value mismatch" = field HASH (atr, stopPrice, comment, ...) khác với cái chart tự
  dựng bằng `build_signal`/`to_hash_fields` của wes.
- **Giới hạn:** dùng CÙNG mã chiến lược với wes nên kiểm tra việc đọc/tính/ghi và độ kịp thời, KHÔNG kiểm tra logic
  chiến lược (để tự đánh giá logic bằng mắt: khung chi tiết hiện các giá trị mà luật dựa vào). Chỉ thấy nến đã đóng
  (DFS không phát nến đang chạy) và tối đa 1200 nến (sức chứa db14); giờ trên trục là giờ ảo (UTC). Nếu
  `trend_filter_enabled` bật trong `wes_config.yaml` thì trang báo lỗi rõ (chưa hỗ trợ đường 2 timeframe).
  Chưa đo độ trễ nến → tín hiệu theo mili-giây (cần luồng nghe keyspace + Pub/Sub; chưa làm).
- Đã kiểm chứng 2026-10-10 trên dữ liệu thật: mọi cặp thử (combo H2/H4/H1, ma_cross M10/M20/M30/M45) đối chiếu sạch
  (0 lỡ/thừa/lệch); theo dõi 11 cặp ma_cross M10 trong 150 s thấy 12 tín hiệu mới hiện ngay, 1 lần bắt được
  "wait → OK" sau 0,70 s; giao diện kiểm bằng Chrome không giao diện (không lỗi JS; các màu wait/MISSED/EXTRA/OK và
  khung chi tiết kiểm bằng dữ liệu giả).
- **Nghiệm thu:** so db15 với `L_PastSignal` (DB0, trend tắt) cùng giai đoạn — wes và `wisdom_engine` cho cùng tín
  hiệu và ATR khi trend tắt (đã so 45 bộ; entry combo chỉ lệch khi `symbol_x` thiếu symbol). Giá trong Redis làm
  tròn 2 số lẻ nên có thể lệch rất nhẹ ở vài nến sát ngưỡng.
- Đã kiểm chứng 2026-10-07: 54 test hermetic + đầu cuối trên Redis thật (db12 → db13, nến ghi bằng chính Lua
  của DFS): tín hiệu của nến cách giờ máy ~9 tháng vẫn được ghi, HASH/LIST không TTL, message Pub/Sub trùng HASH,
  ghi lặp không tạo trùng, event `del` bị bỏ qua.
