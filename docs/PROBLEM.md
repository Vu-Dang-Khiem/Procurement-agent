# Vấn đề thực tế của quy trình mua hàng

Khi một phòng ban cần mua gì đó, nhân viên không thể tự đi mua. Tiền là của công ty, nên công ty cần chắc chắn rằng món đồ thực sự cần mua, phòng đó còn ngân sách, giá hợp lý và người đồng ý có đủ thẩm quyền. Quy trình mua hàng (procurement) sinh ra để đảm bảo những điều đó.

## Ví dụ: phòng IT cần 10 màn hình, khoảng 55 triệu đồng

Ví dụ này dùng để minh họa, các mức tiền lấy theo quy định của công ty giả lập trong dự án.

| Bước | Việc phải làm | Chỗ hay vướng |
|---|---|---|
| 1. Gửi yêu cầu | Nhân viên nhắn: "Cần 10 màn hình như mấy cái cũ, tầm 5–6 triệu" | Thiếu thông tin: loại nào, cần khi nào? |
| 2. Làm rõ | Nhân viên mua hàng hỏi lại | Mất thời gian trao đổi qua lại |
| 3. Kiểm tra ngân sách | Xem phòng còn bao nhiêu tiền | Dễ bỏ sót khoản đã cam kết chi nhưng chưa trả |
| 4. Thu thập báo giá | Mức tiền này cần 2 báo giá | Mỗi nơi một mẫu PDF, có báo giá đã hết hạn |
| 5. So sánh | Xem giá, thời gian giao, nhà cung cấp có được duyệt không | Dễ so sai, ví dụ giá trước và sau VAT |
| 6. Trình duyệt | Trưởng phòng, rồi Finance | Trình sai người, hồ sơ nằm chờ |
| 7. Đặt hàng | Tạo đơn hàng | Trùng đơn, giá lệch so với báo giá |

## Khó khăn nằm ở đâu

Từng bước đều không khó. Khó khăn nằm ở chỗ chúng cộng dồn lại:

- **Việc nhỏ nhưng nhiều:** khoản mua nhỏ cũng phải đi qua gần như đủ các bước.
- **Thời gian chủ yếu là chờ:** chờ trả lời, chờ báo giá, chờ duyệt, và khó biết hồ sơ đang kẹt ở đâu.
- **Thông tin rải rác:** email, PDF, Excel và ERP tách rời, nên phải đọc rồi nhập lại bằng tay.
- **Quy định nhiều, dễ sót:** đọc nhầm một báo giá hoặc tính sai ngân sách có thể dẫn đến duyệt sai.
- **Chậm thì sinh ra lách quy trình:** cần gấp, nhân viên mua trước rồi hợp thức hóa sau, và công ty mất kiểm soát chi tiêu.

Phần mềm truyền thống làm tốt các việc có cấu trúc, như chuyển hồ sơ theo mức tiền. Nhưng nó khó xử lý câu "như mấy cái cũ" hay một file PDF lạ, nên con người vẫn phải đọc, hỏi lại và nhập liệu. Các hãng lớn cũng đang đưa agent vào đúng khoảng trống này, ví dụ [SAP](https://erp.today/sap-joule-agents-ariba-fieldglass-procurement-automation-2026/) và [Coupa](https://procurementmag.com/news/coupa-zip-ivalua-oro-best-breed-procurement-vendor).

## Agent đảm nhận phần nào

| Bước | Agent làm | Con người làm |
|---|---|---|
| 1–2 | Đọc yêu cầu, hỏi lại phần còn thiếu | Trả lời câu hỏi |
| 3 | Kiểm tra ngân sách bằng tool | |
| 4–5 | Đọc và so sánh các báo giá đã nhận | Xin báo giá từ nhà cung cấp |
| 6 | Gửi hồ sơ đến đúng người duyệt | Duyệt hoặc từ chối |
| 7 | Tạo bản nháp đơn hàng sau khi đủ phê duyệt | Quyết định đặt hàng, thanh toán |
