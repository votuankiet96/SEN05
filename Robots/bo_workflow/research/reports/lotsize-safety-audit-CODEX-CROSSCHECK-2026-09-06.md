# Cross-check độc lập: lot size và an toàn thực thi Combo / MA Cross

Ngày: **2026-09-06**. Đối tượng phản biện: [audit Claude](lotsize-safety-audit-2026-09-06.md). **Audit, không sửa hoặc build lại `.cs`, không thay tham số giao dịch.**

## 1. Kết luận và phạm vi bằng chứng

**Không chấp nhận kết luận tổng quát “mọi phát hiện/đề xuất đều đã được xác nhận”.** Phép chia tính volume là đúng trong mô hình SL lý tưởng, nhưng chưa đủ để xác nhận an toàn thực thi hoặc tuân thủ FTMO. Có sai sót trong lập luận B3/B7, đề xuất D2, hướng trừ credit của D6, và các khẳng định “chỉ BTCUSD an toàn”, “gap-cap riêng giữ được RiskPercent”.

**Cập nhật sau tự audit ngày 2026-09-06:** danh sách triển khai ban đầu quá rộng, trộn lỗi cục bộ với tính năng mở rộng. Khuyến nghị hiện tại ở **§10.1–§10.4**: hai nhóm sửa nhỏ (Close/Cancel và dữ liệu số), thêm sửa PM hiện hữu khi sử dụng PM. Portfolio coordinator, stress engine và các profile mới không phải điều kiện để thực hiện hai nhóm sửa nhỏ. Phân tích rủi ro bên dưới không đồng nghĩa mọi rủi ro đều cần thêm code.

Đã đọc toàn bộ `CLAUDE.md`, `AGENT.md` §0–§5 và entry §8 ngày 2026-09-06, cùng MEMORY.md và bốn memory được chỉ định. Giữ nguyên cảnh báo khoảng trống lịch sử 09-03→09-06 và catalog exit cũ không phản ánh code. Nguồn kiểm tra là code hiện tại, API cài trên máy, artifact gốc và nguồn chính thức; các báo cáo cũ chỉ là đầu mối để kiểm lại.

### Cách phân loại độ chắc chắn

- **CONFIRMED:** lập luận được dựng lại từ source và bằng chứng đối chứng phù hợp.
- **PARTIALLY:** phần cốt lõi đúng nhưng lý do, độ lớn, phạm vi hoặc đề xuất đi kèm sai/chưa chứng minh.
- **REJECTED:** bác phát biểu như đã viết; không phủ nhận một rủi ro khác gần giống nó.
- **CẦN THÊM BẰNG CHỨNG:** không nâng giả thuyết lên kết luận. Nêu cụ thể phép đo còn thiếu.

Với khẳng định API dùng tài liệu chính thức + reflection/XML cài tại máy hoặc artifact thực nghiệm; luật FTMO đối chiếu nhiều trang chính thức. Phát hiện code đối chiếu control flow với hợp đồng API và mô hình ca biên. **Mô hình PowerShell không phải failure injection vào cTrader**; không lấy chúng làm bằng chứng broker thật đã gặp lỗi. Những câu về lịch sử gap, bản fix API và partial fill thiếu xác minh thứ hai được đánh dấu chưa kết luận.

### Hồ sơ có thể chạy lại

[Verify-AuditEvidence.ps1](../diagnostics/lotsize-crosscheck-2026-09-06/Verify-AuditEvidence.ps1) và [verification.json](../diagnostics/lotsize-crosscheck-2026-09-06/verification.json): **21 kiểm tra pass**, gồm so sánh hai region sizing, reflection kiểu trả về, .NET NaN, số học restart/tier/gap-cap, DST 2026, các mô hình đóng lệnh thất bại, và tính lại P/L vàng từ artifact gốc. Không chạy backtest mới vì đã có tick events/report đủ để xác minh ví dụ cần dùng.

```powershell
& .\research\diagnostics\lotsize-crosscheck-2026-09-06\Verify-AuditEvidence.ps1
```

Source SHA-256 tại thời điểm audit:

| File | SHA-256 |
|---|---|
| `Combo/Combo/Combo.cs` | `B2C94C3A7A8AB08B2E67600A81D66AF6C792E1A3A30DD75189D5EB7D67CED4DC` |
| `MA Cross/MA Cross/MA Cross.cs` | `46E937994E209564D1A44A1D48E41A4014056832DBCC9BF25C72B766C5CCB356` |

Reflection đọc `C:/Users/Administrator/Documents/cAlgo/API/cAlgo.API.dll`; không gọi compiler. Desktop cài tại `.../app_5.9.10.52700/`; artifact CLI thuộc luồng standalone **5.9.0.38**, không được coi là thử nghiệm Desktop 5.9.10 chỉ vì cùng máy. API assembly version `1.0.0.0` cũng không phải product version.

## 2. Tự đọc source và kiểm kê bot

`rg --files --hidden --no-ignore -g '*.cs' -g '*.algo'` trả về hai source Robot, một `GlobalUsings.cs`, hai `.algo` ở root. `Data/cBots/` có hai thư mục `Combo`, `MA Cross`. Hai khai báo kế thừa `Robot` tương ứng với hai bot này. **Đúng hai cBot trong phạm vi repo + Data/cBots được yêu cầu**; không suy rộng rằng cả máy không thể có bản khác ở nơi khác.

| Vùng | Combo | MA Cross | Đối chứng |
|---|---|---|---|
| `CalculateVolume` | dòng 331–367 | 283–319 | Cùng balance risk, kiểm trước min, cap max, normalize Down, margin cap |
| `CapVolumeByMargin` | 392–459 | 329–392 | Cùng toàn bộ phép tính/nhánh/counter; khác prefix và cách ghép chuỗi log |
| SL đưa vào sizing | `abs(Entry-SLprice)/PipSize`, 271–274 | `KSL*ATR/PipSize`, 234 | Bằng nhau về đại số với dữ liệu hợp lệ; Combo thêm sai số phép trừ hai giá lớn |
| Lệnh | `PlaceStopOrder`, 281–289 | `ExecuteMarketOrder`, 244–250 | Combo SL **và TP tuyệt đối** theo signal.Entry; MA Cross khoảng cách pips từ fill |
| Reconcile | Positions + PendingOrders, 227–265 | chỉ Positions, 205–228 | Đúng khác biệt thiết kế market/pending; cả hai bỏ qua kết quả Close |
| PM | 465–528 | 398–459 | Cùng baseline, daily và latch; Combo còn xóa tracking pending |

Script xác nhận hai **region sizing thực thi tương đương** sau loại comment/whitespace/prefix và gộp string literals liền nhau. Không phải cùng một hàm dùng chung trong thư viện: đang là hai bản copy, có nguy cơ lệch khi sửa tương lai. Không đúng câu F1 “B1–B11 áp dụng y hệt”: riêng cơ chế absolute protection B4 thuộc Combo. [S1][S9] + source và kiểm tra region.

Đặt `B=Balance`, `r=RiskPercent/100`, `d=SLpips`, `p=PipValue`, công thức `V=B*r/(d*p)` có đơn vị volume-unit. Lot hiển thị bằng `V/LotSize`. Chỉ khi dữ liệu hữu hạn dương, valuation đúng, SL/fill đúng mô hình và normalize không tăng volume thì phần **gross loss lý tưởng** không vượt `B*r`. Không bao gồm phí, gap, FX drift hoặc tính nguyên tử của chuỗi lệnh.

## 3. Bảng phản biện B1–B11

| Mục | Claude nói | Codex verdict + bằng chứng độc lập | Đồng ý / bác / sửa |
|---|---|---|---|
| **B1** | Margin cap không đủ chặn notional/gap; FTMO index có thể mất 40–62%; BTC đủ an toàn | **PARTIALLY.** `N=M*L` và stress `loss≈N*g` đúng cho hợp đồng tuyến tính, cùng currency/time. Code không có stress cap. Nhưng 40–62% là **kịch bản giả định dùng hết cap**, không phải lỗ đã đo; còn MIN với risk-volume, broker max và FreeMargin. GER40 F2 chia notional EUR cho margin USD sai thứ nguyên. BTC 5,9% ngay trong bảng đã vượt daily 5%/3%. Nguồn [S3][S4][S20] + phép tính §5/§7 và raw logs. | Giữ rủi ro; bác “chỉ BTC an toàn”, bác gọi các mức stress là kết quả thực tế đã xác minh. |
| **B2** | Nullable có thể fail-open; API có bug CFD nên margin hiện tại có thể rác | **PARTIALLY.** Reflection DLL thật và [S1] đều trả **double**, không nullable; `double?` là wrapper do bot tự tạo, nhánh null **dead code**, không phải đường fail-open đang hoạt động. Thiếu kiểm hữu hạn/validity là thật. Forum [S5] chứng minh lỗi **Account.Margin trong backtest stock CFD 2023**, code tái hiện không gọi GetEstimatedMargin. Không chứng minh lỗi còn trong FTMO/5.9.10. [S7] có cải thiện margin backtest nhưng không ánh xạ ticket. | Sửa chẩn đoán; không dùng forum làm chứng cứ API hiện tại sai. |
| **B3** | Margin theo tier không tuyến tính nên scale tỉ lệ xuống có thể vẫn vượt budget | **PARTIALLY, bác lý do tier thông thường.** Với margin lồi, M(0)=0, state cố định: `M(aV)≤aM(V)`. Scale `a=.98*budget/M(V)` là bảo thủ. Tài liệu tier [S4] + mô hình 200 units/M=300, budget150 → V98/M98 xác nhận. Vẫn cần recheck vì giá, conversion, exposure/netting, broker repricing có thể đổi. | Giữ revalidation, sửa chứng minh; không quy mọi sai lệch cho dynamic tier. |
| **B4** | Combo absolute SL khiến entry slippage tăng risk; MA pips chuẩn hơn | **CONFIRMED cơ chế; PARTIALLY mức độ.** Source và native gold events xác nhận. Lệnh sell vàng có entry slip 1,79 giá = chỉ 0,0412% giá nhưng **9,2635% SL-distance**, gross/fees đưa net loss lên106,47 thay vì budget99,9976 (§5). MA tránh phần tăng khoảng cách vì entry slip nhưng vẫn chịu exit slip/rounding/FX/costs. [S9] + events/report. | Giữ; bác “trượt nhỏ theo % giá nên không đáng kể” và “MA risk tuyệt đối chính xác”. |
| **B5** | RiskPercent không phải upper bound khi gap/SL slip | **CONFIRMED.** SL thường thực thi theo thanh khoản; tick backtest vàng đã vượt target. [S9][S10] + §5. Nhưng gọi risk là “best-case” không chính xác: lệnh thắng/đóng sớm/volume làm tròn có thể lỗ ít hơn. | Đổi thành **nominal gross SL-risk**, không phải cam kết net loss. Retail cũng chịu rủi ro. |
| **B6** | PM mặc định tắt và kiểm theo bar nên quá chậm | **CONFIRMED, cần mở rộng.** OnTick chỉ gọi scheduler; PM được gọi khi signal đến hạn và OnBarClosed. H1 thường trễ tới gần một giờ, thị trường không tick còn lâu hơn [S14]. Bật PM cũng không bảo đảm đóng được: latch đặt trước Close, không retry; kiểm equity account nhưng chỉ đóng label/symbol (§4). | Ưu tiên cao hơn Claude; không gọi đây là “chốt chặn chắc chắn”. |
| **B7** | OnStart Balance thay initial capital; restart sau lãi làm lỏng, sau lỗ làm chặt | **PARTIALLY; hướng tác động bị đảo.** Initial10.000, restart10.800 → floor9.720 **chặt hơn** floor9.000. Restart9.200 →8.280 **lỏng hơn**9.000. Code + số học kiểm lại; [S20][S22]. Còn mất trạng thái breach đã xảy ra trước restart. | Giữ lỗi baseline; bác hướng giải thích. Không sửa bằng trailing bất kỳ. |
| **B8** | PipValue snapshot gây drift khi quote khác account | **CONFIRMED.** [S1] ghi snapshot; [S8] xác nhận account currency; XML tại máy tương ứng. Report GER40/JP225 + raw P/L cho đúng chiều conversion (§6). Drift không có bound “nhỏ”; tỷ giá đổi x% làm risk tiền đổi x% trong mô hình tuyến tính, trước costs. | Giữ; bỏ định tính “nhẹ” và không khẳng định mọi tài khoản đã test. |
| **B9** | Bonus/credit nằm trong Balance nên phải trừ Account.Credit | **REJECTED như đề xuất tổng quát; cần account mẫu.** `Account.Credit` thực sự có. [S13] và XML mô tả **Equity có Bonus cộng riêng**; chưa chứng minh Balance chứa Credit trên tài khoản nào ở đây. Trừ khỏi Balance có thể trừ hai lần. Margin basis Equity có credit lại là vấn đề khác. | Bỏ subtraction tự động; yêu cầu định nghĩa economic capital được xác minh theo account. |
| **B10** | Daily formula/reset/restart lệch FTMO | **CONFIRMED cho so sánh 2-Step, PARTIALLY phạm vi.** Bot trừ `%*dayBalance`, reset theo UTC, lấy balance lần quan sát; FTMO trừ `%*initial`, ngày CE(S)T. 1-Step là3%, 2-Step5%; [S20][S21][S23]. Source còn truyền **giờ mở bar đã đóng** vào rollover (§4). | Sửa theo product + timezone có DST + baseline bền vững; offset cố định không đủ. |
| **B11** | Risk theo Balance và margin theo Equity là đúng, FTMO cũng đòi như vậy | **PARTIALLY.** Hai basis là lựa chọn thiết kế hợp lệ, không lỗi đơn vị. FTMO quy định loss budget của account, **không bắt buộc** mọi lệnh size theo Balance hiện tại. Equity thấp nhưng Balance lớn có thể dùng hết room còn lại; code không kiểm room. [S20][S21] + source/§7. | Giữ thiết kế hiện tại; bác việc viện luật FTMO để chứng minh lựa chọn duy nhất đúng. |

### Sửa riêng nhận định “không cần đụng” phần E

`x<=0` không loại NaN; `.NET Double.TryParse("NaN", Float, InvariantCulture)` thành công trên máy, NaN không thỏa `<=0`. CSV loaders chỉ dùng `atr<=0`, Combo thêm `entryPrice<=0`, nên dữ liệu không hữu hạn có thể vào đường đặt lệnh. Chưa xác nhận broker chấp nhận NaN — khả năng reject/crash không được đổi thành “đặt lệnh quá lớn chắc chắn”. Cần finite-positive guards cho input và intermediate, bao gồm PipSize, Balance/Equity/budget/estimated margin/normalized volume, đồng thời kiểm SL/TP hợp lệ. Source + kiểm .NET thực trong script.

Max/Min kiểm **trước** normalize chưa chứng minh min sau normalize; margin fast-path trả volume ngay. Các tham số risk/daily/DD không có MaxValue100; giá trị bất hợp lý phải được validator/policy loại, không dựa vào UI min. Không khuyến nghị tự động ép volume lên min để sửa lỗi này.

## 4. Phát hiện mới hoặc bị đánh giá thiếu

### N1 — Reconcile không có xác nhận đóng/hủy thành công [CAO, code confirmed]

Combo250/261, MA224 gọi Close/Cancel nhưng bỏ `TradeResult`. API trả kết quả có thể lỗi [S11][S12]; source luôn `return true`. Mô hình ca biên trong script xác nhận đường logic:

| Kết quả hai bước | Trạng thái có thể có |
|---|---|
| Close thành công, entry bị từ chối hoặc volume bị block | **Flat**; signal đã IsHandled, không retry/rollback |
| Close thất bại, entry thành công trên hedging | Còn vị thế cũ và mới ngược chiều |
| Cancel pending thất bại, entry mới thành công | Pending cũ còn có thể fill; Combo đã xóa lifetime của nó |
| Exposure vừa thay đổi trong lúc reconcile | Snapshot ToList không còn mô tả state hiện tại |

Flat sau reversal reject **không tự nó là lỗi an toàn**; đó là trạng thái hợp lý khi không được phép mở lệnh mới. Sai là log/counter ngụ ý reversal đã hoàn thành, và vẫn gửi lệnh mới sau failure chưa xử lý. Không thể biến hai market operations thành transaction nguyên tử. Thiết kế tốt: preflight trước close để loại lỗi biết trước, xác nhận close/cancel, refresh state/risk, chỉ mở nếu sạch; nếu entry reject thì ghi `ReversalClosedButEntryRejected`, giữ flat. Không tự khôi phục lệnh cũ.

### N2 — PM đặt latch trước close và không retry [CAO, code confirmed]

Combo473/485 và MA407/418 đặt cờ trước `ForceCloseAll`; Close/Cancel trong hàm đều không kiểm result. Các lần PM sau bị điều kiện `!_accountBreached`/`!_haltedForDay` bỏ qua. Lệnh còn sót có thể tiếp tục lỗ khi bot đã báo halted. Combo còn clear lifetime toàn bộ. Đối chứng hợp đồng TradeResult [S11][S12] + mô hình ba lần PM chỉ một lần close attempt.

Cần tách **cấm entry** khỏi **đang cố đưa exposure về trạng thái mục tiêu**; trạng thái Breaching→Closing→Halted/Failed phải retry có giới hạn tốc độ, theo order/position ID, xác nhận bằng event và snapshot mới. Không log “đã đóng hết” trước xác nhận.

### N3 — Cờ halt không bao phủ lệnh đang trên server và toàn account [CAO]

OnPositionClosed chỉ bật `_haltedForDay`, không cancel pending (Combo510–516, MA443–448). HandleSignal kiểm cờ trước reconcile nhưng không kiểm lại sau close. Nếu callback đóng lệnh gây đủ chuỗi thua ngay trong Close, signal đó vẫn đi tới Place; nếu callback được giao sau, lệnh có thể đã gửi. **Thời điểm callback chính xác cần đo runtime**, nhưng thiếu recheck và thiếu xử lý pending đã xác nhận từ source. Các signal đến hạn cùng tick chỉ cập nhật PM một lần; chi phí/trạng thái thay đổi giữa những lệnh trong batch chưa được re-evaluate.

Equity là account-wide, ForceCloseAll chỉ lọc `Label && SymbolName`. Một bot không thể bảo đảm account daily/DD khi bot khác/manual trade tiếp tục mở hoặc giữ lệnh. Ngược lại nó có thể đóng lệnh của instance khác cùng label. Cần chốt coordinator account và phạm vi được quản lý; không tự chuyển sang đóng mọi lệnh của người dùng. [S13][S20] + cả hai source.

### N4 — Hai instance cùng bot/symbol khác timeframe dùng chung exposure [CAO nếu chạy nhiều instance]

Label cố định: `ComboCsvPending` / `MACrossCsvMarket`. Code thực tế dùng `Where(label && symbol)`, **không gọi Positions.FindAll(label)** như ví dụ gợi ý, nhưng hệ quả thiếu timeframe/instance là có thật. H1 có thể skip, đảo chiều hoặc force-close lệnh M15; cả hai đếm cùng một close vào loss streak. Combo lifetime dictionary lại riêng từng process. Instance không có locking/ownership, cùng tick cả hai có thể cùng thấy flat. Netting không tự giải quyết ownership: vị thế symbol có thể gộp giao dịch nhiều nguồn. Source state/reconcile + API position metadata [S11].

Chọn một trong hai thiết kế: mỗi instance label bền vững riêng + account coordinator; hoặc cố ý dùng exposure chung và chỉ một coordinator quyết định. Thêm timeframe vào label đơn thuần không giải quyết cap toàn account và restart.

### N5 — Pending tạo trước, margin/risk đánh giá sau [CAO với Combo]

Size/cap chạy một lần trước PlaceStopOrder. [S9] nói pending không bị ràng bởi available margin khi tạo; đủ margin được kiểm ở trigger. Vì vậy **placement accepted không có nghĩa fill accepted**, và broker chỉ kiểm đủ margin, không biết `MaxMarginPercent=50` riêng của bot. Không có reservation cho pending ở code, không subscribe Filled/Cancelled/Positions.Opened/Modified để revalue.

Không có phép kiểm ở `PendingOrders.Filled` nào chặn được fill đã xảy ra. Có thể revalue/cancel **trước** trigger, theo dõi pending, reserve budget cả portfolio; sau fill chỉ kiểm/giảm/đóng exposure phản ứng. Cancel-vs-fill race vẫn tồn tại. Margin nhiều pending across symbols không được cộng; hai order mỗi cái50% không tạo portfolio cap50%. [S9][S10] + source và mô hình margin script.

### N6 — Restart/stop làm mất lifetime pending và lịch sử risk [CAO khi vận hành dài]

OnStop chỉ in thống kê; OnStart không dựng lại `_pendingOrderLifetimes`, `_currentDay`, midnight balance, loss streak hay breach. Pending server có thể tiếp tục tồn tại sau khi bot dừng; expiration code chỉ duyệt dictionary đã rỗng. Đúng hay không việc để lệnh tiếp tục là policy, nhưng **giới hạn 3 bar không còn được bảo đảm qua restart**. Ràng buộc placement hiện không gửi expiration server. [S9][S12] + Combo147–186/659–680.

Rollover còn dùng `Bars.OpenTimes.Last(0)` trong OnBarClosed: đó là **open time của bar vừa đóng** [S14]. H1 tại00:00 thường truyền23:00 ngày trước, tới01:00 mới rollover nếu không có signal xen giữa; sau cuối tuần timestamp còn cũ hơn. Không chỉ là lệch UTC↔CEST. Baseline lấy khi callback chạy cũng không chắc là balance đúng nửa đêm.

### N7 — Counter không phải audit trail thực thi [TRUNG BÌNH]

- `_reversalsExecuted++` trước khi xác nhận Close/Cancel; Combo tăng cả khi chỉ hủy pending đối hướng, chưa từng có position. Entry sau đó có thể fail.
- `_ordersPlaced` của Combo đếm pending được nhận, không phải filled. Trigger reject sau đó không vào `_orderFailures`.
- `_expiredOrders++` và thông báo cancelled xuất hiện dù Cancel trả lỗi; tracking bị xóa.
- `_marginCapped` tăng trước order acceptance; `_marginBlocked` có thể đồng thời dẫn tới `_orderFailures`, nên các counter không phải tập rời để cộng.
- `_signalsProcessed` tăng trước guard và before placement; invalid/missing CSV không phải broker reject.

Source tại các increment + phân biệt order/deal/position của [S9][S10]. Nên có events với SignalId, InstanceId, OrderId, PositionId và outcomes attempt/accepted/partial/filled/rejected/cancel-failed; không sửa lịch sử định nghĩa counter âm thầm.

### N8 — Partial fill chưa được kiểm chứng cho account này [KHÔNG KẾT LUẬN lỗi live]

Stop kích hoạt market order; docs cảnh báo partial execution; Open API có `ORDER_PARTIAL_FILL` [S9][S10]. Combo không xử lý fill/modified event và không đối chiếu requested-vs-filled volume/VWAP, residual hoặc protection cuối. **Không được khẳng định** phần dư luôn còn pending, luôn bị cancel, hay một order luôn bằng nhiều positions: phụ thuộc execution policy/account, phải đo deals và lifecycle thực tế. Cũng không có bằng chứng hiện tại rằng partial fill làm mất SL/TP. Partial fill thường giảm volume đã vào, nhưng VWAP, phí tối thiểu, residual và race vẫn cần audit. Bổ sung acceptance scenario; không viết thêm lệnh bù volume tự động.

### N9 — Dữ liệu “leverage thay đổi theo thời gian” chưa chứng minh broker đổi tiers [TRUNG BÌNH, bằng chứng mới]

Đọc lại **raw logs**, không chỉ báo cáo `leverage-pipvalue-crosscheck-2026-09-04.md`:

| Artifact | Số log margin | Estimated margin / requested unit |
|---|---:|---:|
| `cli_batches/Combo_leverage_crosscheck_2025H1/BTCUSD/log.txt` | 338 | 105.764,0769–105.764,2000 USD/unit; mean105.764,1121 |
| `cli_batches/Combo_leverage_probe_20260903-093610/BTCUSD/log.txt` | 110 | 66.819,9091–66.820,0000 USD/unit; mean66.819,9516 |

Trong mỗi run, tỷ lệ **gần như hằng số** đến sai số log2 decimals dù volume đổi. Điều đó không chứng minh tier đổi theo lịch sử; cũng phù hợp với valuation/snapshot của engine khác giữa run. Chia historical price cho hai denominator khác nhau sẽ tạo “effective leverage” khác nhau mà chưa xác định nguyên nhân. Mẫu338 không loại được **systematic bias/confounding**, chỉ giảm một phần sampling noise. Muốn kết luận broker đổi leverage phải ghi đồng thời Bid/Ask, conversion, DynamicLeverage tiers, account leverage, estimated margin và actual margin delta tại cùng thời điểm, nhiều mức volume, kiểm cả GUI/CLI.

GER40 F2 thừa nhận giá EUR nhưng mẫu số USD; phải đổi notional EUR→USD trước khi gọi kết quả là leverage. Không chọn default D2=15 từ những số đo chưa đồng nhất. [S3][S4] + hai raw logs; con số thống kê đầu được lưu trong verification.json.

### N10 — Dữ liệu D1 và việc xác nhận “gap thật” thiếu provenance đủ mạnh

Report gap09-03 mô tả công thức đúng `abs(Open_i-Close_prev)/Close_prev` trên DWH/Capital.com D1, nhưng không kèm các cặp OHLC nguồn/timestamp/session/raw query cho từng cực trị. **Audit này chưa tái trích xuất DWH**, nên không xác nhận các con số5,38/6,61/9,95% là khoảng không có thanh khoản tương đương FTMO. Đặc biệt việc ghép ngày/sự kiện không tự chứng minh đúng dữ liệu: cần kiểm holiday missing bars, timezone, adjustment/rollover, bid/ask, coverage và chiều long/short. D1 không đo intraday tick jumps hay widening spread. Dữ liệu đến09/2026 dùng để đánh giá lịch sử là được, nhưng dùng làm tham số backtest01/2026 rồi gọi out-of-sample sẽ có look-ahead.

Không bác khả năng gap lớn; **bác mức chắc chắn** của F2/F3/F5 và câu “7 nguồn cùng xác nhận” khi vài nguồn chỉ lặp lại kết luận báo cáo cũ. Nguồn stress framework [S35] + kiểm metadata/method trong report gap.

## 5. Chi phí và TP tuyệt đối: tính lại từ backtest vàng thật

Artifact: [tick run 01–07/01/2026](../cli_runs/Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125/), gồm `events.json`, `report.json`, `signal-trace.csv`, `params.cbotset` và log. Log chạy account7563609, broker `FTMO Platform`, XAUUSD/h1, ticks. Không lấy trường `brokerTitle` trống của native report để suy broker khác.

Lệnh sell order5: signal entry4347,52; ATR19,323065572483955, KSL1; volume5units=.05lot; SL4366,843065572484; fill4345,73; close4366,92. Balance sau reversal trước đó9999,76, risk budget99,9976.

| Thành phần net loss | USD |
|---|---:|
| Volume × khoảng cách signal entry→SL | 96,6153278624 |
| Entry slippage: `(4347,52−4345,73)×5` | 8,95 |
| Exit slippage: `(4366,92−4366,843065572484)×5` | 0,3846721376 |
| Swap | 0,52 |
| Commission trong run | 0 |
| **Tổng, khớp report.history.net** | **106,47** |

Vượt target6,4724 USD = **6,4726% risk budget**, khoảng0,0647% balance. Phần entry slip riêng chiếm9,2635% SL-distance. Có5 trades trong run tuần; tổng swap−0,52. Run Jan–Feb đang mở trong IDE có49 trades, tổng swap−20,38, commission0 và `applyCommissionAutomatically=false`. **Đây là đối chứng execution đã có phí swap, nhưng chưa phải mô phỏng net costs đầy đủ của FTMO**. Không áp phí hiện tại lên lịch sử mà gọi đó là phí đã phát sinh thật. [S9][S18][S24] + raw reports.

Công thức lỗ tiền tổng quát, với `k_t` là account-currency / price-point / unit tại valuation thích hợp:

```text
loss_net ≈ V × (d_SL_price + adverse_entry_slip + adverse_exit_slip) × k_t
           + entry_commission + exit_commission + adverse_swap + other_fees
```

MA relative protection bỏ phần tăng d_SL do entry slip trong mô hình SL từ fill; không bỏ exit slip/cost/FX. Commission tối thiểu không tuyến tính với volume; swap tùy số đêm/ba-ngày/direction, có thể dương hoặc âm. Không có một % “risk thực cao hơn” chung cho mọi lệnh.

**Spread không được cộng hai lần.** Với SL pips từ giá fill, gross loss fill→giá đóng đã phản ánh bid/ask thực thi; cộng thêm full spread mặc định vào cùng khoản gross đó là double count. Nếu khoảng cách tính từ mid/Bid/reference khác execution side thì phải điều chỉnh đúng side. Spread widening còn có thể kích hoạt SL sớm hoặc tạo slippage, không nhất thiết bằng một spread cố định. [S9][S10][S18] + ví dụ đối chứng.

TP Combo cũng tuyệt đối. Với direction `s=±1`, `δ=s*(fill−signalEntry)` dương khi fill bất lợi:

```text
risk_distance_actual   = KSL*ATR + δ
reward_distance_actual = KTP*ATR − δ
RR_actual = (KTP*ATR − δ)/(KSL*ATR + δ)   # trước phí/rounding/exit slip
```

Order5 có RR dự kiến2,618 nhưng sau fill còn khoảng2,311 (trước fees). Nếu δ tiến gần reward-distance, phần thưởng còn rất nhỏ; nếu vượt TP phải kiểm broker xử lý protection ngay lúc fill, không tự khẳng định TP được dời. **Tự dời SL/TP theo fill sẽ đổi thiết kế Combo**, không phải fix sizing thuần túy.

## 6. API và hai implementation bên ngoài

### API: những gì xác nhận được và không được

| API/ý nghĩa | Kết quả cross-check |
|---|---|
| PipValue snapshot | Xác nhận từ [S1] + XML local; TickValue cũng snapshot. |
| PipValue per unit | Ví dụ volume API của [S1] + raw vàng: gross105,95 / (21,19pips×5units)=1USD/pip/unit; LotSize100. EURUSD chuẩn100.000units/lot:10USD/pip/lot→**0,0001**, không phải0,001 như ví dụ G1 Claude. |
| Account currency | [S8] + raw GER40/JP225/HK50 trong các artifact của audit09-02: profit-currency phải quy về deposit asset. Không nhân FX lần nữa vào PipValue đã converted. |
| GetEstimatedMargin signature | `double GetEstimatedMargin(TradeType tradeType, double volume)`; reflection local xác nhận nonnullable. Đừng nhầm với `Account.MarginLevel` nullable. |
| Dynamic/tier leverage | [S4] mô tả tiers/exposure/broker repricing; [S3] nêu backtest estimator bỏ qua account exposure. Không suy ra live≈backtest mọi state, không suy ra luôn linear hoặc luôn gross/incremental đơn giản. |
| Version fix margin | [S5] là bug lịch sử reproduced12/2023, chưa nêu build fix. [S7] 5.9 có cải thiện margin/stop-out nhưng không định danh ticket. **Chưa biết chính xác bản fix, chưa chứng minh bug còn mở trên5.9.10.** |
| Normalize precision | [S6] bug GOLD20→19,9 ở4.0; cùng thread ghi Spotware beta trả20. Không có số release fix cuối; [S7] không bổ sung mapping. **Không được nói bug vẫn tồn tại hoặc đã fix ở bản cụ thể.** |
| Min/Max/Step/LotSize | Min/max lượng được giao dịch, Step increment units, LotSize units/lot; [S1] + report.usedSymbols/volume/quantity. Không dùng decimals của lot để thay step của units. |
| TickValue/PipValue | Với cùng valuation linear, `TickValue/TickSize≈PipValue/PipSize=k`. [S1] + cách cTrader-GURU tính giá trị SL [S19]. Snapshot TickValue không chữa drift của PipValue. |

Về grid normalize: docs/reference local **không công bố thuật toán origin0 hay min cho mọi cấu hình min/step**; methods abstract nên reflection không thực thi được. Với min là bội của step, hai quy ước trùng nhau: nếu `min<V<min+step`, Down lý tưởng cho **min**, không phải min−step hay zero. Ví dụ min1,step1,V1,2→1; case min0,03/step0,02 mới phân biệt được origin. Raw FTMO vàng lot100/step1 và sizing floor phù hợp grid thường, nhưng chưa có phép gọi runtime ca min không chia hết step/precision boundary. **Cần thêm bằng chứng**, không tự viết lại API normalize bằng `min+floor((V-min)/step)*step` rồi gọi đó là chuẩn cTrader.

Bất kể origin: sau normalize phải kiểm finite, `min≤V≤max`, `V≤mọi cap` và `V>0`; dưới min thì **skip**. Không `Max(normalized,min)` như G2 Claude nếu vượt risk/stress/margin cap. Trường hợp min.03,step.02 ở đây chỉ là probe phân biệt, không khẳng định broker này cho phép metadata đó.

### Hai implementation đã đọc trực tiếp, không chỉ trang quảng cáo

| Implementation, commit cố định | Cách tính đã đọc | Điều học được / giới hạn |
|---|---|---|
| EarnForex **cTrader** Position Sizer, `b77ef1fbca2ec0ae6c23f8ca8a798cc1f7f99605`, `TradeSizeMethods.cs` [S17] | Money target chia `(RealStopLossPips*PipValue + round-trip commission per unit)`, normalize; tính lại result risk | Có commission theo loại/currency, portfolio risk, balance/equity/balance-minus-portfolio-risk, margin/swaps UI [S18]. Sizing chính vẫn dùng snapshot PipValue; hiển thị swap không đồng nghĩa reserve mọi swap tương lai. |
| cTrader-GURU **EMA Power**, `fd4d0bb070d573244470ef9ddf44c8c5c642ce1f`, `Extensions.MonenyManagement.GetLotSize` [S19] | Risk capital chia `(SLpips*PipSize*TickValue/TickSize)` rồi đổi units→lots | Đại số tương đương repo nếu k nhất quán. Nhưng Math.Round(lots,2), clamp lên min và FakeSL khác SL thật có thể tăng/lệch risk; market-range order2pips giới hạn entry slip nhưng có thể không fill. Không copy cả risk module như chuẩn an toàn. |

EarnForex `Tools/VolumeTools.cs` còn có helper công thức cơ bản giống repo, nhưng đường sizing chính nói trên **đã thêm commission**. `ForMargin.cs` dùng GetEstimatedMargin và account/custom leverage; không thấy một bộ giải tiered-margin độc lập bảo đảm đúng mọi portfolio. Vì vậy không bot bên ngoài nào được dùng làm oracle cho toàn bộ D2. Những implementation có lịch sử công khai hữu ích để đối chiếu, không phải chứng nhận production an toàn. Source [S17][S19] + docs [S18]/API [S1].

## 7. Audit từng đề xuất D1–D7

| Đề xuất | Quyết định | Phiên bản Codex |
|---|---|---|
| **D1 gap/notional cap** | **HOÃN khỏi scope sửa hiện tại** | Rủi ro gap có thật; chưa có scenario/default đủ bằng chứng. Không thêm một engine để sửa công thức SL-risk vốn hợp lệ. |
| **D2 sanity margin fallback** | **GIỮ phần nhỏ, BỎ fallback tự đoán** | Kiểm số hữu hạn; dùng đúng kiểu double của API. Không cần tự dựng margin model hay chặn mọi lệnh chỉ vì chưa có model thứ hai. |
| **D3 PM on/tick/initial/reset** | **TÁCH sửa PM hiện hữu khỏi mở rộng** | Khi bật PM: sửa nhịp kiểm, halt/close và retry. Baseline/ngày đúng FTMO cần làm cho product thực dùng trước khi dựa vào PM để bảo vệ account; không làm mọi mode, không tự bật default. |
| **D4 recheck margin** | **HOÃN thuật toán tìm volume mới** | Chưa chứng minh cách scale hiện tại sai vì tiered leverage. Nếu có nhu cầu xác minh cuối, một estimate lại rồi skip khi vượt là phương án nhỏ hơn solver. |
| **D5 safety factors** | **BỎ đề xuất thêm knob** | Giữ ý nghĩa .98 là haircut trong nhánh scale; không quảng cáo thành buffer toàn cục. Thêm .90/.95 khi chưa hiệu chỉnh chỉ thay exposure tùy ý. |
| **D6 credit/offset/trailing** | **BỎ trừ credit; timezone thuộc PM** | Không thêm EconomicCapital abstraction hay trailing mode chưa dùng. |
| **D7 Combo slip buffer/log** | **HOÃN thay sizing/execution** | Có bằng chứng slippage; chưa có buffer hợp lý. Giữ SL/TP theo thiết kế, dùng trace hiện có; không tự partial-close, dời SL/TP hoặc đổi loại lệnh. |

### D1 — Đúng thứ nguyên, không phải cam kết không lỗ vượt cap

Gọi `P` đơn vị price-point, `p=PipValue` có đơn vị account-money/(pip×unit), `q=PipSize` có đơn vị price-point/pip; vậy `k=p/q` có đơn vị account-money/(price-point×unit). `P*g*k` là account-money/unit, và `Equity*h/(P*g*k)` trả units: **đúng thứ nguyên** cho exposure tuyến tính. XAU example ở§5 kiểm được k=1. [S1][S8] + số học này.

Giới hạn điều kiện: chỉ bound loss trong **kịch bản đã giả định**, nếu giá tham chiếu và k đúng, adverse move≤g, fees được tính, execution không tệ hơn giả định. Không có chặn tuyệt đối khi gap lớn hơn, thanh khoản mất, FX biến động, broker đổi hợp đồng hoặc process/network chết. Dùng snapshot k vẫn sai cho quote≠account; bid cho cả buy/sell/pending không nhất quán với worst fill/reference. Cần phân biệt:

1. **Tổng lỗ từ entry:** nếu gap xảy ra khi đã đi gần SL thì stress-distance có thể là `SL-distance + gap-jump`, không chỉ `entryPrice*g`.
2. **Lỗ thêm từ equity hiện tại:** room tính từ equity đã mark-to-market, nên không trừ lại khoản floating loss đã nằm trong equity. Phải stress các positions từ giá hiện tại và trừ chi phí thoát chưa ghi nhận.

Lấy MIN của cap đúng nghĩa không bao giờ tăng volume; normalize sau MIN và revalidate. Cap ra dưới min thì skip. `Equity≤0`, Bid/PipSize/k/g≤0, NaN/Infinity hoặc phép nhân overflow/underflow → không có estimate hợp lệ, **không gửi lệnh**. Giá gần0 khiến percentage-gap gần0 và cap tăng vô hạn; cần absolute price-move floor/contract-aware scenario. Không tuyên bố công thức dùng được mọi sản phẩm phi tuyến/inverse/giá âm.

**Hai knob không hóa giải mâu thuẫn kinh tế.** Với L cố định, notional cap và margin cap tương đương qua `m=N/(L*E)`. Ví dụ E10.000, giá100, SL1, risk100 →100units, N=E; muốn chịu gap10% mà mất≤100 chỉ được10units; SL-risk còn10. Dù gọi knob nào, vẫn phải giảm risk thực. RiskPercent là ceiling/target trước constraints, không “vô nghĩa” khi một constraint khác chặt hơn. D1 tách ý nghĩa hai budget là tốt; hứa giữ risk1% trong mọi trường hợp là sai. Script đã kiểm phản ví dụ.

**Default:** không có g=2% hay notional10x nào được dữ liệu hiện tại xác nhận an toàn cho mọi symbol. D1 ví dụ `MaxGapLossPercent=25` vượt xa total-loss10% FTMO; phải bác như default. **Tự audit: rút cả đề xuất Codex `min(0,5% InitialCapital, 25% remaining account room)` làm giá trị khởi điểm.** Chưa hiệu chỉnh nên không đủ cơ sở khuyên dùng, dù trước đó đã gắn nhãn giả định. Không thay nó bằng một bộ số tùy ý khác.

Với g, chưa đủ dữ liệu thì profile thiếu cấu hình phải hiện trạng thái “chưa đủ stress specification”, không dùng2% ngầm. Cách chọn: `max(validated event scenarios, adverse gap percentile with confidence allowance, ATR-multiple price move, absolute floor)` theo symbol/chiều/session/holding horizon. ATR phải cùng time horizon: dùng ATR H1 đơn lẻ cho weekend gap là không đủ. Historical max không bound tương lai; quantile cần sample size/coverage và validation OOS. §4N10 chỉ ra vì sao chưa dùng nguyên bảng09-03 làm default chắc chắn. [S35] + phản ví dụ ở đây.

### D2 — Tại sao fallback Claude không fail-closed thực sự

`notionalMargin=V*Bid*k/AssumedLeverage` đúng đơn vị nhưng giá trị leverage không tự biết từ asset class. `Max(API,0,5*notionalMargin)` cho phép margin thực **gấp đôi** floor đã suy ra, chưa kể AssumedLeverage quá cao. Ví dụ notional30.000, assumed15 →margin model2.000; nhân0,5 còn1.000. Nếu leverage thật5, margin6.000: đề xuất vẫn underestimates6x. Không có chứng minh cho hệ số0,5.

`Account.PreciseLeverage` là account ceiling; không thay dynamic symbol tiers/margin currency/hedged margin. [S3][S4] + [S18] cho thấy estimate còn phụ thuộc exposure; margin incremental bằng0/âm có thể có ý nghĩa khi giảm/hedge exposure, nên không định nghĩa mọi0/âm là bug mà không biết contract. Repo thường đóng trước nhưng còn có positions label khác.

Phiên bản đề xuất: finite validation; xác định standalone/incremental margin semantics cho state đang xét; lấy official account-symbol metadata/tier schedule và conversion tại thời điểm; tính conservative model nếu hỗ trợ; so sánh với API, log cả hai và actual fill delta. Nếu model không có/không hiểu disparity thì block entry hoặc giảm theo policy được xác nhận, **không invent leverage**. Nếu có model bảo thủ đáng tin, lấy max đầy đủ API/model phù hợp semantics, không nhân0,5. Model giả định leverage1 cũng không phổ quát cho hợp đồng margin khác notional; phải tuyên bố domain.

### D3 — PM theo product, có state machine và thời gian đúng

Không đơn giản bật3checkbox rồi gọi “FTMO safe”. Profile FTMO cần initial allocation explicit từ account contract/MetriX; `InitialCapital=0→BalanceOnStart` chỉ hợp lệ ở tài khoản mới được xác nhận, không sửa restart nói chung. Persist midnight balance, EOD high-water, account ID/product/currency, day-key, breach và pending ownership; reconstruct từ transaction history gồm deposits/withdrawals/fees khi cần. Bot đang pause không làm lịch sử account biến mất.

UTC code: dùng `Server.TimeInUtc` (hoặc chứng minh Server.Time UTC theo Robot attribute) → `TimeZoneInfo` zone **Europe/Prague**; Windows map `Central Europe Standard Time`. `Date` sau conversion làm day-key. Không hardcode+1/+2, không lấy timezone Windows hiện tại của VM. Script thực đã kiểm rollover mùa đông/hè và DST29/03,25/10/2026; Microsoft API dùng adjustment rules [S30] + phép chạy Windows. Ngày DST dài23/25h; timer next midnight phải dựng theo local calendar và đổi ngược UTC, không cộng24h máy móc.

OnTick kiểm vài số account/threshold O(1) thường nhỏ; **không scan/close toàn bộ positions trên mọi tick**. Dùng event-driven cached state; timer kiểm thêm khi chart symbol im tick nhưng account vẫn biến động ở symbol khác; ngay trước entry phải refresh PM, kể cả sau reconcile. Close retry per ID/in-flight guard, backoff và log theo state transition để tránh spam/rate-limit [S15]. Chưa benchmark overhead trên VM, không hứa con số latency. Mất feed/network thì tick/timer không tạo giá mới hoặc bảo đảm thoát được.

Kích hoạt khi equity vừa chạm legal boundary có thể đã muộn; cần internal threshold cao hơn boundary một buffer, và trước order phải reserve stop/stress/cost của mọi exposure. Static, EOD trailing và intraday equity trailing là **ba mode khác nhau**; không gọi chúng cùng `MaxDrawdown`. Luật profile ở§8. Default-on nên thuộc profile đủ dữ liệu, không đổi ngầm behavior của backtest cũ khi người dùng mới chỉ yêu cầu audit.

### D4 — Recheck là tốt, giảm1step3–4vòng không đủ

Với tickstep0,01 và estimate tăng20%, giảm0,03–0,04 có thể không khắc phục. Progressive tiers cố định không phải lý do chính cần loop (chứng minh B3), nhưng final estimate và cap invariant vẫn hữu ích. Nếu M(V) monotone trên domain hợp lệ, binary search/grid search lượng phù hợp, cap số lần/time; nếu không monotone do hedge/netting thì không áp chứng minh binary search tùy tiện. Normalize rồi recheck risk/margin/stress/min/max. Cạn retry hoặc state đổi không kiểm được → skip, không trả last-invalid volume. [S1][S4] + mô hình tier/rounding.

### D5 — Haircut hiện tại không phải buffer toàn cục

`.98` chỉ chạy khi `estimatedMargin>budget`. Nếu estimate=budget hoặc nhỏ hơn rất ít thì fast-path trả nguyên volume, **không để2% buffer**. Đổi parameter .90 mà giữ vị trí áp dụng vẫn bỏ lọt fast-path. Nên reserve ngân sách: `allowedMargin=safetyFactor*budget` trước mọi nhánh (hoặc explicit cash buffer), rồi cap/verify thống nhất. Factor phải finite và0<factor≤1.

Một RiskSlippageFactor=.95 không thể kiểm soát gap tail; nó chỉ giảm đều volume5%, không phản ánh commission-minimum hoặc SL hẹp. Cost-adjusted sizing với adverse-slippage-distance được hiệu chỉnh có ý nghĩa hơn thêm nhiều hệ số rời. Market/pending có execution model khác; dùng .90 cho pending là giả định chính sách, không kết quả chứng minh. Source fast-path + ví dụ đơn giản estimate=budget và [S9].

### D6 / D7 — Những phần cần bỏ, gộp, bổ sung

D6 credit: không trừ vào Balance khi chưa chứng minh credit đã nằm trong Balance. Nếu policy dùng economic equity loại bonus, phải xác định cách broker phản ánh bonus ở Equity và cash flows; code hiện tại không có dữ liệu mẫu credit để chứng nhận công thức phổ quát. Reset offset đã gộp D3; trailing cần đúng product§8. [S13] + XML local.

D7 buffer SL để size nhỏ hơn có thể giữ nguyên **giá SL/TP tuyệt đối** của Combo. Sau fill, tính theo EntryPrice/StopLoss/TakeProfit thật, volume/VWAP và phí; xác minh protection có mặt/hợp lệ, capture actual risk. Nếu quá cap, policy có thể partial-close/close, nhưng giảm sau fill không undo loss/fees và partial-close cũng có min/step/error. Stop-limit/market-range hạn chế entry price nhưng đổi xác suất fill, nên là **thay đổi chiến lược cần chốt**, không thay PlaceStopOrder lén. [S9][S11] + tick gold.

GSL chỉ có ý nghĩa khi broker/account/product thật sự cung cấp và phí đã budget; nguồn IG mô tả bảo đảm giá và premium [S36], protocol cTrader có field GSL [S10]. **Không có bằng chứng account FTMO này hỗ trợ GSL qua overload bot đang dùng**; không đề nghị một parameter giả là sẽ giải quyết gap.

## 8. Luật FTMO: giải quyết static / trailing theo đúng account type

Phạm vi dưới đây là **CFD**, không nhập nhằng với FTMO Futures. Nguồn chính thức hiện tại [S20] đối chiếu Academy [S21][S22], giới thiệu1-Step [S23] và trang sản phẩm [S38]. `I`=Initial Simulated Capital; `B_day`=balance đúng00:00 Prague; `H_day=max(I,highest midnight balance so far)`.

| Product | Daily equity floor | Total equity floor | Quy tắc liên quan |
|---|---|---|---|
| Challenge/Verification và FTMO Account **2-Step** | `B_day−0,05I` | **Static** `0,90I` | Evaluation tối thiểu4 trading days; không có Best Day50% trong bộ objectives2-Step |
| Challenge và FTMO Account **1-Step** | `B_day−0,03I` | **EOD trailing** `H_day−0,10I` | Best Day≤50% tổng profit của **các ngày có lãi**, không phải50% net profit toàn kỳ |

Floors so với equity gồm floating P/L, swaps và commissions; daily reset00:00 CE(S)T, không phải midnightUTC hoặc MetaTrader server time. EOD high-water1-Step chỉ ratchet ở day boundary, không theo tick equity high; reward withdrawal kèm cấp account mới reset floor theo quy định. Best Day vượt50% không lập tức breach account: cần tiếp tục đạt điều kiện lợi nhuận trước pass/reward. [S20][S21][S23][S38].

**Mâu thuẫn static/trailing được giải quyết dứt điểm theo product:** Academy static là2-Step;1-Step mới có trailing. Tuy nhiên log `FTMO Platform`, demo/hedging/leverage30 **không chứng minh account7563609 thuộc product nào**. Chưa tự gán profile. Standard/Swing là chiều account conditions khác với1-Step/2-Step; không suy product từ leverage hoặc từ cách bot trade.

### Leverage chính thức hiện tại và lý do không hardcode theo asset class

Trang [Symbols][S24] render bảng bằng JavaScript; đã truy API **của chính FTMO** [S25] và inspect bundle [S26]. Response global nhận được trong phiên:

| Nhóm / symbol minh họa | Standard | Swing |
|---|---:|---:|
| Forex EUR/USD | 1:100 | 1:30 |
| Phần FX như NZD/USD: phải đọc từng symbol | Không dùng100 cho tất cả | Không dùng30 cho tất cả |
| US30/US100/GER40 cash | 1:50 | 1:15 |
| HK50.cash (và ngoại lệ index cần đọc riêng) | 1:30 | 1:9 |
| **XAU/USD và XAG/USD global response** | **1:50** | **1:15** |
| BTCUSD | 1:3,33 | 1:1 |

Response cho XAU và XAG ghi marginPercent2, leverageStandard50/leverageSwing15; EURUSD margin1, BTCmargin30. Code trang `/en/` dùng trường từ API; map riêng trong JS được dùng cho `/au/` và có XAG30/9. **Không trộn global với Australia.** Trang FAQ cũ [S27] ghi metals30/9; đó không đủ làm “số hiện tại mọi metal”, và không phải hai nguồn đồng ý cho XAG. Kết quả phù hợp giá trị XAU gần15 đo trong các log account leverage30, nhưng không suy đó là phép chứng minh tiers của broker.

Giới hạn bằng chứng: API response đọc được hai lần, các lần tải lưu lại sau đó gặp timeout/TLS; bundle đã lưu [ftmo-symbols-table.js.txt](../diagnostics/lotsize-crosscheck-2026-09-06/ftmo-symbols-table.js.txt). Không có snapshot đầy đủ thành công của response để replay. Bảng là **giá trị official endpoint quan sát trong phiên**, không chứng nhận contract của account đang dùng; chưa có account-side probe thứ hai cho XAG. Vì quy tắc hai nguồn, XAG/account7563609 và các asset ngoài bảng **cần xác nhận Symbol specification thật trước khi dùng sizing**. Không bịa bảng đầy đủ cho mọi metal/FX từ một thông số quảng cáo.

[S28] cấm risk management bất thường/cumulative exposure của một trade idea hoặc correlated symbols; [S29] cũng cảnh báo dùng phần lớn margin cho exposure thiếu đa dạng. Vì vậy “chưa chạm5%/10%” không tự chứng minh mọi sizing được phép. Không tìm thấy hard1%/trade universal trong objectives được kiểm; không biến khuyến nghị hoặc enforcement cá biệt thành luật chung. Rule news/overnight phụ thuộc product/stage/Standard–Swing; bot hiện không có profile lịch này, cần đối chiếu contract khi triển khai.

## 9. Framework định lượng và pseudo-code đề xuất

Không có framework nào vừa dùng dữ liệu quá khứ vừa **bảo đảm tuyệt đối** gap tương lai. So sánh mục tiêu:

| Cách | Nó giải quyết gì | Có thay được D1 / PM không? |
|---|---|---|
| **Scenario stress budget + remaining account room** | Hạn chế lỗ danh mục trong các cú sốc giá/FX/spread được định nghĩa; reserve pending | Thích hợp nhất cho hard account limits hiện tại; cần data và assumptions rõ |
| Volatility targeting | Exposure theo inverse vol; paper Moreira–Muir dùng inverse variance cho managed portfolios [S32] | ATR sizing hiện đã giảm exposure khi ATR tăng. Thêm regime factor có thể trùng/double de-risk; không bắt gap/illiquidity chắc chắn, cần OOS [S33] |
| Fractional / risk-constrained Kelly | Tối ưu growth với distribution và ràng buộc xác suất drawdown [S31] | Cần edge/distribution đáng tin, dễ nhạy estimate; không dùng để “chứng minh an toàn” khi nhiệm vụ hiện tại là kiểm chứng, không tối ưu |
| CVaR / Expected Shortfall | Tail **average** loss và portfolio dependence, có thể tối ưu dưới scenario constraints [S34][S35] | Không phải maximum loss: worst scenario vẫn có thể lớn hơn CVaR. Kết hợp hard stress constraints, không thay kill-switch |

Khuyến nghị trên là suy luận thiết kế từ mục tiêu repo và các nguồn; không phải paper nào chứng nhận riêng Combo/MA Cross. Hai nguồn nghiên cứu chính [S31][S34] và stress guidance [S35] củng cố việc ghi assumptions, tập scenario và ràng buộc danh mục, thay vì một adverse-gap% cố định không hiệu chỉnh.

Pseudo-code mở rộng để tham khảo, **đã rút khỏi phạm vi sửa hiện tại sau tự audit §10.1**. Không dùng khối này làm checklist bắt buộc hoặc yêu cầu bot ngừng giao dịch vì chưa có stress profile:

```text
BeforeSignal(signal):
    validate finite prices/ATR/contract/account/profile
    refresh account risk state using UTC -> Prague calendar
    if entry halted or state/valuation unavailable: skip
    preflight signal price, protection, volume feasibility
    reconcile owned exposure:
        confirm every close/cancel; refresh state after each result
        on failure: prohibit new entry, retain ownership/tracking, reconcile later
    refresh risk state again                         # fees / callbacks / other bots
    if entry halted: skip

    V_stop = largest admissible volume whose SL loss + costs <= Balance*r
    room = Equity - max(daily_floor, total_floor) - execution_cash_buffer
    # Existing scenario losses are ADDITIONAL losses from current marks.
    # Already-booked/floating losses in Equity are not deducted a second time.
    V_stress = largest volume such that for every approved scenario s:
        existing_additional_loss[s] + pending_reserved_loss[s]
          + candidate_loss_from_execution[s](V) <= room
        candidate_loss_from_execution[s](V) <= per_idea_stress_allocation
    V_margin = solve with verified account/symbol margin model and available budget
    V = NormalizeDown(min(V_stop, V_stress, V_margin, broker_max))
    assert finite V >= broker_min and all constraints still satisfied
    if no feasible V: skip                           # NEVER clamp upward to min
    atomically reserve account budget if multiple instances participate
    send order, record TradeResult and IDs
    refresh reservation/state on accepted/filled/partial/cancelled/rejected

RiskMonitor(tick + timer + trade events):
    maintain day/reference/high-water state and persist at defined boundaries
    if internal risk threshold crossed:
        prohibit entries immediately
        cancel owned pending and close/reduce owned exposure per approved policy
    retry unresolved close/cancel with rate limits and in-flight tracking
    verify flat/target exposure before reporting completion
```

Candidate loss trong scenario phải dùng side/VWAP/cost conversion đúng và có thể nonlinear theo volume; không nhất thiết giải bằng một phép chia. Reservation chỉ có tác dụng khi **mọi** instance tham gia coordinator; giao dịch manual/external vẫn phải quan sát và reserve thận trọng.

## 10. Bất đồng còn lại giữa 2 audit — để người dùng chốt

**Các sửa sai có đủ bằng chứng, không phải sở thích:** B7 bị đảo chiều; B2 nullable dead code và forum không chứng minh GetEstimatedMargin5.9.10 lỗi; D2×0,5 không có bảo đảm; B9/D6 không có cơ sở trừ Credit khỏi Balance; D1 không giữ nguyên risk khi stress cap bind; BTC không được chứng nhận an toàn; FTMO2-Step static khác1-Step EOD trailing; Combine absoluteTP làm RR thay đổi; close/cancel errors bị bỏ qua.

**Những lựa chọn chỉ cần chốt nếu triển khai các phần mở rộng tương ứng** (không chặn sửa Close/Cancel và dữ liệu số):

1. Account/product/profile cụ thể, initial capital, Standard/Swing và scope account coordinator.
2. Giữ invariant nominal SL-risk và thêm costs riêng, hay đổi RiskPercent thành target **net** risk; mức per-idea/portfolio stress room và cash buffer.
3. Instance được độc lập theo timeframe hay dùng chung exposure; ownership qua restart.
4. Khi fill vượt cap: log + halt entry, partial-close hay close toàn bộ; tiếp tục giữ SL/TP signal tuyệt đối hay thay execution type (đổi chiến lược).
5. Có authorize phiên đo riêng cho API min/step precision, partial fills, callback ordering và estimator-vs-actual margin trên5.9.10 hay không. Đây là thiếu bằng chứng kỹ thuật, không được giải quyết bằng bỏ phiếu “tin Claude/Codex”.

Thứ tự đề xuất ban đầu đã được thu hẹp trong phần tự audit dưới đây. Không sửa `.cs` trong audit này.

### 10.1. Tự audit đề xuất Codex: cái gì thực sự đáng sửa?

**Kết luận: không nên triển khai cả chín nhóm trong câu trả lời trước.** Codex đã đi từ audit một cBot nghiên cứu sang thiết kế hệ thống quản trị tài khoản tổng quát. Điều đó không tương xứng với yêu cầu clean, đơn giản của người dùng và quy ước đầu CLAUDE.md: đúng logic chiến lược, code sạch, tránh tính năng phụ không được yêu cầu. Claude đồng ý không tạo thêm bằng chứng độc lập.

Tiêu chí giữ một sửa đổi: có đường đi cụ thể trong source vi phạm hành vi đã định; hậu quả đáng xử lý; có cách sửa cục bộ mà không đổi chiến lược. Phân biệt **chứng minh thiếu xử lý lỗi** với **đã tái hiện lỗi trên broker**. Những backtest MA Cross đã xác nhận hết overlap ở luồng thành công vẫn có giá trị; audit này chưa tái hiện broker từ chối Close/Cancel.

| Nhóm Codex từng đề xuất | Phán quyết sau tự audit | Cách làm nhỏ nhất / lý do không làm ngay |
|---|---|---|
| 1. Kiểm Close/Cancel | **GIỮ, đáng sửa** | Combo250/261, MA224 bỏ qua kết quả. Nếu bước đóng/hủy thất bại, dừng xử lý signal; không mở lệnh mới. Đọc lại exposure liên quan khi cần, không dùng snapshot cũ để kết luận đã sạch. Không cần hàng đợi tự retry entry. |
| 2. ForceCloseAll / halt | **GIỮ sửa hành vi khi PM được bật** | Combo469–487, MA402–420 chỉ thử close một lần vì latch đã bật. Bật halt ngay là đúng; lỗi là đồng nhất chặn entry với hoàn tất thoát lệnh. Giữ halt, thử lại phần còn tồn tại theo nhịp có giới hạn và chỉ log hoàn tất khi xong. Không cần framework state machine tổng quát. |
| 3. NaN/Infinity, volume cuối | **GIỮ, sửa nhỏ có lợi** | Combo333/362–366, MA285/314–318 chưa kiểm finite đầy đủ. Kiểm dữ liệu CSV/sizing tại ranh giới; kiểm volume cuối sau normalize/cap, dưới min thì skip. Chưa chứng minh Normalize của bản đang dùng lỗi; không viết lại hàm broker. |
| 4. InstanceId / label theo timeframe | **KHÔNG tự thêm** | Combo219–225 mô tả một hướng exposure theo bot/symbol. Tách label có thể cho hai timeframe giữ hai hướng, đổi chính quy tắc này. Chỉ thiết kế ownership độc lập khi người dùng thực sự cần các instance độc lập. |
| 5. Toàn bộ vòng đời pending, partial fill, restart | **TÁCH phần lỗi cục bộ khỏi phần vận hành** | Combo677–679 xóa tracking và tăng expired dù cancel thất bại: sửa cùng nhóm1, giữ tracking để còn thử lại. Restart làm mất bộ đếm là giới hạn thật cần xử lý nếu restart khi còn pending; không vì vậy xây toàn bộ persistence/event engine. Không đổi “3 nến thực” thành “3 giờ” hay thời gian lịch. |
| 6. Portfolio cap / coordinator | **HOÃN** | Tham số ghi rõ “Per Trade”; dùng 50% cho từng lệnh không tự là lỗi triển khai. Chặn tổng danh mục là yêu cầu khác, cần khi chạy các exposure chung ngân sách account. Không bắt mỗi cBot mang một coordinator phân tán. |
| 7. Toàn bộ profile FTMO | **THU HẸP theo mục đích dùng** | PM mặc định tắt. Khi dùng nó bảo vệ FTMO, phải sửa đúng baseline/ngày và product thực dùng; không gọi module hiện tại là bảo đảm FTMO. Không cần cài đồng thời static, EOD trailing, intraday trailing và mọi account type. |
| 8. Cost engine / hành động sau fill | **HOÃN thay chiến lược** | Gold đã có lỗ106,47 so budget99,9976; không phủ nhận. Đây chứng minh nominal SL-risk khác net loss, không chứng minh công thức chia volume sai. Buffer chưa hiệu chỉnh, tự cắt sau fill hoặc dời TP/SL có thể làm khác chiến lược. |
| 9. Gap model / margin model / solver | **BỎ khỏi đợt sửa này** | Chưa chứng minh margin API hiện tại sai; mô hình tự đoán leverage có thể tệ hơn API. Kelly/CVaR/volatility regime không cần để sửa xử lý kết quả giao dịch. ATR hiện đã làm volume giảm khi SL rộng hơn. |

### 10.2. Phạm vi sửa tối thiểu được đề xuất

**A — Kết quả thao tác giao dịch.** Dùng `TradeResult.IsSuccessful`; close/cancel thất bại thì không tiếp tục entry. Với pending hết hạn, chỉ bỏ tracking khi đã hủy thành công hoặc xác nhận order không còn pending; không đếm trường hợp đã fill thành “hủy thành công”. Không gửi lại signal đã xử lý để cố khôi phục vị thế cũ. Nếu đóng lệnh cũ thành công nhưng lệnh mới bị từ chối, trạng thái không có vị thế là kết quả có thể chấp nhận; entry mới vốn đã có kiểm `IsSuccessful`. Không tự mở lại hướng cũ.

Counter/log cần phản ánh lý do skip đúng trong nhánh mới: caller hiện mặc định mọi `Reconcile=false` đều là cùng hướng (Combo203–209, MA180–186). Sửa điểm này cùng bản vá, không xây hệ thống telemetry mới. `_reversalsExecuted` có thể đổi tên thành số lần **thử** đảo chiều; không cần định nghĩa nó là số pending đã fill nếu code chưa theo dõi fill. Cancel/close có thể đua với server; vài check cục bộ không tạo giao dịch đảo chiều atomic giữa hai instance.

**B — Số đầu vào và volume cuối.** Giữ công thức `Balance × RiskPercent / 100 / (SLpips × PipValue)`, broker max, Normalize Down và margin cap. Chặn NaN/Infinity trước khi dùng làm price/SL/volume/margin. Dùng kiểu `double` đúng của estimator, bỏ nhánh nullable không thể xảy ra khi sửa cùng hàm. Kiểm volume sau các phép normalize/cap; không nâng lên broker min. Không đặt floor PipValue tự đoán, không mặc định estimator0/âm là lỗi khi chưa biết ngữ nghĩa margin, không yêu cầu margin model thứ hai để được giao dịch.

**C — PM hiện hữu, khi được sử dụng.** Cần nhịp kiểm ngưỡng theo tick bằng thời gian hiện tại, chặn entry sau reconcile nếu close đã kích hoạt guard, hủy pending còn có thể mở exposure khi halted, và retry close/cancel còn dở với tần suất giới hạn. Phép so sánh ngưỡng nhẹ; không quét/đóng tất cả lệnh ở mỗi tick. Có thể dùng các cờ hiện có cộng trạng thái thoát chưa xong và thời điểm thử lại; không cần event bus/coordinator. Ngày mới không được vô tình bỏ một yêu cầu thoát còn dở. Thứ tự callback thực tế vẫn cần kiểm khi triển khai; một recheck riêng lẻ không chứng minh đã giải quyết mọi thứ tự callback.

Phần C sửa hành vi module hiện tại; nếu mục tiêu là FTMO, vẫn phải làm đúng initial capital, baseline ngày và timezone cho account thực dùng (§8). Đây là công việc có điều kiện sử dụng rõ ràng, không được che bằng nhãn “optional” rồi tuyên bố PM hiện tại đủ an toàn. Cũng không lấy nó làm lý do trì hoãn A/B.

### 10.3. Những đề xuất của chính Codex được rút lại

- Không thêm tham số để thay `.98` bằng nhiều hệ số .90/.95 chưa có căn cứ; `.98` hiện là haircut của nhánh scale, không phải cam kết có đệm2% ở mọi nhánh.
- Rút giá trị khởi điểm stress0,5%/25% do Codex tự đề xuất. Chưa đủ dữ liệu để chọn default chung.
- Rút yêu cầu có stress profile, mô hình margin độc lập hoặc portfolio coordinator mới được gửi lệnh cơ bản.
- Không thay `MaxMarginPercent=50` đã được người dùng chọn. Giữ giá trị đó không đồng nghĩa audit chứng nhận an toàn cho mọi gap/account.
- Không tự đổi SL/TP tuyệt đối của Combo theo fill, đổi Stop thành Stop-Limit, tự partial-close, thêm guaranteed stop hay thay RiskPercent theo regime.
- Không refactor hai sizing region sang thư viện chung chỉ để giảm lặp; hai project độc lập hiện đang dùng công thức tương đương.

### 10.4. Bằng chứng và kiểm chứng vừa đủ

Đã đọc lại trực tiếp các nhánh trên ở cả hai source; reflection lại `cAlgo.API.dll` local cho `TradeResult Close()`, `TradeResult Cancel()`, `Double GetEstimatedMargin(...)`; đối chiếu lại tài liệu chính thức [Position.Close](https://help.ctrader.com/ctrader-algo/references/Trading/Positions/Position/#close), [PendingOrder.Cancel](https://help.ctrader.com/ctrader-algo/references/Trading/Orders/PendingOrder/#cancel), [TradeResult.IsSuccessful](https://help.ctrader.com/ctrader-algo/references/Trading/TradeResult/#issuccessful). Chạy lại phép so sánh .NET xác nhận NaN/+Infinity vượt qua guard chỉ kiểm `<=0`. Đây là kiểm API/số học, không phải tái hiện lỗi broker.

Lượt tự audit chỉ sửa tài liệu; hai hash `.cs` vẫn khớp §1, không có bản vá để build/test. Nếu triển khai A/B, kiểm đủ các ca có ý nghĩa: luồng thành công giữ nguyên signal/volume/SL/TP; Close/Cancel trả lỗi không gửi entry mới; cancel hết hạn thất bại không mất tracking; NaN/Infinity và volume cuối dưới min bị skip. Nhánh lỗi cần mô phỏng có kiểm soát hoặc log broker thật; một backtest bình thường pass không chứng minh retry đúng. Build qua GUI và một lượt regression phù hợp cho mỗi bot sau khi có code; không chạy lại ma trận hàng tháng chỉ để xác minh phép kiểm boolean. C chỉ cần các ca ngưỡng/halt/retry/đổi ngày khi thực hiện C.

## 11. Nguồn và truy vết

Đọc ngày2026-09-06; ưu tiên nguồn chính thức/author source. Forum là bằng chứng lịch sử có phạm vi, không phải current release certification. Các con số thực nghiệm dùng file gốc dưới đây, không tính các báo cáo dẫn lại nhau là nhiều xác minh độc lập.

### Source và artifact nội bộ

- [Combo.cs](../../Combo/Combo/Combo.cs), [MA Cross.cs](<../../MA Cross/MA Cross/MA Cross.cs>), các dòng dẫn ở§2–§4.
- [Verification script](../diagnostics/lotsize-crosscheck-2026-09-06/Verify-AuditEvidence.ps1), [kết quả21checks](../diagnostics/lotsize-crosscheck-2026-09-06/verification.json).
- [Gold week report](../cli_runs/Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125/report.json), [native events](../cli_runs/Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125/events.json), [trace](../cli_runs/Combo_XAUUSD_h1_ticks_2026Jan01-07_20260905-170125/signal-trace.csv).
- [Gold Jan–Feb report](../cli_runs/Combo_XAUUSD_h1_ticks_2026JanFeb_20260905-175102/report.json).
- [BTC2025H1 raw log](../cli_batches/Combo_leverage_crosscheck_2025H1/BTCUSD/log.txt), [BTC2026 probe raw log](../cli_batches/Combo_leverage_probe_20260903-093610/BTCUSD/log.txt).
- [GER40 raw report](../cli_runs/Combo_GER40.cash_h4_20260901-2339/report.json), [JP225 raw report](../cli_runs/Combo_JP225.cash_h4_20260902-192536/report.json). Audit09-02 liệt kê thêm archival HK50: [currency audit](../../reports/pipvalue-currency-conversion-audit-2026-09-02.md).
- [Gap report cần kiểm lại provenance](max-margin-percent-safe-ceiling-2026-09-03.md), [sensitivity](margin-percent-sensitivity-2026-09-03.md), [leverage report bị phản biện](leverage-pipvalue-crosscheck-2026-09-04.md).

### Nguồn ngoài — link đầy đủ

1. [S1 — cTrader Symbol reference](https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/).
2. [S3 — cTrader Margin estimations](https://help.ctrader.com/ctrader-algo/guides/estimated-margin/).
3. [S4 — cTrader Dynamic leverage](https://help.ctrader.com/trading-with-ctrader/dynamic-leverage/).
4. [S5 — Stock-CFD backtest margin bug, December2023](https://community.ctrader.com/forum/ctrader-algo/42466/).
5. [S6 — Normalize precision thread, March2021](https://community.ctrader.com/forum/ctrader-support/35338/).
6. [S7 — cTrader Algo changelog](https://help.ctrader.com/ctrader-algo/documentation/changelog/).
7. [S8 — PipValue account currency, cTrader support](https://community.ctrader.com/forum/cbot-support/22424/).
8. [S9 — cTrader Orders/protections/trigger-time margin](https://help.ctrader.com/trading-with-ctrader/orders/).
9. [S10 — Open API model messages, fills/GSL](https://help.ctrader.com/open-api/model-messages/).
10. [S11 — Position API](https://help.ctrader.com/ctrader-algo/references/Trading/Positions/Position/).
11. [S12 — PendingOrder API](https://help.ctrader.com/ctrader-algo/references/Trading/Orders/PendingOrder/).
12. [S13 — IAccount](https://help.ctrader.com/ctrader-algo/references/Account/IAccount/).
13. [S14 — Bar events](https://help.ctrader.com/ctrader-algo/documentation/cbots/cbot-bar-events/).
14. [S15 — Rate limits](https://help.ctrader.com/ctrader-algo/documentation/rate-limits/).
15. [S16 — Currency converter](https://help.ctrader.com/ctrader-algo/guides/currency-conversion/).
16. [S17 — EarnForex TradeSizeMethods, pinned commit](https://github.com/EarnForex/cTrader-Position-Sizer/blob/b77ef1fbca2ec0ae6c23f8ca8a798cc1f7f99605/PositionSizer/PositionSizer/Model/TradeSizeMethods.cs); [commission source](https://github.com/EarnForex/cTrader-Position-Sizer/blob/b77ef1fbca2ec0ae6c23f8ca8a798cc1f7f99605/PositionSizer/PositionSizer/Model/CommissionMethods.cs); [margin source](https://github.com/EarnForex/cTrader-Position-Sizer/blob/b77ef1fbca2ec0ae6c23f8ca8a798cc1f7f99605/PositionSizer/PositionSizer/Model/Main/ForMargin.cs).
17. [S18 — EarnForex cTrader Position Sizer documentation](https://www.earnforex.com/ctrader-robots/cTrader-Position-Sizer/).
18. [S19 — cTrader-GURU EMA Power, pinned commit](https://github.com/cTrader-Guru/EMA-Power/blob/fd4d0bb070d573244470ef9ddf44c8c5c642ce1f/EMA%20Power/EMA%20Power.cs).
19. [S20 — FTMO Trading Objectives](https://ftmo.com/en/trading-objectives/).
20. [S21 — FTMO Academy Maximum Daily Loss](https://academy.ftmo.com/lesson/maximum-daily-loss/).
21. [S22 — FTMO Academy Maximum Loss](https://academy.ftmo.com/lesson/maximum-loss/).
22. [S23 — Introducing FTMO1-Step](https://ftmo.com/en/blog/introducing-the-1-step-ftmo-challenge/).
23. [S24 — FTMO Symbols](https://ftmo.com/en/symbols/).
24. [S25 — FTMO official symbols JSON endpoint](https://ftmo.com/wp-json/ftmo/symbols).
25. [S26 — FTMO symbol-table frontend bundle inspected](https://ftmo.com/app/themes/ftmo-com/build/js/symbols-table-Telxdiux.js).
26. [S27 — FTMO older account-condition FAQ](https://ftmo.com/en/blog/a-few-answers-to-your-questions/).
27. [S28 — FTMO Forbidden Trading Practices](https://ftmo.com/en/forbidden-trading-practices/).
28. [S29 — FTMO Trading Update22Aug2024, margin overexposure](https://ftmo.com/en/blog/trading-updates/trading-update-22-aug-2024/).
29. [S30 — Microsoft ConvertTimeFromUtc adjustment rules](https://learn.microsoft.com/mt-mt/dotnet/api/system.timezoneinfo.converttimefromutc?view=net-7.0).
30. [S31 — Busseti/Ryu/Boyd, Risk-Constrained Kelly Gambling](https://www.web.stanford.edu/~boyd/papers/kelly.html); [research paper](https://arxiv.org/abs/1603.06183).
31. [S32 — Moreira/Muir, Volatility-Managed Portfolios](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513); [author working paper](https://conference.nber.org/confer/2016/LTAMs16/Moreira_Muir.pdf).
32. [S33 — Cederburg et al., On the performance of volatility-managed portfolios](https://www.lehigh.edu/~xuy219/research/COWY.pdf).
33. [S34 — Uryasev author publication list, CVaR2000](https://uryasev.github.io/publications/); [Acerbi/Tasche, Expected Shortfall](https://arxiv.org/abs/cond-mat/0105191). Original CVaR PDF linked by author could not be retrieved reliably; no purported quotation from that PDF.
34. [S35 — BIS Stress testing guidance](https://www.bis.org/committees/bcbs/basel-consolidated-guidelines/module/rma/30); [BIS tail-risk and volatility-scaling analysis](https://www.bis.org/publ/qtrpdf/r_qt2303a.pdf).
35. [S36 — IG guaranteed stop and premium](https://www.ig.com/uk/help-and-support/articles/686687-what-is-a-guaranteed-stop).
36. [S38 — FTMO1-Step product](https://ftmo.com/en/1-step-challenge/); [official Best Day/daily-loss explanation](https://promo.ftmo.com/new-1-step-challenge/).

[S1]: https://help.ctrader.com/ctrader-algo/references/MarketData/Symbols/Symbol/
[S3]: https://help.ctrader.com/ctrader-algo/guides/estimated-margin/
[S4]: https://help.ctrader.com/trading-with-ctrader/dynamic-leverage/
[S5]: https://community.ctrader.com/forum/ctrader-algo/42466/
[S6]: https://community.ctrader.com/forum/ctrader-support/35338/
[S7]: https://help.ctrader.com/ctrader-algo/documentation/changelog/
[S8]: https://community.ctrader.com/forum/cbot-support/22424/
[S9]: https://help.ctrader.com/trading-with-ctrader/orders/
[S10]: https://help.ctrader.com/open-api/model-messages/
[S11]: https://help.ctrader.com/ctrader-algo/references/Trading/Positions/Position/
[S12]: https://help.ctrader.com/ctrader-algo/references/Trading/Orders/PendingOrder/
[S13]: https://help.ctrader.com/ctrader-algo/references/Account/IAccount/
[S14]: https://help.ctrader.com/ctrader-algo/documentation/cbots/cbot-bar-events/
[S15]: https://help.ctrader.com/ctrader-algo/documentation/rate-limits/
[S16]: https://help.ctrader.com/ctrader-algo/guides/currency-conversion/
[S17]: https://github.com/EarnForex/cTrader-Position-Sizer/blob/b77ef1fbca2ec0ae6c23f8ca8a798cc1f7f99605/PositionSizer/PositionSizer/Model/TradeSizeMethods.cs
[S18]: https://www.earnforex.com/ctrader-robots/cTrader-Position-Sizer/
[S19]: https://github.com/cTrader-Guru/EMA-Power/blob/fd4d0bb070d573244470ef9ddf44c8c5c642ce1f/EMA%20Power/EMA%20Power.cs
[S20]: https://ftmo.com/en/trading-objectives/
[S21]: https://academy.ftmo.com/lesson/maximum-daily-loss/
[S22]: https://academy.ftmo.com/lesson/maximum-loss/
[S23]: https://ftmo.com/en/blog/introducing-the-1-step-ftmo-challenge/
[S24]: https://ftmo.com/en/symbols/
[S25]: https://ftmo.com/wp-json/ftmo/symbols
[S26]: https://ftmo.com/app/themes/ftmo-com/build/js/symbols-table-Telxdiux.js
[S27]: https://ftmo.com/en/blog/a-few-answers-to-your-questions/
[S28]: https://ftmo.com/en/forbidden-trading-practices/
[S29]: https://ftmo.com/en/blog/trading-updates/trading-update-22-aug-2024/
[S30]: https://learn.microsoft.com/mt-mt/dotnet/api/system.timezoneinfo.converttimefromutc?view=net-7.0
[S31]: https://www.web.stanford.edu/~boyd/papers/kelly.html
[S32]: https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513
[S33]: https://www.lehigh.edu/~xuy219/research/COWY.pdf
[S34]: https://uryasev.github.io/publications/
[S35]: https://www.bis.org/committees/bcbs/basel-consolidated-guidelines/module/rma/30
[S36]: https://www.ig.com/uk/help-and-support/articles/686687-what-is-a-guaranteed-stop
[S38]: https://ftmo.com/en/1-step-challenge/
