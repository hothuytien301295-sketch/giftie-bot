# Giftie Bot – Chatbot tư vấn quà tặng trên Messenger (Gemini API miễn phí)

Luồng hoạt động:
Khách nhắn Fanpage → Facebook gọi webhook `/webhook` → máy chủ gọi Gemini API (kèm system prompt + catalog) → gửi câu trả lời lại qua Messenger Send API.

## Cấu trúc thư mục
| File | Vai trò |
|---|---|
| `app.py` | Máy chủ Flask: webhook, gọi Gemini, gửi tin nhắn |
| `system_prompt.txt` | Hướng dẫn cho AI (Phụ lục B của báo cáo) |
| `catalog.csv` | Danh mục sản phẩm (sheet `Product` trong GIFTIE_C_S.xlsx, 22 sản phẩm). Sửa/thêm sản phẩm tại đây |
| `variants.csv` | Biến thể (sheet `Variant`, 18 biến thể: dung tích nước hoa, loại/size áo, màu vòng tay). Chỉ biến thể có `Status = Còn` và `Stock > 0` được đưa cho AI |
| `.env.example` | Mẫu biến môi trường |
| `requirements.txt` | Thư viện Python |

## Bước 1. Lấy API key của Gemini (miễn phí)
1. Vào https://aistudio.google.com, đăng nhập tài khoản Google.
2. Chọn **Get API key** → tạo key, lưu lại làm `GEMINI_API_KEY`. Gói miễn phí không cần nhập thẻ.
3. Gói miễn phí có giới hạn số lượt gọi mỗi phút/ngày tùy mô hình (xem trong AI Studio). Nếu vượt giới hạn, bot sẽ trả lời tin nhắn báo lỗi tạm thời. Hãy đọc điều khoản về dữ liệu của gói miễn phí và nêu trong mục quyền riêng tư của báo cáo.

## Bước 2. Tạo Facebook App
1. Vào https://developers.facebook.com → My Apps → Create App → chọn loại **Business** (hoặc "Other" → Business).
2. Trong app, thêm sản phẩm **Messenger** (Add Product → Messenger).
3. Mục **Settings → Basic**: copy **App Secret** → `APP_SECRET`.
4. Mục **Messenger → API setup (Messenger API Settings)**: bấm *Add or remove Pages*, chọn Fanpage Giftie, rồi **Generate token** → `PAGE_ACCESS_TOKEN`.

## Bước 3. Chạy thử trên máy (tùy chọn)
```bash
cd giftie-bot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp        # rồi điền 4 giá trị thật vào
python app.py
```
Để Facebook gọi được vào máy bạn, dùng ngrok: `ngrok http 5000` và lấy địa chỉ `https://xxxx.ngrok-free.app`.

## Bước 4. Đưa lên máy chủ (khuyến nghị Render.com, có gói miễn phí)
1. Đưa thư mục `giftie-bot` lên GitHub (nhớ **không** commit file `.env`).
2. Render → New → **Web Service** → chọn repo.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app --workers 1 --threads 8 --timeout 60`
5. Mục Environment: thêm `GEMINI_API_KEY`, `PAGE_ACCESS_TOKEN`, `APP_SECRET`, `VERIFY_TOKEN`, `GEMINI_MODEL`.
6. Deploy xong sẽ có địa chỉ dạng `https://giftie-bot.onrender.com`.

> Dùng **1 worker** vì lịch sử hội thoại đang lưu trong RAM. Gói free của Render sẽ "ngủ" sau 15 phút không có yêu cầu, và mất khoảng 1 phút để dậy lại, nên tin nhắn đầu tiên có thể trả lời chậm. Có thể dùng dịch vụ ping định kỳ (ví dụ UptimeRobot) vào địa chỉ `/` để hạn chế.

## Bước 5. Kết nối webhook
1. Facebook App → Messenger → **Configure webhooks**.
2. Callback URL: `https://<địa-chỉ-của-bạn>/webhook`
3. Verify token: đúng giá trị `VERIFY_TOKEN` bạn đặt.
4. Bấm Verify and Save, rồi **Subscribe** trường `messages` cho Fanpage Giftie.

## Bước 6. Thử nghiệm
- Ở chế độ **Development**, chỉ tài khoản có vai trò Admin/Developer/Tester của App mới nhắn được cho bot. Vào **App roles → Roles** để thêm 30 người thử nghiệm làm **Tester** (họ phải chấp nhận lời mời).
- Nhắn tin vào Fanpage Giftie và thử các kịch bản: chọn quà theo ngân sách, đổi tiêu chí, hỏi phí ship (bot phải chuyển nhân viên).
- Muốn mở cho mọi khách hàng thật, cần chuyển app sang **Live** và xin duyệt quyền `pages_messaging` (App Review). Với bài tập học phần, dùng chế độ Tester là đủ.

## Cách bot chuyển nhân viên
Khi AI thêm mã `[CHUYEN_NHAN_VIEN]`, bot sẽ bỏ mã này khỏi tin nhắn, ghi log và **im lặng 12 giờ** với khách đó để nhân viên trả lời thủ công trong Hộp thư Fanpage (Meta Business Suite). Xem log trên Render để biết khách nào cần hỗ trợ.

## Giới hạn hiện tại (nên nêu trong báo cáo, mục 3.5 và Kết luận)
- Catalog và biến thể đọc từ `catalog.csv` và `variants.csv` khi khởi động, chưa tự đồng bộ từ Google Sheets. Khi sửa Excel: xuất lại sheet `Product` và `Variant` thành CSV (UTF-8), thay file rồi deploy lại.
- Bot chưa gửi ảnh sản phẩm (cột `Image_URL` đang là link Google Drive, chưa dùng).
- Tồn kho (`Stock`) chỉ được dùng để loại biến thể hết hàng, không trừ kho khi có đơn.
- Lịch sử hội thoại lưu trong RAM, mất khi máy chủ khởi động lại.
- Bot chỉ xử lý tin nhắn chữ; chưa xử lý ảnh/sticker.
- Chưa có dashboard thống kê; dữ liệu thử nghiệm ghi nhận qua Google Forms và Excel theo thiết kế báo cáo.

## Chi phí
Hosting Render gói free và Gemini API gói miễn phí nên chạy thử nghiệm không tốn phí. Nếu cần đổi mô hình, sửa biến `GEMINI_MODEL` (chọn mô hình có ghi "Free of charge" ở cột Free tier tại https://ai.google.dev/gemini-api/docs/pricing).
