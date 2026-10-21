# wisdom_engine (WE) — Kiến trúc & Quy chuẩn hệ thống

Tài liệu kiến trúc và quy chuẩn duy nhất của `wisdom_engine` (viết tắt **WE**; hệ
con mô phỏng là `des` = **Decision Engine Simulation**). Hệ thống nội bộ (SEN05), không có
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

**Đổi tên `wes` → `des` 2026-10-14** (người dùng yêu cầu: hệ con mô phỏng đặt tên lại là **DES = Decision Engine Simulation** — mô phỏng *decision engine*, chữ "WE Simulator"
cũ không còn đúng ý nghĩa): `wes/` → **`des/`**, package `wisdom_engine.wes.*` → **`wisdom_engine.des.*`**, `wes_config(.example).yaml` → **`des_config(.example).yaml`** (`.gitignore` đổi cùng lượt: file
thật chứa mật khẩu), `wes-signal.service` → **`des-signal.service`**, `wes_run.log*` → **`des_run.log*`**, profile Chrome `~/.cache/wes-chart-chrome` → `~/.cache/des-chart-chrome`, `test_wes_engine.py` →
`test_des_engine.py`, tiền tố khoá Redis db15 **`L_WES_*` → `L_DES_*`** (kênh `L_CHANNEL_SIM_*` giữ nguyên), menu `run_we.sh` "WES Chart" → "DES Chart", các token `wes`/`WES` trong mã, chart và tài liệu thành `des`/`DES`.
**Quy ước đọc file này:** các ghi chú ngày TRƯỚC 2026-10-14 ở đoạn đầu (từ "Đổi tên 2026-10-08" tới ghi chú này) giữ nguyên tên `wes` (và `L_WES_*`, `wes_config.yaml`, `wes-signal`...) vì là lịch sử đúng
tại thời điểm đó — đọc `wes` ở đó là `des`; từ mục 1 trở xuống đã dùng tên mới. Chữ "OG" (engine mà hệ con mô phỏng), `og_list_key`... KHÔNG đổi (chưa được yêu cầu).
**Đã triển khai 2026-10-14 12:26 UTC** (cùng lượt, người dùng chốt "cứ triển khai"): dựng `des/` song song từ `wes/` bằng script (thay chữ có kiểm: AST của mọi `.py` chỉ khác tên, YAML chỉ khác tên khoá/giá trị `wes`, không còn token
`wes` nào sót; giữ nguyên các từ như `newest`/`lowest`; phrase "WE Simulator" → "Decision Engine Simulation"), sửa 4 file ngoài (`run_we.sh`, `.gitignore`, 2 comment ở `we_core`), rồi chuyển dịch vụ: sao lưu db15 (169 LIST / 1.826 tín hiệu, chép ra
scratchpad), cài `des-signal.service` (đã xoá `wes-signal.service` khỏi `~/.config/systemd/user/`; cả hai đều chưa enable), dừng wes cũ, xoá 1.995 khoá `L_WES_*` ở db15 (đã kiểm db15 chỉ có tiền tố đó), chuyển log `wes_run.log*` → `des_run.log*`, bật `des-signal`
(SIGTERM cũ 12:26:05,79 → startup mới 12:26:06,78: gián đoạn ~1 s; log: `strategies=combo,ma_cross,macd_break`, `signals_cleared db=15 key_prefix=L_DES keys=0`), restart server chart bằng `wisdom_engine.des.src.chart_simul.server` (log `/tmp/des_chart.log`),
đổi tên profile Chrome (không có Chrome nào đang dùng), xoá thư mục `wes/` sau khi đối chiếu từng file với `des/` (sao lưu `wes/` ở scratchpad). Test: `we_core` 119 + `des` 145 qua; ruff/vulture sạch; import thử 40 module không lỗi.
**Chưa làm:** `main` của GitHub vẫn có `wisdom_engine/wes/` (cần đồng bộ lại khi người dùng yêu cầu); `we-worker` chưa restart (nạp mã/tên mới ở lần restart kế); graphify cập nhật phần code.

**Đổi tên `levels.py` → `entries.py` 2026-10-15** (người dùng yêu cầu, cả `we_core` lẫn `des`, "chưa cần restart, restart một lần sau"): file này chỉ điền điểm vào lệnh
(`entry_price`/`entry_time`; SL/TP không còn thuộc hệ này từ 2026-10-04) nên tên "levels" là di sản. `we_core/src/levels.py` → **`we_core/src/entries.py`** (`git mv`), `des/src/levels.py` → **`des/src/entries.py`**.
Đổi cùng lượt cho khỏi lệch tên: `add_{combo,ma_cross,macd_break,sma_trend}_levels` → **`add_*_entries`**, `_add_close_market_levels` → `_add_close_market_entries`, trường `StrategySpec.add_levels` → **`add_entries`**,
2 tên test (`…_from_strategy_levels` → `…_from_strategy_entries`, `test_ma_cross_levels_add_market_entry` → `test_ma_cross_entries_add_market_entry`), và mọi comment/docstring nhắc `levels.py` (17 file `.py`, 71 dòng).
KHÔNG đổi: tên cột `entry_price`/`entry_time`, chữ "entry/level" dùng như từ chung chỉ giá vào lệnh trong comment, schema CSV/Redis (không đụng). Đã kiểm: AST của cả 17 file khớp bản cũ sau khi bỏ docstring và ánh xạ tên (import so theo tập vì ruff sắp lại thứ tự); ruff/vulture sạch;
`we_core` 119 + `des` 145 test qua; import thử 40/40 module; module đường cũ không còn import được. **Không restart gì**: `configuration.py` là chỗ DUY NHẤT import file này (chạy ở lúc nạp), nên `we-worker`,
`des-signal`, server chart và dashboard đang chạy vẫn dùng mã đã nạp trong bộ nhớ; lần restart kế dùng tên mới. **Quy ước đọc:** các ghi chú ngày TRƯỚC 2026-10-15 nhắc `levels.py`, `add_*_levels`, `add_levels` là tên lúc đó — đọc là `entries.py`, `add_*_entries`, `add_entries`;
từ mục 1 trở xuống đã dùng tên mới. Sao lưu 17 file trước khi sửa ở scratchpad (`levels_to_entries_before`).

**Gom 3 file luật dùng chung vào `src/rules/` + đổi `trend.py` → `trend_tf.py` 2026-10-15** (người dùng yêu cầu, cả `we_core` lẫn `des`, "chưa restart, restart một lần"): `src/alternating.py` → **`src/rules/alternating.py`**,
`src/entries.py` → **`src/rules/entries.py`**, `src/trend.py` → **`src/rules/trend_tf.py`** (mỗi hệ một thư mục `rules/` riêng; `git mv` với file đã theo dõi — `we_core` entries/trend — còn lại `mv`; KHÔNG có `__init__.py`, namespace package như `strategies/`).
Lý do: ba file cùng là luật dùng chung cho mọi chiến lược, áp lên tín hiệu BUY/SELL — `alternating`, `trend_tf` quyết tín hiệu nào được đi qua, `entries` quyết vào lệnh ở giá nào — và cùng là tầng đáy (không import gì trong `src/`). Chủ ý KHÔNG đặt tên `filters/` (entries không phải bộ lọc),
`pipeline/` (đã dùng để chỉ cách `configuration.py` nối các bước), `core/` (trùng `we_core`), `signal*` (đã có `we_signal/`, `signal_display/`), `utils`/`common` (dễ thành chỗ chứa mọi thứ). `indicator.py`, `db_connector.py` ở lại `src/` (không phải luật). Tên `trend_tf.py` chỉ là tên FILE: biến/khoá `trend_tf`, `TREND_TF`,
`default_trend_tf`, cờ `--trend-tf`, hàm `trend_filter_enabled`... KHÔNG đổi (đã tra thuật ngữ: "context/trigger" chỉ là cách nói quen dùng, không phải chuẩn; "trigger" còn bị chiếm bởi tin Pub/Sub báo backfill, 27 chỗ trong mã nên không dùng cho khung vào lệnh).
Đã kiểm: AST của 19 file `.py` sửa/di chuyển khớp bản cũ sau khi bỏ docstring và ánh xạ đường dẫn module (import so theo tập vì ruff sắp lại 5 khối import, lỗi duy nhất là I001); ruff/vulture sạch; `we_core` 119 + `des` 145 test qua; import thử 40/40 module; đường cũ `…src.{trend,alternating,entries}` không còn import được ở cả hai hệ.
Trước khi đổi đã kiểm: mã sản xuất không có import chạy muộn (thụt lề) nào, test không dùng đường dẫn module dạng chuỗi, không test kiến trúc nào liệt kê file trong `src/`. **Không restart gì**: tiến trình đang chạy (`we-worker`, `des-signal`, server chart, dashboard) đã nạp mã bằng đường dẫn cũ trong bộ nhớ và không import muộn; lần restart kế dùng cấu trúc mới.
**Quy ước đọc:** các ghi chú ngày TRƯỚC 2026-10-15 nhắc `src/trend.py`, `src/alternating.py`, `src/entries.py` (và trước nữa `strategies/trend.py`, `strategies/alternating.py`, `levels.py`) là đường dẫn lúc đó — đọc là `src/rules/trend_tf.py`, `src/rules/alternating.py`, `src/rules/entries.py`;
từ mục 1 trở xuống đã dùng tên mới. Sao lưu 19 file trước khi sửa ở scratchpad (`rules_folder_before`).

**Tách khối xác nhận xu hướng (`trend_methods/`) 2026-10-15** (người dùng yêu cầu và duyệt kế hoạch trước khi làm; cả `we_core` lẫn `des`; "xử lý KNN trước, phương pháp mới đưa vào sau,
mỗi phương pháp xử lý riêng"): mục tiêu là mỗi phương pháp xác nhận xu hướng khung lớn là MỘT khối riêng, độc lập (một file), và mỗi chiến lược chọn bật khối nào, một hoặc nhiều. Giai đoạn 1 (đã làm): KNN thành khối đầu tiên +
dựng khung cho các khối sau, KHÔNG đổi tín hiệu nào. **Đã duyệt:** tên thư mục/khoá `trend_methods`; nhiều khối thì ĐỒNG THUẬN tất cả; mọi khối dùng chung một khung `TREND_TF`.
**Đổi:** `src/trend_methods/knn.py` (MỚI: `calc_ai_trend_navigator` + `_price_source` + `_target_source` chép NGUYÊN VĂN từ `indicator.py`, cùng `KNN_TREND_DEFAULT_PARAMS`, `knn_trend_indicator_params`, `build()`);
`src/trend_methods/registry.py` (MỚI: sổ đăng ký, `parse_methods`, `build_trend_frame`); `rules/trend_tf.py` (bỏ phần KNN, thêm `combine_trend_bias`; `trend_filter_enabled` và `apply_trend_filter` giữ nguyên AST);
`indicator.py` (bỏ 4 hàm KNN; `merge_trend_reference` không còn liệt kê cứng cột KNN: mang sang mọi cột `trend_*` trừ `trend_bias_<tên>` nội bộ, thứ tự cột như cũ); `configuration.py` (khoá config `trend_type` → `trend_methods`,
tham số `TREND_TYPE` → `TREND_METHODS` là tuple, bỏ hai chỗ chặn "chỉ KNN", `run_strategy_with_trend_reference` gọi `build_trend_frame`); dashboard (`server.py`, `live_page.py`: `trend_type` → `trend_methods`, payload `trendType` →
`trendMethods`, đường vẽ lấy từ `OVERLAYS` của từng khối); `export_cli.py` (`--trend-type` → `--trend-methods`, tên file ghép các khối bằng "+"); log khởi động (worker `bootstrap_start` ghi `trend_methods_<chiến lược>`, `des` ghi
`trend_methods` ở dòng `event=trend_filter`); 4 file config (bỏ `trend_type`, thêm `trend_methods: [knn]`; file thật chỉ sửa đúng khoá, quyền 600 giữ nguyên). **KHÔNG đổi:** các chiến lược, `apply_trend_filter`, `_apply_trend_wait`
của ma_cross, alternating, entries, logic worker, khoá Redis, schema CSV, logic `des/live_worker`, `chart_simul`.
**Chứng minh không đổi** (chụp bằng mã CŨ rồi so với mã MỚI, dữ liệu đầu vào lưu một lần: nến SQL của 4 symbol × 8 khung × 5.000 nến và nến Redis db14 của `des` 500 nến): 539 ca — chạy 1 khung và 2 khung (trend H4) cho 3 chiến lược,
biến thể `TREND_TF` H1/H2/H3 và tắt alternating, ca lỗi mong đợi, tham số chuẩn hoá, payload `/api/scan` và CSV của dashboard, file export CLI — băm từng cột của khung kết quả (kể cả thứ tự cột và dtype): **539/539 giống từng bit**.
Đối chứng âm (cố ý đảo hướng khối KNN trong bộ nhớ) làm 223/539 ca báo khác, nên bộ so sánh có nhạy. Dashboard kiểm bằng Chrome thật + SQL chỉ đọc: chọn Trend Filter ra "trend H4 KNN · raw 27 → filtered 3", chart khung lớn hiện,
quay về No trend thì ẩn, không lỗi JS (404 của `/favicon.ico` có sẵn từ trước). Test: `we_core` 119 → 142, `des` 145 → 166 (mỗi hệ thêm `test_trend_methods.py`: hợp đồng khối, sổ đăng ký, ghép nhiều khối bằng một khối GIẢ chỉ có trong test);
ruff/vulture sạch; import thử 44/44 module.
**Cần biết:** (1) khối thứ hai CHƯA tồn tại: phần ghép nhiều khối mới được kiểm bằng khối giả, chưa bằng dữ liệu thật, và hợp đồng khối có thể phải chỉnh nhẹ khi phương pháp thật đầu tiên đến; (2) `TREND_TYPE` truyền qua `--param`/URL nay bị BỎ QUA im lặng
như mọi khoá lạ khác (chỉ `TREND_METHODS` có tác dụng); (3) cột nội bộ mới `trend_bias_knn` chỉ nằm ở khung trend của dashboard, không ra CSV/Redis/khung vào lệnh. **Không restart gì:** `configuration.py` import lúc nạp nên `we-worker`, `des-signal`, server chart và
dashboard (8517) đang chạy giữ mã cũ trong bộ nhớ; config thật đã có `trend_methods` và đã bỏ `trend_type` nên nếu một tiến trình cũ tự khởi động lại giữa chừng sẽ nạp mã mới và config mới nhất quán. **Quy ước đọc:** ghi chú ngày TRƯỚC 2026-10-15
nhắc `trend_type`, `TREND_TYPE`, `--trend-type`, `add_knn_trend_indicators`, hay `calc_ai_trend_navigator` ở `indicator.py` / `KNN_TREND_DEFAULT_PARAMS` ở `rules/trend_tf.py` là tên/vị trí lúc đó. Mã cũ (đã che mật khẩu), baseline và bộ so sánh nằm ở scratchpad
(`trend_methods_before`, `trend_baseline.pkl`, `trend_harness.py`).

**Gộp `trend_methods/registry.py` vào `rules/trend_tf.py` 2026-10-15** (người dùng yêu cầu sau khi hỏi vì sao cần file registry riêng: phần "đăng ký" thật chỉ là 3 dòng `METHODS`, còn lại là kiểm
hợp đồng khối, parse tên khối và dựng khung trend; muốn `trend_tf.py` là cửa vào duy nhất của phần xu hướng): chuyển NGUYÊN VĂN `METHODS`, `_CONTRACT`, `bias_column`, `_check_contract`, `method_names`, `get_method`,
`parse_methods`, `build_trend_frame` vào `rules/trend_tf.py` (cả `we_core` lẫn `des`) và xoá `registry.py`; đổi import ở `configuration.py` (cả hai hệ) và `signal_display/server.py`, các docstring nhắc `registry`
(`knn.py`, `indicator.py`, `configuration.py`), và `test_trend_methods.py` (`registry.` → `trend_tf.`). **Giá đã nói trước:** `rules/trend_tf.py` là ngoại lệ DUY NHẤT của quy tắc "`rules/` là tầng đáy" (nó import
`trend_methods/*.py` và `indicator.timeframe_minutes`), nên ba chiến lược import `rules.trend_tf` gián tiếp kéo theo khối KNN và `indicator.py` — không vòng import, hành vi không đổi; mục 2 đã sửa theo. Các khối
vẫn KHÔNG được import `trend_tf.py`. Đã kiểm: cây cú pháp của cả 9 hàm khớp bản cũ từng hàm (6 từ registry + 3 từ trend_tf); ruff/vulture sạch; test `we_core` 142 + `des` 166 qua (đủ số như trước, chỉ đổi tên 2 test);
bộ baseline **539/539 giống từng bit**; import 42/42 module và import độc lập 10 module trong tiến trình riêng (bắt vòng import) đều sạch; dashboard thật (SQL chỉ đọc) trả đúng `/`, `/api/scan`, `/api/export`
và lỗi 400 cho tên khối lạ. Không restart gì. **Quy ước đọc:** ghi chú ngày trước nhắc `trend_methods/registry.py`, `registry._check_contract`, `trend_methods.registry` thì nay là `rules/trend_tf.py`.
Sao lưu 14 file trước khi gộp ở scratchpad (`registry_merge_before`).

**Tăng tốc dashboard: dựng payload theo cột + nén gzip ("Gói C") 2026-10-15** (người dùng duyệt "triển khai gói C"; phạm vi người dùng đặt từ đầu: KHÔNG sửa backend, chỉ nhóm file
`signal_display/` — nên "tính KNN một lần" bị bỏ vì phải sửa `configuration.py`, và gộp `stats` raw→filtered phía server cũng chưa làm). Đổi: (1) `signal_display/payload.py`: 4 hàm dựng chuỗi dài
(`candlestick_points`, `line_points`, `histogram_points`, `trend_line_points`) chạy theo cột bằng numpy thay cho `iterrows` (thêm `_epoch_seconds`, `_floats`, `_round2`, `_column_points`); 11 hàm/hằng còn lại giữ nguyên cây cú pháp
(đã kiểm bằng script). Hai bẫy khi vector hoá đã chặn: làm tròn phải là `round()` của Python trên từng giá trị (KHÔNG `np.round`: lệch ở ca sát nửa, `round(2.675, 2)` = 2.67 còn `np.round` ra 2.68) và màu histogram/trend
phải xét trên giá trị ĐÃ làm tròn (−0.004 → −0.0 vẫn tính ≥ 0 → xanh; 0.999 → 1.0 tính là hướng 1). (2) `signal_display/server.py`: hook `after_request` nén gzip (mức 5) JSON/HTML từ 1 KB khi trình duyệt gửi `Accept-Encoding: gzip`, luôn ghi
`Vary: Accept-Encoding`; lỗi, phản hồi nhỏ và CSV của `/api/export` không nén; muốn bỏ chỉ cần xoá dòng `app.after_request(_gzip_response)`. **Chứng minh không đổi** (bản cũ = bản chép nguyên từng byte của `payload.py` trước khi sửa, nay có bản chạy được ở
`tests/payload_reference.py`): (a) fuzz 16.500 phép gọi (11 hàm × 1.500 khung ngẫu nhiên: `bartime` ns/us/ms/s/có múi giờ/chuỗi/object/NaT/lẻ giây, số float64/float32/int/object/nullable/NaN/None/inf, giá trị sát nửa, index lạ/trùng,
thiếu cột, khung rỗng) → 0 khác; 1.197 lần cả hai cùng ném lỗi; 231 lần bản cũ ném `TypeError` (cột OHLC kiểu object chứa None) mà bản mới trả NaN — chỉ ở `candlestick_points`, SQL thật không có; 0 lần bản mới lỗi mà bản cũ không; chính bộ fuzz đã
bắt được 2 lỗi cố ý bẻ (`np.round`, tô màu theo giá trị thô) nên không phải bộ so sánh vô hiệu. (b) 292 ca `run_scan` trên dữ liệu SQL đã lưu (4 chiến lược, 4 symbol, mọi khung, 300–4.000 nến, có/không trend, tải lịch sử, khoảng thời gian) → 0 khác.
(c) 24 ca trên SQL sống (chỉ đọc; 3 chiến lược × 500/2.000/5.000/20.000 nến × trend tắt/bật) → phản hồi HTTP giống TỪNG BYTE. (d) Chrome thật (cổng tạm 18527): trang vẽ đúng, bật/tắt trend không lỗi JS (chỉ 404 `/favicon.ico` sẵn từ trước), trình duyệt
nhận bản nén (trang chủ 59 KB trên 194 KB; `/api/scan` 19 KB trên 91 KB) và giải nén đúng. **Số đo** (SQL sống, trung vị 3 chiến lược × trend tắt/bật, `/api/scan` cả request trước → sau): 500 nến 334 → 167 ms (riêng dựng payload 180 → 14 ms),
2.000 nến 949 → 263 ms (716 → 30), 5.000 nến 2.092 → 428 ms (1.829 → 64), 20.000 nến 7.867 → 1.066 ms (7.012 → 200); JSON 105 → 20 KB, 421 → 78 KB, 1.054 → 193 KB, 4.241 → 756 KB sau gzip. Nén mức 5 tốn ~1 ms (500 nến), ~7 ms (2.000), ~74 ms (20.000);
**gzip chỉ có ích khi mở dashboard qua mạng/đường hầm — cùng máy (127.0.0.1) không thấy khác biệt**, cái làm nhanh thật là dựng payload theo cột. Còn lại ở 500–2.000 nến: SQL (54–215 ms) là khâu lớn nhất; KNN còn tính hai lần (≤ 13 ms/request).
Test: `we_core` 142 → 174 (+25 so với bản lặp cũ và +7 cho gzip), `des` 166 không đổi; ruff/vulture sạch. **Không restart gì:** dashboard cổng 8517 (nếu đang chạy) giữ mã cũ trong bộ nhớ, đầu ra giống hệt; mã mới có hiệu lực ở lần restart kế.
Chi tiết đo + nguồn: `research_notes/topic_2026_dashboard-frontend-ux-standards.md` mục 4b (T004). Sao lưu trước khi sửa ở scratchpad (`package_c_before`).
**Phía `des` (người dùng hỏi có sửa đồng bộ chưa; trả lời: CHƯA sửa gì, và theo lệnh "chưa code"):** `des` không có `signal_display`; trang của nó là `des/src/chart_simul/` (payload/server riêng, không import `we_core`).
`payload.py` của nó đã dựng điểm theo cột (`unix_times` vector hoá + `zip` các cột, không `iterrows`) nên không có phần vector hoá nào cần đồng bộ; chỉ thiếu nén gzip (`/api/view` 1.200 nến ≈ 243 KB, gzip ≈ 37 KB; server trả trong ~78 ms) —
có ích chỉ khi mở qua mạng; chưa làm, chờ người dùng quyết.

**Dashboard chỉ liệt kê 11 symbol vận hành 2026-10-15** (người dùng hỏi "tại sao macd_break có cặp tiền" rồi chọn hướng A trong 3 hướng đề xuất). Nguyên nhân: ô chọn symbol lấy từ `tuned_symbols` của chiến lược, chỉ `combo` có (11 symbol); `ma_cross`/`macd_break`/`sma_trend`
để trống nên trang rơi về cả 37 symbol của `DWH.Dim_Symbol` (26 cặp forex, 9 chỉ số, vàng, BTCUSD), trong khi worker, des và CSV chỉ chạy 11 symbol (khoá `strategies.combo.symbol_x`) và nến làm tròn 2 chữ số làm các cặp giá ~1,1 thành đường phẳng
(EURUSD H1 500 nến: 457 nến O=H=L=C, chỉ 5 giá đóng cửa khác nhau; USDJPY và chỉ số bình thường). Đổi: `signal_display/server.py` thêm `_operational_symbols()` — giao của `Dim_Symbol` với `COMBO_SYMBOL_X` (import chỉ-đọc từ `configuration`) — cho danh sách symbol nhúng vào trang.
KHÔNG đổi: `live_page.py`, `configuration.py`, `tuned_symbols` của combo, và API — `/api/scan?symbol=EURUSD` vẫn chạy (chỉ danh sách trên trang bị giới hạn; CLI/CSV không đổi). Kiểm: Chrome thật (cổng tạm, SQL chỉ đọc) cả 4 chiến lược hiện đúng 11 symbol, 0 cặp forex,
0 ngoại lệ JS, mặc định vẫn BTCUSD; test mới bắt được hành vi cũ. Test: `we_core` 174 → 176 (+2); ruff/vulture sạch. Thứ tự hiển thị như trước: combo theo thứ tự config, ba chiến lược còn lại theo bảng chữ cái. Không restart gì: lúc kiểm (15/10) cổng 8517 không có tiến trình nào chạy,
lần mở dashboard kế dùng mã mới. `des/chart_simul` không đổi — nó vốn chỉ liệt kê `sorted(COMBO_SYMBOL_X)` của `des_config.yaml`.

**Đảo thứ tự lọc: trend TRƯỚC, alternating SAU (`we_core`) 2026-10-15** (người dùng yêu cầu sau khi thấy DB1 có tín hiệu liền nhau cùng chiều: "đổi ngược lại, trend trước alternating sau; tắt trend thì kết quả phải giống alternating hiện tại"; phạm vi người dùng đặt: `we_core`). Đổi: `strategies/combo.py` và
`strategies/macd_break.py` (`detect_*_signals`: `raw_signal` giữ nguyên = alternating không có trend; `signal` = `apply_trend_filter` trên setup rồi `apply_alternating_filter`; hằng `_BUY_REASON`/`_SELL_REASON`, `signal_reason` theo hướng của `signal`), docstring `rules/alternating.py` và comment `signal_display/payload.py`; `rules/trend_tf.py`,
`rules/alternating.py` (mã), `ma_cross`, worker, config KHÔNG đổi. Số đo, hệ quả (DB1 xen kẽ tuyệt đối nhưng DB1 ⊄ DB0, "trí nhớ" của alternating dài hơn cửa sổ warm-up ở 144 khoảng) và phạm vi ảnh hưởng nằm ở ⚠ mục 6. Test: `we_core` 176 → 182 (2 test "lock" viết lại, thêm test tắt trend, test khớp bản tham chiếu độc lập trên 150 khung ngẫu nhiên
× 4 tổ hợp cờ — thất bại trên mã cũ như dự kiến; test `test_trend_methods` bỏ khẳng định "tập con" vì không còn đúng, giữ khẳng định "số tín hiệu không tăng"), `des` 166 không đổi; ruff/vulture sạch. **Lúc sửa chưa restart** (`we-worker` giữ mã cũ trong bộ nhớ); đã restart cùng ngày 09:31 UTC theo yêu cầu người dùng (ghi chú kế tiếp). CSV bản không trend (`..._nt.csv`) không đổi nên không phải xuất lại; `exports/` không có CSV bản trend nào.
`des` chưa đổi (xem ⚠ mục 6). Chụp mã cũ + bộ đo ở scratchpad (`order_before`, `order_capture.py`, `order_compare.py`, `order_delta.py`).

**Restart `we-worker` 2026-10-15 09:31:22 UTC** (người dùng yêu cầu "restart worker nhé" sau khi chốt đảo thứ tự lọc; PID 384599 chạy từ 2026-10-13 11:17 → PID 524674). Quy trình: (1) kiểm keyspace chỉ-đọc: DB0 169.880 khoá, DB1 84.495 khoá, 100% là tiền tố của ta, 0 khoá lạ; `NUMSUB df:events:backfill` = 1; unit đã cài giống hệt bản trong repo (ExecStart đã là `…we_signal.redis_io.worker`);
(2) chụp nội dung DB0/DB1 ra scratchpad (169.704 + 84.319 dòng); (3) kiểm worker import được bằng mã mới trong tiến trình riêng; (4) `systemctl --user restart we-worker`. Log: `signals_cleared keys_deleted=169880 keys_deleted_trend=84495` (đúng bằng số đếm trước, không xoá nhầm khoá nào), `bootstrap_start … alternating_combo=True alternating_macd_break=True trend_methods_*=knn`,
**`bootstrap_done` 328,8 s, 352/352 lần ghi, 0 WARNING/ERROR**, đã subscribe lại kênh (`NUMSUB` = 1); Redis ~109 MB (không đổi). Lần restart này cũng áp dụng MỌI thay đổi đang chờ từ 14–15/10 (đổi tên package `og_signal` → `we_signal`, `strategies/` → `rules/`, `levels` → `entries`, khối `trend_methods` + khoá config `trend_methods`, thứ tự lọc mới):
**các ghi chú ngày trước nói "`we-worker` CHƯA restart / còn chạy mã cũ" đã hết hiệu lực từ lúc này.** **Đối chiếu sau restart** (chụp lại Redis, so với chụp trước và với kết quả tham chiếu tính độc lập từ SQL cắt tại 2026-10-14 00:00, các dòng có bartime < T):
(a) DB0: giống hệt 176/176 LIST, số dòng y nguyên (combo 35.845, macd_break 29.805, ma_cross 104.054); (b) DB1 `ma_cross` (mã không đổi): giống hệt 44/44 LIST (61.402 dòng); (c) DB1 `combo` + `macd_break` SAU restart == tham chiếu "trend trước, xen kẽ sau" **132/132 cặp**; DB1 TRƯỚC restart == tham chiếu "xen kẽ trước, trend sau" 132/132 cặp
(nên bộ đo khớp worker thật từng dòng); DB0 == tham chiếu 132/132; (d) DB1 combo 8.732 → 4.840 (−44,6%), macd_break 14.185 → 18.771 (+32,3%); **cặp tín hiệu liền nhau cùng chiều trong DB1: combo 5.175 → 0, macd_break 4.441 → 0** (tính trên mọi dòng, kể cả sau T), DB0 combo/macd_break vốn 0; ma_cross DB0/DB1 không đổi (16.829 / 39.615 cặp cùng chiều, ma_cross không dùng alternating);
(e) dòng có ở cả TRƯỚC lẫn SAU: 13.767, giống hệt mọi field (atr, entry, signal); chỉ ở TRƯỚC 9.150 (bị bộ lọc mới loại), chỉ ở SAU 9.844 (trước đây bị chặn); (f) DB1 ⊄ DB0 đúng như đo: tín hiệu DB1 không có ở DB0 combo 28,1% (1.358/4.840), macd_break 45,2% (8.486/18.771), gộp 41,7% (9.844/23.611 — đúng bằng số "chỉ ở SAU"),
không cặp nào DB1 nhiều hơn DB0; ma_cross DB1 vốn không ⊆ DB0 (30,4%, cơ chế chờ trend bắn ở nến sau — không đổi); (g) schema không đổi (combo: atr, bartime, entry, signal; còn lại: atr, bartime, signal). **`des` không bị ảnh hưởng:** `des_run.log` 0 WARNING/ERROR từ mốc restart và vẫn phát tín hiệu đều; quét chart của 176 tổ hợp:
2.726 `OK`, 0 `missed`, 0 `extra`, 0 `wrong_side`, 0 `fieldDiff` (1.312 `unchecked` nằm ngoài vùng đối chiếu, như mọi lần). Hạn chế đã nêu (144 khoảng setup-qua-trend > 300 nến warm-up; ~30 sự kiện/năm, 0,61% tín hiệu DB1 có thể bắn thừa ở lần làm mới delta, nằm lại tới lần restart kế) **người dùng chấp nhận, KHÔNG nâng warm-up** — tự hết ở mỗi lần restart.
Chưa làm: đồng bộ thứ tự lọc sang `des`; CHƯA đo thứ tự nào tốt hơn về P&L. Việc báo VM-BO20 do người dùng tự quyết (nội dung DB1 combo/macd_break đổi: −44,6% / +32,3%). Bản chụp Redis trước/sau + bộ đối chiếu ở scratchpad (`redis_before.pkl`, `redis_after.pkl`, `redis_snapshot.py`, `redis_compare.py`).

**Đổi khung trend mặc định của `combo` H4 → H1 2026-10-16** (người dùng quyết định sau khi xem thống kê khung trend: "để lọc trend bằng default là H1", "chưa cần restart we"; phạm vi = `combo`: `ma_cross` và `macd_break` GIỮ H4 vì chưa có thống kê/quyết định cho chúng — `ma_cross` sắp được bàn riêng): `strategies.combo.default_trend_tf` H4 → **H1** trong `we_core/we_config.yaml` (quyền 600 giữ nguyên) và `we_config.example.yaml`; `trend_timeframes` (H1–H4) và `trend_methods: [knn]` không đổi. Sửa cùng lượt để khỏi lệch: docstring `refresh_pair` ở `we_signal/redis_io/worker.py` (bỏ chữ "mặc định H4" vì mỗi chiến lược có khung riêng; chỉ đổi docstring), và 3 chỗ test khoá giá trị H4 của combo: `test_strategies.py::test_combo_trend_config_has_only_strategy_level_switches` (`TREND_TF == "H1"`), `test_export_cli.py::test_export_signals_names_file_with_trend_filter_defaults` (tên file `…_tf_knn_H1.csv`, hai lần tải H1/H1, `trend_tf == "H1"`) và `…::test_resolve_trend_reference_uses_config_methods_and_rejects_unknown_ones`. Test: `we_core` 182 qua, `des` 166 qua, ruff/vulture sạch; config thật nạp ra `combo` H1, `ma_cross` H4, `macd_break` H4. **Số liệu căn cứ** (SQL SELECT, 11 symbol, từ 2022-01-01 đến 2026-10-14; F129–F134 trong `research_notes/topic_2026_ms-fractal-trading-range-trend.md`): tín hiệu `combo` DB1 sẽ từ 4.835 (trend H4) lên 11.997 (+148%); so với DB0 (35.807) giữ 33,5% thay vì 13,5%, so với không lọc gì (53.866) giữ 22,3% thay vì 9,0%; trung bình mỗi symbol mỗi năm theo khung vào lệnh H1/H2/H3/H4: 69 / 67 / 52 / 40 (trước: 43 / 16 / 15 / 18); trend H1 là khung lọc YẾU nhất trong bốn khung (chỉ có số lượng, CHƯA đánh giá chất lượng/lãi-lỗ). **KHÔNG restart (theo yêu cầu): `we-worker` (PID 524674, chạy từ 2026-10-15 09:31:22 UTC) vẫn giữ trend H4 trong bộ nhớ nên DB1 combo trên Redis vẫn là bản H4 cho tới lần restart kế** (hoặc lần systemd tự khởi động lại sau sự cố, khi đó worker nạp config mới và bootstrap lại); khi restart, bootstrap ~5,5 phút và DB1 combo đổi nội dung (số trên) mà tên key không đổi; DB0, DB1 ma_cross/macd_break không đổi. Dashboard (8517) và CLI export dùng khung mới ngay từ lần chạy kế (CSV bản trend của combo có hậu tố `tf_knn_H1`; `exports/` hiện không có CSV bản trend). Khi khung trend = khung vào lệnh (combo vào H1) code tải cùng dữ liệu hai lần — không sai, chỉ thừa một truy vấn SQL. `des` KHÔNG đổi (`des_config.yaml` giữ H4, trend tắt). **Quy ước đọc:** ghi chú ngày TRƯỚC 2026-10-16 nhắc "trend H4" cho `combo` là cấu hình lúc đó.

**Đổi luật `ma_cross` sang luật trạng thái + khung trend mặc định H2 (`we_core`) 2026-10-16** (người dùng chốt sau khi xem số đo: "1. Chốt đổi luật, 2. H2, 3. Chưa" — `des` chưa đồng bộ; và "sau khi xong cũng chưa cần restart"). Luật mới: BUY ở mọi nến có SMA13 > SMA34 và MACD hist > 0, SELL ở mọi nến có SMA13 < SMA34 và MACD hist < 0 (mỗi nến thoả là một setup), rồi cùng khuôn `combo`/`macd_break`: trend lọc TỪNG setup, lọc lặp cùng chiều SAU trên các setup đã qua trend. Lý do người dùng nêu: luật cũ "viết khá lòng vòng" (DB0 = sự kiện cắt + MACD cùng nến; DB1 = máy trạng thái chờ `_apply_trend_wait` → hai luật khác nhau, DB1 ⊄ DB0 ~30%, ~65% cặp tín hiệu liền nhau của DB1 cùng chiều, ma_cross là chiến lược duy nhất chưa chống trùng). **Đổi:** `strategies/ma_cross.py` viết lại (bỏ `_apply_trend_wait`, bỏ sự kiện cắt; hằng `_BUY_REASON`/`_SELL_REASON`); `indicator.add_ma_cross_indicators` bỏ 2 cột `prev_fast_ma`/`prev_slow_ma` (hết người đọc); `configuration.py` thêm cờ `ALTERNATING_FILTER_ENABLED` cho ma_cross (default + `MA_CROSS_PARAM_FIELDS` + normalize, giống combo/macd_break); `we_config.yaml` (quyền 600 giữ nguyên) và `we_config.example.yaml`: `strategies.ma_cross.alternating_filter_enabled: true` và `default_trend_tf` H4 → **H2**; docstring `rules/alternating.py`. Worker không phải sửa (log `bootstrap_start` tự thêm `alternating_ma_cross`). **Test:** `we_core` 182 → 187 (xoá `test_ma_cross_requires_macd_histogram_confirmation` và `test_ma_cross_trend_wait_…`; thêm 7 test: setup theo trạng thái, không còn cột SMA kỳ trước, nhiều nến liền nhau chỉ bắn nến đầu, khoá thứ tự trend→chống trùng, trend tắt ⇒ `signal == raw_signal`, tích hợp trend trên khung nến, golden cờ tắt; ma_cross cũng vào phép so với bản tham chiếu độc lập trên 150 khung ngẫu nhiên × 4 tổ hợp cờ; golden dữ liệu mẫu tính lại: 10 tín hiệu, cờ tắt 128; 2 test cờ/config và 1 test log của worker sửa theo); `des` 166 không đổi; ruff/vulture sạch. **Số đo** (T007, F135–F141 trong research_notes; SQL SELECT, 44 cặp M10–M45, 2022-01-01 → 2026-10-14; luật cũ dựng lại nguyên văn khớp Redis 15/10 trong ±0,1%: DB0 103.956 so với 104.054, DB1 trend H4 61.342 so với 61.402): setup thô 1.581.660 = 42% số nến; chỉ trend (chưa chống trùng) vẫn 23–32% số nến → chống trùng BẮT BUỘC; luật mới DB0 110.813 (+7% so với cũ); DB1 theo khung trend H1 / H2 / H3 / H4 = 72.421 / 45.631 / 31.603 / 23.863 (luật cũ: 86.384 / 72.607 / 65.315 / 61.342 → −16% / −37% / −52% / −61%); mỗi năm mỗi cặp 344 / 217 / 150 / 113 (luật cũ + H4: 291). Tín hiệu ≤ số lần trend đổi hướng + 1 ở 176/176 (bằng 60% / 75% / 78% / 78% số lần đổi hướng); 0 cặp liền nhau cùng chiều; khoảng cùng chiều dài nhất giữa hai setup đã qua bộ lọc 244 nến (M10, trend H4) và 177 với H2, 0 khoảng vượt 300 nến warm-up. **Kiểm chứng mã thật:** mã sản phẩm sau khi đổi khớp số đo 220/220 phép (44 cặp × DB0 + 4 khung trend), kèm bất biến (trend tắt ⇒ `signal == raw_signal`, `raw_signal` không phụ thuộc trend, không cặp cùng chiều liền nhau, DB1 ≤ DB0, entry = close, mọi tín hiệu DB1 cùng chiều `trend_bias`); dashboard (`run_scan` ma_cross US30 M30: 39 tín hiệu không trend, 25 có trend; trang có ô "Alternating Filter" cho ma_cross) và export CSV (thư mục tạm, 3 cột, hậu tố `tf_knn_H2`) chạy đúng. **Lưu ý đo:** lần đo đầu cache KNN theo `id()` của DataFrame nên vài ô (UK100) lấy nhầm khung trend của symbol khác; đã sửa khoá cache và đo lại — số cuối cùng ở trên là bản đã sửa (số báo miệng sớm hơn trong ngày lệch ≤ 3%: ví dụ DB1 H2 44.628 → 45.631). **KHÔNG restart (theo yêu cầu): `we-worker` (PID 524674, từ 2026-10-15 09:31:22 UTC) vẫn chạy mã/config CŨ trong bộ nhớ**, nên DB0/DB1 `ma_cross` và DB1 `combo` trên Redis vẫn là bản cũ; khi restart (bootstrap ~5,5 phút, gom cùng thay đổi combo H1 ngày 2026-10-16): DB0 ma_cross ~104.054 → ~110.8k, DB1 ma_cross ~61.402 (luật cũ, trend H4) → ~45.6k (luật mới, trend H2), DB1 combo ~4.8k → ~12.0k (trend H1); key không đổi, `macd_break` không đổi (BO20 do người dùng tự báo). **`des` CHƯA đồng bộ** (theo yêu cầu): `des/src/strategies/ma_cross.py` vẫn luật cắt + khớp đúng 1 nến, chưa có chống trùng; hệ quả: db15 của des cho ma_cross sẽ KHÔNG còn khớp DB0 của we_core cho tới khi đồng bộ, và engine live DE (ngoài repo) cũng cần đổi tương ứng kẻo backtest ≠ live. **Quy ước đọc:** các ghi chú ngày TRƯỚC 2026-10-16 nhắc `_apply_trend_wait`, `prev_fast_ma`, "ma_cross không có cờ / chưa dùng bộ lọc", "cơ chế chờ trend", "DB1 ma_cross ⊄ DB0 (30,4%)" hay 16.829 / 39.615 cặp cùng chiều là về luật CŨ. Bản chụp trước khi sửa: không lưu file (luật cũ nằm trong git index/HEAD của nhánh và trong ghi chú này).

**Restart `we-worker` 2026-10-16 02:32:46 UTC** (người dùng yêu cầu "hãy restart we" sau khi chốt đổi luật `ma_cross` + khung trend H2 và `combo` H1; PID 524674 chạy từ 2026-10-15 09:31:22 → PID 562541). Quy trình: (1) kiểm keyspace chỉ-đọc: DB0 169.979 khoá, DB1 85.237 khoá, 100% là tiền tố của ta, 0 khoá lạ; `NUMSUB df:events:backfill` = 1; unit đã cài giống hệt bản trong repo; worker nạp được mã mới trong tiến trình riêng (176 cặp; combo H1, ma_cross H2, macd_break H4; cờ chống trùng cả ba = true); (2) chụp nội dung DB0/DB1 ra scratchpad (169.803 + 85.061 dòng, 02:32:33 UTC); (3) `systemctl --user restart we-worker`. Log: `signals_cleared keys_deleted=169979 keys_deleted_trend=85237` (đúng bằng số đếm trước, không xoá nhầm khoá nào), `bootstrap_start … alternating_combo=True alternating_ma_cross=True alternating_macd_break=True trend_methods_*=knn`, **`bootstrap_done` 349,8 s (02:38:38 UTC), 352/352 lần ghi, 0 WARNING/ERROR**, đã subscribe lại (`NUMSUB` = 1, NRestarts = 0); Redis ~114 MB (không đổi). Lưu ý đọc log: `we_run.log` xoay vòng ngay giữa lúc bootstrap nên phải gộp `we_run.log` và `we_run.log.1` mới đếm đủ 352 lần ghi. **Đối chiếu sau restart** (chụp 02:39:36 UTC, so với chụp trước và với tham chiếu tính độc lập từ SQL bằng mã chiến lược sản phẩm, các dòng bartime < 2026-10-16 00:00): (a) KHÔNG đổi, giống hệt từng dòng: DB0 combo 44/44, DB0 macd_break 88/88, DB1 macd_break 88/88 LIST; (b) ĐỔI và khớp tham chiếu từng dòng/field: DB0 ma_cross 44/44, DB1 ma_cross 44/44, DB1 combo 44/44 LIST; (c) số dòng trước → sau: DB0 ma_cross 104.105 → 110.971 (+6,6%), DB1 ma_cross 61.432 → 45.715 (−25,6%), DB1 combo 4.842 → 12.017 (+148%); DB0 combo 35.873, DB0 macd_break 29.825, DB1 macd_break 18.787 giữ nguyên; (d) cặp tín hiệu liền nhau cùng chiều = 0 ở cả 6 nhóm (DB0/DB1 × 3 chiến lược; ma_cross trước đó 16.806 / 39.509); schema field không đổi (combo: atr, bartime, entry, signal; còn lại: atr, bartime, signal); DB1 không bao giờ nhiều hơn DB0 (0/176 LIST); tín hiệu DB1 không có (cùng hướng, cùng nến) ở DB0: combo 23,9%, ma_cross 69,6%, macd_break 45,2%. `des` không bị ảnh hưởng: `des_run.log` 0 WARNING/ERROR từ lúc restart, `des-signal` vẫn chạy. **Dashboard cổng 8517 (PID 526383, người dùng bật 2026-10-15 10:13:38) KHÔNG restart (không có yêu cầu) và đang giữ mã/config CŨ trong bộ nhớ**: `ma_cross` luật cũ, trend mặc định combo/ma_cross H4, chưa có ô chống trùng cho `ma_cross` — muốn xem luật/khung mới phải restart nó (server không giữ trạng thái; mở lại bằng `run_we.sh` mục 2 hoặc `python -m wisdom_engine.we_core.src.signal_display.server`). Server chart des (8518) vẫn mã cũ nhưng không liên quan. **Quy ước đọc:** các ghi chú ngày 2026-10-16 phía trên nói "`we-worker` chưa restart / DB1 vẫn bản cũ" đã hết hiệu lực từ 02:32:46 UTC; ghi chú nói `des` chưa đồng bộ `ma_cross` vẫn còn hiệu lực. Bản chụp Redis trước/sau + bộ đối chiếu ở scratchpad (`redis_before2.pkl`, `redis_after2.pkl`, `redis_snapshot.py`, `redis_compare2.py`).

**Xoá `sma_trend` khỏi `we_core` 2026-10-16** (người dùng: "xoá sma_trend khỏi hệ thống we_core, tôi sẽ dùng đúng 3 chiến lược hiện tại trước thôi"): sổ đăng ký chỉ còn `combo`, `ma_cross`, `macd_break`; `strategies/` còn đúng 3 file. **Xoá:** `strategies/sma_trend.py`; `indicator.add_sma_trend_indicators`; `rules/entries.add_sma_trend_entries`; trong `configuration.py`: import, `_SMA_TREND_CFG`, `SMA_TREND_*`, `normalize_sma_trend_params`, mục đăng ký `STRATEGIES["sma_trend"]` và hai hàm phụ chỉ nó dùng (`_timeframes`, `_reject_trend_filter`); mã hỗ trợ chiến lược CÓ TRẠNG THÁI mà không ai khác dùng: trường `StrategySpec.state_warmup_start` và, ở `signal_display/server.py`, `_extend_to_state_warmup` (cùng đoạn cắt phần khởi động trạng thái trong `_load_and_run`) + import `load_range` hết dùng; overlay `_STRATEGY_OVERLAYS["sma_trend"]`; khối `strategies.sma_trend` trong `we_config.yaml` (quyền 600 giữ nguyên) và `we_config.example.yaml`; câu chữ "or sma_trend" ở help của `export_cli`/`signal_display.cli` và prompt của `run_we.sh`. **Không đụng:** `des` (không có sma_trend; chỉ sửa 1 chú thích trong `des/tests/test_chart_simul.py`, test vẫn dùng khoá "sma_trend" làm ví dụ chiến lược lạ → 400), `research_notes` (lịch sử; thêm 1 dòng ghi chú ở đầu `scripts/t001_sideway_regime_test.py`), `exports/` (không có file sma_trend nào). **Kiểm:** cây cú pháp (bỏ docstring) của 6 file mã sửa chỉ khác đúng các nút của sma_trend; ruff/vulture sạch; `we_core` 187 → 188 test (sma_trend vốn không có test riêng; sửa 2 test cờ/config và thêm `test_chi_con_ba_chien_luoc_sma_trend_da_xoa_khoi_he_thong`, thêm 1 assert vào test trang chủ dashboard), `des` 166; `get_strategy("sma_trend")`/`run_strategy`/`export_signals` báo `KeyError: Unknown strategy 'sma_trend'. Available: combo, ma_cross, macd_break`; dashboard thật (SQL chỉ SELECT) chạy đúng cả 3 chiến lược có/không trend; worker nạp đủ 176 cặp. **Không restart gì:** `we-worker` (PID 562541) và dashboard 8517 (PID 526383) vẫn giữ mã cũ trong bộ nhớ (worker chưa bao giờ chạy sma_trend; dashboard cũ còn liệt kê "SMA Trend (research)" tới khi restart); lần restart kế nạp mã/config mới nhất quán. **Khôi phục nếu cần:** `git show HEAD:src/strategies/sma_trend.py` (và `add_sma_trend_indicators` trong `src/indicator.py`, `add_sma_trend_entries` trong `src/levels.py` ở HEAD); khối config: `sma_period: 200`, `allow_short: true`, `supported_timeframes: [M10…H4]`, `default_timeframe: H4`, `state_warmup_start: "2025-01-01"`. Bản chụp trước khi xoá ở scratchpad (`sma_trend_removal_before`). **Quy ước đọc:** các ghi chú ngày TRƯỚC 2026-10-16 nhắc `sma_trend`, "4 chiến lược", `state_warmup_start` là về mã đã xoá.

**Rà soát `rules/` + `indicator.py` 2026-10-16** (người dùng đang rà từng file chức năng của we_core; chỉ đọc/đo, KHÔNG sửa mã): **Đúng như mô tả, không thấy lỗi logic.** Kiểm trên dữ liệu thật (US30 H1 8.000 nến): `sma/ema/wma/rma/macd_hist/atr` và `add_*_indicators` đều NHÂN QUẢ (tính trên tiền tố == tính trên toàn chuỗi rồi cắt, 0 lệch) và khớp cài đặt độc lập bằng numpy/vòng lặp thuần (ATR = Wilder seed SMA, TR nến đầu = high−low, đúng kiểu TradingView); `merge_trend_reference` vét cạn 7.703 nến: 0 nhìn trước, luôn đúng nến trend ĐÓNG MỚI NHẤT, đúng hướng; `indicator.timeframe_minutes` khớp `Dim_Timeframe` của SQL ở cả 15 mã; đồ thị import không vòng và không vi phạm quy tắc tầng của mục 2 (`indicator`, `rules/alternating`, `rules/entries` không import gì trong `src/`; `rules/trend_tf` chỉ import `indicator` + `trend_methods`; khối `trend_methods` không import `rules`/`strategies`); vulture 60% không báo gì trong 4 file; KNN không phải điểm nghẽn bootstrap (~47 s trong 350 s; `wma(5)` trên 29.000 nến H1 = 0,06 s); dashboard chế độ "N nến cuối" (không warm-up) chỉ lệch 0–3 nến ở mép trái cửa sổ so với tính toàn lịch sử. **Điểm quan trọng nhất — quy ước ghép trend là tham số ẩn rất nhạy:** `merge_trend_reference` lấy trend bar có `trend_close_time <= giờ MỞ nến vào lệnh` (nguyên văn từ commit đầu 2026-09-11; mã order_gateway live cũ OG8/OG18 trong git và `des` dùng y hệt; DE hiện tại chưa kiểm vì cần SSH). Hệ quả: hướng trend luôn trễ ít nhất một nến vào lệnh; vào H4 + trend H1 thì 4 nến H1 nằm TRONG nến H4 bị bỏ qua. Đo `combo`, trend H1, 11 symbol từ 2022: nếu đổi sang `<= giờ ĐÓNG nến vào lệnh` thì hướng trend khác ở 9,9% / 16,6% / 22,5% / 28,1% số nến (vào H1 / H2 / H3 / H4) và số tín hiệu DB1 3.629 → 11.293 (+211%), 3.519 → 7.831 (+123%), 2.728 → 5.530 (+103%), 2.121 → 4.256 (+101%). Mọi số về khung trend (F129–F134 trong research_notes) đều ĐIỀU KIỆN theo quy ước hiện tại. **Cột ghi mà không ai đọc (trái nguyên tắc mục 2):** `entry_time` (ghi 4 chỗ ở `entries.py`, không src/test nào đọc), `raw_signal_reason` (3 chiến lược ghi, không ai đọc — des mới dùng làm `comment`), `trend_filter_status` (chỉ test đọc), `sl_price`/`tp_price`/`risk_reward` (luôn NaN; chỉ được chép thành null vào JSON chi tiết tín hiệu và giao diện còn in "SL - TP - R:R -"; `des/src/rules/entries.py` đã bỏ 3 cột này). **Trùng lặp:** `add_ma_cross_entries` ≡ `_add_close_market_entries` (kiểm trên dữ liệu thật: giống từng ô); "đọc cờ bool" có 3 bản (`trend_filter_enabled`, `alternating_filter_enabled`, `configuration._to_bool`); khối nối bộ lọc 20 dòng ×3 ở strategies. **Lệch tài liệu/vệ sinh:** docstring `atr()` ghi "EWM … min_periods" nhưng mã là Wilder seed SMA; có 1 ký tự TAB lạ trong docstring đầu `indicator.py`; `indicator.py` còn chứa `merge_trend_reference` và `timeframe_minutes` (không phải chỉ báo) — cũng là lý do `rules/trend_tf.py` phải import `indicator` (ngoại lệ tầng); `method_names()` chỉ test dùng; `wma` và `timeframe_minutes` không có test trực tiếp; `apply_trend_filter` mặc định `raw_signal`/`signal` không còn nơi nào dùng (strategies luôn truyền tên cột riêng). `des` so với we_core: `alternating.py` và `trend_tf.py` giống từng hàm; `indicator.py` chỉ khác `add_ma_cross_indicators`; `entries.py` khác (des không có cột SL/TP) — đúng như đã ghi. Chưa sửa gì; chờ người dùng quyết.

**Dọn cột chết + gộp mã trùng + sửa tài liệu 2026-10-16** (người dùng: "thực hiện 2 và 3" sau vòng rà `rules/` + `indicator.py`; mục (a) cách ghép trend CHƯA quyết, còn chờ). **Bỏ cột không ai đọc:** `entry_time`, `sl_price`, `tp_price`, `risk_reward` (`rules/entries.py` giờ chỉ tạo `entry_price`; `bartime` không còn nằm trong tập cột bắt buộc của hàm entry), `raw_signal_reason` (3 chiến lược); ở dashboard bỏ khoá `sl`/`tp`/`rr` của bảng tín hiệu (`payload.signal_table_rows`) và chữ "SL - TP - R:R -" ở dòng chi tiết tín hiệu (`live_page.py`; vẫn còn Entry và lý do). Giữ `trend_filter_status` (test đọc, người dùng không yêu cầu bỏ). **Gộp mã trùng:** `add_ma_cross_entries` gọi `_add_close_market_entries` (như macd_break); ba bản "đọc cờ bool" gộp thành `flags.py` MỚI (tầng đáy: `to_bool`, `flag_enabled`) — `configuration._to_bool` bị xoá và 4 chỗ gọi đổi sang `to_bool`, `trend_filter_enabled` và `alternating_filter_enabled` còn là hàm một dòng gọi `flag_enabled`; `rules/alternating.py` nay import `flags.py` (cập nhật câu "không import gì trong src/" ở mục 2 và docstring `trend_tf.py`). **Sửa tài liệu:** docstring `atr()` (Wilder seed SMA, TR nến đầu = high−low, `period−1` nến đầu NaN) và ký tự TAB lạ ở đầu `indicator.py`. **Chứng minh không đổi hành vi (dữ liệu SQL thật, 3 symbol × 6 ca chiến lược/khung × DB0 và DB1, chụp trước rồi sau):** 36/36 khung enriched giống hệt từng ô ở mọi cột còn lại (đúng thứ tự cột, chỉ mất 5 cột bỏ); 36/36 `signal_frame` (phần đi ra CSV/Redis) giống hệt, 11.488 dòng tín hiệu; 36/36 JSON `run_scan` của dashboard giống hệt sau khi bỏ khoá sl/tp/rr (1.424 dòng bảng tín hiệu trước đó có 3 khoá toàn null, 0 sau đó); cây cú pháp: các hàm đổi chỉ khác đúng phần dự định (đổi tên `_to_bool`→`to_bool` ở 4 hàm, bỏ 3 câu lệnh `raw_signal_reason` ở 3 chiến lược, bỏ 3 khoá ở `signal_table_rows`); fuzz 20.000 giá trị ngẫu nhiên (bool/None/số/chuỗi hoa-thường-khoảng trắng/list/dict/bytes/NaN): bản gộp và ba bản gốc lệch 0 lần. **Test:** `we_core` 188 → 245 (thêm `test_flags.py` 55 ca gồm kiểm ba bộ đọc cờ cùng một quy tắc và cùng `normalize_*_params`, thêm 2 test strategies: entries chỉ còn `entry_price` + ma_cross/macd_break chung hàm, và chiến lược không còn `raw_signal_reason`; bỏ các assert `sl_price`/`tp_price`/`risk_reward` ở 5 chỗ, đổi tập khoá bảng tín hiệu), `des` 166 giữ nguyên; ruff/vulture sạch. **`des`:** KHÔNG sửa (có bản sao riêng và vốn đã không có cột SL/TP); `des/src/rules/{alternating,trend_tf}.py` và `configuration` của des vẫn tự đọc cờ bằng mã inline — hành vi y hệt (fuzz ở trên), chỉ khác hình thức so với we_core cho tới lần đồng bộ des kế. **Không restart gì:** `we-worker` (PID 562541) và dashboard 8517 (PID 526383) giữ mã cũ trong bộ nhớ (đầu ra DB0/DB1/CSV không đổi nên không cần); dashboard cũ còn in "SL - TP - R:R -" tới khi restart. **Quy ước đọc:** ghi chú rà soát cùng ngày nói "chưa sửa gì" đã hết hiệu lực với mục (b)(c)(d); các ghi chú cũ nhắc `entry_time`/`sl_price`/`raw_signal_reason`/`_to_bool` là về mã đã bỏ. **Riêng việc gộp ba bản đọc cờ thành `flags.py` đã được HOÀN NGUYÊN ngày 2026-10-16 (người dùng: file thừa) — xem ghi chú "Bỏ flags.py và đồng bộ des theo we_core": `_to_bool` và hai hàm đọc cờ nằm lại ở `configuration.py`, `rules/trend_tf.py`, `rules/alternating.py`.**

**Đo độ nhạy quy ước ghép trend + so thứ tự lọc trên cấu hình hiện tại 2026-10-16** (người dùng: "quay trở lại phân tích về việc ghép trend, tôi cần hiểu rõ hơn", rồi "nếu lọc alternating trước rồi mới lọc trend sau thì signal sẽ thế nào? So sánh"; chỉ đọc/đo bằng SQL SELECT + Redis đọc, KHÔNG sửa mã, KHÔNG restart; chi tiết F142–F143 ở `research_notes/topic_2026_ms-fractal-trading-range-trend.md`; 11 symbol, từ 2022-01-01, dữ liệu cắt 2026-10-14, đúng khung trend production: combo H1, ma_cross H2, macd_break H4). **(1) Quy ước ghép trend** (đang dùng "MỞ": trend đóng ≤ giờ MỞ nến vào lệnh; thử "ĐÓNG": ≤ giờ ĐÓNG nến vào lệnh): chỉ `combo` nhạy — setup thô qua bộ lọc trend 40,1% → 81,6%, DB1 11.997 → 28.910 (+141%); `ma_cross` 64,8% → 66,6%, DB1 45.631 → 47.498 (+4%); `macd_break` 80,8% → 81,9%, 18.738 → 19.235 (+3%). Ma trận combo (vào lệnh × trend, % tăng DB1 khi đổi sang ĐÓNG): đường chéo (khung trend = khung vào lệnh) +211% / +227% / +226% / +211% cho H1 / H2 / H3 / H4, càng đi về phía trend lớn hơn khung vào lệnh càng ít (vào H1: trend H2 +60%, H3 +16%, H4 +6%). Cơ chế (suy luận, ví dụ thật US30): hướng KNN của một nến = dấu của (`ai_knn` nến đó − nến trước) nên chính nến mạnh tạo ra setup cũng đổi hướng KNN của nó; ĐÓNG cho phép setup "tự xác nhận", MỞ buộc trend có TRƯỚC nến vào lệnh (trễ ≥ 1 nến vào lệnh; vào H4 + trend H1 thì 4 nến H1 nằm bên trong bị bỏ qua). Hai ví dụ thật: 02/09/2026 11:00→12:00 US30 combo H1 BUY (MỞ dùng trend đóng 11:00 = −1 → loại; ĐÓNG dùng trend đóng 12:00 = +1 → giữ); nến H4 14/09/2026 14:00→18:00 (bốn nến H1 bên trong: −1, −1, +1, +1; MỞ dùng −1, ĐÓNG dùng +1). **Lưu ý khi cân nhắc ĐÓNG ở live (suy luận, chưa kiểm với DE):** khi khung trend LỚN hơn khung vào lệnh, nến trend đóng cùng lúc tới SAU nến nhỏ (DF phát nhỏ → lớn; đo 10/10: nến H4 đến sau M10 khoảng 8–9 giây, xem mục 7 hạn chế số 6), nên một engine xử lý ngay lúc nến vào lệnh đóng chưa thấy nó — ĐÓNG chỉ khả thi khi khung trend ≤ khung vào lệnh (`combo` H1: được; `ma_cross` H2 / `macd_break` H4: không, mà hai chiến lược này chênh lệch chỉ +3–4%). **CHƯA quyết:** giữ MỞ (khớp mã live cũ OG8/OG18 trong git, `des` và mọi số liệu trước đó) hay đổi; chờ người dùng và kiểm DE trên og18 (cần SSH). Chưa đánh giá chất lượng (P&L) của quy ước nào. **Các số DB1 ở (1) đo lúc mã chạy cách A; đo lại sau khi đổi về cách B cùng ngày (F142b): `combo` 13.325 → 29.374 (+120%), `ma_cross` 42.931 → 46.535 (+8%), `macd_break` 14.162 → 14.868 (+5%); đường chéo ma trận `combo` +242..258%. Kết luận định tính không đổi: chỉ `combo` nhạy.** **(2) Thứ tự lọc** (A = trend rồi chống trùng, đang dùng; B = chống trùng rồi trend, cũ trước 15/10; cả hai quy ước ghép MỞ): số tín hiệu A / B — combo 11.997 / 13.325, ma_cross 45.631 / 42.931, macd_break 18.738 / 14.162; tập trùng nhau chỉ 76% / 30% / 55% của A (69% / 32% / 72% của B). A không bao giờ có hai tín hiệu liền nhau cùng chiều; B có (36% / 57% / 32% số cặp liền nhau). B ⊆ DB0 (0% ngoài DB0), A có 23,8% / 69,6% / 45,2% tín hiệu không có ở DB0. B bỏ nguyên một đợt setup cùng chiều nếu nến đầu lệch trend, A vẫn bắn ở nến sau: 2.860 / 31.732 / 8.467 đợt. "Trí nhớ" cần của chống trùng (khoảng cùng chiều giữa hai setup đưa vào bộ lọc) dài hơn warm-up 300 nến ở A: 6 / 0 / 104 khoảng (dài nhất 377 / 177 / 525), ở B: 0 / 0 / 0 (150 / 72 / 288). Hai ví dụ thật US30 `ma_cross` M45: 07/08/2025 B bỏ mất cả đợt BUY vì nến đầu 08:15 lệch trend, A bắn BUY lúc 09:00; 06–07/03/2025 trong một pha trend −1 B bắn hai SELL (13:00 rồi 13:45 hôm sau), A chỉ một. **Đối chiếu bộ đo với Redis thật** (đếm LIST DB1, bartime < 14/10, đọc 16/10): `macd_break` 18.738 = 18.738; `ma_cross` 45.629 so 45.631; `combo` 11.995 so 11.997 (tổng lệch 4/76.366; DB0 lệch 3/176.383) — nghi DF backfill nhỏ, chưa tra từng dòng. Không đổi gì: thứ tự giữ A (người dùng chọn 15/10); A hay B tốt hơn về chất lượng (P&L) vẫn chưa ai đo.

**Đổi lại thứ tự lọc về cách B (alternating TRƯỚC, trend SAU) 2026-10-16** (người dùng, sau khi xem bảng so A–B: "Tôi muốn đổi lại theo cách B nhé, hãy thực hiện", kèm câu hỏi "Cách nào trả lời được cho câu hỏi tại thời điểm trend xuất hiện thì mới đặt lệnh theo trend?"; phạm vi: cả ba chiến lược `combo`, `macd_break`, `ma_cross` của `we_core`; `des` KHÔNG sửa vì vốn đã là cách B). **Đổi:** trong `strategies/{combo,macd_break,ma_cross}.py`, `raw_signal = apply_alternating_filter(buy_cond, sell_cond, params)` (giữ nguyên) rồi `out = apply_trend_filter(out, params, raw_signal_col="raw_signal", output_col="signal")`; bỏ cách dựng `setup`/`staged` của cách A; `signal_reason` theo hướng của `signal` + hậu tố "; aligned with trend reference" khi trend bật (không đổi). Sửa theo: docstring 3 chiến lược, `rules/alternating.py` (thứ tự + phần "trí nhớ", xem hạn chế bên dưới), 1 cụm chữ ở `signal_display/payload.py`, và các đoạn mô tả ở mục 1/6/7/10 của file này. KHÔNG đổi: mã `rules/*.py`, `configuration.py`, config, worker, `des`. **Khôi phục cách A nếu cần:** ở cả 3 chiến lược thay lệnh `apply_trend_filter` bằng `setup = apply_alternating_filter(buy_cond, sell_cond, None)`; `staged = apply_trend_filter(out.assign(setup_signal=setup), params, raw_signal_col="setup_signal", output_col="trend_setup")`; `out["signal"] = apply_alternating_filter(staged["trend_setup"].eq(1), staged["trend_setup"].eq(-1), params)`; `out["trend_filter_status"] = staged["trend_filter_status"]`, và đổi các test khoá thứ tự (3 test `*_lock_xen_ke_truoc_roi_trend_sau`, `_signal_tham_chieu`, 1 khẳng định ở `test_trend_methods.py`).
**Test:** `we_core` 245 qua (số lượng giữ nguyên; viết lại 3 test "lock" thứ tự, bản tham chiếu độc lập `_signal_tham_chieu` và test ngẫu nhiên 150 khung × 4 tổ hợp cờ × 3 chiến lược — nay kiểm `signal ⊆ raw_signal`, `raw_signal` không phụ thuộc trend, và có ca hai tín hiệu liền nhau cùng chiều xuất hiện thật; `test_trend_methods` khôi phục khẳng định "bật hai khối cho tập con của một khối"), `des` 166 qua; ruff/vulture sạch.
**Chứng minh** (176 cặp = 3 chiến lược × 11 symbol × khung, từ 2022-01-01, SQL SELECT cắt 2026-10-14, chạy đúng đường của worker `run_strategy` / `run_strategy_with_trend_reference` + `signal_frame`; chụp mã cách A rồi mã cách B): (1) DB0 (trend tắt) giống hệt TỪNG BIT ở 176/176 cặp — mọi cột, mọi dtype, `signal_frame` (176.383 dòng); (2) DB1: `bartime`, setup thô, `raw_signal`, `trend_bias` và mọi cột khác giống hệt, chỉ 4 cột `signal`/`signal_reason`/`trend_filter_status`/`entry_price` đổi; (3) `signal` mới == công thức độc lập "raw_signal nếu == trend_bias, ngược lại 0" tính từ cột bản chụp cách A: 176/176; (4) bất biến: `signal ⊆ raw_signal`, mọi tín hiệu cùng chiều trend, `trend_filter_status`/`signal_reason`/`entry_price` nhất quán với `signal`, mọi dòng `signal_frame` DB1 có (giống hệt) ở DB0; (5) **so với dữ liệu thật trước khi sang cách A**: DB1 `macd_break` (88 LIST, trend H4, luật không đổi) trong bản chụp Redis 2026-10-15 09:31 UTC (lúc còn cách B) giống hệt DB1 mã mới — 88/88 LIST, 14.162 dòng (cùng nến, signal, atr; combo/ma_cross không so được vì đã đổi khung trend/luật); (6) `des` (cách B, không sửa) cho cùng `raw_signal`, `signal`, `trend_filter_status` với `we_core` ở 24/24 ca combo/macd_break (khung × trend tắt/bật, 12.722 tín hiệu).
**Số lượng DB1, cách A → cách B:** combo 11.997 → 13.325 (+11,1%), ma_cross 45.631 → 42.931 (−5,9%), macd_break 18.738 → 14.162 (−24,4%), tổng 76.366 → 70.418 (−7,8%); DB0 giữ 35.807 / 110.813 / 29.763. Cặp tín hiệu liền nhau cùng chiều trong DB1: 0 → 4.747 / 24.581 / 4.438; tín hiệu DB1 không có ở DB0: 2.861 / 31.742 / 8.472 → 0.
**Câu hỏi "vào lệnh đúng lúc trend xuất hiện" (research_notes F144):** phân loại mỗi tín hiệu DB1 theo quan hệ setup/trend ở nến TRƯỚC nến bắn (a = trend mới xuất hiện khi setup đã có từ trước, b = setup mới xuất hiện khi trend đã có sẵn, c = cả hai cùng mới ở nến đó). Cách A (combo / ma_cross / macd_break): a 0,9% / 49,1% / 6,7%, b 65,0% / 40,2% / 85,5%, c 34,1% / 10,7% / 7,8%; cách B: a 0 / 0 / 0, b 62,2% / 91,4% / 95,5%, c 37,8% / 8,6% / 4,5%. Tức chỉ cách A "đợi trend xuất hiện rồi mới vào" (với ma_cross: một nửa số tín hiệu; độ trễ loại a so với lúc đợt setup bắt đầu: trung vị 1 / 3 / 1 nến, p90 1 / 8 / 3); cách B quyết định ngay ở nến setup đầu tiên của đợt: chỉ vào khi tín hiệu mới xuất hiện VÀ trend đã đúng ngay nến đó, trend xuất hiện muộn hơn trong cùng đợt thì bỏ qua. Người dùng chọn cách B sau khi nhận câu trả lời này.
**Hạn chế mới phát hiện khi kiểm (có từ trước, KHÔNG do lần đổi này): "trí nhớ" thực của đường delta ngắn hơn 300 nến.** Chỉ báo chưa hợp lệ ở đầu cửa sổ thì không có setup nên không "nhớ" gì: trong 300 nến warm-up còn nhớ được 281 nến (combo, `ma` SMA20), 267 (ma_cross, SMA34), **200 (macd_break, mất `LOOKBACK_BARS` = 100 nến đầu)**. Mô phỏng đường delta của worker bằng SQL thật (cửa sổ + 300 nến warm-up, so với bản toàn lịch sử cách B): 704 cửa sổ ngẫu nhiên 3 ngày (có 1.162 tín hiệu DB1 bên trong) → 0 lệch ở DB0 lẫn DB1; nhưng cửa sổ 1 nến đặt đúng tại setup mà setup thô cùng chiều liền trước (không có setup ngược giữa chúng) cách ≥ 150 nến — 345 sự kiện, 344 ở macd_break: 311 sự kiện với khoảng ≤ 200 nến → 0 lệch; **34 sự kiện với khoảng > 200 nến → 34/34 lệch ở DB0 và 25/34 ở DB1** (9 ca còn lại bị trend lọc mất sẵn); khoảng nhỏ nhất bị lệch 201, lớn nhất không lệch 198 (khớp 300 − 100 = 200). Ý nghĩa: một lần làm mới delta rơi đúng setup sau khoảng > 200 nến của macd_break bắn một tín hiệu mà tính lại từ đầu sẽ chặn; tín hiệu thừa nằm lại tới lần restart worker kế. Mức độ: cách B có 34 khoảng như vậy trong 4,7 năm × 88 LIST macd_break (~7/năm, 0,11% tín hiệu DB0 của macd_break); cách A có 655 khoảng như vậy ở macd_break và 10 khoảng > 281 nến ở combo (ma_cross: 0 ở cả hai cách), nên cách B an toàn hơn cho worker. Lời khẳng định cũ "không khoảng nào vượt 300 nến ⇒ rủi ro = 0" (ghi chú 2026-10-13, docstring alternating/macd_break) chỉ đúng nếu cửa sổ nhớ đủ 300 nến, nên đã sửa docstring. CHƯA sửa mã (người dùng chưa yêu cầu); cách giảm: nâng warm-up của macd_break lên ≥ `LOOKBACK_BARS` + 300 (= 400) hoặc warm-up riêng từng chiến lược, hoặc nhịp reconcile 30 phút tính lại vài ngày gần nhất.
**ĐÃ restart cùng ngày 2026-10-16 10:21 UTC** (người dùng: "Hãy restart nhé"; số đo thật ở ghi chú "Restart `we-worker` + dashboard 2026-10-16 10:21 UTC" bên dưới): Redis DB1 của cả ba chiến lược đã là cách B; DB0, schema, tên key không đổi. **Quy ước đọc:** các ghi chú ngày 2026-10-15/16 nói "trend trước, xen kẽ sau", "DB1 ⊄ DB0", "144 khoảng > 300 nến", "DB1 xen kẽ tuyệt đối" là cách A lúc đó; số đo A vs B của ghi chú 2026-10-16 ngay phía trên (research_notes F143) vẫn đúng như phép đo.

**Làm rõ cách ghép trend theo ý người dùng 2026-10-16** (người dùng giải thích mục tiêu trend = tham chiếu khung khác, lớn hoặc nhỏ hơn; hai nhận xét: (1) khung tham chiếu lớn chưa đóng thì phải lấy nến lớn đã đóng trước đó, (2) khung tham chiếu nhỏ hơn thì khi nến tín hiệu đóng nến nhỏ đóng cùng lúc chắc chắn đã có, nên lấy nến đóng cùng lúc, chưa kịp thì lấy nến gần nhất trước đó; hỏi "hiện có đang giống vậy?"; CHỈ phân tích, KHÔNG sửa mã). **Trả lời đã đưa:** (1) ĐÚNG và mã đang làm vậy (`merge_trend_reference` chỉ lấy nến tham chiếu ĐÃ đóng, không bao giờ nến đang chạy; kiểm US30 H1+H4: kể cả nến H1 13–14 đóng cùng lúc H4 10–14 vẫn lấy H4 06–10). (2) CHƯA giống: mã lấy nến tham chiếu đóng ≤ giờ MỞ nến tín hiệu nên bỏ qua mọi nến nhỏ nằm trong lòng nến tín hiệu, kể cả nến đóng cùng lúc (US30 H2 10–12 + H1: đang lấy H1 09–10, theo ý người dùng là H1 11–12), tức trend của tín hiệu khung lớn trễ đúng một nến tín hiệu. Cũng đúng cho khung lớn không chia đều (M45 với H2/H4, H3 với H4: nến lớn đóng giữa nến tín hiệu đang bị bỏ qua). Đo tác động nếu đổi (research_notes F146; DB1 cách B, chỉ số lượng): combo 13.325 → 21.253 (+59,5%; vào H2/H3/H4 với tham chiếu H1 +96% / +71% / +63%, vào H1 không đổi), ma_cross +2,2% (chỉ M45), macd_break +0,6% (chỉ H3, M45); phần lớn tín hiệu cũ giữ nguyên, chủ yếu thêm tín hiệu. **Còn mở:** khung tham chiếu BẰNG khung tín hiệu (combo vào H1 + tham chiếu H1 = mặc định hiện tại): nến đóng cùng lúc chính là nến tín hiệu; mình giả định lấy nến trước đó (như hiện tại), lấy chính nó thì combo H1/H1 gấp ~3,5 lần (trend tự xác nhận) — chờ người dùng xác nhận. Nếu đổi: sửa `indicator.merge_trend_reference` (cần biết khung tín hiệu và khung tham chiếu, `run_strategy_with_trend_reference` đã có cả hai), bản sao của `des`, test, kiểm chứng, restart `we-worker`; DE live chưa kiểm. Phần "lỡ chưa kịp đóng thì lấy nến gần nhất trước đó" chỉ xảy ra ở live; backtest từ SQL coi nến nhỏ luôn có sẵn. **Look-ahead (người dùng hỏi tiếp, F147):** kiểm bằng cắt dữ liệu tại giờ đóng nến tín hiệu rồi chạy lại cả đường ống (7 ca, 165 nến mẫu): quy tắc hiện tại 0 lệch, quy tắc F146 0 lệch, đối chứng âm cố tình nhìn trước 13 lệch ⇒ KHÔNG có look-ahead ở cả hai; lưu ý khác: tham chiếu nhỏ hơn thì nến tham chiếu cuối là một phần của chính nến tín hiệu (trùng thông tin, không phải nhìn trước). Bản chất người dùng mô tả (trend có trước → tín hiệu tính độc lập → chỉ pub khi cùng chiều trend, lệch thì bỏ) = cách B đã đổi cùng ngày; với khung BẰNG nhau, "trend có trước" ⇒ lấy nến trước đó (khớp giả định ở trên; chờ người dùng xác nhận rõ). **Ca khung BẰNG nhau (người dùng hỏi "lấy đúng nến đó thì khác biệt thế nào", F148):** combo vào H1 + tham chiếu H1: qua lọc trend 3.303 (19,4% số tín hiệu chống trùng) khi lấy nến trước → 11.424 (67,1%) khi lấy chính nến, gấp 3,46 lần (H2/H3/H4 gấp 3,4–3,6); 71% tín hiệu thêm là nến mà trend đổi hướng ngay trên nến tín hiệu; macd_break H4/H4 chỉ +8%. Không phải look-ahead nhưng trend không còn "có trước"; khuyến nghị giữ nến trước. **Tỷ trọng so với DB0 (F149):** hiện tại combo 37% / ma_cross 39% / macd_break 48%; theo quy tắc F146 combo 59% (vào H2/H3/H4 với tham chiếu H1: 48/57/60% → 93/97/98%, bộ lọc gần như vô hiệu vì nến H1 cuối nằm trong nến tín hiệu), ma_cross 40%, macd_break 48%; lấy chính nến cả khi bằng nhau: combo 82%. **Đề xuất quy tắc "khung trend ≥ khung tín hiệu" (người dùng 2026-10-16; mình đồng ý, CHƯA làm):** khung bằng nhau vẫn được, khung nhỏ hơn bị cấm. Với quy tắc này quy tắc ghép hiện tại (nến trend đã đóng hẳn trước khi nến tín hiệu mở) đã đúng nên KHÔNG cần sửa `merge_trend_reference`, đề xuất F146 không cần nữa. Cần: (1) kiểm tra khung trend < khung tín hiệu thì báo lỗi rõ (`run_strategy_with_trend_reference`, CLI, worker; dashboard chỉ liệt kê khung ≥ khung tín hiệu); (2) chỉ `combo` vi phạm (mặc định H1 nhưng vào H2–H4); `ma_cross` (H2, vào M10–M45), `macd_break` (H4) và toàn bộ `des` (H4) đã đúng. Hai cách cho combo (DB1 cách B, MỞ, 11 symbol, từ 2022; DB0 = 35.807; hiện tại 13.325 = 37% nhưng vi phạm): trend H4 cho mọi khung vào = 8.720 (H1 5.763, H2 1.398, H3 693, H4 866; 24% DB0) — mình khuyên vì chỉ đổi một giá trị config; hoặc trend = khung vào lệnh = 6.942 (3.303, 1.652, 1.121, 866; 19%) — cần thêm logic lấy max(khung vào, mặc định). Đổi sẽ làm DB1 combo giảm ~35% (13.325 → 8.720) và cần restart `we-worker`. **Hướng nghiên cứu H3 / "trade khung nào dùng trend khung đó" / bỏ nến ở pane trend (F150) — người dùng BỎ QUA (16/10: "Bỏ qua nhé, quay lại phần trên") và quay lại quyết định quy tắc "khung trend ≥ khung tín hiệu" + cách chọn cho combo (trend H4 cho mọi khung, mình khuyên; hoặc trend = khung vào lệnh).** Số đo F150 (combo mặc định H3 = 7.706 tín hiệu; trend cùng khung gần như không lọc ở ma_cross 83% / macd_break 91,5%) giữ làm tham khảo, KHÔNG phải yêu cầu; không mở trend M10–M45 cho ma_cross, không đổi pane trend. CHƯA đổi gì, CHƯA restart.

**Quy tắc "khung trend ≥ khung tín hiệu" + `combo` mặc định H4 2026-10-16** (người dùng: "Ok, làm nhé, cứ giữ H4 mặc định", sau khi chốt phương án trong cuộc bàn cùng ngày; phạm vi đã nói trước: kiểm tra + dashboard + config `combo` + test + kiểm chứng; KHÔNG đụng quy tắc ghép trend, `ma_cross`, `macd_break`, `des`). **Quy tắc:** khung trend phải LỚN HƠN HOẶC BẰNG khung tín hiệu (bằng nhau vẫn được, khi đó lấy nến ngay trước nến tín hiệu như mọi trường hợp khác). Lý do: nến trend luôn là nến đã đóng hẳn TRƯỚC khi nến tín hiệu mở (`indicator.merge_trend_reference`) nên trend "có trước" tín hiệu, không chồng thời gian, không look-ahead (kiểm bằng cắt dữ liệu: 165 nến mẫu, 0 lệch, đối chứng nhìn trước 13 lệch — research_notes F147); khung trend NHỎ hơn khung tín hiệu thì hoặc trễ một nến tín hiệu (quy tắc ghép hiện tại) hoặc phải dùng nến nhỏ nằm trong nến tín hiệu (trùng thông tin, bộ lọc giữ 93–98% tín hiệu — F149). **Đổi:** (1) `configuration.check_trend_timeframe(entry_tf, trend_tf)` MỚI (raise `ValueError` "TREND_TF X must not be smaller than the entry timeframe Y …"), gọi trong `run_strategy_with_trend_reference` (đường chung của dashboard, CLI export và worker DB1), trong `signal_display/server._load_and_run` và `we_signal/export_cli.resolve_trend_reference` (hai nơi này báo lỗi TRƯỚC khi tải SQL; dashboard trả HTTP 400 kèm thông báo); (2) `worker.check_trend_timeframes()` MỚI, gọi ở `main()` TRƯỚC `run_forever()` nên trước `clear_all_signals()`: duyệt mọi cặp (chiến lược, khung vào lệnh) của `_STRATEGY_TIMEFRAMES`, nếu `default_trend_tf` nhỏ hơn khung vào lệnh thì dừng với `fatal_config_error` liệt kê MỌI cặp sai (exit 1), Redis còn nguyên; (3) dashboard: ô Trend TF chỉ liệt kê khung ≥ khung tín hiệu đang chọn và dựng lại khi đổi khung tín hiệu (`live_page.populateTrendControls`; lựa chọn tay còn hợp lệ thì giữ, không thì về mặc định, hành vi giữ lựa chọn khi đổi chiến lược có từ trước); (4) `--trend-tf` của CLI ghi rõ trong help; (5) config: `strategies.combo.default_trend_tf` H1 → **H4** ở `we_config.yaml` (quyền 600 giữ nguyên, chỉ đúng 1 dòng đổi) và `we_config.example.yaml`; `ma_cross` (H2, vào M10–M45), `macd_break` (H4) và toàn bộ `des` (H4) vốn đã thoả quy tắc. Mặc định `combo` từng là H4 → H1 (sáng 2026-10-16, để có nhiều tín hiệu) → H4 (nay): vào H2–H4 với trend H1 vi phạm quy tắc. **Kiểm chứng:** (a) 176 cặp trên SQL thật (cách B, từ 2022-01-01, dữ liệu cắt 2026-10-14, đúng đường worker `run_strategy` / `run_strategy_with_trend_reference` + `signal_frame`), chụp trước rồi sau: 132 cặp `ma_cross`/`macd_break` giống hệt TỪNG CỘT ở cả DB0 lẫn DB1 (băm cột, `signal_frame`, mảng tín hiệu); 44 cặp `combo` DB0 giống hệt; `combo` DB1 13.325 → **8.720** (H1 3.303 → 5.763, H2 4.091 → 1.398, H3 3.291 → 693, H4 2.640 → 866), đúng bằng phép đo ma trận trước đó (F142b) và đúng công thức độc lập `signal = raw_signal nếu == trend_bias`, `signal ⊆ raw_signal`, mọi tín hiệu cùng chiều trend; (b) Chrome thật (headless, profile và cổng tạm, SQL chỉ đọc): 18/18 — ô Trend TF của combo vào H1/H2/H3/H4 liệt kê H1–H4 / H2–H4 / H3–H4 / H4 và chọn H4; macd_break M10–H4 đúng tương tự; chọn tay H2 rồi đổi sang vào H3 thì về H4, chọn tay H3 rồi đổi sang vào H2 thì giữ H3; quét combo H2 + trend H4 không lỗi (raw 29 → filtered); (c) ruff/vulture sạch; test `we_core` 245 → **271** (viết lại 3 test khoá mặc định H1: tên file CSV `…_tf_knn_H4.csv`, `resolve_trend_reference` trả H4, `TREND_TF == "H4"`; thêm 26 ca: hàm kiểm tra bằng/lớn hơn/nhỏ hơn/mã sai, 3 chiến lược bị chặn, CLI chặn trước khi tải dữ liệu, dashboard HTTP 400 không tải dữ liệu, trang chủ có bộ lọc, worker pass với config thật và liệt kê đủ cặp sai khi cấu hình sai, `main()` dừng ở lỗi cấu hình trước khi xoá Redis, config thật và config mẫu thoả quy tắc với mọi khung vào lệnh của worker), `des` 166 giữ nguyên. **Hệ quả:** DB1 `combo` giảm ~35% so với trend H1 (13.325 → 8.720, 24% của DB0 35.807); các hệ quả khác của cách B và của quy ước ghép trend KHÔNG đổi. **ĐÃ restart `we-worker` và dashboard 8517 cùng ngày 10:21 UTC** (người dùng: "Hãy restart nhé"; chi tiết ở ghi chú kế tiếp): Redis DB1 của `combo` nay là trend H4 (và cách B cho cả ba chiến lược); nếu `we_config.yaml` bị sửa lại H1 thì worker KHÔNG khởi động (lỗi cấu hình) thay vì chạy sai. **Lưu ý khi sửa config:** đặt lại `default_trend_tf: H1` cho combo sẽ làm worker từ chối khởi động (H1 nhỏ hơn khung vào lệnh H2–H4); muốn bỏ quy tắc phải xoá `check_trend_timeframe` và `worker.check_trend_timeframes`. **Quy ước đọc:** các ghi chú ngày 2026-10-16 ở trên nhắc "combo trend mặc định H1", "combo H1 / ma_cross H2 / macd_break H4" hay "DB1 combo 12.017 / 13.325" là cấu hình lúc đó. Các hướng đã bàn cùng ngày nhưng người dùng BỎ QUA hoặc không còn cần, KHÔNG làm: mở trend M10–M45 cho ma_cross, chế độ "trend = khung vào lệnh", bỏ nến ở pane trend, mặc định combo H3, quy tắc ghép "nến mới nhất đã có" (F146).

**Restart `we-worker` + dashboard 2026-10-16 10:21 UTC** (người dùng: "Hãy restart nhé" sau khi được hỏi có restart `we-worker` và dashboard không; áp dụng cách B + combo trend H4 + quy tắc khung trend ≥ khung tín hiệu). **Worker (PID 562541 chạy từ 02:32:46 → PID 575066):** (1) kiểm trước, chỉ đọc: unit đã cài giống hệt bản trong repo; Redis DB0 176.879 khoá và DB1 76.699 khoá, 100% là tiền tố của ta, 0 khoá lạ; `NUMSUB df:events:backfill` = 1; worker nạp được mã mới trong tiến trình riêng và `check_trend_timeframes()` pass (176 cặp; combo H4 / ma_cross H2 / macd_break H4); (2) chụp nội dung DB0/DB1 ra scratchpad lúc 10:21:25 (176.703 + 76.523 dòng); (3) `systemctl --user restart we-worker` lúc 10:21:39 (SIGTERM êm). Log: `signals_cleared keys_deleted=176879 keys_deleted_trend=76699` (đúng bằng số đếm trước, không xoá nhầm khoá nào), `bootstrap_start … alternating_combo/ma_cross/macd_break=True trend_methods_*=knn`, **`bootstrap_done` 311,5 s (xong 10:26:52), `result=ok`, 176 cặp, 176 `pair_refreshed` DB0 + 176 DB1 = 352/352 lần ghi, 0 WARNING/ERROR/CRITICAL**, `subscribing`, `NUMSUB` = 1, `NRestarts` = 0, Redis 114,4 MB; trend_tf ghi trong log DB1 đúng config (combo H1–H4 → H4, macd_break M10–H4 → H4, ma_cross M10–M45 → H2). **Đối chiếu sau restart** (chụp lại Redis 10:27:28, so với chụp trước và với tham chiếu tính độc lập từ SQL bằng mã sản phẩm, dữ liệu cắt tại 2026-10-16 00:00, các dòng bartime < T): (a) DB0 giống hệt trước restart 176/176 LIST (combo 35.878, ma_cross 110.994, macd_break 29.831 dòng, y nguyên); (b) cả DB0 lẫn DB1 SAU restart == tham chiếu SQL từng dòng/field ở 352/352 LIST (mỗi bên combo 44/44, ma_cross 44/44, macd_break 88/88); (c) số dòng DB1 trước → sau: combo 12.017 → 8.737 (−27,3%), ma_cross 45.716 → 43.005 (−5,9%), macd_break 18.790 → 14.200 (−24,4%) — gồm đổi cách A → B cho cả ba và combo trend H1 → H4; dòng chung trước/sau: combo 1.559, ma_cross 13.915, macd_break 10.296 (chỉ trước / chỉ sau: 10.458 / 7.177; 31.800 / 29.081; 8.491 / 3.902); (d) bất biến cách B trên Redis: tín hiệu DB1 không có (cùng hướng, cùng nến) ở DB0 = 0 (0/8.737, 0/43.005, 0/14.200), DB1 không bao giờ nhiều dòng hơn DB0, DB0 không có cặp liền nhau cùng chiều, DB1 có (combo 5.177, ma_cross 24.615, macd_break 4.444 — đúng bản chất cách B), schema field không đổi (combo: atr, bartime, entry, signal; còn lại: atr, bartime, signal). Chưa thấy trigger backfill nào tới sau bootstrap nên đường delta chưa được quan sát bằng log thật (đã kiểm bằng mô phỏng ở các ghi chú trên). **Dashboard 8517:** PID 564962 (từ 03:11:04, mở bằng cửa sổ terminal của `run_we.sh`) tắt bằng SIGTERM; PID 575148 (từ 10:22:20) khởi động bằng đúng đường `nohup` của launcher (`nohup bash -lc "cd … && python -m …signal_display.server"`, log `/tmp/wisdom_engine_dashboard.log`), KHÔNG nằm trong cửa sổ terminal: cửa sổ cũ còn hiện "Press Enter to close…" và đóng được bằng Enter; trình duyệt đang mở trang phải tải lại (F5) mới có bộ lọc Trend TF mới. Kiểm sau restart: `/health` ok, trang chủ có bộ lọc khung trend và `defaultTrendTf` H4, `/api/scan` với combo H2 + trend H1 trả HTTP 400 kèm thông báo quy tắc. **`des`:** không đụng, `des-signal` vẫn `active`, 0 WARNING/ERROR trong 400 dòng cuối `des_run.log`. **Quy ước đọc:** các ghi chú ngày 2026-10-15/16 nói "`we-worker` chưa restart / Redis còn cách A / DB1 combo còn trend H1 / dashboard còn mã cũ" hết hiệu lực từ 10:21:40 UTC. Bản chụp Redis trước/sau + bộ đối chiếu ở scratchpad (`redis_before3.pkl`, `redis_after3.pkl`, `redis_compare3.py`).

**Khung trend riêng theo (chiến lược, symbol, khung vào lệnh) — `trend_tf_by_symbol` 2026-10-16** (người dùng: "tìm ra khung lọc nào cho tín hiệu nhiều nhất theo từng chiến lược, từng symbol và từng khung vào lệnh để tạo bộ signal tối ưu và tránh lọc nhầm", rồi "đưa vào config để ra bản lọc chuẩn hơn và customize hơn"; người dùng xác nhận 5 điểm trước khi làm: (1) ứng viên khung trend chỉ H1–H4, không dùng khung nhỏ hơn; (2) đo trên dữ liệu từ 2024-01-01 tới nay; (3) thước đo = SỐ tín hiệu còn lại; (4) hình dạng config symbol → khung vào → khung trend, ô thiếu thì dùng `default_trend_tf`, KHÔNG cần công tắc bật/tắt riêng từng cặp — "db0 vẫn không dùng trend mà chỉ chống trùng, còn db1 chính là có yếu tố trend này nữa"; (5) làm `we_core` trước; sau đó "Hãy triển khai"; `des` KHÔNG đổi). **Config:** `strategies.{combo,ma_cross,macd_break}.trend_tf_by_symbol: {SYMBOL: {khung_vào: khung_trend}}` (tuỳ chọn, có trong cả `we_config.yaml` — quyền 600 giữ nguyên — lẫn `we_config.example.yaml`). Thứ tự tra khung trend cho một cặp: `TREND_TF` đè TƯỜNG MINH (CLI `--trend-tf`, `--param`, ô/URL dashboard) > ô trong bảng > `default_trend_tf` của chiến lược (nay chỉ còn là dự phòng cho ô thiếu). Kiểm lúc nạp config, MỘT `ValueError` liệt kê mọi ô sai: symbol ngoài `symbol_x`, khung vào ngoài tập khung của chiến lược, khung trend ngoài `trend_timeframes`, khung trend nhỏ hơn khung vào (sai kiểu → `TypeError`). Bỏ bảng (xoá khối hoặc `{}`) = quay về một khung chung `default_trend_tf`. **Mã:** `configuration.py` (`_strategy_trend_tf_by_symbol`, `resolve_trend_tf`, `_raw_params_with_cell_trend_tf` — gọi trong `normalize_{combo,ma_cross,macd_break}_params`; trường `StrategySpec.trend_tf_by_symbol`); `worker.check_trend_timeframes()` kiểm theo TỪNG (chiến lược, symbol, khung vào), gộp lỗi theo nhóm, vẫn chạy trước `clear_all_signals()`; worker tra khung qua `normalize_params(None, symbol, timeframe)` nên DB1 từng cặp dùng đúng khung của ô (log `pair_refreshed` ghi `trend_tf`; TÊN KEY REDIS KHÔNG chứa khung trend nên chỉ log cho biết cặp nào dùng khung nào); `export_cli.resolve_trend_reference` tra theo ô nên tên file CSV bản trend mang hậu tố khung của ô (vd `…_tf_knn_H4.csv` cho ma_cross M10, `…_tf_knn_H1.csv` cho M20); dashboard nhận `trendTfBySymbol` trong config trang, ô Trend TF **đặt lại về khung đã cấu hình** mỗi khi đổi chiến lược, symbol hoặc khung vào (trước đó giữ lựa chọn tay qua các lần đổi; đổi vì mặc định giờ phụ thuộc ô; lựa chọn tay chỉ giữ tới lần đổi kế). **Cách chọn bảng:** `research_notes/scripts/t008_tune_trend_tf.py` (chỉ đọc SQL, KHÔNG sửa config): mỗi (chiến lược, symbol, khung vào) thử mọi khung trend thuộc `trend_timeframes` và ≥ khung vào, bằng đúng đường `run_strategy_with_trend_reference`, đếm tín hiệu từ 2024-01-01 tới 2026-10-16 (có assert DB1 ⊆ DB0 ở mọi tổ hợp); chọn khung có SỐ tín hiệu lớn nhất, hoà đúng số thì ưu tiên `default_trend_tf` nếu nằm trong nhóm hoà, không thì khung lớn hơn; ghi CSV mọi tổ hợp + đoạn YAML. Chạy lại (từ thư mục cha): `wisdom_engine/.venv/bin/python -B wisdom_engine/research_notes/scripts/t008_tune_trend_tf.py --from 2024-01-01 --out-dir <thư mục>`; bảng chỉ đúng với luật hiện tại (cách B, quy ước ghép nến trend, KNN) — đổi luật thì phải đo lại. **Bảng đã chọn (số tín hiệu từ 2024-01-01; "giữ" = ô có khung bằng `default_trend_tf`):** `combo` 44 ô (mặc định H4): vào H1 → H4, H2 → H2 (FR40, J225 → H4), H3 → H3, H4 → H4; giữ 24, đổi 20; DB0 21.059, DB1 5.123 (24,3%) → 5.554 (26,4%, +8%). `ma_cross` 44 ô (mặc định H2): MỌI symbol như nhau — M10 → H4, M20/M30/M45 → H1; đổi 44/44; DB0 65.203, DB1 25.109 (38,5%) → 37.413 (57,4%, +49%). `macd_break` 88 ô (mặc định H4): M10/M20 → H1 ở mọi symbol, M30 → H1 (US30 → H2), M45/H1/H2/H3 tuỳ symbol, H4 → H4; giữ 24, đổi 64; DB0 17.561, DB1 8.333 (47,5%) → 14.118 (80,4%, +69%). **Cần biết khi dùng bảng:** (1) tiêu chí là SỐ tín hiệu, KHÔNG phải chất lượng — "nhiều tín hiệu nhất" nghĩa là chọn khung lọc YẾU nhất ở từng ô, nên bảng làm bộ lọc của `ma_cross`/`macd_break` yếu đi rõ (giữ lại 57%/80% DB0 thay vì 38,5%/47,5%) mà hệ thống CHƯA đo lãi/lỗi nên chưa biết lọc yếu hơn là tốt hay xấu; (2) chọn và đo trên CÙNG đoạn dữ liệu 2024→nay, không có kiểm tra ngoài mẫu; (3) 52 ô chênh dưới 5% giữa khung nhất và khung nhì (combo 1, ma_cross 7, macd_break 44) — ở đó khung được chọn gần như tuỳ ý, đã chọn theo số lớn nhất như yêu cầu, người dùng có thể sửa tay; (4) bảng ma_cross giống hệt nhau ở 11 symbol nên chiều "theo symbol" chưa mang thông tin riêng. **Kiểm chứng:** (a) nạp config thật: khung trend tra ra cho cả 176 (chiến lược, symbol, khung vào) == bảng YAML 176/176; (b) chụp "kiểu worker" (SQL SELECT, 176 cặp, nến tới 2026-10-16, mỗi cặp qua `normalize_params` rồi `run_strategy` / `run_strategy_with_trend_reference` + `signal_frame`) so với bản chụp trước (mặc định chung combo H4 / ma_cross H2 / macd_break H4, nến cắt 2026-10-14): DB0 giống hệt từng cột 176/176; DB1 của 48 ô GIỮ khung cũ giống hệt 48/48, 128 ô đổi khung; bất biến ở cả 176 cặp (`signal` ⊆ `raw_signal`, mọi tín hiệu cùng chiều `trend_bias`, `signal` == công thức độc lập, `raw_signal` DB0 == DB1); số tín hiệu từ 2024-01-01 của khung đã chọn == bảng tuning 176/176 (0 lệch); (c) Chrome thật (headless, cổng/profile tạm, SQL chỉ đọc): 26/26 — ô Trend TF mặc định đúng khung của ô cho các chiến lược/symbol/khung vào đã thử, đặt lại khi đổi, lựa chọn tay giữ tới lần đổi kế, quét combo BTCUSD H2 + trend H2 không lỗi; (d) test `we_core` 271 → **281** (thêm: đọc/kiểm bảng — hợp lệ, symbol lạ, khung vào lạ, khung trend ngoài tập, khung trend nhỏ hơn khung vào, sai kiểu, gộp mọi lỗi; thứ tự tra đè tường minh > ô > mặc định; `normalize_*_params` ba chiến lược; `StrategySpec` mang bảng; config thật thoả quy tắc ở MỌI symbol × khung vào, config mẫu qua đúng hàm đọc; CLI tra theo ô và chặn khung nhỏ hơn; worker kiểm theo ô, `main()` dừng trước khi xoá Redis; dashboard nhúng `trendTfBySymbol` và chặn khung nhỏ hơn bằng HTTP 400), `des` 166 giữ nguyên; ruff/vulture sạch. **Hệ quả (đo TRƯỚC restart, cùng điểm cắt 2026-10-14, từ 2022-01-01):** DB1 combo 8.720 → 9.407 (+7,9%; 26,3% DB0 35.807), ma_cross 42.931 → 63.675 (+48,3%; 57,5% DB0 110.813), macd_break 14.162 → 23.782 (+67,9%; 79,9% DB0 29.763); DB0, schema, tên key không đổi. **ĐÃ restart `we-worker` và dashboard 8517 cùng ngày 12:01 UTC** (người dùng: "Xong rồi hãy restart nhé"; số đo thật ở ghi chú kế tiếp). **Quy ước đọc:** các ghi chú ngày 2026-10-16 ở trên nói "khung trend = `default_trend_tf` của từng chiến lược (combo H4, ma_cross H2, macd_break H4)" mô tả khung CHUNG lúc đó; từ ghi chú này, `default_trend_tf` chỉ là khung dự phòng cho ô thiếu và khung thực dùng tra theo ô. Chưa làm: đồng bộ sang `des` (đang tắt trend), kiểm DE live, đánh giá chất lượng (lãi/lỗi) theo khung trend. Công cụ đo + kết quả ở scratchpad (`t008/`, `trendtf_cell_capture.py`, `trendtf_cell_compare.py`, `dash_trendtf_chrome.py`).

**Restart `we-worker` + dashboard 2026-10-16 12:01 UTC** (người dùng: "Xong rồi hãy restart nhé", trả lời câu hỏi có restart `we-worker` và dashboard không; áp dụng bảng `trend_tf_by_symbol`). **Worker (PID 575066 chạy từ 10:21:39 → PID 578497):** (1) kiểm trước, chỉ đọc: unit đã cài giống hệt bản trong repo; Redis DB0 176.907 khoá và DB1 66.132 khoá, 100% là tiền tố của ta, 0 khoá lạ; `NUMSUB df:events:backfill` = 1; worker nạp được mã + config mới trong tiến trình riêng, `check_trend_timeframes()` pass và khung trend tra ra đúng bảng ở 176 cặp; (2) chụp nội dung DB0/DB1 ra scratchpad lúc 12:00:56 (176.731 + 65.956 dòng); (3) `systemctl --user restart we-worker` lúc 12:01:16 (êm). Log: `signals_cleared keys_deleted=176907 keys_deleted_trend=66132` (đúng bằng số đếm trước, không xoá nhầm khoá nào), `bootstrap_start … alternating_combo/ma_cross/macd_break=True trend_methods_*=knn`, **`bootstrap_done` 360,5 s (xong 12:07:18), `result=ok`, 176 cặp, 176 `pair_refreshed` DB0 + 176 DB1 = 352/352 lần ghi, 0 WARNING/ERROR/CRITICAL**; khung `trend_tf` ghi ở 176 dòng `pair_refreshed` của DB1 == bảng tuning 176/176 (H1 75 cặp, H2 18, H3 24, H4 59); `NUMSUB` = 1, `NRestarts` = 0; Redis 114,9 → 120,7 MB. **Đối chiếu sau restart** (chụp 12:07:46; so với chụp trước, với tham chiếu tính từ SQL bằng mã sản phẩm — khung trend tra theo ô, dữ liệu cắt tại T = 2026-10-16 00:00, các dòng bartime < T — và với bản chụp kiểu worker `trendtf_cell_after.pkl`): (a) DB0 giống hệt trước restart 176/176 LIST (combo 35.884, ma_cross 111.012, macd_break 29.835 dòng, y nguyên); (b) DB0 và DB1 SAU restart == tham chiếu SQL từng dòng/field ở 352/352 LIST, và == bản chụp kiểu worker (bartime + hướng) 352/352 LIST; (c) số dòng DB1 trước → sau: combo 8.740 → 9.430 (+7,9%), ma_cross 43.013 → 63.789 (+48,3%), macd_break 14.203 → 23.838 (+67,8%), tổng 65.956 → 97.057 (+47,2%) — đúng bằng tổng của bản chụp; dòng chung trước/sau (bartime < T): combo 7.639, ma_cross 36.573, macd_break 12.283; chỉ trước / chỉ sau: 1.097 / 1.785, 6.423 / 27.194, 1.915 / 11.547 (đổi khung trend nên một số tín hiệu cũ mất, nhiều tín hiệu mới xuất hiện); (d) bất biến cách B trên Redis: DB1 nhiều dòng hơn DB0 ở 0/176 LIST, tín hiệu DB1 không có (cùng hướng, cùng nến) ở DB0 = 0, 100% dòng DB1 có ở DB0 với field giống hệt (atr, entry, signal); DB0 không có cặp liền nhau cùng chiều, DB1 có (combo 5.404, ma_cross 27.689, macd_break 3.574 — bản chất cách B); schema field không đổi (combo: atr, bartime, entry, signal; còn lại: atr, bartime, signal). **Tỷ lệ DB1/DB0 trên Redis sau restart** (đúng bằng dự đoán của bản chụp; trong ngoặc: trước restart): combo 26,3% (24,4%) — H1 33,9 / H2 19,3 / H3 19,3 / H4 19,8; ma_cross 57,5% (38,7%) — M10 45,3 / M20 51,6 / M30 84,8 / M45 84,5; macd_break 79,9% (47,6%) — M10 67,5 / M20 86,2 / M30 89,8 / M45 89,5 / H1 94,0 / H2 94,5 / H3 94,7 / H4 92,3. Ở nhiều ô bộ lọc gần như không còn lọc (85–95% tín hiệu DB0 đi qua: ma_cross M30/M45, macd_break từ M20 trở lên) — hệ quả của tiêu chí "nhiều tín hiệu nhất" (xem "Cần biết khi dùng bảng" ở ghi chú trên). **Dashboard 8517:** PID 575148 tắt bằng SIGTERM; PID 578537 (từ 12:01:31) khởi động bằng đúng đường `nohup` của launcher (log `/tmp/wisdom_engine_dashboard.log`); `/health` ok, trang chủ nhúng `trendTfBySymbol` của cả 3 chiến lược; `/api/scan` với ma_cross BTCUSD M30 + trend H1, combo US30 H2 + trend H2, macd_break GOLD M45 + trend H1 trả HTTP 200, còn combo H2 + trend H1 trả HTTP 400 kèm thông báo quy tắc; trình duyệt đang mở trang phải tải lại (F5). **`des`:** không đụng, `des-signal` vẫn `active`, 0 WARNING/ERROR trong `des_run.log` kể từ 12:01. Tới 13:47 UTC worker chỉ có heartbeat, chưa có trigger backfill nào sau bootstrap, nên đường delta với bảng mới CHƯA được quan sát bằng log thật. **Hạn chế "DB1 có thể cũ sau nến trend mới" (mục 7, vòng đời worker, điểm 6) — ĐO THẬT sau khi người dùng hỏi có bằng chứng thực tế không (16/10, SQL SELECT + bản chụp Redis, chỉ đọc):** DF báo dữ liệu mới theo ĐỢT, 6 đợt/ngày vào ~03:03, 07:07, 11:11, 15:15, 19:19, 23:23 UTC (cách nhau 4 giờ 4 phút; 79–88 `trigger_accepted` mỗi đợt, worker xử lý trong 80–160 s; đếm từ log 12–16/10, 27 đợt). Bản chụp Redis 12:00:56 là trạng thái ngay sau đợt 11:11 hôm nay (cấu hình CŨ, sau bootstrap 10:27 tính đúng từ đầu nên mọi sai lệch chỉ có thể đến từ đợt này): so với kết quả tính ĐÚNG từ SQL (nến đóng tới 11:10), DB1 khớp ở 176/176 cặp (0 dòng sai) và DB0 đối chứng lệch 0. Mô hình xấu nhất ("mọi nến trend của đợt chưa có trong SQL", nến trend chỉ tới 07:07) dự đoán 4 dòng sai với cấu hình cũ (ma_cross HK50 M10, SP35 M20 và M30; macd_break SP35 M20) và 1 dòng với bảng mới (ma_cross J225 M20) — thực tế KHÔNG dòng nào xảy ra, nên mô hình quá bi quan và không có bằng chứng bảng mới làm lỗi tăng. Mẫu nhỏ: mới 1 đợt, và chỉ 13 tín hiệu DB1 (combo 3, ma_cross 8, macd_break 2) rơi vào cửa sổ 08:00–11:10 của đợt đó. Ca 10/10 (FR40 M10 ma_cross, 2 dòng / 230.242) thuộc HỆ CŨ (ma_cross luật cắt + chờ trend, trước hàng loạt thay đổi từ 11/10) nên KHÔNG dùng làm bằng chứng cho hệ hiện tại; trên hệ hiện tại mới có 1 đợt đo (11:11, 0 dòng sai). Chưa rõ vì sao đợt 11:11 không bị (nghi nến trend đã có trong SQL lúc worker tính, nhưng log không ghi giờ DF ghi SQL nên CHƯA xác định). Đã đặt phép đo tự động cho đợt 15:15 UTC với bảng mới (script `stale_live_check.py` ở scratchpad). **Quy ước đọc:** ghi chú "Khung trend riêng theo …" phía trên nói "chưa áp dụng / chưa restart / Redis còn bản một khung chung" hết hiệu lực từ 12:01:16 UTC; các ghi chú ngày 2026-10-16 nói "combo H4 / ma_cross H2 / macd_break H4" cho khung trend DB1 thực tế là mô tả khung CHUNG trước lần restart này. Bản chụp Redis trước/sau + bộ đối chiếu ở scratchpad (`redis_before4.pkl`, `redis_after4.pkl`, `redis_compare4.py`, `log_check4.py`, `ratio_vs_db0.py`).

**Bỏ `flags.py` và đồng bộ `des` theo `we_core` 2026-10-16** (người dùng: "tôi muốn des phải giống we_core, bạn hãy làm cho giống trước"; trong lúc làm, người dùng hỏi về file `flags.py` mới rồi quyết "Bỏ nhé, file này thừa"; luôn hỏi trước khi tạo file mới trong repo). **(1) `flags.py` bỏ ở CẢ HAI hệ:** xoá `we_core/src/flags.py`, `we_core/tests/test_flags.py` (55 test) và bản sao `des/src/flags.py` (mới tạo trong lượt đồng bộ này, chưa từng chạy); trả ba chỗ đọc cờ về bản chép tay như trước 2026-10-16 ở cả hai hệ: `configuration._to_bool` (4 chỗ gọi), `rules/trend_tf.trend_filter_enabled`, `rules/alternating.alternating_filter_enabled` (hành vi y hệt: fuzz 20.000 giá trị lúc gộp đã cho 0 lệch). `rules/alternating.py` lại là tầng đáy không import gì. Test `we_core` 281 → **226** (đúng 55 test của file đã xoá); ruff/vulture sạch. **(2) Đồng bộ `des` (chỉ sửa file có sẵn, KHÔNG tạo file mới):** `strategies/ma_cross.py` đổi sang luật trạng thái + lọc lặp cùng chiều + trend (chép từ `we_core`; chuỗi lý do trong field `comment` đổi thành "fast SMA above/below slow SMA; MACD histogram > / < 0"); `indicator.add_ma_cross_indicators` bỏ cột `prev_fast_ma`/`prev_slow_ma`; `strategies/combo.py` và `macd_break.py` dùng hằng `_BUY_REASON`/`_SELL_REASON` thay cột `raw_signal_reason` (chuỗi giữ nguyên từng ký tự); `rules/entries.py` bỏ cột `entry_time` (không ai đọc) và dùng chung `_add_close_market_entries` cho ma_cross/macd_break; `configuration.py` thêm cờ `ALTERNATING_FILTER_ENABLED` cho ma_cross, `check_trend_timeframe` (khung trend ≥ khung vào), bảng `trend_tf_by_symbol` theo (symbol, khung vào) (`_strategy_trend_tf_by_symbol`, `resolve_trend_tf`, `_raw_params_with_cell_trend_tf`, `StrategySpec.trend_tf_by_symbol`) và `trend_reference_for(key, symbol, timeframe)` tra theo ô; `redis_io/live_worker.py` gọi `trend_reference_for(strategy, symbol, timeframe)`, thêm `check_trend_timeframes()` trong `_validate_config` (cấu hình sai thì dừng trước khi kết nối Redis/xoá db15) và ghi `trend_tf_cells=N` ở dòng khởi động `event=trend_filter`; `chart_simul/payload.RULE_INPUTS["ma_cross"]` bỏ hai dòng "Prev fast/slow" (phần chart ngoài việc này CHƯA đụng, người dùng bảo bàn sau); `des_config.yaml` (quyền 600 giữ nguyên) và `des_config.example.yaml`: `ma_cross.alternating_filter_enabled: true`, `ma_cross.default_trend_tf` H4 → **H2** (như `we_core`) và khối `trend_tf_by_symbol` của cả 3 chiến lược chép từ `we_config.example.yaml` (44 + 44 + 88 ô; chọn theo SỐ tín hiệu, xem ghi chú "Khung trend riêng theo …"; trend của des vẫn TẮT nên chưa ảnh hưởng đầu ra). Không port tham số `_RUN_TF` mà `we_core.run_strategy*` đặt vào params (không ai đọc). **(3) Kiểm chứng "giống":** so cây cú pháp từng hàm: `indicator`, `rules/{alternating,entries,trend_tf}`, `strategies/{combo,ma_cross,macd_break}`, `trend_methods/knn` GIỐNG HỆT giữa hai hệ (0 hàm khác; riêng `configuration.py` còn khác ở cách nạp config `require` vs `_require`, phần SQL/ngày của `we_core` và hàm `trend_*_for`/`alternating_filter_for` của `des`); chạy CẢ HAI hệ trên cùng nến SQL thật (6.000 nến mỗi chuỗi, chỉ SELECT) cho 176 (chiến lược, symbol, khung vào): tham số đã chuẩn hoá giống 176/176, khung trend tra ra giống 176/176, khung kết quả `run_strategy` giống hệt từng cột/dtype 176/176, `run_strategy_with_trend_reference` với khung trend theo ô giống hệt 176/176. Test `des` 166 → **187**: cập nhật golden ma_cross (7 BUY / 7 SELL, SELL đầu ở bar 33, digest `25df7175…`; trước 7/6, bar 86, `d456518f…`; số còn lại khi bật trend H2/H3/H4 = 13/3/0 trên 14 raw), chuỗi lý do, khẳng định cờ/config mẫu của ma_cross, 5 test giả `trend_reference_for` nhận thêm (symbol, khung vào); thêm 21 test vào `test_des_engine.py` (bản tham chiếu độc lập 150 khung ngẫu nhiên × 4 tổ hợp cờ × 3 chiến lược, luật trạng thái ma_cross, thứ tự xen kẽ→trend, `check_trend_timeframe`, đọc/kiểm bảng khung trend và báo mọi ô sai một lần, tra theo ô + override tường minh, config thật/mẫu thoả quy tắc, pre-flight của live_worker dừng trước khi nối Redis); ruff/vulture sạch. **(4) CHƯA áp dụng vào tiến trình đang chạy:** `des-signal` (PID 497402, từ 14/10 12:26) và server chart des giữ mã cũ trong bộ nhớ nên db15 hiện tại vẫn là luật ma_cross CŨ (~31 nghìn tín hiệu từ sim 2023-03-21); mã mới chỉ có hiệu lực sau khi restart `des-signal`, và restart XOÁ db15 (cơ chế của des) rồi chạy tiếp từ nhịp DFS lúc đó (DFS ở nhịp ~218 nghìn/493 nghìn, còn ~6,4 ngày) — cách áp dụng (restart ngay / đợi DFS xong giữ kết quả luật cũ làm đối chứng / chạy lại DFS từ đầu) CHƯA được người dùng chọn. Nếu một tiến trình `des-signal` tự khởi động lại sau sự cố thì nó nạp mã + config mới ngay. **Quy ước đọc:** các ghi chú nói "`des` chưa đồng bộ ma_cross", "`des` không có cờ lọc lặp cho ma_cross", "166 test" (des), "281 test" (we_core), `flags.py`/`test_flags` là trạng thái lúc đó. Bản sao lưu `des/src`, `des/tests` và config mẫu trước khi sửa ở scratchpad (`des_sync_before`; config thật chỉ có bản che mật khẩu).

**Xoá `default_trend_tf` (we_core + des) 2026-10-16** (người dùng hỏi `default_trend_tf` dùng làm gì khi mỗi chiến lược × symbol × khung vào đã có khung trend riêng; sau khi mình nêu ba vai trò phụ của nó — khoá bắt buộc, dự phòng khi thiếu ô, giá trị hiển thị khi không chỉ rõ ô — người dùng hỏi "về bản chất là thừa phải ko?" rồi chốt "Ok, xóa nhé"). **Đã làm (chỉ sửa file có sẵn):** (1) config: xoá `default_trend_tf` khỏi cả 4 file (`we_config.yaml`, `we_config.example.yaml`, `des_config.yaml`, `des_config.example.yaml`; quyền file thật giữ 600), sửa chú thích của bảng; (2) `configuration.py` (hai hệ): `_strategy_trend_defaults` không còn `TREND_TF`; `resolve_trend_tf(by_symbol, symbol, entry_tf)` trả `""` khi thiếu ô; `_normalize_trend_reference_params`: `TREND_TF` = đè tường minh > ô trong bảng > `""` (chưa xác định, không raise ở đường không trend); `_strategy_trend_tf_by_symbol` BẮT BUỘC (`KeyError` nếu vắng khoá) và phải ĐỦ Ô (mọi symbol_x × mọi khung vào; thiếu → cùng `ValueError` liệt kê mọi lỗi); (3) `we_core`: `export_cli.resolve_trend_reference` báo "No trend timeframe configured for SYM TF; pass --trend-tf." khi chưa xác định; dashboard `_load_and_run` trả lỗi (HTTP 400) "…choose Trend TF." trước khi tải SQL, bỏ khoá `defaultTrendTf` khỏi cấu hình trang và JS `configuredTrendTf` rơi về chuỗi rỗng (ô chọn lấy khung nhỏ nhất được phép); thông báo/chú thích của worker bỏ nhắc default; (4) `des`: `trend_reference_for(key)` không kèm (symbol, khung vào) trả `(enabled, "")`, dòng log khởi động `event=trend_filter` bỏ trường `trend_tf=` (còn `trend_tf_cells=N`); (5) script `t008_tune_trend_tf.py`: luật hoà đổi thành "chọn khung lớn hơn" (bỏ ưu tiên default); các bảng đã tạo bằng luật cũ GIỮ NGUYÊN, không chạy lại. **Hành vi đầu ra KHÔNG đổi** vì cả ba bảng đã đủ ô (44 + 44 + 88): so lại 176 (chiến lược, symbol, khung vào) giữa `des` và `we_core` trên SQL thật — tham số chuẩn hoá, khung trend tra ra, kết quả không trend và kết quả có trend đều giống hệt 176/176; cây cú pháp mọi module dùng chung vẫn giống hệt. **Test:** `we_core` 226 → **227** (viết lại các test dựa vào khung mặc định, thêm test lỗi rõ khi thiếu ô ở dashboard/CLI, test bảng bắt buộc + đủ ô; xoá fixture `khung_trend_mac_dinh_theo_chien_luoc` vì hết người dùng), `des` **187** (giữ số lượng, sửa 3 test); ruff/vulture sạch. **Không restart gì:** `we-worker`, dashboard 8517, `des-signal` và server chart des đang chạy giữ mã + config cũ trong bộ nhớ (đầu ra không đổi nên không cần); lần restart kế nạp mã mới cùng config không còn khoá `default_trend_tf` nhất quán. **Quy ước đọc:** các ghi chú ngày 2026-10-16 nhắc `default_trend_tf`, "khung trend mặc định H2/H4", "thiếu ô thì dùng default", `defaultTrendTf` là trạng thái lúc đó.

**Bản đồ db mới (db13 nến / db14 tín hiệu / db15 tín hiệu đã lọc trend) + `des` ghi hai đầu ra + trang chart `/trend` 2026-10-16** (người dùng: "tôi muốn tạo thêm 1 trang thứ 3 ... có lọc trend và hiển thị biểu đồ trend, và hiển thị theo chính xác
config trong des. Ví dụ combo us30 khung H1 dùng trend H4 thì chỉ hiển thị đúng thế thôi, ko chuyển trend gì cả", kèm "des cũng phải publish signal lên 1 kênh nữa, nhưng có yếu tố trend"; rồi chốt bản đồ db: "dfs sẽ dùng kênh db13 ..., des
... db14 ..., dùng kênh db15 để gửi signal có lọc trend"; các quyết định: DFS đã đổi sang db13 và restart, "Lần chạy dỡ ko sao, restart lại hết", không hệ nào khác đọc db14/db15, đầu ra trend KHÔNG cần Pub/Sub, giữ cờ `trend_filter_enabled`
"giống y như phía we_core", và cho phép tạo `des/src/chart_simul/trend_page.py`). **`we_core` KHÔNG đổi, `we-worker` KHÔNG restart.** **Bản đồ:** db13 = nến do DFS ghi (`L_DFS_*`, `dfs:*`); db14 = tín hiệu `des` không lọc trend (có Pub/Sub);
db15 = tín hiệu `des` đã lọc trend (cùng khuôn khoá `L_DES_*`, không Pub/Sub). **Đổi (chỉ `des` + `run_we.sh`):** `des_config.yaml` (thật, quyền 600 giữ nguyên) và `des_config.example.yaml`: `redis.input.db` 14 → 13, `redis.output.db` 15 → 14,
mục mới `redis.output_trend {db: 15, key_prefix: L_DES}`; `redis_io/candle_reader.normalize_redis_config` đòi `redis.output_trend.{db,key_prefix}`; `redis_io/signal_publisher.publish_signal(channel_prefix=None)` (script Lua chỉ `PUBLISH`
khi đối số kênh không rỗng); `redis_io/live_worker.py`: `process_candle_update(..., trend_output_client=None)` + `_publish_signal_row` + `_process_trend_output`, `_validate_config` (ba db khác nhau, `output_trend.key_prefix` không rỗng,
TỪ CHỐI `trend_filter_enabled: true` — khác `we_core`, xoá khối chặn nếu muốn y hệt), `run_forever` tạo thêm client và xoá CẢ HAI db đầu ra đúng một lần mỗi tiến trình, log khởi động `redis_output_trend_db=…`; `chart_simul`: `source.py` (client + tiền tố db15,
`signal_count/read_signals(..., trend=True)`), `payload.py` (`TrendViewData`, `trend_pane`, `trend_line_points`, `trend_overlays`, `trend_change_marks`, `filtered_rows`, `build_trend_payload`), `server.py` (`/trend`, `/api/trend`, `build_trend_view`,
`_exact_trend_signal_fn`), `trend_page.py` MỚI, liên kết "Trend chart" ở `live_page.py` và `mtf_page.py`; `run_we.sh` mục 6 và chữ db mới; `des-signal.service` (chỉ dòng Description; đã cài lại + `daemon-reload`, không restart); comment/docstring nhắc db cũ.
Hành vi chi tiết của hai đầu ra và của trang `/trend` nằm ở mục 10. **Test:** `des` 187 → 194 (giai đoạn des: test hai đầu ra, cấu hình, cờ, `publish_signal(None)`, cô lập lỗi đường trend, xoá hai db) → 210 (+16 cho chart: `RedisSource` đọc db15,
payload trend/filtered, `/api/trend` đủ trạng thái, dấu vân tay, lỗi 400/503, cửa sổ `exact_signal`, chạy hàm thật, trang và liên kết, tiếng Anh, kiến trúc 7 file) → **211** (2026-10-17: +1 test khoá thứ tự pane; pane khung trend chuyển lên TRÊN cùng theo yêu cầu người dùng; sửa lỗi cột "Reason" của dòng `filtered` ghi KHUNG VÀO LỆNH thay vì khung trend — test cũ khẳng định chính cái sai đó nên đã sửa test); `we_core` 227 không đổi; ruff/vulture sạch.
**Triển khai 2026-10-16:** `systemctl --user restart des-signal` lúc 16:26:53 UTC (PID 497402 → 586582; unit đã cài giống hệt bản trong repo); log: `startup … redis_input_db=13 redis_output_db=14 redis_output_trend_db=15`, `signals_cleared db=14 keys=0`
và `db=15 keys=32194` (tín hiệu của lần chạy cũ — người dùng đồng ý bỏ), `subscribed db=13`; db14 vốn đã trống (nến DFS cũ do bên DFS dọn). Server chart (8518) khởi động lại trên mã cuối (PID hiện 587664). DFS đang chạy run `710e2940ffcb`, bắt đầu TRƯỚC
khi des bật (des start khi giờ ảo ≈ 2022-01-02 17:00) nên des KHÔNG thấy các mốc trước đó; muốn đủ mọi mốc từ tick 0: `dfs stop` → bật des → `dfs resume`/chạy lại (người dùng quyết). **Kiểm chứng:** (a) Chrome thật (headless, cổng/profile tạm,
Redis chỉ đọc): `/trend` cho combo US30 H1 (trend H4), ma_cross BTCUSD M30 (trend H1) và macd_break US30 H1 (trend H1) vẽ đủ ba chart, không lỗi JS, ba chart thẳng hàng theo thời gian sau khi zoom bằng chuột, đổi chiến lược bằng ô chọn chạy đúng;
(b) so với `we_core` (đọc Redis db0/db1 + db14/db15; vùng 2022-01-02 16:00 → 2022-02-20 05:15 giờ ảo = 14.346 mốc, chừa 20 phút cuối): **db14 vs DB0** — des 5.061, we_core 5.062, trùng 5.061 (cùng hướng, ATR, entry), 0 chỉ-des, 1 chỉ-we_core;
**db15 vs DB1** — 2.801 / 2.801 trùng 100%, 0 lệch. Tín hiệu chỉ-we_core duy nhất là ma_cross BTCUSD M10 nến mở 2022-01-02 16:10 (đóng 16:20), trước tín hiệu sớm nhất des ghi (đóng 17:00): nhiều khả năng đóng trước khi des bật (ước lượng từ nhịp DFS,
KHÔNG chứng minh được vì không có log từng sự kiện ở mức INFO); toàn bộ phần còn lại — gồm nhiều ngày trong tuần, tải nặng — 0 lỡ/thừa; (c) 2026-10-17 00:08 UTC: `des-signal` active, NRestarts 0, 0 WARNING/ERROR của tiến trình mới, DFS tick 14.343
(giờ ảo 2022-02-20 05:00), db14 5.237 khoá, db15 2.973 khoá. **Cần biết:** (1) mốc kiểm của chart là giờ MỞ nến của tín hiệu sớm nhất nên có thể báo `MISSED` giả cho nến đóng trước khi des chạy, tự hết khi trôi khỏi cửa sổ (đã biết từ 14/10, chưa sửa); (2) pane trend
vẽ cả `KNN Avg` (RMA 50 của KNN, theo khai báo `OVERLAYS` của khối; chậm hơn giá nhiều khi giá chạy mạnh) nên trục giá của pane giãn và nến trend trông dẹt — giống dashboard WE; (3) một số cặp có seed thưa ở quá khứ xa (BTCUSD M30: 300 nến cuối
kéo từ 2017-08 tới 2022-01 lúc DFS mới bắt đầu) nên chart đầu lần chạy có khoảng trống lớn — dữ liệu, không phải lỗi chart; (4) `/trend` tính KNN hai lần mỗi lần dựng (một trong `run_strategy_with_trend_reference`, một cho pane), thêm ~50–100 ms, chấp nhận;
(5) `des` vẫn chỉ xét nến cuối của cặp nên các hạn chế cũ (hàng đợi một luồng...) không đổi; đường trend thêm tải nhưng chưa đo CPU riêng. **Quy ước đọc:** các ghi chú ngày TRƯỚC 2026-10-16 nhắc "DFS ghi db14", "des ghi db15", "một đầu ra",
"`chart_simul` 6 file / chưa hỗ trợ đường trend" là bản đồ/thiết kế lúc đó; ghi chú "Bỏ flags.py và đồng bộ des theo we_core" nói `des-signal` còn mã cũ và db15 là luật ma_cross cũ hết hiệu lực từ 2026-10-16 16:26:53 UTC.

**Thêm nhóm forex EURUSD/USDJPY/GBPUSD/USDCAD với entry theo ATR cho combo (`we_core`) 2026-10-20** (người dùng: "tôi muốn triển khai lên dữ liệu cho 4 symbol này nữa ... entry kiểu `high/low ± a% × ATR(5)`", "chỉ làm trên we_core", "không đổi cho nhóm symbol cũ, chỉ làm entry kiểu mới này cho nhóm 4 symbol forex"; a% = **5% chung** cho cả 4 cặp, làm tròn **5 chữ số (3 với USDJPY)**; người dùng chọn cả hai). **Quy tắc entry combo giờ có HAI kiểu, mỗi symbol đúng một:** (1) nhóm cũ 11 symbol (`strategies.combo.symbol_x`): giữ nguyên `high + X` / `low − X` với X cố định, KHÔNG đổi một byte (xem kiểm chứng); (2) nhóm mới `strategies.combo.symbol_entry_atr_pct` (đơn vị %, hiện EURUSD/USDJPY/GBPUSD/USDCAD = 5.0): `entry = high + a% × atr` (BUY) / `low − a% × atr` (SELL), `atr` = ATR chu kỳ `atr_period` (5) tại ĐÚNG nến tín hiệu (có sẵn khi nến đóng, không nhìn trước). Một symbol ở cả hai nhóm là lỗi lúc nạp config. Ý nghĩa của X (đã trao đổi với người dùng 2026-10-20, CHƯA đối chiếu với cBot Combo.cs/DE): mức vượt qua mép nến tín hiệu để xác nhận phá ra rồi mới đặt lệnh chờ. **Đổi (chỉ `we_core`; `des`, `des_config.yaml`, DFS KHÔNG đụng):** `configuration.py` (`COMBO_SYMBOL_ENTRY_ATR_PCT`, `OPERATING_SYMBOLS` = nhóm X + nhóm ATR dùng cho bảng khung trend của cả 3 chiến lược, `check_trend_timeframes`, bootstrap và lọc trigger của worker; param `ENTRY_ATR_PCT` có trong `COMBO_PARAM_FIELDS` nên đè được bằng `--param`/dashboard; `PRICE_DECIMALS_BY_SYMBOL` + `price_decimals(symbol)`), `rules/entries.py` (`add_combo_entries`: `ENTRY_ATR_PCT` khác None thì dùng % ATR và bỏ qua X, thiếu cột `atr` thì `ValueError`), `we_signal/export_cli.py` (`signal_frame`/`to_csv` thêm `decimals`, mặc định 2 nên mọi nơi gọi cũ y nguyên; `export_signals` truyền `price_decimals(symbol)`), `we_signal/redis_io/worker.py` (đổi `COMBO_SYMBOL_X` → `OPERATING_SYMBOLS` ở 3 chỗ; `_compute_and_publish` truyền `price_decimals(symbol)`), config thật (quyền 600 giữ nguyên) và config mẫu: khối `strategies.combo.symbol_entry_atr_pct`, `defaults.price_decimals` {EURUSD: 5, GBPUSD: 5, USDCAD: 5, USDJPY: 3} (khoá bắt buộc, có thể `{}`; symbol không liệt kê = 2) và 4 dòng `trend_tf_by_symbol` mỗi chiến lược (bảng bắt buộc đủ ô). **Cả ba chiến lược chạy cho 4 cặp mới** (tập symbol dùng chung): worker 176 → **240 cặp** (15 symbol × 16 khung-chiến lược); `ma_cross`/`macd_break` vào ở `close` nên chỉ cần số chữ số lấy từ `price_decimals`. **Bảng khung trend của 4 cặp** do `research_notes/scripts/t008_tune_trend_tf.py` chọn theo SỐ tín hiệu nhiều nhất từ 2024-01-01 như nhóm cũ (script thêm tuỳ chọn `--symbols`; 18 ô gần hoà <5%): combo mọi cặp vào H1→H4, H2→H2, H3→H3, H4→H4; ma_cross M10→H4, M20/M30/M45→H1; macd_break theo từng cặp (xem config). Cùng lưu ý với bảng cũ: tiêu chí là số lượng, không phải chất lượng. **Sai sót tôi đã sửa lại:** trước đó tôi nói SQL cũng bị làm tròn 2 chữ số; đo 2026-10-20 cho thấy SQL của 4 cặp này CÓ ĐỦ độ chính xác (EURUSD/GBPUSD/USDCAD 5 chữ số, USDJPY 3, 0% nến phẳng O=H=L=C); chỉ `atr`/`entry` ở bước xuất (`signal_frame`) và `payload.py` của dashboard làm tròn 2 chữ số. **Dashboard CHƯA hiển thị 4 cặp forex:** ô chọn symbol vẫn là 11 symbol cũ (`_operational_symbols` giữ `COMBO_SYMBOL_X`) vì `signal_display/payload.py` làm tròn giá nến 2 chữ số nên chart forex sẽ phẳng; `/api/scan` gọi thẳng bằng URL vẫn chạy (giá hiển thị làm tròn). Muốn hiện chúng phải làm tròn theo symbol ở payload (chưa làm, chờ quyết định). **Kiểm chứng:** (a) nhóm cũ: so `signal_frame` mới với Redis DB0 hiện tại (bartime < 2026-10-19) ở 80/80 cặp (combo 44 cặp + ma_cross/macd_break × US30/GOLD/BTCUSD): giống từng dòng/field, 0 lệch; (b) 4 cặp trên SQL thật (cả 3 chiến lược, mọi khung, từ 2022-01-01): chạy không lỗi; combo mọi tín hiệu có `entry − high = 5% × atr` (BUY) và `low − entry = 5% × atr` (SELL); số tín hiệu DB0 cộng mọi khung: combo 3.456 / 3.515 / 3.420 / 3.196 (EURUSD / GBPUSD / USDCAD / USDJPY), ma_cross 10.924 / 10.999 / 10.916 / 10.599, macd_break 2.909 / 2.970 / 2.935 / 2.628; DB1 ⊆ DB0 ở 3 ô thử (combo EURUSD H1: 1.679 → 573 với trend H4; ma_cross USDJPY M30: 1.674 → 1.401; macd_break GBPUSD H2: 94 → 90); (c) CSV thử vào thư mục tạm: EURUSD `atr` 0,00101 / `entry` 1,12558 (5 chữ số), US30 vẫn 2 chữ số; `exports/` không đụng (176 file); (d) test `we_core` 227 → **238** (thêm 11, sửa 2 test giả định 11 symbol, riêng test dashboard "chỉ liệt kê symbol vận hành" nay chỉ kiểm danh sách vì chuỗi "EURUSD" xuất hiện trong bảng khung trend nhúng ở trang), `des` 211 giữ nguyên, ruff/vulture sạch. **Số đo căn cứ chọn a%** (combo DB0, từ 2024-01-01, 21.153 tín hiệu 11 symbol cũ): bảng a% của người dùng cho đệm trung bình = 0,98 × X; tỉ lệ giá chạm entry trong 1 / 3 / 6 nến sau: 59,3 / 74,3 / 81,6% (X cũ) so với 61,4 / 75,6 / 82,4% (a% mới); a% chung theo symbol làm đệm ở H1 chỉ bằng 0,5–0,8 × X và ở H4 bằng 1,1–1,7 × X; người dùng quyết định GIỮ a% chung theo symbol cho nhóm cũ và nhóm cũ KHÔNG chuyển sang kiểu ATR (chỉ nhóm forex dùng a%); các phép đo này chưa có đánh giá lãi/lỗi. **Chưa restart:** `we-worker` (PID 578497, từ 2026-10-16 12:01) và dashboard 8517 vẫn giữ mã/config cũ trong bộ nhớ; Redis DB0/DB1 chưa có 4 cặp forex cho tới lần restart `we-worker` kế (bootstrap dự kiến dài hơn ~36% so với ~6 phút do thêm 64 cặp). Khi restart: nội dung và tên key của 176 LIST cũ không đổi (đã kiểm ở (a)); thêm 64 LIST forex mới (4 symbol × 16 khung-chiến lược, LIST chỉ xuất hiện khi có tín hiệu đầu tiên). BO20 đọc DB0/DB1 và `atr`/`entry` của forex có 5 (JPY 3) chữ số thập phân thay vì 2 — người dùng tự báo BO20 khi cần. Sao lưu mã/test/config trước khi sửa ở scratchpad (`fx_before`).

**Restart `we-worker` có chọn phạm vi 2026-10-20** (người dùng: "một cơ chế linh hoạt restart, restart sẽ xoá db0 của symbol/timeframe/chiến lược được chọn, hoặc xoá full"; người dùng chốt: xoá **DB0 + DB1** của cặp được chọn, các cặp còn lại **giữ nguyên và không tính lại**, phạm vi truyền bằng **tham số dòng lệnh / biến môi trường** — không sửa menu `run_we.sh`, không sửa unit systemd). **Cách dùng:** không đặt gì = restart FULL như cũ (xoá sạch DB0 + DB1 của ta, bootstrap mọi cặp). Đặt phạm vi: `--strategy combo,ma_cross --symbol EURUSD,USDJPY --tf H1,H2` (mỗi mục bỏ trống = tất cả ở chiều đó; không phân biệt hoa thường; chấp nhận phân cách bằng dấu phẩy, chấm phẩy hoặc khoảng trắng) hoặc biến `WE_SCOPE_STRATEGY` / `WE_SCOPE_SYMBOL` / `WE_SCOPE_TF` (tham số thắng biến). Chạy foreground: thêm tham số vào lệnh `python -m …redis_io.worker`. Chạy qua systemd: `systemctl --user set-environment WE_SCOPE_SYMBOL=EURUSD,USDJPY,GBPUSD,USDCAD` rồi `systemctl --user restart we-worker`, **sau đó `systemctl --user unset-environment WE_SCOPE_SYMBOL`** (biến còn trong user manager thì lần worker tự khởi động lại sau sự cố cũng dùng phạm vi đó thay vì xoá full). **Đổi:** `publisher.clear_pair_signals()` (xoá LIST + mọi HASH `<list_key>:*` của ĐÚNG một cặp, khớp chính xác: US30 không đụng US300, COMBO không đụng MACD_BREAK, H1 không đụng H10); `worker.select_scope_pairs()` (lọc 240 cặp; giá trị lạ hoặc tổ hợp không ra cặp nào, vd combo + M10, là `ValueError` báo trước khi chạm Redis → `fatal_config_error`, exit 1), `worker.parse_scope()` (đọc tham số/biến), `run_forever(config, scope_pairs=None)` (có phạm vi: lượt đầu chỉ `clear_pair_signals` DB0 + DB1 rồi `bootstrap_all(pairs=…)` cho đúng các cặp; log `restart_scope` ở `main()` và `signals_cleared_scoped` kèm số key đã xoá; `bootstrap_start` có thêm field `scoped`), `main(argv)` (không còn đọc `sys.argv` khi gọi từ test). **Phạm vi chỉ áp dụng cho lượt khởi động đầu của tiến trình:** nếu sau đó Redis rớt rồi nối lại thì bootstrap tính lại MỌI cặp như cũ (không xoá), để Redis mất dữ liệu thì cặp ngoài phạm vi cũng được nạp lại. **Cần biết:** (1) cặp ngoài phạm vi giữ dữ liệu do mã/config cũ — dùng phạm vi khi thay đổi chỉ liên quan nhóm cặp (vd thêm 4 forex: `--symbol EURUSD,USDJPY,GBPUSD,USDCAD` nạp 64 cặp mới, 176 cặp cũ giữ nguyên); đổi luật hay tham số chung (cờ lọc, khối trend, khung trend của nhiều cặp) thì phải restart FULL; (2) vẫn kiểm `check_trend_timeframes()` trước khi xoá như restart full; (3) phạm vi trống/không đặt = FULL, nên một biến môi trường còn sót lại là cách duy nhất để restart tự động ra kết quả khác FULL. **Kiểm chứng:** test `we_core` 238 → **246** (thêm 8: xoá đúng một cặp và không lan sang cặp giống tên, cặp chưa có dữ liệu xoá 0 key, lọc phạm vi và báo lỗi, đọc tham số/biến môi trường với tham số thắng, `run_forever` FULL giữ nguyên, `run_forever` có phạm vi + nối lại Redis tính lại tất cả, `main` dừng khi phạm vi sai), ruff/vulture sạch; Redis thật (DB5 trống, key tạm tiền tố riêng, đã dọn về 0 key): `clear_pair_signals` xoá đúng 6 key (1 LIST + 5 HASH) của US30 H1 combo, 19 key của 4 cặp còn lại (US30 H1 MACD_BREAK, US30 H2, US300 H1, EURUSD H1) còn nguyên; DB0/DB1 đang chạy KHÔNG đụng. **Đã dùng ngay ở restart 2026-10-20 15:04 UTC (4 cặp forex), xem ghi chú kế tiếp.** Sao lưu `worker.py`, `publisher.py`, test trước khi sửa ở scratchpad (`scope_before`).

**Restart `we-worker` có phạm vi cho 4 cặp forex 2026-10-20 15:04:42 UTC** (người dùng: "hãy restart với 4 symbol đó"; PID 578497 chạy từ 2026-10-16 12:01:16 → PID 854061). Lệnh: `systemctl --user set-environment WE_SCOPE_SYMBOL=EURUSD,USDJPY,GBPUSD,USDCAD` → `restart` → `unset-environment` ngay sau đó (đã kiểm `show-environment`: không còn `WE_SCOPE_*`; biến chỉ cần có mặt lúc tiến trình khởi động). **Kiểm trước (chỉ đọc):** unit đã cài == bản trong repo; DB0 177.262 khoá / DB1 97.424 khoá, đều 176 LIST, 100% tiền tố của ta, 0 khoá lạ, 0 LIST forex; `NUMSUB df:events:backfill` = 1; mã nạp được, `check_trend_timeframes()` pass, phạm vi chọn đúng 64 cặp (combo 16 + ma_cross 16 + macd_break 32); chụp 176 cặp cũ × 2 DB (274.334 dòng) ra scratchpad. **Log:** `restart_scope pair_count=64 symbols=EURUSD,GBPUSD,USDCAD,USDJPY`, `signals_cleared_scoped pair_count=64 keys_deleted=0 keys_deleted_trend=0` (chưa có gì để xoá), `bootstrap_start … scoped=True`, **`bootstrap_done` 142,8 s (15:07:16), 64 cặp, 128 `pair_refreshed` (DB0 + DB1), 0 WARNING/ERROR/CRITICAL**, `subscribing`, `NUMSUB` = 1, `NRestarts` = 0. **Đối chiếu sau (15:07–15:09):** (a) 176 cặp cũ × 2 DB: so lại với bản chụp trước restart → giống hệt **352/352 LIST**, 274.334 dòng y nguyên (cơ chế phạm vi không đụng cặp ngoài phạm vi); (b) DB0 và DB1 nay mỗi bên **240 LIST** (64 LIST forex mới), 0 khoá lạ; (c) 64 cặp forex so với tính lại độc lập từ SQL bằng mã sản phẩm (dòng bartime < 2026-10-20 00:00, DB1 với khung trend tra theo ô): **DB0 khớp 64/64, DB1 khớp 64/64** từng dòng/field (68.445 dòng DB0, 37.703 dòng DB1); mẫu: EURUSD H1 combo `atr 0.00169 entry 1.12317` (5 chữ số), USDJPY H1 combo `atr 0.127 entry 158.251` (3 chữ số). **Dashboard 8517 chưa restart** (vẫn mã cũ; chưa hiện 4 forex, xem ghi chú forex). **`des` không đụng.** Đợt backfill của DF kế tiếp lúc 15:15 UTC sẽ là lần đầu đường delta chạy với 4 cặp forex và mã mới — CHƯA quan sát. **Quy ước đọc:** các ghi chú ngày 2026-10-20 ở trên nói "Chưa restart / Redis còn 176 cặp / DB0-DB1 chưa có forex" hết hiệu lực từ 15:04:42 UTC; các ghi chú nói `we-worker` PID 578497 là tiến trình cũ. Bản chụp trước/sau + bộ đối chiếu ở scratchpad (`before_scope.pkl`, `after_scope.pkl`, `snap.py`, `verify_redis_fx.py`).

**Dashboard hiện 4 cặp forex 2026-10-20** (người dùng: "trên dashboard tôi chưa thấy hiển thị, hãy restart dashboard luôn" rồi chốt kế hoạch "Ok, làm nhé"; chỉ `signal_display/`, không đụng worker/Redis/des). **Đổi:** `payload.py` (`_round2`, `_column_points`, `candlestick_points`, `line_points`, `histogram_points`, `trend_line_points`, `signal_table_rows` nhận thêm `digits` mặc định 2 → symbol cũ giữ nguyên từng byte), `server.py` (`price_decimals(symbol)` truyền `digits=` vào mọi hàm dựng chuỗi, `meta.priceDigits` mới trong `/api/scan`, `/api/export` truyền `decimals=price_decimals(symbol)`, `_operational_symbols()` dùng `OPERATING_SYMBOLS` → 15 symbol), `configuration.py` (`tuned_symbols` của combo = `OPERATING_SYMBOLS`, nếu không ô chọn của combo vẫn chỉ có 11), `live_page.py` (`priceDigits` + `priceFormat()` đặt `precision`/`minMove` cho nến giá, nến trend, histogram MACD và mọi đường overlay). Form có thêm ô "Entry % ATR" (override `ENTRY_ATR_PCT`, "auto" = lấy từ config). Symbol ngoài vũ trụ vận hành (vd AUDCAD gọi thẳng bằng URL) vẫn làm tròn 2 chữ số. **Kiểm chứng:** (a) dữ liệu cũ KHÔNG đổi: so `/api/scan` của dashboard đang chạy (mã cũ) với mã mới trên SQL thật, 8 ca (combo/ma_cross/macd_break × US30/GOLD/BTCUSD/DE40/HK50/J225/US500/UK100, có/không trend, 1.500 nến) giống hệt 8/8 (sau khi bỏ `meta.priceDigits`) và 2/2 CSV `/api/export`; bộ fuzz bản lặp `payload_reference.py` vẫn qua (test `giong_het_ban_lap` nay bọc bản cũ bằng adapter bỏ `digits=2`); (b) Chrome thật (headless, cổng 18601–18603/profile tạm, SQL chỉ đọc): EURUSD H4 combo trục giá `1.10000–1.19000` (5 chữ số), nến không phẳng, MA và MACD histogram có giá trị (trục ±0.00200), 27 tín hiệu; USDJPY trục `150.000–168.000` (3 chữ số); US30 vẫn `50000.00` (2 chữ số); không lỗi JS; (c) test `we_core` 246 → **251** (thêm 5: payload `digits` mặc định 2 và 5, `/api/scan` forex 5 chữ số + chart không phẳng + MACD khác 0, bảng tín hiệu và CSV 5 chữ số + USDJPY 3, ô chọn symbol có 4 cặp forex và combo `tunedSymbols`, trang có `priceFormat`; sửa 4 test giả định 11 symbol / meta cũ), `des` 211 giữ nguyên, ruff/vulture sạch. **ĐÃ restart dashboard 8517 15:22:43 UTC** (PID 855454 → 856666, `nohup` như launcher, log `/tmp/wisdom_engine_dashboard.log`): `/health` ok, ô symbol có đủ 15 symbol, `/api/scan` GBPUSD ma_cross M30 `priceDigits` 5; trình duyệt đang mở trang phải tải lại (F5). **Quy ước đọc:** các đoạn ghi chú ngày 2026-10-20 ở trên nói "dashboard CHƯA hiển thị 4 cặp forex / ô chọn vẫn 11 symbol / payload làm tròn 2 chữ số làm chart phẳng" hết hiệu lực từ 15:22:43 UTC (restart trước đó 15:14:51 cùng ngày chỉ nạp mã cũ chưa có phần này). Sao lưu `signal_display/`, `configuration.py` và test trước khi sửa ở scratchpad (`dash_before`).

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
đó. Ngoại lệ có chủ đích: thư mục `des/` (mục 10) là **hệ con mô phỏng** engine OG
đó, đọc nến do DFS phát lên db13 và ghi tín hiệu ra db14 (không lọc trend) + db15 (đã lọc trend), không dùng chung code với `we_core/src/`. `entries.py` chỉ điền `entry_price` (các cột `entry_time`, `sl_price`, `tp_price`,
`risk_reward` đã bỏ 2026-10-16 vì không ai đọc).

**3 chiến lược đăng ký** (`configuration.STRATEGIES`):

| Key | Nhãn | Timeframe | Giới hạn cứng? | Trend filter KNN | Worker Redis chạy? |
|---|---|---|---|---|---|
| `combo` | Combo | khuyến nghị H1–H4 | Không | Có | Có (H1–H4) |
| `ma_cross` | MA Cross | M10/M20/M30/M45 | Có (`run_strategy` raise) | Có (kiểu Combo: chống trùng trước, rồi khớp đúng 1 bar — mục 6) | Có (M10–M45) |
| `macd_break` | MACD Break | M10–M45, H1–H4 | Có | Có (kiểu Combo: khớp đúng 1 bar) | Có (M10–M45 + H1–H4) |

`macd_break` (thêm 2026-10-06) là bản biến tấu của mẫu breakout trong
research_notes S005 (xem git history của `breakout_atr.py` đã đổi tên), **chưa có
căn cứ backtest** — các số liệu T001/F057–F059 trong research_notes đo cho
`breakout_atr` cũ (có trailing stop, không có MACD), KHÔNG áp dụng cho nó.
`rules/trend_tf.py` **không phải** chiến lược — là module lọc theo hướng
khung lớn dùng chung; mỗi phương pháp xác nhận xu hướng (KNN, ...) là một khối riêng trong `trend_methods/`.

## 2. Nguyên tắc thiết kế — bắt buộc tuân thủ khi sửa/thêm code

- **Chỉ code khi đã được chốt/đồng thuận triển khai rõ ràng — áp dụng cho MỌI
  session.** Không tự ý sửa/thêm/xoá code hay file ngoài đúng phạm vi vừa
  được yêu cầu, kể cả khi thấy hợp lý hay "hiển nhiên". Bàn bạc, đề xuất,
  phân tích — làm tự do. Chỉ thực thi (sửa file, chạy lệnh đổi trạng thái)
  khi người dùng xác nhận rõ ràng (vd "triển khai đi", "làm đi", "chốt vậy").
  Không tự suy diễn đồng ý từ ngữ cảnh.
- **Hành động phá huỷ trên Redis → kiểm tra đối tượng trước khi làm.** Từ
  2026-10-05 Redis trên OG8 chỉ dành cho `wisdom_engine` (db0/db1) và hệ mô phỏng
  DFS/des (db13/db14/db15, mục 5 và 10), nhưng vẫn phải xem `keyspace` trước khi
  `FLUSH*`: nếu thấy key lạ ở db0/db1 (không phải `L_PastSignal*`)
  thì dừng và hỏi — có thể hệ khác đã dùng lại server này.
- **Mỗi file một trách nhiệm duy nhất** (bảng mục 3).
- **Phụ thuộc một chiều, không vòng lặp.** `db_connector.py`/`indicator.py` và `rules/entries.py`/`rules/alternating.py`
  là tầng đáy (không import gì trong `we_core/src/`; `rules/` = luật dùng chung cho mọi chiến lược, từ 2026-10-15,
  không còn nằm rời ở `src/` và không bao giờ ở `strategies/`). `trend_methods/*.py` (mỗi file một khối xác nhận
  xu hướng) chỉ import các hàm toán của `indicator.py`, không import lẫn nhau và KHÔNG import `rules/trend_tf.py`
  (sẽ vòng import). **Ngoại lệ có chủ đích (2026-10-15): `rules/trend_tf.py`** là cửa vào duy nhất của phần xu hướng —
  nó import `trend_methods/*.py` (sổ đăng ký `METHODS`, `build_trend_frame`) và `indicator.timeframe_minutes`, đồng thời
  chứa bộ lọc `apply_trend_filter`; vì thế ba chiến lược import `rules.trend_tf` gián tiếp kéo theo các khối và
  `indicator.py` (không vòng, hành vi không đổi). `strategies/*.py` chỉ import `rules.trend_tf` (`combo.py`, `ma_cross.py`,
  `macd_break.py`) và `rules.alternating` (`combo.py`, `ma_cross.py`, `macd_break.py`) — không import lẫn nhau và KHÔNG biết có khối
  xác nhận xu hướng nào (chỉ đọc cột `trend_bias`). `configuration.py` nối indicator +
  strategies + `rules.entries` + `rules.trend_tf` (dựng khung trend) thành pipeline. `we_signal/*`, `signal_display/*`
  là tầng wiring.
- **Không dư thừa, không phức tạp hoá.** Không tạo abstraction/setting cho
  trường hợp chưa xảy ra. Code chưa nối vào pipeline là orphan — xoá (đã có
  git history), không để "dự phòng".
- **Không ghi cột DataFrame mà không ai đọc.** Cột mới phải có nơi đọc thật
  (strategy, entries, payload, export, test).
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
  - Tham số kỹ thuật của từng khối xác nhận xu hướng (vd KNN: `KNN_TREND_DEFAULT_PARAMS`) nằm ngay trong file
    khối đó (`trend_methods/<tên>.py`); config chỉ chọn khối nào được bật.
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
  Chính xác hơn (rà soát 2026-10-16, đã kiểm trên dữ liệu thật: chỉ đưa vào hàm đúng các cột nó đọc thì `raw_signal`/`signal` giống hệt pipeline, xáo các cột khác không đổi): điều kiện setup BUY/SELL (`valid`/`buy_cond`/`sell_cond`) chỉ nằm ở 3 file này; nhưng ý nghĩa đầy đủ của điều kiện còn cần các cột do `indicator.add_*_indicators` tính (vd `prior_high_close`/`prior_low_close`, `ma`, `prev_close`, `fast_ma`/`slow_ma`, `macd_h`) và tham số từ config; mỗi `detect_*_signals` còn có khối nối bộ lọc 20 dòng GIỐNG HỆT nhau ở cả ba file (alternating/trend → `raw_signal`, `signal`, reason, `trend_filter_status`), và cột ±1 thật sự do `rules/alternating.py` tạo từ hai mask; nơi khác (`rules/entries.py` giá vào lệnh, `signal_frame` đổi −1 → 2, `payload.py` marker/thống kê, worker/publisher ghi Redis) chỉ đọc/chuyển mã/hiển thị, không quyết định hướng. `des` có bản sao tay riêng của cả ba luật.
- **`entries.py` CHỈ có logic entry. `indicator.py` chỉ tính chỉ báo thuần.**
- **Test phải hermetic** — không test nào đụng SQL Server/Redis thật (mục 8).
- **Dọn artifact sau khi chạy/test thật**: `__pycache__/`, `.pytest_cache/`,
  `.ruff_cache/` (mục 9). KHÔNG tự xoá `we_core/deploy/runtime/exports/`.
- **Sửa code xong luôn chạy `ruff check .` + `vulture we_core/src we_core/tests des/src des/tests
  --min-confidence 80` + `pytest -q`** (từ thư mục cha cho pytest, mục 8).
  Cả 3 phải sạch.

## 3. Kiến trúc — bảng trách nhiệm từng file

```text
wisdom_engine/                       (tên thư mục BẮT BUỘC, xem mục 2)
  run_we.sh                        Launcher Linux: export CSV, mở dashboard, điều khiển worker, mở DES Chart.
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
                                      merge_trend_reference (ghép trend bar đã đóng vào nến vào lệnh),
                                      timeframe_minutes,
                                      add_{combo,ma_cross,macd_break}_indicators.
      rules/                         Luật dùng chung cho mọi chiến lược (entries, alternating = tầng đáy; trend_tf là ngoại lệ, xem mục 2):
        entries.py                   Entry: combo = high+X / low−X; ma_cross/macd_break = close.\n                                      Do configuration.py gọi, sau bước tín hiệu.
        trend_tf.py                  Cửa vào duy nhất của phần xu hướng: sổ đăng ký METHODS + parse_methods + build_trend_frame
                                      (chạy các khối đang bật, ghép thành trend_bias, thêm trend_close_time), rồi trend_filter_enabled,
                                      combine_trend_bias (ghép đồng thuận), apply_trend_filter (combo/macd_break/ma_cross dùng).
        alternating.py               Lọc lặp cùng chiều dùng chung (combo, macd_break, ma_cross): alternating_filter_enabled,
                                      alternating_signals, apply_alternating_filter. Cờ trong config (mục 5).
      trend_methods/                 Các khối xác nhận xu hướng khung lớn, MỖI PHƯƠNG PHÁP MỘT FILE (mục 6):
        knn.py                       Khối KNN ("AI Trend Navigator"): công thức + tham số + build() -> trend_bias_knn.
                                      (mỗi phương pháp mới = một file như thế; đăng ký ở rules/trend_tf.py)
      strategies/
        combo.py                     detect_combo_signals (CHỈ tín hiệu).
        ma_cross.py                  CHỈ tín hiệu, luật TRẠNG THÁI (fast>slow & MACD>0, đổi 2026-10-16), trend filter kiểu Combo, lọc lặp cùng chiều (cờ trong config).
        macd_break.py                CHỈ tín hiệu, chỉ có lọc lặp cùng chiều (như Combo, cờ trong config), có trend filter kiểu Combo.
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
        payload.py                   DataFrame -> candles/markers/overlays/panels/table/stats (thuần). 4 hàm chuỗi dài
                                      (candlestick/line/histogram/trend_line) chạy theo cột bằng numpy, không iterrows.
        renderer.py                  candles/markers -> 1 file HTML tĩnh tự chứa.
        cli.py                       Chart HTML tĩnh: load → run_strategy → payload → renderer.
                                      Giữ BARS_MIN/BARS_MAX (50/20000) + clamp_bars.
        live_page.py                 HTML dashboard sống.
        server.py                    Flask: /, /api/scan, /api/export, /health; nén gzip JSON/HTML từ 1 KB khi trình duyệt xin.
        vendor/lightweight-charts.js Vendor sẵn (không cần internet).
    tests/                         251 test, hermetic (mục 8). payload_reference.py = bản lặp từng dòng cũ của payload.py (chỉ để test).
  des/                             Hệ con: mô phỏng engine OG trên nến do DFS phát (mục 10). Package riêng
                                    `wisdom_engine.des.src.*` (KHÔNG import `wisdom_engine.we_core.src`), 211 test riêng.
    deploy/                        des-signal.service + runtime/logs/ (des_run.log, gitignored) — TÁCH RIÊNG khỏi we_core.
    src/chart_simul/               Trang chart sống CHỈ ĐỌC cho des: nến db13 + tín hiệu db14 (không lọc trend) + chỉ báo, đối
                                    chiếu kỳ vọng/đã publish + trang /trend (đã lọc trend, db15, có pane xu hướng) + trang /mtf nhiều timeframe (mục 10, "chart_simul"). 7 file, mỗi file một vai trò.
  research_notes/                  Ghi chú nghiên cứu + scripts/t001_* (không phải code hệ thống).
```

**Luồng dữ liệu** — 2 đường, đều ở `configuration.py`:

```text
[A] 1 timeframe — run_strategy()
load() | load_range() | load_range_with_warmup()
  -> normalize_params -> add_indicators -> detect_signals -> add_entries

[B] 2 timeframe — run_strategy_with_trend_reference()
entry_bars + trend_bars (khung trend tra theo (symbol, khung vào lệnh) trong `trend_tf_by_symbol` của chiến lược — bảng bắt buộc đủ ô,
                         không có khung mặc định chung; luôn >= khung vào lệnh, `configuration.check_trend_timeframe` chặn nếu nhỏ hơn — mục 5, 6)
  -> add_indicators(entry) + build_trend_frame(trend)   (chạy các khối trend_methods đang bật, ghép thành trend_bias)
  -> merge_trend_reference (chỉ trend bar ĐÃ ĐÓNG, không lookahead)
  -> detect_signals (lọc theo trend_bias) -> add_entries
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
  combo / ma_cross / macd_break: {...}  # tham số, timeframe, trend, symbol_x (chỉ combo)
  # combo còn có symbol_entry_atr_pct {SYMBOL: a%} (nhóm entry theo ATR, hiện 4 cặp forex; symbol không nằm đồng thời trong symbol_x) — xem 'Thêm nhóm forex' ở đầu file
  # defaults.price_decimals {SYMBOL: số chữ số} (làm tròn atr/entry khi xuất; symbol không liệt kê = 2)
  # combo, macd_break và ma_cross còn có alternating_filter_enabled (true/false) — xem 'Cờ lọc lặp cùng chiều' bên dưới
  # combo / ma_cross / macd_break có trend_methods: [knn] (khối xác nhận xu hướng được bật) — xem 'Khoá trend_methods' bên dưới
  # combo / ma_cross / macd_break có trend_tf_by_symbol (khung trend riêng từng symbol × khung vào lệnh, bắt buộc đủ ô) — xem 'Khoá trend_tf_by_symbol' bên dưới
```

Thiếu `defaults`/`strategies.*` hoặc key con → `KeyError` lúc import. Thiếu
`redis.backfill_event.channel`, `redis.past_signal.{db,key_prefix}`,
`redis.past_signal_trend.{db,key_prefix}` → `KeyError` lúc nạp config worker.
Sửa `strategies:` có hiệu lực ở lần chạy kế (worker cần restart).
**Cờ lọc lặp cùng chiều** `strategies.{combo,macd_break,ma_cross}.alternating_filter_enabled` (2026-10-14; `ma_cross` thêm 2026-10-16; `des`: `strategies.combo` trong
`des_config.yaml`): true = chỉ bắn tín hiệu đầu tiên khi hướng đổi chiều (`alternating.py`); false = bar nào thoả điều kiện
cũng bắn (số liệu trước khi có bộ lọc). Cờ là một công tắc true/false trong config, cùng khuôn với `trend_filter_enabled`; bắt buộc có
(thiếu → `KeyError` lúc import), mặc định HIỆN TẠI = true cho cả ba chiến lược (với `ma_cross` cờ tắt làm ~42% số nến thành tín hiệu: chỉ để xem/so sánh). Đè tạm được bằng `--param ALTERNATING_FILTER_ENABLED=false` (CLI export, cho ra bản không lọc vào `--output-dir` riêng) hoặc ô
"Alternating Filter" của form dashboard. **Đổi giá trị trong config làm đổi NỘI DUNG DB0/DB1/CSV mà tên key/tên file không đổi**, nên
quy trình là: sửa config → restart `we-worker` (bootstrap ~5,5 phút, DB0/DB1 trống trong lúc đó) → xuất lại CSV nếu cần dùng bản CSV mới (việc báo VM-BO20 do người dùng tự quyết).
Worker ghi cờ đang áp dụng vào log `bootstrap_start` (`alternating_combo=… alternating_macd_break=… alternating_ma_cross=…`), `des` ghi `event=alternating_filter`
lúc khởi động — dùng để tra dữ liệu hiện tại được tạo với cấu hình nào.
**Khoá `trend_methods`** (`strategies.{combo,ma_cross,macd_break}.trend_methods`, thay `trend_type` từ 2026-10-15; `des_config.yaml` cùng khuôn): danh sách khối xác nhận xu hướng khung lớn được bật
(tên có trong `METHODS` ở `rules/trend_tf.py`, hiện chỉ `knn`); một hoặc nhiều, nhiều khối = đồng thuận (mục 6). Bắt buộc có (thiếu → `KeyError` lúc import); tên lạ/rỗng/trùng → `ValueError` kèm danh sách tên hợp lệ.
Đè tạm: `--trend-methods knn[,x]` (CLI export, thay `--trend-type`) hoặc `trend_methods=` trên URL dashboard.
Đổi danh sách làm đổi NỘI DUNG DB1 (và CSV bản trend) mà tên key không đổi: worker ghi
`trend_methods_<chiến lược>=…` vào log `bootstrap_start`, `des` ghi `trend_methods=…` ở dòng `event=trend_filter` (chỉ khi trend bật).
**Khoá `trend_tf_by_symbol`** (`strategies.<tên>.trend_tf_by_symbol`; khoá `default_trend_tf` đã XOÁ 2026-10-16 ở cả `we_core` lẫn `des`): khung trend của một cặp (symbol, khung vào lệnh) = ô
`trend_tf_by_symbol: {SYMBOL: {khung_vào: khung_trend}}`; `TREND_TF` đè tường minh (`--trend-tf`, `--param`, ô dashboard) thắng ô. Bảng là khoá BẮT BUỘC (vắng → `KeyError` lúc import) và phải ĐỦ Ô: mọi symbol
trong `strategies.combo.symbol_x` × mọi khung vào lệnh của chiến lược (combo H1–H4, ma_cross M10–M45, macd_break M10–H4); nạp và kiểm lúc import (symbol thuộc `symbol_x`, khung vào thuộc tập khung của chiến lược,
khung trend thuộc `trend_timeframes`, khung trend >= khung vào lệnh; một `ValueError` liệt kê mọi ô sai hoặc thiếu). Không có khung trend mặc định chung: với symbol ngoài 11 symbol vận hành (CLI/API của `we_core`)
phải chỉ rõ khung trend — CLI báo "No trend timeframe configured for SYM TF; pass --trend-tf.", dashboard trả HTTP 400 "…choose Trend TF" trước khi tải SQL, `TREND_TF` trong params là `""` khi chưa xác định. Mọi khung trend
phải >= khung vào lệnh tương ứng: sai thì worker không khởi động (`check_trend_timeframes`, trước khi xoá Redis), CLI/dashboard báo lỗi khi đè `--trend-tf`/`trend_tf` nhỏ hơn khung vào lệnh (quy tắc 2026-10-16, mục 6).
Bảng hiện tại do `research_notes/scripts/t008_tune_trend_tf.py` chọn theo SỐ tín hiệu nhiều nhất (không phải chất lượng; sửa tay được; chi tiết và số đo ở ghi chú 2026-10-16 "Khung trend riêng theo …"; luật hoà của
script nay là "chọn khung lớn hơn", các bảng tạo trước đó dùng luật cũ và được GIỮ NGUYÊN). Đổi bảng làm đổi NỘI DUNG DB1 (và CSV bản trend) mà tên key không đổi → restart `we-worker` (bootstrap ~5,5 phút);
log `pair_refreshed` của DB1 ghi `trend_tf` từng cặp. Hậu tố tên file CSV bản trend mang khung của ô (`…_tf_knn_<khung>.csv`).
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

### Redis trên OG8 — `wisdom_engine` (db0/db1) và hệ mô phỏng DFS/des (db13/db14/db15)

**Chốt 2026-10-05 (người dùng quyết định):** Redis server trên OG8
(`127.0.0.1:6379`) từ nay chỉ phục vụ phần chiến lược của `wisdom_engine`. Cùng
ngày đã `FLUSHALL` toàn bộ server (trước đó DB0 chứa `L_CANDLE_*` của DP, DB1 chứa
`L_SIGNAL_*`/`L_CHANNEL_*`/`L_OG_*` của OG18) rồi cho worker nạp lại.

| DB | Key (tiền tố) | Nội dung |
|---|---|---|
| **DB0** | `L_PastSignal_*` | past-signal không lọc trend (`past_signal`) |
| **DB1** | `L_PastSignal_Trend_*` | past-signal có lọc trend (`past_signal_trend`) |
| DB2, DB3 | — | không dùng (DB cũ của `wisdom_engine`, đã xoá) |
| DB4–DB12 | — | không dùng |
| **DB13** | `L_DFS_*` + `dfs:*` | nến do DFS (DP6) phát lên, `des` đọc (mục 10); trước 2026-10-16 là DB14 |
| **DB14** | `L_DES_*` | tín hiệu `des` KHÔNG lọc trend (có Pub/Sub `L_CHANNEL_SIM_*`); trước 2026-10-16 là DB15 |
| **DB15** | `L_DES_*` | tín hiệu `des` ĐÃ lọc trend (cùng khuôn khoá, KHÔNG Pub/Sub; mới từ 2026-10-16) |

- Hệ khác (DP live, OG18, OF) **không còn ghi vào db0/db1**. `L_DFS_*` chỉ hợp lệ
  ở db13 (do DFS ghi; đổi từ `L_CANDLE_*` ngày 2026-10-09, từ db14 sang db13 ngày 2026-10-16); thấy nó (hay `L_SIGNAL_*`...) ở db0/db1 thì có hệ khác đang
  dùng chung — kiểm tra trước khi `FLUSH*` hay `clear_all_signals`. `wisdom_engine`
  không bao giờ ghi/xoá ở db13/db14/db15.
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
  sao lưu vì worker luôn clear + bootstrap DB0/DB1 từ SQL; db13–db15 là mô phỏng — OG8/Redis
  khởi động lại thì mất, phải chạy lại DFS từ đầu (không `resume` được vì checkpoint DFS
  nằm trong db13). Muốn giữ db14/db15 thì chủ động `BGSAVE`/`redis-cli --rdb <file>`. Không bật
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

**Warm-up 300 nến KHÔNG có nghĩa là bộ lọc chống trùng nhớ được 300 nến (đo 2026-10-16, research_notes F145):** setup chỉ
tồn tại ở nến mà chỉ báo đã hợp lệ (combo từ nến thứ 19, ma_cross từ 33, macd_break từ 100 vì `LOOKBACK_BARS` = 100), nên "trí nhớ" thực
của đường làm mới delta chỉ là 281 / 267 / **200** nến. Lần làm mới delta rơi đúng một setup mà setup thô cùng chiều liền trước cách xa hơn
số đó sẽ bắn tín hiệu mà tính lại từ đầu sẽ chặn: đo cho macd_break, khoảng ≤ 200 nến → 0/311 lệch, > 200 nến → 34/34 lệch ở DB0 và 25/34 ở DB1
(tín hiệu thừa nằm lại tới lần restart worker kế). Cách giảm CHƯA làm: warm-up macd_break ≥ `LOOKBACK_BARS` + 300 (= 400) hoặc warm-up riêng từng chiến lược.

## 6. Chiến lược

Logic "khi nào/hướng nào" luôn nằm trong khối banner comment ở
`strategies/<tên>.py`. `combo`, `macd_break` và `ma_cross` **không có state machine giữa các bar** — mỗi bar thoả
điều kiện là một setup (OG không theo dõi lệnh thật; có nhớ "tín hiệu đầu tiên"/đang giữ
lệnh hay không là việc của hệ thống đặt lệnh) và chỉ có MỘT trạng thái
duy nhất: lọc lặp cùng chiều (`alternating.py` ở `src/rules/`, dùng chung, bật/tắt bằng cờ
`alternating_filter_enabled` trong config — mục 5; `macd_break` từ 2026-10-13, `ma_cross` từ 2026-10-16; `des` có bản sao riêng
`des/src/rules/alternating.py` vì không import `we_core`, sửa một bên phải sửa cả bên kia).

**⚠ Thứ tự hai bộ lọc là một phần của nội dung dữ liệu — KHÔNG đổi tuỳ ý. HIỆN TẠI (từ 2026-10-16, người dùng yêu cầu): `alternating` chạy TRƯỚC, `trend_tf` chạy SAU ("cách B")** — cùng thứ tự với `des` và với mọi dữ liệu trước 2026-10-15. Lịch sử: cách B từ đầu → cách A (trend TRƯỚC, alternating SAU) 2026-10-15 09:31 UTC → đổi lại cách B 2026-10-16 (ghi chú "Đổi lại thứ tự lọc về cách B 2026-10-16" ở đầu file). Hai bộ lọc KHÔNG giao hoán nên đổi thứ tự là đổi TẬP tín hiệu DB1 và CSV bản trend (tên key không đổi).
Trong `combo`/`macd_break`/`ma_cross`: `alternating` (có trí nhớ: nhớ hướng setup gần nhất) chạy trên MỌI setup và cho `raw_signal` (đúng cột DB0, cũng là tín hiệu TRƯỚC khi lọc trend); rồi `apply_trend_filter` giữ tín hiệu có `raw_signal == trend_bias` đúng nến đó và cho `signal`. Hệ quả: **`signal` ⊆ `raw_signal` (DB1 ⊆ DB0)**; **trend TẮT ⇒ `signal = raw_signal`**; `trend_filter_status` mô tả tầng trend trên chính `raw_signal` (aligned/filtered/neutral_trend/no_raw_signal); `signal_reason` theo hướng của `signal`. Mã: `detect_*_signals` gọi `apply_alternating_filter(buy_cond, sell_cond, params)` cho `raw_signal`, rồi `apply_trend_filter(out, params, raw_signal_col="raw_signal", output_col="signal")`.
Hệ quả cần biết (đo 2026-10-16, SQL SELECT, 11 symbol, từ 2022-01-01, dữ liệu cắt 2026-10-14, khung trend LÚC ĐO theo config: combo H1 / ma_cross H2 / macd_break H4 — combo nay H4 (xem ghi chú 'Quy tắc khung trend ≥ khung tín hiệu'); dữ liệu so A–B ở research_notes F143):
(a) một setup bị trend loại VẪN chiếm "lượt đầu" của hướng đó: DB1 có thể có hai tín hiệu liền nhau CÙNG chiều (giữa chúng có setup ngược chiều bị trend loại): 4.747 / 24.581 / 4.438 cặp (combo / ma_cross / macd_break; 36% / 57% / 32% số cặp liền nhau), và một đợt setup mà nến ĐẦU lệch trend thì mất cả đợt — bộ lọc KHÔNG chờ trend đổi chiều giữa chừng (các đợt mà cách A vẫn bắn ở nến sau: 2.860 / 31.732 / 8.467);
(b) số tín hiệu DB1 cách B so với cách A: combo 13.325 so với 11.997, ma_cross 42.931 so với 45.631, macd_break 14.162 so với 18.738; trùng nhau chỉ 76% / 30% / 55% của A;
(c) "trí nhớ" của alternating là khoảng giữa hai setup THÔ liên tiếp cùng chiều: lớn nhất 150 / 72 / 288 nến (cách A: setup đã qua trend, 377 / 177 / 525 nến, 6 / 0 / 104 khoảng > 300 nến). Nhưng warm-up của cửa sổ làm mới worker là 300 nến mà chỉ phần chỉ báo đã hợp lệ mới nhớ được setup: combo 281 nến, ma_cross 267, **macd_break chỉ 200** (100 nến đầu dành cho `LOOKBACK_BARS`): xem "Hạn chế đã kiểm chứng" trong ghi chú 2026-10-16 (đường delta lệch với macd_break khi khoảng giữa hai setup cùng chiều > 200 nến: 34 khoảng lịch sử ở cách B, 655 ở cách A).
Giới hạn: mới là số lượng/thành phần, CHƯA có đánh giá chất lượng (P&L) nên chưa biết cách nào tốt hơn. **Cách B đã có hiệu lực ở mã từ 2026-10-16; Redis chỉ nạp cách B sau lần restart `we-worker` kế** (xem ghi chú). **Quy ước đọc:** các ghi chú ngày 2026-10-15/16 nói "trend trước, xen kẽ sau", "DB1 xen kẽ tuyệt đối", "DB1 ⊄ DB0", "144 khoảng > 300 nến", "bộ 539 ca" là cách A lúc đó.

- **Combo**: BUY = `close>open` & `close>ma` & `prev_close<ma` & `macd_h>0`;
  SELL đối xứng. Lọc session `session_hours_utc` (rỗng = mọi giờ). **Lọc lặp cùng chiều**
  (`alternating.py`, khôi phục 2026-10-11, cả WE lẫn des; cờ config): chỉ bar setup ĐẦU TIÊN khi hướng
  đổi chiều bắn tín hiệu; các setup cùng chiều sau đó bị chặn cho tới khi có setup ngược (bar
  không có setup không đổi trạng thái). Entry = `high+X` (BUY) / `low−X` (SELL) với X cố định cho nhóm 11 symbol cũ, hoặc `high ± a% × ATR` cho nhóm forex (`symbol_entry_atr_pct`). Trend filter
  (`apply_trend_filter`) lọc `raw_signal` đã xen kẽ (giữ khi `raw_signal` == `trend_bias` đúng bar đó); lọc
  xen kẽ chạy TRƯỚC trend filter (cách B, chốt lại 2026-10-16, xem ⚠ phía trên): `raw_signal` = xen kẽ trên mọi setup, `signal` = raw_signal đã qua trend; DB1 ⊆ DB0.
- **MA Cross** (luật TRẠNG THÁI, đổi 2026-10-16): setup BUY ở MỌI nến có `fast_ma > slow_ma` (SMA 13/34) và `macd_h > 0` (MACD 5/25/5);
  setup SELL đối xứng (`fast < slow` và `macd_h < 0`); bằng nhau hoặc NaN thì không có setup; cần `atr` không NaN. Entry = `close`.
  Không còn sự kiện "cắt", không còn cột `prev_fast_ma`/`prev_slow_ma`, không còn `_apply_trend_wait` (đã xoá). Cùng khuôn Combo/MACD Break:
  lọc lặp cùng chiều (cờ `strategies.ma_cross.alternating_filter_enabled`, mặc định true) chạy TRƯỚC trên mọi setup cho `raw_signal`, rồi
  trend filter (`apply_trend_filter`, khớp đúng 1 bar) lọc `raw_signal` cho `signal` (cách B, chốt lại 2026-10-16: `signal` ⊆ `raw_signal`); **tắt trend ⇒ `signal = raw_signal`
  từng bit.** Setup thô chiếm ~42% số nến (tắt cờ chống trùng ⇒ ~42% nến là tín hiệu) nên chống trùng là BẮT BUỘC. Tín hiệu là nến ĐẦU TIÊN của mỗi đợt setup
  (fast/slow và MACD khớp sau một setup ngược chiều); khi trend bật phải cùng chiều trend đúng nến đó — đợt nào có nến đầu lệch trend thì mất cả đợt, và hai
  tín hiệu liền nhau có thể cùng chiều. Khung trend: bảng `trend_tf_by_symbol` (M10 → H4, M20/M30/M45 → H1 ở mọi symbol, chọn theo số tín hiệu 2026-10-16) cho 63.789 tín hiệu (57,5% DB0 111.012) trên Redis từ restart 2026-10-16 12:01 UTC; trước đó, khi còn một khung chung H2 (khoá `default_trend_tf`, nay đã xoá): 42.931 tín hiệu (~204/năm/cặp, 38,7% DB0).
  Các con số tín hiệu/năm theo khung trend H1–H4 (344 / 217 / 150 / 113) ở ghi chú đổi luật 2026-10-16 là của cách A; cách B chưa đo theo từng khung trend. Cột
  `trend_filter_status` nay là của `apply_trend_filter` (`disabled|aligned|filtered|neutral_trend|no_raw_signal`). Số đo + ghi chú đổi luật:
  ghi chú ngày 2026-10-16 bên dưới và `research_notes/topic_2026_ma-cross-state-rule.md` (T007).
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
  (khớp đúng 1 bar), chạy SAU lọc xen kẽ (cách B, chốt lại 2026-10-16, cùng thứ tự như Combo: DB1 ⊆ DB0).
  Riêng macd_break chỉ nhớ được 200 nến trong 300 nến warm-up của cửa sổ làm mới worker (xem ⚠ phía trên).
  `LOOKBACK_BARS` bị kẹp tối đa = `defaults.warmup_bars` (300) để không mất tín hiệu đầu
  khoảng sau khi cắt warmup. Biến tấu từ `breakout_atr` cũ (ATR10, trailing stop, có trạng thái).

- **Khối xác nhận xu hướng** (`trend_methods/`, từ 2026-10-15): mỗi phương pháp là MỘT file độc lập nhận nến khung lớn và trả hướng +1/−1/0 trong cột
  `trend_bias_<tên>`; config từng chiến lược chọn khối nào bật qua `trend_methods: [..]` (một hoặc nhiều). Nhiều khối = ĐỒNG THUẬN: `trend_bias` chỉ ra ±1 khi TẤT CẢ khối
  cùng hướng, còn lại 0 (`rules/trend_tf.combine_trend_bias`). Chiến lược và `apply_trend_filter` chỉ đọc `trend_bias` nên không biết có bao nhiêu khối. Mọi khối đang bật
  dùng CHUNG một khung tham chiếu (`TREND_TF`; khung này tra theo (symbol, khung vào lệnh) ở `trend_tf_by_symbol`, mục 5), phải >= khung tín hiệu (`configuration.check_trend_timeframe`, 2026-10-16). **Hợp đồng một khối** (kiểm lúc nạp ở `rules/trend_tf._check_contract`): `NAME`; `OUTPUT_COLUMNS` (cột riêng, được mang sang
  khung vào lệnh); `OVERLAYS` (đường vẽ trên chart khung lớn của dashboard); `build(trend_bars, params)` (thêm cột riêng + `trend_bias_<NAME>` kiểu int, không xoá/đổi cột có sẵn);
  tham số kỹ thuật nằm trong chính file khối. Cột hướng riêng `trend_bias_<tên>` chỉ ở khung trend (nội bộ), KHÔNG sang khung vào lệnh.
- **Khối KNN** (`trend_methods/knn.py`, khối đầu tiên và hiện là DUY NHẤT): indicator deterministic (không phải model ML),
  chạy trên trend timeframe; hướng = dấu của (`ai_knn` nến này − nến trước); `merge_trend_reference` gắn trend bar đã đóng
  (`trend_close_time <= entry bartime`). Mặc định tắt trong config.

### Công thức ATR/RMA — khớp TradingView

`atr()`/`rma()` dùng `_wilder_smooth()`: bar hợp lệ đầu seed bằng **SMA(period)**
rồi mới đệ quy `alpha=1/period`. **KHÔNG thay bằng `ewm(alpha=1/period,
adjust=False)` thuần** (seed bằng 1 điểm, lệch 2,5–4% ở bar đầu, cần +24..+81
bar mới hội tụ). `we_core/tests/test_indicators.py` khoá hành vi này.

### Thêm chiến lược mới — checklist

1. `indicator.py`: `add_<tên>_indicators`.
2. `strategies/<tên>.py`: `detect_<tên>_signals`.
3. `entries.py`: `add_<tên>_entries`.
4. `configuration.py`: `_<TÊN>_CFG` + hằng timeframe + `<TÊN>_DEFAULT_PARAMS` +
   `<TÊN>_PARAM_FIELDS` + `normalize_<tên>_params` + đăng ký `StrategySpec`.
5. `we_config.yaml` **và** `we_config.example.yaml`: mục `strategies.<tên>` (nếu chiến lược dùng lọc lặp cùng chiều: thêm
   `alternating_filter_enabled`, đọc bằng `_strategy_alternating_default` + `_normalize_alternating_param` + dòng trong
   `PARAM_FIELDS`, gọi `apply_alternating_filter` trong `detect_<tên>_signals`; nếu dùng trend: thêm
   `trend_tf_by_symbol` (bắt buộc, đủ mọi symbol × khung vào), đọc bằng `_strategy_trend_tf_by_symbol` và tra bằng `_raw_params_with_cell_trend_tf` trong
   `normalize_<tên>_params`, truyền vào `StrategySpec.trend_tf_by_symbol`; chạy lại `research_notes/scripts/t008_tune_trend_tf.py` để có bảng).
6. `we_signal/export_cli.py`: `csv_columns()`/`signal_frame()` nếu schema CSV
   khác mặc định. **Cảnh báo:** `csv_columns()` trả schema mặc định
   (`bartime, atr, signal`) cho key lạ thay vì raise; chiến lược không có cột
   `atr` vẫn ra CSV nhưng cột `atr` toàn rỗng (hiện cả ba chiến lược đều có `atr`).
7. `signal_display/server.py`: `_STRATEGY_OVERLAYS` (đường overlay trên chart).
8. `run_we.sh`: dòng nhắc `Strategy (...)`.
9. Muốn worker Redis chạy chiến lược này: thêm vào `_STRATEGY_TIMEFRAMES`
   (`redis_io/worker.py`). Từ 2026-10-06 các tập timeframe CÓ THỂ giao nhau
   (`_strategies_for_timeframe()` trả list; macd_break chồng cả combo lẫn ma_cross);
   mỗi chiến lược ghi LIST riêng vì key có STRATEGY. Mọi chiến lược dùng chung tập
   symbol = key của `strategies.combo.symbol_x`.

### Thêm khối xác nhận xu hướng mới — checklist

1. `trend_methods/<tên>.py` (CẢ `we_core` VÀ `des`, mỗi hệ một bản): `NAME`, `OUTPUT_COLUMNS`, `OVERLAYS`, `build()` theo hợp đồng ở mục 6; cột riêng đặt `trend_<tên>_…`, cột hướng
   `trend_bias_<tên>` (int); chỉ import hàm toán của `indicator.py`; tham số kỹ thuật cố định trong file.
2. `rules/trend_tf.py`: import file khối và thêm một dòng vào `METHODS` (config/CLI/dashboard tự nhận tên mới).
3. Test hermetic: `build()` (cột, dtype int64, không sửa input) và khai báo hợp đồng; phần ghép với KNN đã có test chung (khối giả).
4. Đo trên dữ liệu thật trước khi bật: tỷ lệ +1/−1/0, mức đồng thuận với KNN, số tín hiệu còn lại khi bật riêng và khi bật cả hai.
5. KHÔNG đổi `trend_methods` mặc định (vẫn `[knn]`) cho tới khi người dùng quyết; ghi CLAUDE.md nhãn "chưa có căn cứ backtest".
6. Bật cho một chiến lược = sửa `trend_methods` của chiến lược đó: đổi NỘI DUNG DB1/CSV mà tên key không đổi → restart worker (bootstrap ~5,5 phút); worker và `des` ghi `trend_methods` vào log khởi động.

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
   mode : nt (no_trend) | tf_<trend_methods>_<TREND_TF>   vd tf_knn_H4 (nhiều khối nối bằng "+", vd tf_knn+x_H4)
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
   - DB0 = không lọc trend (`past_signal`); DB1 = có lọc trend (khung trend tra theo từng (symbol, khung vào) trong `trend_tf_by_symbol`, luôn >= khung vào lệnh; đã nạp vào Redis từ restart `we-worker` 2026-10-16 12:01 UTC — xem ghi chú 2026-10-16 "Khung trend riêng theo …"; DB1/DB0: combo 26,3%, ma_cross 57,5%, macd_break 79,9%)
     (`past_signal_trend`). Cùng schema, trend đổi dòng nào có signal (DB1 ⊆ DB0 vì alternating chạy trước trend, chốt lại 2026-10-16 — mục 6; riêng 2026-10-15…16, ở cách A, DB1 từng có dòng mà DB0 không có).
   - **Không TTL, không LTRIM** — giữ toàn bộ lịch sử từ `signal_start_date`.
     Ghi bằng pipeline transaction `DELETE + HSET + RPUSH` (ghi đè idempotent).
   - Không còn kênh Pub/Sub báo ngược (bỏ 2026-09-26: 0 subscriber).
   - VM-BO20 (ctrader-cli) đọc Redis DB0/DB1 này (trước 2026-10-14 tài liệu ghi đọc cả CSV; người dùng nói
     hiện chỉ đọc Redis db0/db1 — chưa đối chiếu với mount SSHFS ở mục 9) — **đổi schema, tên key, mã
     `signal` hay DB vẫn phải cân nhắc báo bên đó**. Thay đổi chỉ về NỘI DUNG (số tín hiệu, bật/tắt bộ
     lọc...) thì KHÔNG cần báo: người dùng khẳng định (2026-10-14) BO20 không cần biết, người dùng tự báo
     khi cần — không nhắc việc này như việc tồn đọng.
3. **Dashboard sống** (`signal_display.server`, cổng **8517**, mặc định
   `127.0.0.1`): form strategy/symbol (chỉ 15 symbol vận hành: 11 + 4 forex)/tf/bars/override, chart giá + MACD +
   chart trend khung lớn đồng bộ, bảng tín hiệu, nút Export CSV. Chế độ `latest`
   (N bar) hoặc `range` (`from_time`/`to_time`). Một `_DB_LOCK` tuần tự hoá truy
   vấn SQL.
4. **Chart HTML tĩnh** (`signal_display.cli`, mặc định `we_core/deploy/runtime/charts/`).

**Phạm vi worker Redis**: symbol = `OPERATING_SYMBOLS` = key của `strategies.combo.symbol_x` (11 symbol, entry combo X cố định) + key của
`strategies.combo.symbol_entry_atr_pct` (4 cặp forex từ 2026-10-20, entry combo theo % ATR) = 15 symbol × timeframe riêng từng chiến lược (combo: H1–H4;
ma_cross: M10/M20/M30/M45; macd_break: M10–M45 + H1–H4) = 15 × 16 = **240 cặp** trong mã/config hiện tại, từ `signal_start_date`. **Redis đã có đủ 240 cặp từ lần restart `we-worker` 2026-10-20 15:04 UTC** (các con số 176/352 ở các
đoạn ghi chú ngày trước là phạm vi lúc đó). Trigger ngoài phạm vi bị bỏ qua (DP backfill cả universe rộng hơn).

**Vòng đời worker** (`redis_io/worker.py`):

1. Khởi động tiến trình → `clear_all_signals()` **đúng 1 lần** cho DB0 và DB1
   (chỉ tiền tố của ta; cờ `_CLEARED_THIS_PROCESS` phân biệt process restart
   với reconnect) → Redis sạch trước khi ghi. **Restart có chọn phạm vi (2026-10-20):** có `--strategy/--symbol/--tf`
   (hoặc `WE_SCOPE_*`) thì chỉ xoá DB0 + DB1 của các cặp được chọn (`clear_pair_signals`) và chỉ bootstrap các cặp đó;
   không đặt = FULL như trên (xem ghi chú 2026-10-20 ở đầu file).
2. `bootstrap_all()` full-recompute 176 cặp (từ `signal_start_date` 2022-01-01: đo 960 s ngày 2026-10-11 khi còn
   Dow — trong 756 s ghi ở các dòng `pair_refreshed`: ma_cross 627 s, macd_break 105 s, combo 24 s; sau khi
   xoá Dow chỉ còn 367 s (11/10), 322 s (13/10), 329 s (15/10), 350 s (16/10 02:32), 312 s (16/10 10:21) và 360 s (16/10 12:01); trước đó ~9–10 phút từ 2024-01-01; lúc này DB0/DB1 chưa đủ dữ
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
   phát sau khi nến của khung đó đã vào SQL. Chiến lược lọc trend dùng nến H4 (`TREND_TF`; đo lúc cả ba chiến lược còn dùng H4 — từ 2026-10-16 config của `ma_cross` là H2 và của `combo`/`macd_break` là H4 (combo từng là H1 trong ngày 2026-10-16 rồi đổi lại H4 theo quy tắc khung trend ≥ khung vào lệnh), nên hạn chế này còn với cả ba chiến lược khi khung trend lớn hơn khung vào lệnh: `ma_cross` (trigger H2 đến sau trigger M10–M45 của cùng symbol), `macd_break` và `combo` (trigger H4 đến sau trigger khung nhỏ hơn); khi khung trend BẰNG khung vào lệnh (combo vào H4, macd_break vào H4) thì không áp dụng vì cùng một trigger), nên khi một nến H4 đóng giữa
   hai đợt backfill, lần làm mới DB1 của khung nhỏ trong đợt đó đọc SQL TRƯỚC khi nến H4 mới được ghi → các nến vào
   sau giờ đóng H4 được tính với trend của nến H4 CŨ hơn. Trigger H4 chỉ tính lại `combo,macd_break` của chính khung H4
   (không tính lại `ma_cross`, và không tính lại DB1 của khung nhỏ), nên dòng sai nằm lại đến lần bootstrap kế (restart).
   DB0 (không lọc trend) KHÔNG bị. Đo thật: so toàn bộ Redis trước/sau một lần restart (2026-10-10, sau ~36 giờ và 8 đợt
   backfill): DB0 giống hệt từng byte (324.243 dòng); DB1 chỉ 1/176 cặp lệch (`FR40 M10 ma_cross`, 2 dòng trên 230.242: thiếu
   BUY 08:00, thừa SELL 10:30 ngày 09/10), và tính lại từ SQL khi thiếu/đủ nến H4 mở 04:00 tái hiện đúng cả hai trạng
   thái. Hiếm vì chỉ sai khi nến H4 mới đổi trend_bias và làm đổi quyết định tín hiệu. Cách khắc phục (chưa làm, chờ quyết
   định): khi nhận trigger H4 của một symbol thì tính lại DB1 của các khung nhỏ dùng trend H4 cho đoạn sau giờ đóng nến
   H4 đó; hoặc dùng nhịp reconcile 30 phút để tính lại DB1 của 1–2 ngày gần nhất. Từ 2026-10-16 12:01 mỗi ô có khung trend riêng (`trend_tf_by_symbol`): hạn chế này áp dụng cho MỌI ô có khung trend lớn hơn khung vào lệnh; đo thật đợt backfill 11:11 16/10 (hệ hiện tại, khung trend chung lúc đó): 0 dòng sai trên 176 cặp (xem ghi chú "Restart … 12:01"; DF báo theo đợt 6 lần/ngày). Số đo 10/10 ở điểm này là của HỆ CŨ, chưa tái hiện được trên hệ hiện tại.

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
4 Open DES Chart, 5 Open DES Multi-timeframe chart, 6 Open DES Trend chart):

```bash
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.export_cli --strategy combo --symbol US30 --tf H1
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.export_cli --strategy ma_cross --symbol US30 --tf M30 --from 2024-01-01 --to 2024-06-01 --trend-mode trend_filter --trend-tf H4
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.signal_display.cli --strategy combo --symbol US30 --tf H1
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.signal_display.server --port 8517
wisdom_engine/.venv/bin/python -m wisdom_engine.we_core.src.we_signal.redis_io.worker      # foreground; thêm --symbol EURUSD --tf H1 ... để restart có chọn phạm vi
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
PYTHONDONTWRITEBYTECODE=1 wisdom_engine/.venv/bin/python -m pytest wisdom_engine/we_core/tests -q -p no:cacheprovider   # 251 test
PYTHONDONTWRITEBYTECODE=1 wisdom_engine/.venv/bin/python -m pytest wisdom_engine/des/tests -q -p no:cacheprovider   # 211 test (cần des/des_config.yaml)
cd wisdom_engine
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m ruff check . --no-cache
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m vulture we_core/src we_core/tests des/src des/tests --min-confidence 80
```

Test **không được đụng SQL Server/Redis thật**: `we_core/tests/conftest.py` có fixture
`autouse` nạp sẵn cache `db_connector.symbols()`/`tf_minutes()` bằng dữ liệu
tổng hợp. Cần `we_config.yaml` tồn tại (import `configuration` đòi). File test:
`test_dashboard_server`, `test_db_connector`, `test_export_cli`,
`test_indicators`, `test_signal_display`, `test_strategies`, `test_trend_methods`. Phần lớn là
characterization/golden test — sửa logic có chủ đích thì chạy lại pipeline để
tính giá trị mới rồi cập nhật test, không đoán số. Cột không có reader
production nhưng test đang khoá giá trị (vd `trend_filter_status`) thì muốn xoá phải sửa test trước.

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
1 lượt chạy backtest đang dở). Đường dẫn mà BO20 mount đã đổi ba lần: đổi tên repo (2026-10-08, `…/core_python/runtime/exports` → `…/wisdom_engine/runtime/exports`) rồi tái cấu trúc thư mục (2026-10-09, → `…/wisdom_engine/deploy/runtime/exports`) và dời `deploy/` vào `we_core/` (2026-10-10, → `…/wisdom_engine/we_core/deploy/runtime/exports`); KHÔNG có symlink tương thích cho BO20 (symlink `deploy` ở gốc từng là lối đi tạm cho des, đã xoá 2026-10-11 — đừng trỏ BO20 vào `wisdom_engine/deploy`), nên mount cũ trên BO20 sẽ lỗi cho đến khi BO20 đổi sang đường dẫn mới (user sẽ yêu cầu hiệu chỉnh sau). Muốn dọn phải hỏi trước. `we_core/deploy/runtime/logs/` đã tự xoay vòng
(không dọn tay, trừ khi được yêu cầu). Redis: chỉ xoá tiền tố `L_PastSignal*`
(mục 5); chỉ `FLUSH*` khi người dùng yêu cầu rõ và đã kiểm tra `keyspace`.


## 10. `des` (Decision Engine Simulation, trước là `ogs`) — mô phỏng engine OG trên nến do DFS phát

`des/` (thêm 2026-10-07) là bản **mô phỏng** của engine OG live (OG18): nhận nến do DFS
phát lên Redis OG8 **db13**, chạy đúng luồng OG (combo / ma_cross / macd_break), ghi tín hiệu ra **db14** (không lọc trend, như OG)
và **db15** (đã lọc trend; thêm 2026-10-16 để so với DB1 của `we_core` và xem trên chart).
Hệ con tách biệt: package `wisdom_engine.des.src.*` (không import `wisdom_engine.we_core.src`), config
riêng `des/des_config.yaml`.

```text
DFS (DP6, 10.11.12.6) --ghi nến--> Redis OG8 db13 --keyspace event hset--> des --> db14  L_DES_* (không lọc trend) --> Pub/Sub L_CHANNEL_SIM_*
                                                                                  \--> db15  L_DES_* (đã lọc trend, KHÔNG Pub/Sub)
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
  thật; đổi từ 1,0 s ngày 2026-10-09 vì ở 1,0 s des lỡ ~3,5% tín hiệu M10). Ước tính từ SQL: 493.134 mốc ≈ 11,4 ngày
  ở 2 s (5,7 ngày ở 1 s; `dfs.bat plan` cho số chính xác). **DFS không chờ bên đọc.**
- Cấu trúc ghi: LIST `L_DFS_{SYMBOL}_{TF}` **mới nhất ở đầu**, tối đa 1.200; HASH `...:{YYYY-MM-DD HH:MM:SS}`
  (giờ MỞ nến) 6 field `timestamp/open/high/low/close/time_update` (**không có `volume`**, giá làm tròn 2 số
  lẻ, `time_update` = giờ ảo). Lua giống live ⇒ mỗi nến mới đúng 1 event `hset`; nến bị đẩy ra khỏi LIST sinh
  event `del` (des bỏ qua).
- Guard: chỉ ghi db trong `allowed_dbs` (đọc mã 2026-10-07: `[14]`; từ 2026-10-16 DFS ghi vào db13 theo lời người dùng, chưa đối chiếu lại mã DFS) và db rỗng/của DFS; `clean` chỉ `FLUSHDB` đúng db đó.
  `stop` + `resume` được (mã băm lịch phát chỉ phụ thuộc dữ liệu/khoảng thời gian, không phụ thuộc tốc độ).

### des

- Chạy: `cd ~/Desktop && wisdom_engine/.venv/bin/python -m wisdom_engine.des.src.redis_io.live_worker`, hoặc service
  `des/deploy/des-signal.service` (đã cài vào `~/.config/systemd/user/`, **chưa enable** — start tay bằng
  `systemctl --user start des-signal`). Config thật `des/des_config.yaml` (gitignored; mẫu
  `des_config.example.yaml`). Log: `des/deploy/runtime/logs/des_run.log` (RotatingFileHandler 5 MB × 3; từ lần khởi động 2026-10-11 02:53 UTC;
  log của các lần chạy trước nằm cùng thư mục, cũ nhất là `des_run.log.1` ghi 2026-10-09 20:47).
- **Hai khác biệt HÀNH VI so với OG live.** (1) **Không hết hạn** (chốt 2026-10-07): bỏ cửa sổ hiệu lực
  (`signal_validity`, `valid_until`), bỏ TTL của HASH, bỏ `LTRIM` của LIST và bỏ field `expirationTimestamp`.
  Giữ `valid_from` (giờ nến đóng) và cổng "nến chưa đóng". Mọi thứ khác giữ nguyên: đọc 500 nến, chỉ xét nến
  cuối, combo = lệnh STOP / ma_cross = MARKET, chống trùng bằng "HASH đã tồn tại", Pub/Sub.
  (2) **Mỗi lần START tiến trình là xoá sạch DB tín hiệu** (chốt 2026-10-08): trước khi subscribe,
  `clear_signals()` xoá mọi key `L_DES_*` ở CẢ HAI db đầu ra (db14 và db15; theo tiền tố, KHÔNG FLUSHDB) và log `event=signals_cleared` (mỗi db một dòng).
  Làm đúng 1 lần mỗi tiến trình — reconnect Redis trong cùng tiến trình thì không xoá lại; `_validate_config` bắt buộc
  input, output và output_trend là BA db khác nhau nên không thể đụng db13. Hệ quả cần biết: mọi lần des start lại — kể cả systemd tự
  restart sau lỗi (`Restart=on-failure`) — là mất toàn bộ kết quả đã chạy và bỏ lỡ các mốc DFS phát trong lúc
  des chết; muốn giữ kết quả thì chép db14/db15 ra trước khi start lại (script đọc LIST/HASH `L_DES_*`, hoặc `BGSAVE`
  cả instance — Redis OG8 đang tắt sao lưu nên phải chủ động). Các key `L_OG_*` của lần chạy trước khi đổi tên
  prefix (2026-10-09: ~19.500 key) KHÔNG bị xoá tự động vì prefix mới là `L_DES_*`; xoá tay theo tiền tố (`scan_iter`) khi
  không cần nữa.
- Phần còn lại chỉ là cách đặt lên máy: đổi đường dẫn import, `des_config.yaml` cho OG8 (vào db13, ra db14 và db15 — mục `redis.output_trend` bắt buộc,
  `process_on_startup: false`, tắt Discord, `channel_prefix: L_CHANNEL_SIM` để không lọt vào kênh của OG live,
  `symbol_x` đủ 11 **tên SQL**), log vào `des/deploy/runtime/logs/`. Symbol thiếu trong `symbol_x` ⇒ combo dùng X=0 im lặng.
- **Cờ lọc lặp cùng chiều của cả ba chiến lược** (`strategies.<tên>.alternating_filter_enabled` trong `des_config.yaml`, mặc định true; combo và macd_break từ 2026-10-14, ma_cross từ 2026-10-16):
  bật = chỉ bắn tín hiệu đầu tiên khi hướng đổi chiều; tắt = bar nào có setup cũng bắn. Lúc khởi động `des` ghi
  `event=alternating_filter strategy=combo enabled=…`, `strategy=ma_cross` và `strategy=macd_break` (cạnh `event=trend_filter`). Đổi cờ làm đổi số tín hiệu mà tên key không đổi, và restart
  `des` xoá db14/db15 — nên chỉ đổi khi chạy lại từ đầu. Mã: `des/src/rules/alternating.py` (bản sao của bản `we_core`).
- Tên key: `L_DES_{STRATEGY}_{SYMBOL}_{TF}`, STRATEGY **bỏ gạch dưới** (`COMBO`, `MACROSS`, `MACDBREAK`) — khác quy ước
  `MA_CROSS`/`MACD_BREAK` của `L_PastSignal_*`.
- **`macd_break` trong des (đăng ký 2026-10-14):** lệnh `MARKET` (entry = close nến tín hiệu, không có `stopPrice`, giống ma_cross), chạy trên
  M10–M45 và H1–H4 (8 khung — chồng lên ma_cross và combo nên MỘT nến kích hoạt 2 chiến lược), `comment` =
  `close broke above prior highest close, MACD histogram > 0` / `close broke below prior lowest close, MACD histogram < 0`. Luật là bản sao của
  `we_core/src/strategies/macd_break.py` (sửa một bên phải sửa cả bên kia). `lookback_bars` tối đa 300 vì nến cuối chỉ có tín hiệu khi
  cửa sổ có ít nhất `lookback_bars + 1` nến (des đọc 500). mỗi db đầu ra vì thế có tối đa 176 LIST `L_DES_*` (88 cặp nến × 2 chiến lược; LIST chỉ xuất hiện khi có tín hiệu đầu tiên).
- **Hai đầu ra: không lọc trend (db14) và đã lọc trend (db15) — từ 2026-10-16** (ghi chú "Bản đồ db mới …" ở đầu file). Với nến cuối của một cặp: (1) **đầu ra chính** (db14,
  `redis.output`): `run_strategy` một khung, KHÔNG lọc trend, có Pub/Sub `L_CHANNEL_SIM_*` — như OG live, tương ứng DB0 của `we_core`; (2) **đầu ra trend** (db15, `redis.output_trend`, cùng khuôn khoá
  `L_DES_{STRATEGY}_{SYMBOL}_{TF}`, KHÔNG Pub/Sub, tương ứng DB1 của `we_core`): chỉ tính khi nến cuối có tín hiệu thô (`raw_signal != 0`; `signal` sau lọc trend luôn ⊆ `raw_signal`), đọc thêm nến khung trend của ô
  (`trend_tf_by_symbol`) từ db13 rồi chạy `run_strategy_with_trend_reference` (tự ép trend BẬT); chỉ tín hiệu cùng chiều trend được ghi. Thiếu nến trend hoặc ô chưa có khung trend → BỎ QUA đầu ra trend (log
  `trend_snapshot_missing` / `trend_tf_missing`), KHÔNG BAO GIỜ ghi tín hiệu chưa lọc vào db15; lỗi riêng của đường trend được bắt (`trend_output_failed`) để không làm mất đầu ra chính của chiến lược khác trên
  cùng nến. Cờ `strategies.<tên>.trend_filter_enabled` giữ NGHĨA Y NHƯ `we_core` (chỉ là mặc định của đường 1 khung) và phải là `false` cho cả ba chiến lược: `live_worker._validate_config` từ chối khởi động nếu `true`
  (khác `we_core`, nơi cờ true chỉ làm đường 1 khung báo lỗi lúc chạy). Log khởi động: `event=trend_filter strategy=… enabled=… trend_methods=… trend_tf_cells=N`, `event=signals_cleared db=14 …` và `db=15 …`;
  `signal_filtered_by_trend` (tín hiệu thô bị trend loại) ở mức DEBUG. Mã `rules/trend_tf.py`, `rules/alternating.py`, `trend_methods/knn.py`, `strategies/*` giống `we_core` theo từng hàm (ghi chú 2026-10-16 "Bỏ
  flags.py và đồng bộ des theo we_core"; `rules/entries.py` chỉ khác ở cột SL/TP mà des không tạo). Chi phí: đường trend chỉ chạy cho nến có tín hiệu thô (ước tính ~3,5% số sự kiện) và mỗi lần ≈ +64 ms (đo 2026-10-15
  trên nến lưu: đọc 500 nến khung trend ~14 ms + KNN ~20 ms + tính chiến lược); trong 49 ngày ảo đầu của lần chạy 2026-10-16 không có tín hiệu nào bị lỡ (so với `we_core`, ghi chú đầu file).
- Hợp đồng des cần từ DFS: key/LIST/HASH đúng cấu trúc trên (LIST mới nhất ở đầu, có OHLC), mỗi nến 1 event
  `hset`, ghi nguyên tử theo mốc. des chỉ xử lý nến của 88/165 cặp (symbol × khung): H1–H4 (combo, macd_break) và
  M10–M45 (ma_cross, macd_break); 77 cặp còn lại bị bỏ qua gần như tức thì.
- **Giới hạn đã biết và cách xử lý (đo thật 2026-10-09):** des xử lý tuần tự trong 1 luồng và chỉ xét **nến
  cuối** của cặp, nên nến bị "đè" nếu nến kế tiếp của cùng cặp được ghi trước khi des kịp đọc. Chi phí đo thật mỗi
  sự kiện cần tính: đọc 500 nến ~14 ms + tính combo ~13 ms (≈27 ms) hoặc ma_cross ~13 ms (≈28 ms). Trước
  2026-10-09 ma_cross còn tính cấu trúc Dow (~40 ms, 75% thời gian tính; chỉ để dòng "SL Dow" trong tin Discord đã
  tắt, không ảnh hưởng tín hiệu/HASH — đã kiểm chứng trên 22.000 dòng, 582 tín hiệu, 0 khác biệt) nên cả sự kiện là
  ~69 ms; Dow đã bị xoá khỏi des (và khỏi `we_core/src/` ngày 2026-10-11). Mốc nặng nhất (mọi cặp đóng cùng lúc) cần vài giây.
  Lần chạy 1,0 s/mốc (08/10, 60.000 mốc đầu) so với baseline tính lại từ SQL bằng chính mã chiến lược của des: khớp
  98,88%, lỡ 208/18.592 tín hiệu — **203 ở ma_cross M10 (3,48%)**, 5 ở M20; combo H1–H4, M30, M45 lỡ 0; 0 sai chiều.
  Nguyên nhân: mốc `:00` là mốc nặng nhất trong giờ, hàng đợi của nó chưa xong khi M10 của mốc `:10` (2 s sau) tới
  nên bị nến M10 `:20` đè (185/203 lỡ ở phút `:10`). Cách xử lý: DFS chạy `delay_seconds: 2` và bỏ Dow; mô hình
  hàng đợi (khớp thực tế ở chi phí ×1,0–1,05) dự đoán lỡ 0, chịu được chi phí tăng ~3× — chỉ là mô hình, CHƯA kiểm
  chứng bằng lần chạy thật: sau khi chạy xong phải so db14/db15 với tín hiệu tính lại từ SQL (đã so với DB0/DB1 của `we_core` ngày 2026-10-17 trên 49 ngày ảo đầu: 0 lỡ/thừa, xem ghi chú "Bản đồ db mới …" ở đầu file). Tín hiệu do reconcile
  (30 phút/lần, chặn luồng ~4,7 s) publish thường chỉ là sự kiện đang xếp hàng, không phải bị lỡ.
  **Đồng hồ OG8 nhanh hơn DP6 ~0,673 s** (cụm sự kiện DFS đến OG8 lệch hằng số, độ lệch chuẩn 5 ms): mọi độ trễ
  tính bằng cách so log hai máy phải trừ số này.
- **Trình tự chạy:** `dfs run` (seed xong thì DFS tự chạy từng mốc) → bật des càng sớm càng tốt (mỗi giây chờ
  là 1 mốc des không thấy; bật SAU khi seed xong để tránh bão event làm Redis ngắt subscriber). Không cần dọn
  db14/db15 tay nữa — des tự xoá lúc start (db13 là của DFS, des không đụng). Tốt nhất: `dfs run` rồi ngay `dfs stop` (dừng ở mốc 0), bật des, rồi
  `dfs resume` đúng tham số cũ thì des thấy đủ mọi mốc. Không restart `we-worker` trong lúc chạy.
### chart_simul — trang chart sống để kiểm tra des (thêm 2026-10-10)

`des/src/chart_simul/` là một trang web nhỏ **chỉ đọc Redis** để tận mắt xem des có làm đúng và kịp không:
nến từ db13 (`L_DFS_*`), tín hiệu des từ db14 (trang chính) hoặc db15 (trang `/trend`) (`L_DES_*`), chỉ báo của chiến lược, cập nhật mỗi ~1 s theo nhịp
DFS. Chạy tay, KHÔNG phải service: dùng `run_we.sh` mục **4 Open DES Chart** (mục 5 = `/mtf`, mục **6 = `/trend`**; bật server + mở Chrome với profile riêng
`~/.cache/des-chart-chrome`; đổi cổng bằng biến môi trường `CHART_URL`, mặc định `http://127.0.0.1:8518`), hoặc từ thư
mục cha của repo: `wisdom_engine/.venv/bin/python -m wisdom_engine.des.src.chart_simul.server --port 8518` (mặc định
chỉ nghe 127.0.0.1). URL nhận `?strategy=&symbol=&tf=&bars=`. **Toàn bộ chữ trên chart là tiếng Anh** (yêu cầu của người
dùng 2026-10-10; comment/docstring trong code vẫn tiếng Việt) — có test khoá (`test_giao_dien_chart_hoan_toan_tieng_anh`).

- **Phạm vi chọn:** đúng các tổ hợp des xử lý (176 = combo H1–H4 + ma_cross M10–M45 + macd_break M10–M45 và H1–H4, × 11 symbol), lấy từ
  `des_config.yaml` (`live.enabled_strategies`, timeframe của từng chiến lược, `symbol_x`); prefix/db đọc từ cùng
  file nên đổi prefix không phải sửa code. Chỉ báo vẽ: combo = MA(`ma_period`); ma_cross = Fast/Slow; macd_break = hai đường
  `Prior high close` / `Prior low close` (đỉnh/đáy close của `lookback_bars` nến trước); cả ba kèm panel
  MACD histogram; ATR và các đầu vào của luật nằm trong khung chi tiết khi bấm vào tín hiệu.
- **7 file, một vai trò mỗi file, không kéo chéo (test kiến trúc khoá):** `source.py` (đọc Redis, chỉ lệnh đọc),
  `verify.py` (đối chiếu thuần), `payload.py` (dựng JSON thuần; chỉ import kiểu `Check` của verify), `live_page.py`,
  `mtf_page.py` và `trend_page.py` (HTML/JS, lá), `server.py` (Flask, nơi DUY NHẤT nối các tầng + gọi `run_strategy` / `run_strategy_with_trend_reference` của des). `vendor/` là bản SAO
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
- **Trang `/trend` — đường 2 khung, đối chiếu với db15 (thêm 2026-10-16;** người dùng: "trang thứ 3 ... có lọc trend và hiển thị biểu đồ trend, và hiển thị theo chính xác config trong des"**):** cùng chiến lược × symbol × khung vào
  lệnh như trang chính nhưng KHÔNG chọn được khung trend: server tra `trend_reference_for(chiến lược, symbol, khung vào)` (ô `trend_tf_by_symbol` của `des_config.yaml`) và hiện nó trên thanh trạng thái ("Trend filter: H4 · KNN
  (set in des_config.yaml)"); ô chưa có khung → HTTP 400 nêu `trend_tf_by_symbol`. Chart chạy `run_strategy_with_trend_reference` trên nến db13 (nến vào lệnh + nến khung trend, mỗi khung ≤ 1200) rồi đối chiếu với db15 bằng đúng
  `verify.classify` của trang chính (cùng nhãn OK / wait / MISSED / EXTRA / WRONG SIDE / unchecked); khi cần kiểm lại bằng cái des thấy (`exact_signal`), cửa sổ nến vào lệnh = `snapshot_bars` nến kết thúc tại nến đó và cửa sổ nến
  trend = nến trend đã đóng tới lúc nến vào lệnh đóng (kể cả đóng đúng lúc đó), cũng `snapshot_bars` nến mới nhất. Có thêm **pane khung trend** đặt TRÊN CÙNG (khung tham chiếu lớn ở trên, rồi chart khung vào lệnh và MACD ở dưới — cùng thứ tự với dashboard WE; đổi 2026-10-17 theo yêu cầu người dùng; nến + đường KNN tô xanh/đỏ/xám theo hướng + `KNN Avg` + dấu TREND UP/DOWN khi hướng
  đổi; chỉ phần phủ vùng đang vẽ), đồng bộ với chart giá theo KHOẢNG THỜI GIAN (cách của dashboard WE: gióng mép vào ranh giới nến trend, cờ chống vòng lặp sự kiện; ba chart cùng `rightPriceScale.minimumWidth` 80), và **dấu xám
  "filtered BUY/SELL"** + dòng xám trong bảng cho tín hiệu thô mà bộ lọc trend loại (có ở db14, des không ghi vào db15; chỉ tính từ vùng des đã có thể ghi và bỏ nến đã có kết luận đối chiếu); cột "Trend" (UP / DOWN / NEUTRAL) cho mọi dòng.
  Mốc des bắt đầu (`first_signal_stamp`) luôn lấy từ db14 kể cả ở trang này. Dấu vân tay thêm `thead` (nến trend mới nhất) và `nsig` đếm ở db15. API `/api/trend` nhận cùng tham số `/api/view`. Mở bằng `run_we.sh` mục 6 hoặc liên kết
  "Trend chart" ở hai trang kia (giữ chiến lược/symbol/khung đang chọn). Đo trên Redis thật 2026-10-17: nạp đủ 150–235 ms (70–117 KB), nhịp hỏi nhẹ 4–6 ms / 159 byte.
- **Nhẹ với Redis và des:** mỗi nhịp trình duyệt gửi "dấu vân tay" (nến mới nhất, số tín hiệu, run DFS, cờ chờ);
  không đổi thì server chỉ hỏi 3–4 lệnh nhỏ (~3 ms, 171 byte); có đổi mới đọc đủ cửa sổ (≤1200 nến, ~60–100 ms).
  Server không giữ trạng thái giữa các yêu cầu.
- **Trạng thái đối chiếu** (chart tự chạy chiến lược của des trên nến db13 rồi so với db14 — trang `/trend` so đường 2 khung với db15; chỉ xét từ khi des bắt
  đầu ghi — suy từ tín hiệu sớm nhất trong db14, tính theo giờ MỞ nến nên nến đóng trước khi des chạy có thể bị báo `MISSED` giả cho tới khi trôi khỏi cửa sổ — và khi đã đủ 500 nến lịch sử như des). Nhãn trên chart:
  `OK` khớp · `wait BUY/SELL` (bảng: "waiting for des…") nến mới nhất có tín hiệu kỳ vọng nhưng des chưa ghi — trình
  duyệt đổi thành `MISSED` sau 5 giây thật nếu vẫn chưa có · `MISSED` nến đã cũ hơn nến mới nhất mà des không ghi
  (des chỉ xét nến cuối) · `EXTRA` des ghi mà chart không tính ra · `WRONG SIDE` ngược hướng · `unchecked` (ngoài
  vùng đối chiếu). Mọi khác biệt được kiểm lại bằng đúng cửa sổ 500 nến của des trước khi kết luận (nhiễu hoà SMA
  ~1/12.400 không bị báo nhầm). "value mismatch" = field HASH (atr, stopPrice, comment, ...) khác với cái chart tự
  dựng bằng `build_signal`/`to_hash_fields` của des.
- **Giới hạn:** dùng CÙNG mã chiến lược với des nên kiểm tra việc đọc/tính/ghi và độ kịp thời, KHÔNG kiểm tra logic
  chiến lược (để tự đánh giá logic bằng mắt: khung chi tiết hiện các giá trị mà luật dựa vào). Chỉ thấy nến đã đóng
  (DFS không phát nến đang chạy) và tối đa 1200 nến (sức chứa db13); giờ trên trục là giờ ảo (UTC). Nếu
  `trend_filter_enabled` bật trong `des_config.yaml` thì cả hai trang báo lỗi rõ (HTTP 400; cờ phải `false`, xem trên); đường 2 khung do trang `/trend` đảm nhận.
  Chưa đo độ trễ nến → tín hiệu theo mili-giây (cần luồng nghe keyspace + Pub/Sub; chưa làm).
- Đã kiểm chứng 2026-10-10 trên dữ liệu thật: mọi cặp thử (combo H2/H4/H1, ma_cross M10/M20/M30/M45) đối chiếu sạch
  (0 lỡ/thừa/lệch); theo dõi 11 cặp ma_cross M10 trong 150 s thấy 12 tín hiệu mới hiện ngay, 1 lần bắt được
  "wait → OK" sau 0,70 s; giao diện kiểm bằng Chrome không giao diện (không lỗi JS; các màu wait/MISSED/EXTRA/OK và
  khung chi tiết kiểm bằng dữ liệu giả).
- **Nghiệm thu** (mã `des` đồng bộ với `we_core` từ 2026-10-16; tiến trình `des-signal` chạy mã mới từ 2026-10-16 16:26:53 UTC): so db14 với `L_PastSignal` (DB0) và db15 với `L_PastSignal_Trend` (DB1) của `we_core` trên cùng giai đoạn
  (ghi chú "Bản đồ db mới …" ở đầu file: 49 ngày ảo, 14.346 mốc) — cùng tín hiệu, cùng hướng, cùng ATR và entry (combo `stopPrice` = `entry`). Trước 2026-10-16 (một đầu ra) đã so db15 với DB0 trên 45 bộ. Giá trong Redis làm
  tròn 2 số lẻ nên có thể lệch rất nhẹ ở vài nến sát ngưỡng.
- Đã kiểm chứng 2026-10-07: 54 test hermetic + đầu cuối trên Redis thật (db12 → db13, nến ghi bằng chính Lua
  của DFS): tín hiệu của nến cách giờ máy ~9 tháng vẫn được ghi, HASH/LIST không TTL, message Pub/Sub trùng HASH,
  ghi lặp không tạo trùng, event `del` bị bỏ qua.
