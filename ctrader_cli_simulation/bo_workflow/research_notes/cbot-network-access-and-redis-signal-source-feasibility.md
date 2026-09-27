# Feasibility: cBot đọc tín hiệu trực tiếp từ Redis vs lớp trung gian Python

**Câu hỏi gốc**: đổi `LoadSignalFile()` trong Combo/MA Cross từ đọc CSV cục bộ (`AccessRights.FullAccess`)
sang đọc trực tiếp Redis (server khác, qua mạng — List+Hash) — có khả thi không, và nếu không, cách
làm chuẩn của cộng đồng/tài liệu là gì.

**Phạm vi**: nghiên cứu thuần tuý, không code, không build/backtest. Mọi khẳng định gắn nhãn:
- **[CHÍNH THỨC]** — trích trực tiếp từ `help.ctrader.com` (tài liệu Spotware).
- **[CỘNG ĐỒNG]** — diễn đàn `community.ctrader.com`/`ctrader.com/forum`, không phải tài liệu chính thức,
  độ tin cậy thấp hơn, có thể lỗi thời.
- **[SUY LUẬN]** — ghép nhiều nguồn đã xác nhận lại với nhau, chưa có 1 câu duy nhất nói thẳng.
- **[CHƯA XÁC MINH]** — chưa tìm được bằng chứng, chỉ có thể trả lời chắc chắn bằng thử nghiệm thật
  trên máy này.

---

## 1. Kết luận nhanh (đọc trước)

- **Network access CÓ được phép trong backtest/optimization**, kể cả với `AccessRights.None`, nhưng
  **chỉ qua 2 kênh chính thức**: `cAlgo.API.Http` (GET/POST) và `WebSocketClient` — **không phải raw
  TCP socket**. Redis dùng giao thức RESP qua raw TCP → **không nằm trong kênh chính thức nào cả**.
  **[CHÍNH THỨC]**
- Quan trọng hơn: tài liệu chính thức nói thẳng khi truy cập tài nguyên mạng lúc backtest, cTrader
  **luôn lấy bản MỚI NHẤT (live) chứ không phải bản lịch sử tại đúng thời điểm nến đang mô phỏng**.
  Đây là **lý do kiến trúc, không phải lý do kỹ thuật vặt**, khiến việc đọc Redis trực tiếp trong
  backtest về bản chất **không thể tái lập (non-reproducible)** — không liên quan gì đến việc Redis
  có bị ghi đè giữa chừng hay không (rủi ro đó cộng thêm vào, không phải nguyên nhân chính).
  **[CHÍNH THỨC]**
- Reference NuGet package bên thứ 3 (vd `StackExchange.Redis`) **được tài liệu chính thức cho phép
  và có ví dụ** (Discord.Net), nhưng **compiler mặc định (embedded) của cTrader Automate KHÔNG hỗ trợ
  bất kỳ package .NET bên thứ 3 nào** — bắt buộc chuyển sang ".NET SDK compiler", mà cấu hình này lại
  **cần cài .NET SDK hệ thống riêng** (không phải runtime bundled đi kèm cTrader Desktop) — đúng thứ
  mà theo `CLAUDE.md` máy VM-BO20 này **hiện chưa có**. **[CHÍNH THỨC + SUY LUẬN từ bối cảnh máy]**
- Ngay cả khi build được, cộng đồng có nhiều báo cáo `FileNotFoundException`/`could not load file or
  assembly` **lúc RUNTIME** cho package bên thứ 3 dù build thành công trong Visual Studio/cTrader —
  tài liệu không giải thích rõ file `.algo` có đóng gói (bundle) dependency hay không. **[CỘNG ĐỒNG +
  im lặng của tài liệu chính thức]**
- Không tìm thấy pattern chính thức hay cộng đồng nào cho việc cBot tự đọc DB/Redis/API làm **nguồn
  tín hiệu chiến lược chính** trong backtest. Cơ chế chính thức duy nhất cho "bơm dữ liệu tuỳ biến vào
  backtest" (`BacktestingDataSources.Add`) là **API của Plugin** (không phải cAlgo.Robots cBot), và
  dùng cho dữ liệu GIÁ (tick/bar), không phải tín hiệu chiến lược. **[CHÍNH THỨC + SUY LUẬN]**

**→ Khuyến nghị: Hướng B (lớp trung gian Python đọc Redis → ghi CSV → trỏ `SignalFilePath` như cũ).**
Không phải vì Hướng A "không thể" tuyệt đối, mà vì bằng chứng thu thập được cho thấy nó vừa đụng
đúng giới hạn kiến trúc cốt lõi (network trong backtest = luôn live, không lịch sử — phá vỡ mục đích
tái lập của chính backtest), vừa cộng thêm 2 lớp rủi ro triển khai (compiler switch + cài .NET SDK,
và dependency runtime resolution) mà Hướng B hoàn toàn không có. Xem mục 5 để so sánh đầy đủ.

---

## 2. Network access trong cBot

### 2.1 AccessRights là gì, kiểm soát chính xác cái gì

`AccessRights` (namespace `cAlgo.API`) có **5 giá trị**, không phải chỉ 2 (None/FullAccess) như cách
diễn đạt tắt hay dùng trong `CLAUDE.md`:

| Giá trị | Mô tả nguyên văn |
|---|---|
| `None` | "Algorithm doesn't require any access rights." |
| `FileSystem` | "Access to file system." |
| `Internet` | "Access to Internet or other networks." |
| `Registry` | "Access to windows registry." |
| `FullAccess` | "The unlimited access rights." |

**[CHÍNH THỨC]** — https://help.ctrader.com/ctrader-algo/references/Application/AccessRights/

Trang guide tổng quan diễn giải 2 cực:
- `AccessRights.None`: "the extension only has access to the platform-provided API data. It will not
  be able to access anything outside the platform."
- `AccessRights.FullAccess`: "the extension has unlimited access rights. It can access the Internet,
  read and write files, run other executables, import WinApi functions, use .NET reflection and
  create windows."

**[CHÍNH THỨC]** — https://help.ctrader.com/ctrader-algo/guides/access-rights/

Cơ chế enforce: cTrader chạy mọi cBot/indicator trong **sandbox** — nếu code cố thực hiện hành động bị
cấm, runtime "sẽ ném security exception" (không nói rõ cơ chế implement — .NET 6 không còn Code Access
Security kiểu .NET Framework cũ, nhiều khả năng là 1 lớp kiểm tra runtime tự viết của Spotware, nhưng
tài liệu không mô tả chi tiết kỹ thuật này). **[CHÍNH THỨC, phần cơ chế implement là SUY LUẬN]** —
https://help.ctrader.com/ctrader-algo/faq/

**Điểm quan trọng nhất — có giá trị `Internet` RIÊNG, tách khỏi `FullAccess`.** Điều này gợi ý (nhưng
không có 1 câu tài liệu nào xác nhận thẳng) một bức tranh nhất quán khi ghép với mục 2.2:
- 2 helper riêng của cAlgo (`Http`, `WebSocketClient`) hoạt động ngay cả dưới `AccessRights.None` —
  chúng là "lối đi an toàn" được thiết kế sẵn, không cần khai báo quyền gì thêm.
- Các API .NET chuẩn khác cho network (`System.Net.Http.HttpClient`, `System.Net.Sockets.TcpClient`/
  `Socket`) nhiều khả năng cần `AccessRights.Internet` (hoặc `FullAccess`, tập lớn hơn) mới chạy được.

**[SUY LUẬN]** — ghép từ trang AccessRights reference + trang Network access + 1 thảo luận cộng đồng
(mục 2.3) dùng thẳng `FullAccess` cho `TcpClient` (không thử `Internet` riêng). **Chưa có bằng chứng
trực tiếp xác nhận `AccessRights.Internet` (không kèm FileSystem/FullAccess) có đủ cho `TcpClient`
raw socket hay không — đây là điều CẦN THỬ NGHIỆM THẬT nếu vẫn muốn theo Hướng A.**

### 2.2 Kênh network CHÍNH THỨC: chỉ Http + WebSocketClient, không có raw socket

Trang "Network access" (https://help.ctrader.com/ctrader-algo/guides/network-access/) liệt kê đúng
2 cơ chế:

1. **`Http` interface** — `Http.Get(url)`, `Http.Send(httpRequest)` (POST/PUT/PATCH...). Nguyên văn:
   "Network access works with `AccessRights.None` and supports GET, POST and other HTTP methods as
   well as WebSocket connections." Cũng có thể dùng thẳng `System.Net.Http.HttpClient` chuẩn của .NET,
   nhưng theo 1 thảo luận cộng đồng (mục 2.3), cách này từng lỗi `CookieContainer` với `None` trên bản
   cũ — `Http.Get()` được khuyến nghị thay thế vì "không cần elevated access rights".
2. **`WebSocketClient`** — kết nối WebSocket 2 chiều.

Không có bất kỳ đề cập nào tới `TcpClient`/`Socket`/raw TCP trong tài liệu chính thức này. **[CHÍNH
THỨC]** — cùng nguồn trên, cross-check với how-to:
https://help.ctrader.com/ctrader-algo/how-tos/all-algos/use-network-access/

**Hệ quả trực tiếp cho Redis**: `StackExchange.Redis` (client .NET phổ biến nhất cho Redis) giao tiếp
qua giao thức RESP trên raw TCP socket (`System.Net.Sockets.Socket`/`Pipelines`, không phải HTTP hay
WebSocket) — **không khớp với bất kỳ kênh nào được tài liệu chính thức của cTrader Algo công nhận**.
**[SUY LUẬN kỹ thuật, dựa trên kiến thức chung về StackExchange.Redis — không phải thứ cần tra cứu
riêng trong tài liệu cTrader]**

### 2.3 Raw TCP socket vẫn được cộng đồng dùng, nhưng KHÔNG chính thức, cần FullAccess

Có ít nhất 1 ví dụ code cộng đồng (2015, vẫn được tham chiếu lại trong các thread mới hơn về FIX API)
dùng `TcpClient` để làm socket server copy lệnh giữa các cBot, khai báo
`[Robot(AccessRights = AccessRights.FullAccess)]`. **[CỘNG ĐỒNG]** —
https://community.ctrader.com/forum/cbot-support/4302/

Một thread khác xác nhận: khi cBot khai `AccessRights.None` mà cố dùng socket, sẽ bị chặn bởi security
exception; giải pháp thực tế trong cộng đồng luôn là "đổi sang `FullAccess`". **[CỘNG ĐỒNG]** —
tổng hợp từ kết quả tìm kiếm quanh https://community.ctrader.com/forum/ctrader-support/22849/ và các
thread liên quan tới FIX API/SocketServer.

**Không tìm được bất kỳ thread nào xác nhận raw `TcpClient`/`Socket` hoạt động cụ thể trong môi trường
BACKTEST** (mọi ví dụ tìm được đều nói về live/demo trading hoặc socket server chạy song song, không
nói rõ đã thử trong tab Backtesting chưa). **[CHƯA XÁC MINH]**

### 2.4 Backtest/Optimization: hoạt động, nhưng LUÔN lấy dữ liệu "mới nhất", KHÔNG PHẢI lịch sử

Đây là phát hiện quan trọng nhất của toàn bộ nghiên cứu này. Trích nguyên văn từ trang Network access
chính thức:

> "Note that, when accessing a web resource in backtesting or optimisation, the up-to-date version of
> this resource will be requested instead of a historical one."

**[CHÍNH THỨC]** — https://help.ctrader.com/ctrader-algo/guides/network-access/

Diễn giải hệ quả cho use-case Redis-signal cụ thể của bo_workflow:
- Nếu cBot gọi Redis (giả sử qua 1 kênh nào đó vượt qua được rào cản kỹ thuật ở mục 2.2/2.3) ngay
  trong lúc backtest đang mô phỏng nến ngày X của quá khứ, **kết quả trả về không phải trạng thái
  Redis "tại thời điểm X"** — vì Redis không có khái niệm "point-in-time query" tự nhiên như time-series
  DB, và tài liệu cTrader tự thừa nhận hành vi mạng trong backtest là "always fetch current" — cBot sẽ
  luôn thấy đúng 1 bản Redis y hệt cho mọi bar được mô phỏng, bất kể bar đó thuộc thời điểm nào trong
  quá khứ.
- Điều này **không phải lỗi thực thi có thể sửa bằng code cẩn thận hơn** (khác với rủi ro "Redis bị
  ghi đè giữa chừng" mà bối cảnh dự án đã biết trước) — nó là **giới hạn kiến trúc cố định** của chính
  cơ chế network-trong-backtest do Spotware thiết kế.
- Hệ quả: dù mọi rào cản kỹ thuật ở mục 2.2/2.3 (raw socket, package, compiler) đều được giải quyết,
  **bản thân việc gọi Redis trực tiếp từ cBot trong lúc backtest vẫn không cho ra kết quả tái lập theo
  đúng lịch sử tín hiệu** — trừ khi Redis được thiết kế lại thành 1 kho lưu trữ có thể trả lời đúng
  "giá trị tại thời điểm T" (mà nếu làm vậy thì gần như đã tự tái tạo lại chức năng của 1 file CSV có
  timestamp — quay về đúng Hướng B).

Ghi chú thêm: hành vi này được nêu ở tài liệu Network Access (áp dụng cho `Http`/`WebSocketClient`) —
tài liệu không nói minh thị "raw socket cũng vậy", nhưng do `ctrader-cli backtest` và tab GUI
Backtesting dùng **chung 1 lõi mô phỏng** (đã xác nhận trong nghiên cứu nội bộ trước đó, xem
`bo_workflow/reports/ab-plugin-api-vs-cli-2026-09-11.md`), khả năng rất cao là **mọi truy cập mạng nói
chung** (không riêng gì Http) trong quá trình backtest đều chạy trong cùng 1 vòng lặp mô phỏng dùng
đồng hồ ảo (`Server.Time`) tách biệt khỏi đồng hồ mạng thật — tức bản chất "network luôn là live call
tại thời điểm THẬT của máy chạy, không đồng bộ với thời điểm ẢO của bar" khó có thể khác nhau giữa
Http và raw socket. **[SUY LUẬN, độ tin cậy cao nhưng chưa có câu xác nhận trực tiếp cho raw socket]**

### 2.5 cTrader Cloud: ghi chú phụ (không áp dụng trực tiếp cho use-case hiện tại)

Nếu sau này cân nhắc chạy cBot trên cTrader Cloud (VPS của Spotware) thay vì Desktop cục bộ: 1
moderator xác nhận trên forum "No, internet access is not permitted for cBots executed on the cloud"
(HTTP bị cấm hoàn toàn trên Cloud), WebSocket chỉ được phép qua đúng port 25345 và người dùng vẫn báo
lỗi kết nối thực tế dù server lắng nghe đúng port đó. **[CỘNG ĐỒNG]** —
https://community.ctrader.com/forum/cbot-support/44617/

Không liên quan trực tiếp tới bo_workflow hiện tại (chạy trên Desktop VM-BO20 qua `ctrader-cli`/GUI,
không phải Spotware Cloud), nhưng đáng nhớ nếu sau này muốn chuyển bot sang chạy 24/7 trên Cloud —
Hướng A (Redis trực tiếp) sẽ **chắc chắn không chạy được trên Cloud** dù có chạy được trên Desktop.

---

## 3. Reference NuGet package bên thứ 3 (vd StackExchange.Redis)

### 3.1 Được phép về mặt tài liệu, có ví dụ chính thức

Trang chính thức "Reference third-party libraries" mô tả 2 cách, và dùng **`Discord.Net`** — 1 package
NuGet thật, có nhiều dependency transitive — làm ví dụ minh hoạ chính thức:

```
NuGet\Install-Package Discord.Net -Version 3.16.0
```

Quy trình: cài package trong 1 IDE .NET ngoài (Visual Studio) → thoát VS → vào cTrader → "Manage
references" → tab Libraries → Browse tới DLL đã cài → Apply. Với package không có trên NuGet, dùng
thẳng "Manage references" trỏ tới file `.dll`.

**[CHÍNH THỨC]** — https://help.ctrader.com/ctrader-algo/how-tos/all-algos/reference-third-party-libraries/
(cross-check: https://help.ctrader.com/ctrader-algo/documentation/referencing-dotnet-libraries/ mô tả
thêm cách dùng `Install-Package`/NuGet CLI/GUI trực tiếp).

Đáng chú ý: Discord.Net dùng cả REST (HTTP) lẫn WebSocket Gateway cho Discord API — **không dùng raw
TCP socket** — nên ví dụ chính thức này **không chứng minh** raw-socket package (như StackExchange.Redis)
cũng chạy được, chỉ chứng minh cơ chế "reference NuGet ngoài" nói chung là được phép và có tài liệu.
**[SUY LUẬN — phân biệt quan trọng, tránh áp dụng nhầm ví dụ Discord.Net sang trường hợp Redis]**

### 3.2 Nhưng: compiler MẶC ĐỊNH không hỗ trợ third-party package — đây là rào cản thật

Trang "Compiler" chính thức nói rõ:

> Embedded compiler (mặc định) "does not support any third-party .NET packages and frameworks such as
> WinForms and WPF."
>
> ".NET SDK compiler is strongly recommended for large projects or extensions that use third-party
> .NET libraries."

**[CHÍNH THỨC]** — https://help.ctrader.com/ctrader-algo/documentation/all-algos/compiling/

Để bật ".NET SDK compiler": Settings → tab Algo → Select compiler → chọn 1 bản .NET SDK **đã cài sẵn
trên máy**; nếu chưa có bản nào, cTrader hiện nút "Install .NET SDK" dẫn tới trang tải Microsoft.
**[CHÍNH THỨC, tổng hợp từ trang Compiler + kết quả search cùng chủ đề]**

Một thread cộng đồng thực tế (lỗi "Packages are not recognised inside cTrader 4.2.16 platform") xác
nhận đúng quy trình sửa lỗi 2 bước:
1. Cài `.NET 6 SDK` từ trang chính thức Microsoft (không dùng runtime bundled sẵn của cTrader Desktop).
2. Đổi compiler trong Settings của cTrader sang bản `.NET SDK` (6.0) vừa cài — người dùng xác nhận
   "I have changed the compiler to 6.0 as per your post and successfully build inside cTrader platform."

**[CỘNG ĐỒNG]** — https://community.ctrader.com/forum/ctrader-algo/38557/

**Đối chiếu với bối cảnh máy VM-BO20 (theo `CLAUDE.md` của repo này)**: máy hiện tại **không có** .NET
SDK cài ở tầm hệ thống — `dotnet --version` trong PowerShell báo "not found"; cTrader Desktop chỉ mang
theo 1 bundled .NET runtime riêng, không phải SDK CLI công khai. **Điều này nghĩa là: muốn dùng
StackExchange.Redis (hoặc bất kỳ NuGet ngoài nào) qua build GUI của cTrader Automate trên máy này,
bước ĐẦU TIÊN bắt buộc là cài .NET 6 SDK hệ thống (download từ Microsoft) — một thay đổi môi trường
máy, không phải chỉ thay đổi code.** **[SUY LUẬN, ghép trực tiếp fact về máy trong CLAUDE.md với
docs+forum ở trên]**

Ghi chú riêng cho `ctrader-cli build` (đường build hiện `bo_workflow` đang dùng, theo memory nội bộ
`ctrader-cli-backtest-optimize-facts.md`): `ctrader-cli build <csproj>` đã đo hoạt động ổn định
(~13s/bot) cho csproj HIỆN TẠI (chỉ có `PackageReference cTrader.Automate`) — nhưng **chưa từng được
thử với 1 `PackageReference` bên thứ 3 mới** (như `StackExchange.Redis`). Không rõ pipeline build nội
bộ của `ctrader-cli` tương đương compiler nào (embedded hay .NET SDK) hay nó tự mang theo 1 bản SDK
riêng độc lập với Settings GUI. **[CHƯA XÁC MINH — đây là câu hỏi có thể trả lời rẻ bằng 1 thử nghiệm
thật: thêm `<PackageReference Include="StackExchange.Redis" Version="2.8.16" />` vào 1 bản sao
`.csproj`, chạy `ctrader-cli build`, đọc lỗi trả về, KHÔNG cần credential/mạng Redis thật.]**

### 3.3 Ngay cả khi build thành công: rủi ro FileNotFoundException lúc RUNTIME có thật, tài liệu im lặng

Tài liệu chính thức **không giải thích** cơ chế: file `.algo` xuất ra có tự đóng gói (bundle/merge) mọi
DLL dependency bên thứ 3 hay không, hay chỉ tham chiếu tới chúng và kỳ vọng chúng có sẵn tại runtime.
Trang bảo mật `.algo` (https://help.ctrader.com/ctrader-algo/documentation/protection-measures/) chỉ
nói về mã hoá/chống decompile, không nói gì về dependency bundling. **[CHÍNH THỨC — im lặng, không xác
nhận cũng không phủ nhận]**

Bằng chứng thực tế từ cộng đồng cho thấy đây **không phải rủi ro lý thuyết suông**:
- 1 thread báo lỗi `FileNotFoundException` cho `System.Core` khi dùng `Google.Apis.Sheets.v4` — build
  được trong Visual Studio nhưng crash lúc chạy trong cTrader.
  **[CỘNG ĐỒNG]** — https://community.ctrader.com/forum/cbot-support/21693/
- 1 thread khác: nâng version .NET Framework cho project cBot để dùng NuGet mới hơn → lỗi
  `FileNotFoundException` cho `System.Runtime.CompilerServices.Unsafe`; giải pháp thực tế là **hạ cấp
  version NuGet xuống bản cũ tương thích**, không phải sửa cấu hình cTrader.
  **[CỘNG ĐỒNG]**
- 1 thread về "Referencing an custom indicator" cho thấy dependency LỒNG NHAU (indicator A phụ thuộc
  indicator B, cả 2 đều là `.algo`) cũng có thể gây `FileNotFoundException` — gợi ý mô hình resolve
  dependency của `.algo` không đơn giản/trong suốt như 1 project .NET thông thường.
  **[CỘNG ĐỒNG]** — https://community.ctrader.com/forum/cbot-support/10284/

`StackExchange.Redis` là package managed thuần .NET (không cần native binary cho kịch bản dùng cơ bản
qua TCP/Pipelines), nên rủi ro loại này **thấp hơn** so với các case native/framework-nặng (WinForms,
TensorFlow binding...) đã thấy trong tài liệu Compiler — nhưng do tài liệu không xác nhận cơ chế bundle,
rủi ro này **vẫn tồn tại và chỉ có thể loại trừ bằng build+chạy thử thật**, không phải bằng đọc tài
liệu thêm. **[SUY LUẬN + CHƯA XÁC MINH]**

---

## 4. Pattern cộng đồng/tài liệu cho việc "bơm dữ liệu ngoài vào cBot lúc backtest"

### 4.1 Cơ chế chính thức duy nhất tìm được: `BacktestingDataSources` — nhưng là API của Plugin, không phải cBot

Trang "Backtesting custom data sources" (https://help.ctrader.com/ctrader-algo/guides/backtesting-custom-data-sources/)
mô tả kiến trúc: `BacktestingDataSources.Add(name, options)` + `BacktestingDataSourceOptions` (loại dữ
liệu Tick/M1/OHLCV, min/max time, handler tự phục vụ dữ liệu khi engine yêu cầu). Class này nằm ở
namespace `references/**Plugin**/Backtesting/DataSource/BacktestingDataSources` — tức đây là 1 API
dành cho phát triển **Plugin** cTrader (1 loại extension khác, chạy trong Desktop app, có quyền hạn và
mô hình lifecycle khác hẳn cBot `cAlgo.Robots`), **không phải API mà 1 cBot (`Robot` class) có thể gọi
từ bên trong chính nó**. **[CHÍNH THỨC, kết luận về phạm vi Plugin-only là SUY LUẬN từ vị trí namespace]**

Việc này khớp với 1 phát hiện đã có sẵn trong memory nội bộ dự án (`ctrader-cli-backtest-optimize-facts.md`,
mục "Phương án triển khai"): custom data source "CÓ trong API 5.9.16 [Plugin], CLI không có" — xác
nhận thêm rằng đây là tính năng ở tầng Plugin/Desktop-API, tách biệt hẳn khỏi cả cBot lẫn `ctrader-cli`.

Ngay cả nếu dùng được, `BacktestingDataSources` sinh ra dữ liệu **GIÁ** (tick/OHLCV) để mô phỏng market
data tuỳ biến — không phải cơ chế để cấp 1 chuỗi "tín hiệu chiến lược" (buy/sell/ATR/entry) như file
CSV hiện tại đang làm. Dùng sai mục đích (ép tín hiệu chiến lược vào khe dữ liệu giá) sẽ không tự
nhiên và không có tiền lệ nào được ghi nhận.

### 4.2 File cục bộ (File Operations) vẫn là pattern CHÍNH THỨC duy nhất cho dữ liệu tuỳ biến ở tầm cBot

Tài liệu "File operations" (https://help.ctrader.com/ctrader-algo/guides/file-operations/) mô tả đúng
cơ chế mà `LoadSignalFile()` hiện tại đang dùng: đọc/ghi file trong 1 thư mục chỉ định (sandbox riêng
theo tên cBot khi `AccessRights.None`, hoặc bất kỳ đường dẫn nào khi `FullAccess`). Đây là con đường
**duy nhất được tài liệu chính thức mô tả rõ ràng, đầy đủ, và không có cảnh báo runtime-fragile nào**
cho việc "cấp dữ liệu tuỳ biến từ ngoài vào cBot", áp dụng đều cho cả live lẫn backtest — không có
caveat "chỉ lấy bản mới nhất" như network (vì đọc file không có khái niệm "live vs historical", file
đứng yên tại đúng nội dung đã ghi).

**Không tìm thấy** bất kỳ ví dụ chính thức hay thảo luận cộng đồng nào mô tả 1 cBot tự kết nối DB/Redis/
API bên ngoài làm **nguồn tín hiệu chiến lược chính** trong lúc backtest — mọi thảo luận network tìm
được đều xoay quanh mục đích phụ trợ (gửi thông báo Telegram/Discord SAU KHI đã có quyết định giao
dịch, tra cứu tin tức/giá bổ sung), không phải LÀM tín hiệu quyết định entry/SL/TP.

**→ Kết luận mục 4**: CSV cục bộ hiện tại không chỉ là "cách duy nhất đã biết" mà đúng thực sự là
**cách được tài liệu chính thức khuyến khích và không có cạm bẫy nào** cho đúng nhu cầu này. Không có
pattern thay thế nào tốt hơn được tìm thấy.

---

## 5. So sánh Hướng A vs Hướng B dựa trên bằng chứng

### Hướng A — cBot tự đọc Redis trực tiếp trong `OnStart()`/lúc chạy

| Tiêu chí | Đánh giá | Bằng chứng |
|---|---|---|
| Network channel phù hợp | **Không có sẵn** — Redis cần raw TCP, cTrader chỉ chính thức hỗ trợ Http/WebSocket | [CHÍNH THỨC] mục 2.2 |
| Khả thi bằng raw socket (`TcpClient`) | Có tiền lệ cộng đồng (live/demo), cần `FullAccess` (có thể chỉ cần `Internet`, chưa rõ) | [CỘNG ĐỒNG]+[SUY LUẬN] mục 2.1/2.3 |
| Hoạt động đúng trong BACKTEST | Http xác nhận có chạy nhưng **luôn lấy bản mới nhất, không phải lịch sử** — phá vỡ tính tái lập; raw socket suy luận tương tự nhưng chưa xác nhận trực tiếp | [CHÍNH THỨC] mục 2.4 |
| Reference NuGet Redis client | Được phép về nguyên tắc, có ví dụ chính thức (Discord.Net) cho cơ chế reference | [CHÍNH THỨC] mục 3.1 |
| Build được trên máy này | **Cần cài .NET SDK hệ thống + đổi compiler settings trước** (máy hiện chưa có SDK theo CLAUDE.md); `ctrader-cli build` với NuGet mới — chưa thử | [CHÍNH THỨC]+[CHƯA XÁC MINH] mục 3.2 |
| Rủi ro runtime sau khi build OK | Có tiền lệ `FileNotFoundException` cho package khác dù build sạch — tài liệu không xác nhận cơ chế bundle | [CỘNG ĐỒNG] mục 3.3 |
| Độ phức tạp thay đổi code | Phải sửa `LoadSignalFile()`, đổi `AccessRights`, thêm dependency mới vào `.csproj` của CẢ 2 cBot | — |
| Rủi ro reproducibility (Redis bị ghi đè) | Đã biết trước, CỘNG THÊM vào rủi ro kiến trúc "luôn live" ở trên — 2 lớp rủi ro chồng nhau | (đã có sẵn trong bối cảnh dự án) |

**Kết luận Hướng A**: không "bất khả thi tuyệt đối" nhưng **rủi ro chồng ở mọi lớp** (giao thức, quyền,
compiler/SDK, runtime resolution, VÀ đúng ngay tại điểm mấu chốt là bán bản chất backtest-network luôn
lấy live data) — quá nhiều ẩn số phải thử nghiệm thật mới biết chắc, cho một thay đổi đụng thẳng vào
core của cả 2 cBot đang chạy ổn định.

### Hướng B — `cli_engine` (Python) đọc Redis trước, ghi CSV, trỏ `SignalFilePath` như cũ

| Tiêu chí | Đánh giá |
|---|---|
| Network channel | Python không bị sandbox gì cả — `redis-py` dùng thẳng, không vướng bất kỳ giới hạn nào ở mục 2 |
| Reproducibility | File CSV đứng yên tại đúng nội dung lúc ghi — không có caveat "luôn lấy bản mới nhất" của network-trong-backtest; nếu cần tái lập lại 1 lần chạy cũ, chỉ cần giữ lại đúng file CSV đã dump |
| Thay đổi code cBot | **Bằng 0** — `LoadSignalFile()`, `AccessRights.FullAccess`, `.csproj` giữ nguyên y hệt hiện tại |
| Thay đổi code Python | Thêm đúng 1 bước (LRANGE + HGETALL/HMGET → ghi CSV đúng khuôn cột `bartime,atr,entry,signal`) trước khi build `.cbotset`, tận dụng hạ tầng `cli_engine` đã có sẵn kết nối/orchestrate |
| Rủi ro build/compiler/SDK | Không có — không đụng gì tới `.csproj`/compiler của cTrader |
| Rủi ro runtime dependency | Không có — không thêm NuGet package nào vào cBot |
| Rủi ro Redis bị ghi đè giữa chừng | Vẫn tồn tại (đã biết trước) NHƯNG xảy ra ở tầng Python — dễ kiểm soát hơn nhiều (snapshot 1 lần, log lại thời điểm đọc, retry/lock ở phía mình chủ động) so với việc phải kiểm soát nó bên trong sandbox cBot | 

**Kết luận Hướng B**: đúng tinh thần "triển khai từng chút một" người dùng đang muốn — 0 thay đổi rủi
ro ở cBot, chỉ thêm 1 bước có thể viết/test/rollback độc lập ở phía Python vốn đã linh hoạt sẵn.

---

## 6. Khuyến nghị cuối

**Chọn Hướng B.** Lý do cốt lõi không phải "Hướng A khó code" mà là **Hướng A đụng đúng 1 giới hạn kiến
trúc đã được chính tài liệu Spotware xác nhận** (network access trong backtest luôn trả về bản mới
nhất, không phải lịch sử) — giới hạn này áp dụng bất kể có vượt qua được rào cản raw-socket/NuGet/
compiler hay không, và nó phá đúng thứ quan trọng nhất của backtest: khả năng tái lập theo đúng thời
điểm lịch sử của từng bar.

Nếu sau này vẫn muốn thử Hướng A cho mục đích khác (vd cBot LIVE, không phải backtest, gọi Redis để lấy
tín hiệu real-time — bối cảnh đó KHÔNG bị caveat "luôn live" ở mục 2.4 vì live vốn dĩ luôn cần dữ liệu
mới nhất), thứ tự thử nghiệm rẻ nhất để thu hẹp các ô "CHƯA XÁC MINH" ở trên:
1. Thêm `<PackageReference Include="StackExchange.Redis" Version="2.8.16" />` vào 1 bản sao `.csproj`,
   chạy `ctrader-cli build` — xem có tự restore/link được không, đọc lỗi cụ thể nếu có (rẻ, không cần
   mạng/Redis thật, không đụng file `.algo` chính — nhớ đổi tên project/class như đã ghi trong memory
   `ctrader-cli-backtest-optimize-facts.md` để tránh ghi đè `.algo` thật).
2. Nếu build OK: thử load 1 cBot tối giản chỉ gọi `ConnectionMultiplexer.Connect(...)` trong `OnStart()`
   với `AccessRights.Internet` (thử trước `FullAccess`) trên tab Backtesting GUI — xem exception cụ
   thể là gì nếu bị chặn (sẽ cho biết chính xác `Internet` có đủ hay bắt buộc `FullAccess`).
3. Chỉ khi cả 2 bước trên qua được mới đáng đầu tư thời gian điều tra tiếp caveat "luôn live" có áp
   dụng y hệt cho raw socket như đã xác nhận cho `Http` hay không.

---

## 7. Danh sách nguồn đã dùng

**Chính thức (help.ctrader.com):**
- https://help.ctrader.com/ctrader-algo/guides/access-rights/
- https://help.ctrader.com/ctrader-algo/references/Application/AccessRights/
- https://help.ctrader.com/ctrader-algo/guides/network-access/
- https://help.ctrader.com/ctrader-algo/how-tos/all-algos/use-network-access/
- https://help.ctrader.com/ctrader-algo/guides/file-operations/
- https://help.ctrader.com/ctrader-algo/how-tos/all-algos/work-with-files/
- https://help.ctrader.com/ctrader-algo/how-tos/all-algos/reference-third-party-libraries/
- https://help.ctrader.com/ctrader-algo/documentation/referencing-dotnet-libraries/
- https://help.ctrader.com/ctrader-algo/documentation/all-algos/compiling/
- https://help.ctrader.com/ctrader-algo/documentation/visual-studio-ides/
- https://help.ctrader.com/ctrader-algo/documentation/protection-measures/
- https://help.ctrader.com/ctrader-algo/guides/backtesting-custom-data-sources/
- https://help.ctrader.com/ctrader-algo/references/Plugin/Backtesting/DataSource/BacktestingDataSources/
- https://help.ctrader.com/ctrader-algo/faq/

**Cộng đồng (community.ctrader.com / ctrader.com/forum):**
- https://community.ctrader.com/forum/ctrader-support/41012/ (Telegram + AccessRights.None)
- https://community.ctrader.com/forum/cbot-support/12373/ (NuGet R.NET.Community)
- https://community.ctrader.com/forum/cbot-support/4302/ (TcpClient/SocketServer copy lệnh)
- https://community.ctrader.com/forum/cbot-support/44617/ (HTTP trên cTrader Cloud — bị cấm)
- https://community.ctrader.com/forum/ctrader-algo/38557/ (lỗi NuGet không nhận diện → phải cài .NET SDK + đổi compiler)
- https://community.ctrader.com/forum/cbot-support/21693/ (FileNotFoundException Google.Apis.Sheets.v4)
- https://community.ctrader.com/forum/cbot-support/10284/ (FileNotFoundException dependency lồng nhau giữa 2 .algo)

**Nội bộ dự án (tham chiếu chéo, không phải nguồn ngoài):**
- `bo_workflow/reports/ab-plugin-api-vs-cli-2026-09-11.md` — xác nhận `ctrader-cli backtest` và GUI
  Backtesting dùng chung lõi mô phỏng.
- Memory `ctrader-cli-backtest-optimize-facts.md` — xác nhận `BacktestingDataSources` là API Plugin,
  không có trong CLI; xác nhận `ctrader-cli build` hoạt động cho csproj hiện tại.
