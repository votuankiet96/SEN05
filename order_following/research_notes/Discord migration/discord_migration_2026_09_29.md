# Migration: Telegram → Discord (2026-09-29)

Thay hẳn kênh báo cáo từ Telegram sang Discord (không chạy song song). Áp dụng cho OF10 trước, OF11
sau khi họ review. Nguồn/bằng chứng cho từng quyết định kỹ thuật.

## 1. Cơ chế gửi: Webhook, không dùng Bot đầy đủ

- OF chỉ cần gửi 1 chiều, không bao giờ nhận lại — Webhook đơn giản hơn Bot (không cần giữ kết nối
  gateway, không cần OAuth2 bot token).
- Gửi vào 1 thread có sẵn bằng query param `?thread_id=` ngay trên URL webhook — xác nhận từ tài liệu
  chính thức: *"Send a message to the specified thread within a webhook's channel. The thread will
  automatically be unarchived."* — https://docs.discord.com/developers/resources/webhook
- Nội dung nằm ở field JSON `content`. Discord tự hiểu markdown trong `content`, không có khái niệm
  `parse_mode` như Telegram.
- Giới hạn cứng: *"up to 2000 characters"* — thấp hơn Telegram (4096). `notify()` tự cắt bớt (giữ
  1997 ký tự + "...") thay vì để server từ chối nguyên tin nhắn.
- 1 webhook dùng chung cho mọi OF instance, phân biệt bằng `thread_id` khác nhau mỗi instance (quyết
  định của người vận hành — OF10 và OF11 cùng webhook, khác thread).

## 2. Định dạng: Markdown thay HTML

- `<b>...</b>` → `**...**`, `<code>...</code>` → `` `...` ``.
- Ký tự đặc biệt cần escape trong giá trị tự do (symbol, error text từ broker...):
  `\ * _ ~ \` | >` — khác hẳn bộ `< > &` của HTML.

## 3. Bug thật #1 — SSL certificate verify failed (chỉ Discord, không phải lỗi mạng chung)

- Test trực tiếp: `https://api.telegram.org` gọi được bình thường trên CHÍNH máy này, CÙNG cách gọi
  (`urllib.request.urlopen`, không context riêng) — nhưng `https://discord.com` và
  `https://discordapp.com` đều báo `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`.
- Nguyên nhân: `ssl.get_default_verify_paths().cafile` trỏ tới
  `C:\Program Files\Common Files\SSL\cert.pem` — file này **không tồn tại** trên đĩa
  (`os.path.exists()` = False). Python rơi về dùng kho chứng chỉ Windows (`load_default_certs`), và
  kho đó **thiếu đúng CA ký chứng chỉ discord.com**: `issuer=C=US, O=Google Trust Services, CN=WE1`
  (xác nhận bằng `openssl s_client -connect discord.com:443`). Telegram hoạt động vì CA ký chứng chỉ
  của họ tình cờ đã có sẵn trong kho Windows của máy này, Discord thì không — đặc thù của từng máy,
  không phải lỗi code.
- **Sửa đúng cách** (không tắt xác thực SSL): cài `certifi` (kho CA độc lập, không phụ thuộc hệ điều
  hành), build `ssl.SSLContext(cafile=certifi.where())`, truyền vào mọi `urlopen()` của `discord.py`.
  Xác nhận lại: cùng lệnh gọi `https://discord.com` với context này hết lỗi SSL (ra `HTTP 403` — lỗi
  KHÁC, xem mục 4 — chứng minh SSL đã qua, không còn là vấn đề chứng chỉ).

## 4. Bug thật #2 — Cloudflare chặn User-Agent mặc định của urllib (mã lỗi 1010)

- Sau khi qua SSL, gọi thật vào webhook (cả GET lẫn POST, cả có/không `thread_id`) đều ra `HTTP 403`
  với body `error code: 1010`. Đây **không phải lỗi từ API Discord** (Discord trả JSON dạng
  `{"message":..., "code":...}` với mã lỗi riêng của Discord) — `error code: 1010` dạng text thuần là
  mã lỗi đặc trưng của **Cloudflare** (đứng trước discord.com), nghĩa là *"banned based on your
  browser's signature"* — bị chặn ở tầng Cloudflare, trước khi chạm tới Discord.
- Kiểm chứng bằng thực nghiệm: đổi `User-Agent` từ mặc định (`Python-urllib/3.12`) sang bất kỳ chuỗi
  nào khác — `"Mozilla/5.0"` hay `"DiscordBot (https://discord.com, 1.0)"` — đều ra `HTTP 204` (thành
  công). Tức là chỉ cần KHÁC User-Agent mặc định của urllib là qua được, không bắt buộc phải giả làm
  trình duyệt hay đúng định dạng `DiscordBot (...)` của Discord.
- **Sửa**: đặt cố định `User-Agent: SEN05-OF-Discord-Notifier/1.0` (chuỗi trung thực về nguồn gốc
  request, không giả làm trình duyệt) trên mọi request của `discord.py`. Xác nhận gửi thật thành công
  `HTTP 204` qua đúng module `discord.notify()` (không phải script tay).

## 5. Secret hygiene

- Webhook URL là bearer secret nằm THẲNG trong đường dẫn URL (`/webhooks/<id>/<token>`), không theo
  dạng `key=value` như `client_secret=...` — bộ lọc che secret cũ (`_SECRET_VALUE` trong `log.py`)
  không bắt được dạng này. Thêm regex riêng `_WEBHOOK_URL` che đúng phần token, giữ lại id để còn đối
  chiếu được. Xác nhận bằng `safe_error()` trên 1 exception message có chèn URL thật — token bị che,
  domain+id giữ nguyên.
- `config.yaml` (secret thật) đổi mục `telegram:` thành `discord:`; `config.example.yaml` dùng
  placeholder `YOUR_DISCORD_WEBHOOK_URL`/`YOUR_DISCORD_THREAD_ID`.

## Kết quả

- `engine/telegram.py` xoá hẳn, thay bằng `engine/discord.py`. Toàn bộ nơi gọi (`main.py`,
  `engine/listener.py`, `engine/orders.py`, `engine/sizing.py`, `strategies/combo/adapter.py`)
  chuyển sang gọi `discord.notify()`/`discord.escape_markdown()`/`discord.side_label()`.
- 65/65 test pass (bao gồm 6 test riêng cho `discord.py`: lọc event nội bộ, đúng field JSON + URL
  thread, tự cắt bớt khi vượt 2000 ký tự, dịch reason code, nhãn BUY/SELL, escape markdown).
- Gửi thật thành công vào đúng thread `1554312284103381082` qua webhook thật, bằng đúng module
  `engine/discord.py` (không phải giả lập).
- Dependency mới: `certifi` — bắt buộc trên mọi máy chạy `discord.py`, xem `of_runtime_dependencies`.
