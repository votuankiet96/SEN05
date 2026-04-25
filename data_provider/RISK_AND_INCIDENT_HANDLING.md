# Data Provider Risk And Incident Handling

Tài liệu này tập trung riêng vào câu hỏi:

`Hệ thống data_provider đang xử lý những vấn đề gì khi sự cố xảy ra, và xử lý theo cách nào?`

Mục tiêu của tài liệu này là mô tả chi tiết các lớp bảo vệ, cơ chế phát hiện, cơ chế tự phục hồi, các giới hạn còn tồn tại, và cách người vận hành nên phản ứng khi hệ thống gặp vấn đề.

Tài liệu này được viết để người không code vẫn có thể đọc được, nhưng vẫn đủ sát với hành vi thực tế của source code hiện tại.

---

## 1. Triết lý thiết kế của hệ thống

`data_provider` không giả định rằng nguồn dữ liệu, mạng, TradingView, WebSocket hay chính database sẽ luôn ổn định.

Thay vào đó, toàn bộ hệ thống được xây theo tư duy:

1. chấp nhận rằng lỗi sẽ xảy ra
2. cố ngăn lỗi lan rộng
3. cố không làm hỏng dữ liệu chuẩn khi đang xử lý lỗi
4. cố để hệ thống có thể tiếp tục chạy ở chế độ suy giảm thay vì chết hẳn
5. nếu bắt buộc phải can thiệp, phải có log, cảnh báo và điểm kiểm soát rõ ràng

Nói ngắn gọn:

- ưu tiên `an toàn dữ liệu`
- sau đó mới ưu tiên `tính liên tục`
- cuối cùng mới là `tốc độ`

---

## 2. Ba loại rủi ro chính mà hệ thống đang chống đỡ

### 2.1. Rủi ro từ nguồn dữ liệu

Ví dụ:

- TradingView hết token
- TradingView trả dữ liệu thiếu
- WebSocket bị ngắt
- lịch session đổi do DST
- volume thay đổi nhẹ giữa hai lần pull
- API trả về bar có timestamp sai hoặc future bar

### 2.2. Rủi ro từ hạ tầng nội bộ

Ví dụ:

- mất kết nối SQL Server
- queue ghi DB bị tắc
- process bị kill giữa chừng
- hai tiến trình cùng ghi cùng một vùng dữ liệu
- dữ liệu staging và fact bị lệch trạng thái

### 2.3. Rủi ro từ logic dữ liệu

Ví dụ:

- nhầm market gap thành missing data
- computed timeframe bị lệch anchor
- gap repair sửa quá rộng
- checker coi volume drift là lỗi nghiêm trọng dù thực ra chỉ là restatement nhẹ

---

## 3. Bản đồ rủi ro tổng quan

| Nhóm rủi ro | Vấn đề thực tế | Hệ thống phản ứng thế nào | Rủi ro còn sót |
|---|---|---|---|
| Auth TradingView | token hết hạn, cookie cũ, login lỗi | fallback nhiều tầng, refresh trước batch, refresh giữa chừng, cảnh báo guest mode | vẫn có thể rơi xuống guest và thiếu history |
| Rate limit / throttling | 429, trả rỗng, batch thất bại | retry + backoff + throttle sleep | nếu bị block dài vẫn phải đợi con người xử lý |
| Dữ liệu sai hình dạng | null, duplicate timestamp, future bars, High < Low | `_validate_ohlcv_df()` loại bỏ trước khi ghi DB | nếu nguồn sai nặng liên tục thì vẫn thiếu data |
| DST / anchor drift | H2/H3/H4/M45 lệch mốc giờ | cleanup transition bar, dominant anchor, giữ 2 anchor hợp lệ nếu là session shift thật | một số ca session shift hiếm vẫn cần checker xử lý lại |
| Missing data thật vs market gap | nghỉ cuối tuần / qua đêm trông giống thiếu data | tính trading hours, overnight threshold, verified gaps cache | ngày nghỉ bất thường mới vẫn có thể cần xác nhận thủ công |
| Race condition | ws_live và checker cùng sửa/ghi | `SEN.ActiveTask`, deferred ETL, `_tv_coord.py`, short write locks | nếu lock bị bypass ngoài code thì vẫn nguy hiểm |
| Queue pressure | DB chậm, queue đầy | overflow RAM, sau đó SQLite spool, trạng thái backlog | nếu cả queue, RAM và spool cùng quá tải thì vẫn có nguy cơ mất bar |
| DB lỗi tạm thời | kết nối DB chập chờn | retry kết nối, ghi staging rồi mới ETL, watermark commit sau ETL | DB down lâu vẫn làm live backlog dồn |
| Checker sửa nhầm | volume lệch nhẹ, spike giả, gap giả | tolerances, verify sau sửa, mode manual confirm tùy chọn | auto-repair vẫn có residual risk nếu nguồn TV đang nhiễu |
| Computed TF hỏng | aggregate lệch bucket hoặc tồn tại stale row | proc patched an toàn, continuity check, aggregate_from_fact | vẫn phụ thuộc chất lượng source TF |
| Process chết giữa chừng | lock treo, pending state treo | TTL qua `ExpiresAt`, cleanup expired lock, heartbeat renew | nếu chết đúng lúc write ngoài lock, cần checker rà soát |

---

## 4. Nhóm rủi ro nguồn dữ liệu và cách hệ thống xử lý

## 4.1. Token TradingView hết hạn hoặc không còn hợp lệ

### Vấn đề thực tế

TradingView không đảm bảo token sẽ sống mãi. Token có thể hết hạn, cookie có thể cũ, username/password có thể fail, hoặc login flow có thể thay đổi.

Nếu không xử lý tốt, hệ thống sẽ:

- không kéo được history
- live batch nhận dữ liệu thiếu
- checker so sánh sai vì nguồn pull đã suy giảm

### Cơ chế xử lý hiện tại

Module `_tv_auth.py` dùng nhiều tầng fallback:

1. cache runtime trong `.tv_token_cache`
2. token / cookie đã có trong `.env`
3. refresh token qua cookie session
4. đăng nhập bằng username/password
5. headless browser refresh
6. guest token như phương án cuối cùng

### Ý nghĩa vận hành

- hệ thống cố `không chết ngay` chỉ vì một token cũ
- nếu còn bất kỳ lớp nào lấy được auth hợp lệ, hệ thống tiếp tục chạy
- nếu không còn lớp nào, nó vẫn có thể sống bằng guest mode

### Điểm mạnh

- rất khó bị “đột tử” chỉ vì một nguồn auth hỏng
- có khả năng refresh trước khi bắt đầu batch
- có khả năng refresh giữa chừng khi đang chạy

### Điểm yếu còn lại

- guest mode không phải trạng thái tốt
- history bar trong guest mode có thể ít hơn, làm pipeline/checker thiếu dữ liệu tham chiếu
- nếu TradingView thay đổi hoàn toàn login flow, nhiều tầng fallback có thể cùng hỏng

### Dấu hiệu nhận biết

- log auth warning / auth error
- Telegram alert báo guest mode hoặc refresh thất bại
- live batch chạy nhưng nến về ít bất thường

### Hành động khuyến nghị

1. kiểm tra `.env` và cookie/token còn mới không
2. nếu guest mode kéo dài, không nên tin hoàn toàn vào checker/pipeline history
3. sau khi auth ổn định lại, chạy lại `01_data_pipeline.py --mode gap`
4. nếu nghi ngờ live bị thiếu bar, chạy checker hoặc backfill ngay sau đó

---

## 4.2. TradingView rate-limit hoặc trả dữ liệu rỗng

### Vấn đề thực tế

Khi gọi quá nhanh hoặc kết nối theo pattern bất thường, TradingView có thể:

- trả 429
- trả payload rỗng
- reset connection
- giảm chất lượng phản hồi

### Cơ chế xử lý hiện tại

- retry với backoff tăng dần
- throttle sleep
- batch mode thay vì giữ WebSocket 24/7
- giới hạn số symbol trên mỗi kết nối
- tạm ngưng một lúc nếu thấy chuỗi thất bại liên tiếp

### Ý nghĩa vận hành

Hệ thống cố hành xử “nhẹ nhàng” hơn để giống người dùng thật, tránh bị đánh dấu như bot spam.

### Điểm mạnh

- giảm xác suất bị ban IP
- tránh lặp lỗi quá hung hăng
- khi thất bại tạm thời, nhiều ca có thể tự hồi phục mà không cần người can thiệp

### Điểm yếu còn lại

- nếu bị block thật sự, retry chỉ giúp chậm chết hơn chứ không giải quyết tận gốc
- nếu batch timeout đúng vào nhiều phiên liên tiếp, backlog sẽ tăng

### Hành động khuyến nghị

1. xem live log để biết lỗi là 429, rỗng hay auth
2. nếu guest mode đồng thời tăng, xử lý auth trước
3. nếu queue/backlog tăng nhanh, kiểm tra có phải live đang nhận thiếu dữ liệu do rate-limit không

---

## 4.3. WebSocket bị ngắt hoặc batch không nhận đủ dữ liệu

### Vấn đề thực tế

Live updater không dùng persistent WebSocket, mà mở theo batch rồi đóng. Cách này an toàn hơn, nhưng vẫn có thể gặp:

- batch mở được nhưng không nhận đủ dữ liệu
- mất kết nối giữa chừng
- một vài cặp symbol/timeframe bị “miss”

### Cơ chế xử lý hiện tại

- completion tracking để biết đã nhận đủ hay chưa
- timeout theo batch
- missed pair tracking
- backlog cho cặp bị miss
- nếu một cặp miss nhiều batch liên tiếp thì tăng số bar yêu cầu cho lần sau

### Ý nghĩa vận hành

Hệ thống không giả định “miss một batch là chết”. Nó cố nhớ cặp nào bị lỡ để bù ở batch sau.

### Điểm mạnh

- tự bù dần với những miss ngắn hạn
- không cần phải reset toàn hệ thống chỉ vì vài batch hụt

### Điểm yếu còn lại

- nếu miss liên tục quá lâu, backlog có thể phình to
- live không phải nguồn duy nhất sửa mọi thiếu hụt dài hạn, cuối cùng vẫn cần pipeline/checker

---

## 5. Nhóm rủi ro chất lượng dữ liệu đầu vào

## 5.1. Null, duplicate timestamp, thứ tự sai, future bars, giá vô lý

### Vấn đề thực tế

Nguồn ngoài có thể trả về:

- bar trùng timestamp
- null OHLC
- `High < Low`
- index không tăng dần
- future bar do time alignment sai

### Cơ chế xử lý hiện tại

`_helpers._validate_ohlcv_df()` làm sạch trước khi ghi:

- bỏ null không hợp lệ
- bỏ duplicate timestamp
- sắp xếp lại theo thời gian
- chặn future bars
- phát hiện bar vô lý

### Ý nghĩa vận hành

Hệ thống cố bắt lỗi ngay “ở cửa vào”, thay vì để dữ liệu bẩn chảy sâu vào kho rồi mới sửa.

### Tại sao quan trọng

Nếu future bar lọt vào:

- watermark có thể bị đẩy lên sai
- các batch sau sẽ tưởng đã có dữ liệu mới và bỏ qua bar thật

Nếu duplicate timestamp lọt vào:

- dễ đụng unique key ở fact
- hoặc tệ hơn là gây hiểu sai checker nếu staging và fact không đồng bộ

---

## 5.2. DST shift và anchor drift

### Vấn đề thực tế

Một số symbol / timeframe như `H2`, `H3`, `H4`, đặc biệt `M45`, có thể bị lệch anchor khi broker/session đổi UTC offset theo DST.

Điều này làm xuất hiện:

- transition bar lạ
- anchor remainder không còn đồng nhất
- dữ liệu nhìn như bị thiếu hoặc bị dư

### Cơ chế xử lý hiện tại

Hệ thống có nhiều lớp:

- `clean_staging_transitions()` xóa transition bar nghi nhiễm DST ở staging
- `_validate_ohlcv_df()` kiểm tra dominant anchor
- với `M45`, nếu thấy đúng kiểu session shift thật thì giữ cả hai anchor hợp lệ theo từng đoạn liên tục
- checker có thêm logic phát hiện DST risk

### Ý nghĩa vận hành

Đây là một điểm rất quan trọng: hệ thống không xử lý DST theo kiểu “cứ khác anchor là xóa hết”.

Nó cố phân biệt:

- shift thật do session / DST
- glitch rải rác do nguồn dữ liệu lỗi

### Điểm yếu còn lại

- đây là nhóm lỗi khó triệt tiêu hoàn toàn
- một số shift hiếm vẫn có thể cần repull và verify lại bằng checker

---

## 5.3. Market gap bị hiểu nhầm là missing data

### Vấn đề thực tế

Nếu nhìn thuần theo timestamp, một khoảng nghỉ cuối tuần hoặc qua đêm có thể trông giống như mất dữ liệu.

Ví dụ:

- FOREX nghỉ cuối tuần
- index đóng phiên
- metal có giờ nghỉ ngắn

### Cơ chế xử lý hiện tại

Hệ thống dùng nhiều lớp phân biệt:

- `trading_hours_in_gap()` trừ phần không giao dịch
- overnight threshold theo asset type
- threshold theo symbol
- verified market gaps cache
- với asset nghỉ cuối tuần, gap lịch dài không đồng nghĩa gap trading dài

### Ý nghĩa vận hành

Mục tiêu là không kéo lại vô ích và không “sửa một thứ vốn không hỏng”.

### Điểm mạnh

- giảm false positive
- giảm số lần pull vô nghĩa
- tránh checker/pipeline tự tạo việc không cần thiết

### Điểm yếu còn lại

- ngày nghỉ bất thường hoặc phiên đặc biệt chưa từng thấy vẫn có thể bị đánh dấu nhầm ở lần đầu
- verified gap chỉ giúp các lần sau, không giúp ngay lần phát hiện đầu tiên

---

## 5.4. Volume drift, price discontinuity và spike giả

### Vấn đề thực tế

Một số dữ liệu TV có thể thay đổi nhẹ volume giữa hai lần lấy. Ngoài ra có thể xuất hiện discontinuity:

- close nến trước không gần open nến sau
- jump vượt ATR bình thường

### Cơ chế xử lý hiện tại

- tolerance riêng cho OHLC và volume
- filter volume-only mismatch để tránh sửa quá tay
- `find_price_spikes()` dùng ATR để phát hiện jump bất thường
- continuity check cho aggregate_from_fact

### Ý nghĩa vận hành

Hệ thống cố phân biệt:

- sai lệch nghiệp vụ thật
- dao động nhẹ chấp nhận được

### Điểm yếu còn lại

- nếu nguồn tự restate lịch sử, hệ thống vẫn cần checker để làm sạch
- volume ở một số loại instrument vốn không quá đáng tin nên không nên đối xử như lỗi nghiêm trọng tuyệt đối

---

## 6. Nhóm rủi ro nội bộ khi ghi dữ liệu

## 6.1. Hai tiến trình cùng ghi cùng lúc vào cùng vùng dữ liệu

### Vấn đề thực tế

Các process chính đều có thể đụng vào dữ liệu:

- `01_data_pipeline.py`
- `02_ws_live.py`
- `04_checker.py`

Nếu không điều phối:

- checker xóa bar cũ đúng lúc ws_live ETL bar mới
- pipeline và checker cùng rebuild một vùng
- live vừa commit xong thì checker lại rollback logic của nó

### Cơ chế xử lý hiện tại

Hệ thống dùng hai lớp:

### Lớp 1: `SEN.ActiveTask`

Dùng như advisory lock ở mức DB.

Ví dụ:

- `checker_repair`
- `warehouse_maintenance`
- `ws_live_runtime`

### Lớp 2: `_tv_coord.py`

Điều phối giữa live batch và historical job theo “khung giờ an toàn”.

### Lớp 3: short-lived write lock

Một số đoạn ghi nhạy cảm dùng short write lock để giảm vùng giao tranh.

### Ý nghĩa vận hành

Mục tiêu không phải là cấm mọi thứ chạy song song, mà là cấm `ghi nguy hiểm` chạy song song.

---

## 6.2. Lock bị treo vì process chết giữa chừng

### Vấn đề thực tế

Nếu checker hoặc pipeline chết lúc đang giữ lock, live có thể bị chặn mãi mãi nếu không có cơ chế giải phóng.

### Cơ chế xử lý hiện tại

- `ExpiresAt` trong `SEN.ActiveTask`
- heartbeat renew khi task vẫn đang sống
- cleanup expired lock khi khởi động

### Ý nghĩa vận hành

Đây là dead-man switch:

- còn sống thì phải tự gia hạn lock
- chết rồi thì lock tự hết hạn

### Rủi ro còn lại

- nếu một đoạn xử lý quan trọng kéo dài hơn TTL mà heartbeat fail, lock có thể hết hạn quá sớm
- vì vậy các task dài đều cần renew đều đặn

---

## 6.3. DB queue đầy, RAM buffer đầy, hoặc DB worker chậm

### Vấn đề thực tế

Live updater nhận data theo batch, nhưng ghi DB ở thread riêng. Nếu DB chậm:

- queue sẽ tăng
- RAM overflow buffer sẽ tăng
- sau đó phải dùng SQLite spool

Nếu vẫn không tiêu thụ kịp:

- có thể mất bar

### Cơ chế xử lý hiện tại

Thứ tự thoái lui:

1. queue bình thường
2. overflow buffer trong RAM
3. durable spool bằng SQLite

Đồng thời:

- có log cảnh báo khi buffer gần đầy
- có log cảnh báo khi deferred ETL tăng cao
- backlog được theo dõi riêng

### Ý nghĩa vận hành

Hệ thống không đòi DB phải luôn nhanh. Nó có tầng đệm để chịu sốc tạm thời.

### Điểm yếu còn lại

- nếu áp lực kéo dài quá lâu, spool cũng sẽ lớn dần
- SQLite spool là biện pháp cứu nguy, không phải đích đến lâu dài

### Dấu hiệu nhận biết

- queue depth tăng
- overflow/spool count tăng
- live log có cảnh báo recharge hoặc spool flush bất thường

---

## 6.4. Mất kết nối DB hoặc ghi DB thất bại

### Vấn đề thực tế

SQL Server có thể:

- mất mạng
- timeout
- fail nhất thời
- chậm bất thường

### Cơ chế xử lý hiện tại

- `modules/db_connector.py` có retry khi mở kết nối
- write path chia làm staging trước, ETL sau
- committed watermark chỉ tăng sau khi ETL fact thành công

### Ý nghĩa vận hành

Đây là điểm rất quan trọng:

- hệ thống không coi “đã nhìn thấy data” là “đã commit data”
- vì vậy nó tách `received watermark` và `committed watermark`

### Lợi ích

Nếu ghi vào fact chưa thành công:

- hệ thống chưa nâng committed watermark
- các batch sau vẫn còn cơ hội xử lý lại

### Rủi ro còn lại

- nếu DB down lâu, backlog dồn và live không thể phát huy tác dụng
- lúc đó pipeline/checker sau khi DB hồi cần được dùng để vá lại

---

## 7. Nhóm rủi ro logic sửa dữ liệu

## 7.1. Checker sửa nhầm hoặc sửa quá rộng

### Vấn đề thực tế

Checker là công cụ mạnh. Nếu thiết kế kém:

- có thể xóa đúng dữ liệu rồi kéo lại kém hơn
- có thể biến vấn đề nhỏ thành vấn đề lớn

### Cơ chế giảm rủi ro hiện tại

- phân biệt core issue với mismatch thường
- ngưỡng mặc định thấp nhưng không áp dụng mù quáng cho mọi loại lỗi
- có verify sau repair
- có mode `--dry-run`
- có mode `--manual-confirm`
- có logic chọn repair strategy theo loại lỗi
- có giới hạn số vòng repair

### Ý nghĩa vận hành

Checker không được phép “sửa vô hạn”. Nếu một cặp sửa mãi không sạch, hệ thống sẽ đánh dấu persistent failure thay vì cứ xóa đi kéo lại mãi.

### Rủi ro còn lại

- auto-repair vẫn là hành động mạnh
- nếu chính TradingView đang trả dữ liệu sai, checker có thể “đồng bộ theo cái sai hiện tại”

### Khi nào nên bật `--manual-confirm`

- đang nghi ngờ nguồn TV bất ổn
- chuẩn bị sửa diện rộng
- vừa đổi session / broker / auth
- đang điều tra sự cố lớn, cần con người chốt trước

---

## 7.2. Rebuild computed timeframe bị dùng sai nguồn hoặc bucket sai

### Vấn đề thực tế

Computed TF như `M10`, `M20`, `M90`, `H6`, `H8` phụ thuộc vào direct TF nhỏ hơn. Nếu source TF đã bẩn, computed TF sẽ bẩn theo.

Ngoài ra, nếu logic aggregate không xóa stale row đúng, có thể tồn tại:

- bucket cũ
- anchor cũ
- bucket thiếu đủ số bar

### Cơ chế xử lý hiện tại

- file SQL đã ghi chú bản patched an toàn là bản có hiệu lực cuối
- có `aggregate_from_fact`
- có continuity check sau aggregate
- có logic xóa stale row
- checker có khả năng rebuild computed TF

### Ý nghĩa vận hành

Computed TF không được tin độc lập. Muốn computed TF sạch, source TF cũng phải sạch.

---

## 7.3. Telegram confirm bị process khác “ăn mất”

### Vấn đề thực tế

Có lúc:

- Telegram bot listener đang chạy 24/7
- checker cũng đang chờ xác nhận

Nếu chỉ dựa vào Telegram polling thuần túy, một process có thể nhận message trước, process kia không bao giờ thấy.

### Cơ chế xử lý hiện tại

- bot listener ghi kết quả `/confirm_TOKEN` hoặc `/skip_TOKEN` vào `SEN.ActiveTask.Payload`
- checker vừa đọc Telegram vừa đọc relay từ DB

### Ý nghĩa vận hành

Đây là cơ chế chống “lost confirm”.

### Rủi ro còn lại

- nếu cả Telegram lẫn DB đều lỗi cùng lúc, confirm vẫn có thể thất lạc
- nhưng xác suất thấp hơn nhiều so với dùng một kênh duy nhất

---

## 8. Nhóm rủi ro do mode vận hành

## 8.1. Guest mode kéo dài

### Tại sao nguy hiểm

Guest mode là chế độ suy giảm, không phải chế độ vận hành mong muốn.

Hệ quả:

- history bị giới hạn
- live có thể thiếu depth
- checker có thể so sánh trên nguồn tham chiếu không đầy đủ

### Hệ thống làm gì

- đếm số batch guest liên tiếp
- gửi cảnh báo nặng hơn khi guest kéo dài
- pipeline/checker cảnh báo rõ khi đang chạy bằng guest mode

### Điều người vận hành cần hiểu

Hệ thống có thể vẫn “chạy”, nhưng “chạy” không đồng nghĩa “đáng tin”.

---

## 8.2. Dùng `--reset` sai phạm vi

### Vấn đề thực tế

Reset là hành động có tính phá hủy.

Nếu làm quá rộng:

- xóa dữ liệu tốt
- tạo thời gian trống lớn
- làm live/checker phải vá lại rất nhiều

### Cơ chế giảm rủi ro hiện tại

- `--reset` yêu cầu đi cùng bộ lọc
- có prompt xác nhận
- repull an toàn theo cặp

### Rủi ro còn lại

- nếu người dùng xác nhận nhầm, hệ thống vẫn sẽ làm theo

---

## 8.3. ws_live không phủ hết mọi asset type

### Vấn đề thực tế

Live updater hiện không theo dõi FOREX qua WS.

Điều này không phải bug, mà là quyết định kiến trúc để giảm phức tạp session.

### Hệ quả

- không thể kỳ vọng mọi asset đều được “live” như nhau
- một phần độ tươi của FOREX phụ thuộc nhiều hơn vào pipeline/checker

### Ý nghĩa vận hành

Nếu người vận hành không biết điều này, rất dễ hiểu nhầm rằng live đang “bỏ sót FOREX”.
Thực ra đó là giới hạn thiết kế có chủ đích.

---

## 9. Hệ thống phát hiện sự cố bằng gì?

Hệ thống không có một “trung tâm giám sát” duy nhất, mà phát hiện sự cố qua nhiều tín hiệu:

### 9.1. Log file

- pipeline log
- ws_live log
- checker log

### 9.2. Telegram alert

- auth warning
- guest mode kéo dài
- queue/backlog pressure
- batch lỗi
- checker repair report

### 9.3. Database state

- `SEN.ActiveTask`
- watermark trong bộ nhớ và dữ liệu thực trong fact
- continuity flags / gap query / spike query

### 9.4. Artifact runtime

- `.tv_token_cache`
- `.overflow_spool.db`
- `ws_live_smoke.err`
- `ws_live_smoke.out`

---

## 10. Các kịch bản sự cố điển hình và cách phản ứng

## 10.1. Kịch bản: live vẫn chạy nhưng dữ liệu bị trễ

### Dấu hiệu

- status report cho thấy backlog tăng
- queue/spool tăng
- watermark cũ
- dashboard thấy nến chậm cập nhật

### Cách nghĩ đúng

Đây có thể không phải lỗi TradingView, mà là lỗi “tiêu thụ không kịp”.

### Cần kiểm tra

1. DB có chậm không
2. queue depth, overflow, spool có tăng không
3. checker có đang giữ lock không
4. auth có rơi xuống guest không

### Hành động

1. xử lý nguyên nhân gốc
2. chờ ws_live xả backlog
3. nếu backlog lớn, chạy pipeline gap hoặc checker sau đó để làm sạch

---

## 10.2. Kịch bản: checker báo nhiều cặp lỗi cùng lúc

### Có thể là

- TV đang nhiễu
- auth suy giảm
- source TF bị bẩn hàng loạt
- aggregate drift
- một lần reset/repair trước đó chưa hoàn tất sạch

### Hành động khuyến nghị

1. xem có đang guest mode không
2. ưu tiên `--dry-run` trước nếu nghi ngờ nguồn
3. nếu vấn đề diện rộng, cân nhắc `--manual-confirm`
4. nếu lỗi tập trung ở computed TF, kiểm tra source TF trước

---

## 10.3. Kịch bản: lock không nhả

### Dấu hiệu

- ws_live cứ defer ETL
- pipeline báo `warehouse_maintenance` bận
- `SEN.ActiveTask` còn record cũ

### Hành động

1. kiểm tra `ExpiresAt`
2. xác nhận process giữ lock còn sống không
3. nếu lock đã hết hạn mà chưa dọn, chờ cleanup hoặc dọn an toàn
4. sau đó quan sát backlog và checker lại dữ liệu nếu cần

---

## 10.4. Kịch bản: computed timeframe có vẻ “lệch pha”

### Dấu hiệu

- continuity warnings
- checker thấy nhiều mismatch ở `M10/M20/M90/H6/H8`
- chart nhìn candle không khớp logic source TF

### Hành động

1. kiểm tra direct TF nguồn trước
2. nếu source sạch, rebuild computed TF
3. nếu source bẩn, sửa source rồi mới rebuild

---

## 11. Những gì hệ thống làm tốt, và những gì nó chưa thể đảm bảo

## Hệ thống làm tốt

- không ghi bừa vào fact khi dữ liệu chưa được lọc
- không coi mọi gap là lỗi
- không giả định auth luôn hợp lệ
- không giả định live và checker có thể chạy song song an toàn
- không tin “đã nhận data” đồng nghĩa “đã commit data”
- có khả năng tự sửa nhiều lỗi dữ liệu thường gặp

## Hệ thống chưa thể đảm bảo tuyệt đối

- không thể tự biết TradingView đang sai hay chỉ lịch sử vừa bị restate
- không thể loại bỏ hoàn toàn rủi ro DST/session shift hiếm
- không thể đảm bảo không mất bar nếu toàn bộ pipeline DB bị nghẽn lâu và spool cũng quá tải
- không thể thay thế hoàn toàn vai trò giám sát của con người trong sự cố lớn
- không thể biến guest mode thành trạng thái đáng tin như authenticated mode

---

## 12. Kết luận vận hành

Điểm quan trọng nhất cần nhớ:

`data_provider` không được thiết kế như một đường ống “kéo được dữ liệu là xong”. Nó là một hệ thống tự vệ dữ liệu.`

Nó đang chủ động xử lý rất nhiều lớp rủi ro:

- lỗi auth
- lỗi rate-limit
- lỗi dữ liệu bẩn
- lỗi DST/anchor
- nhầm market gap
- race condition giữa các process
- queue pressure và DB pressure
- computed TF drift
- lost Telegram confirm
- lock treo

Nhưng nó không phải bất tử.

Khi sự cố lớn xảy ra, thứ quan trọng nhất không phải là ép hệ thống chạy tiếp bằng mọi giá, mà là:

1. giữ fact table sạch
2. giữ lock/coordination đúng
3. phân biệt rõ lỗi nguồn với lỗi nội bộ
4. chỉ repair khi đã hiểu mình đang sửa cái gì

Nếu cần một câu tóm tắt duy nhất cho tài liệu này, thì đó là:

`Hệ thống này ưu tiên không làm hỏng dữ liệu hơn là cố tỏ ra luôn online.`
