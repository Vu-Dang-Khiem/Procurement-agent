# Procurement Agent

AI Agent xử lý công việc mua hàng nội bộ của doanh nghiệp, trong một công ty giả lập. Xây dựng bằng LangGraph và MCP.

> **Trạng thái:** đã có thiết kế, chưa có code. Các phần dưới đây mô tả hệ thống đang hướng tới, chưa phải tính năng đã chạy được.

Đây là dự án portfolio về kỹ thuật AI Agent, không phải sản phẩm thương mại.

## Đề tài

Khi một phòng ban cần mua gì đó, ví dụ 10 màn hình, nhân viên không thể tự đi mua. Tiền là của công ty, nên công ty cần chắc chắn rằng món đồ thực sự cần mua, phòng đó còn ngân sách, giá hợp lý và người đồng ý có đủ thẩm quyền.

Quy trình mua hàng (procurement) sinh ra để đảm bảo những điều đó. Đề tài của dự án là để một AI Agent đảm nhận phần việc giấy tờ của quy trình này, còn quyền quyết định vẫn thuộc về con người.

## Vấn đề thực tế

Ví dụ phòng IT cần 10 màn hình, tổng khoảng 55 triệu đồng:

| Bước | Việc phải làm | Chỗ hay vướng |
|---|---|---|
| 1. Gửi yêu cầu | Nhân viên nhắn: "Cần 10 màn hình như mấy cái cũ, tầm 5–6 triệu" | Thiếu thông tin: loại nào, cần khi nào? |
| 2. Làm rõ | Nhân viên mua hàng hỏi lại | Mất thời gian trao đổi qua lại |
| 3. Kiểm tra ngân sách | Xem phòng còn bao nhiêu tiền | Dễ bỏ sót khoản đã cam kết chi nhưng chưa trả |
| 4. Thu thập báo giá | Mức tiền này cần 2 báo giá | Mỗi nơi một mẫu PDF, có báo giá đã hết hạn |
| 5. So sánh | Xem giá, thời gian giao, nhà cung cấp có được duyệt không | Dễ so sai, ví dụ giá trước và sau VAT |
| 6. Trình duyệt | Trưởng phòng, rồi Finance | Trình sai người, hồ sơ nằm chờ |
| 7. Đặt hàng | Tạo đơn hàng | Trùng đơn, giá lệch so với báo giá |

Từng bước đều không khó. Khó khăn nằm ở chỗ chúng cộng dồn lại:

- **Việc nhỏ nhưng nhiều:** khoản mua nhỏ cũng phải đi qua gần như đủ các bước.
- **Thời gian chủ yếu là chờ:** chờ trả lời, chờ báo giá, chờ duyệt, và khó biết hồ sơ đang kẹt ở đâu.
- **Thông tin rải rác:** email, PDF, Excel và ERP tách rời, nên phải đọc rồi nhập lại bằng tay.
- **Quy định nhiều, dễ sót:** đọc nhầm một báo giá hoặc tính sai ngân sách có thể dẫn đến duyệt sai.
- **Chậm thì sinh ra lách quy trình:** cần gấp, nhân viên mua trước rồi hợp thức hóa sau, và công ty mất kiểm soát chi tiêu.

Phần mềm truyền thống làm tốt các việc có cấu trúc, như chuyển hồ sơ theo mức tiền. Nhưng nó khó xử lý câu "như mấy cái cũ" hay một file PDF lạ, nên con người vẫn phải đọc, hỏi lại và nhập liệu. Các hãng lớn cũng đang đưa agent vào đúng khoảng trống này, ví dụ [SAP](https://erp.today/sap-joule-agents-ariba-fieldglass-procurement-automation-2026/) và [Coupa](https://procurementmag.com/news/coupa-zip-ivalua-oro-best-breed-procurement-vendor).

## Dự án giải quyết vấn đề này như thế nào

Người dùng giao mục tiêu bằng tiếng Việt. Agent tự lập kế hoạch, chọn tool và xử lý phát sinh, không đi theo một luồng cố định.

**Agent làm:**

- Đọc email và báo giá PDF, trích xuất thông tin.
- Hỏi lại người yêu cầu khi thiếu thông tin.
- Kiểm tra ngân sách và quy định, so sánh các báo giá đã nhận.
- Gửi hồ sơ đến đúng người duyệt, tiếp tục khi có kết quả.
- Tạo bản nháp đơn hàng sau khi đã đủ phê duyệt.

**Agent không làm:** phê duyệt, thanh toán, gửi email ra ngoài công ty. Agent không có tool cho các việc này.

**Mục tiêu mở agent cần xử lý được, ví dụ:**

- "Xử lý yêu cầu mua 10 màn hình của phòng IT"
- "Vì sao yêu cầu #12 bị treo 5 ngày?"
- "Nếu duyệt hết các yêu cầu đang chờ thì Marketing còn bao nhiêu ngân sách?"
- "Phúc Thành giao trễ 3 lần rồi. Tìm các đơn đang dùng họ và đề xuất nhà cung cấp thay thế"

## Trọng tâm kỹ thuật

Đưa agent vào công việc thật khó ở chỗ làm sao để nó đáng tin, nên dự án tập trung vào:

- **Ranh giới cứng:** quy tắc và phép tính nằm trong code, không dựa vào prompt hay để LLM tự kết luận.
- **Chống prompt injection:** email và PDF là dữ liệu không tin cậy, đi qua một LLM cách ly và không được kích hoạt hành động quan trọng.
- **Con người duyệt:** agent dừng chờ quyết định và tiếp tục được cả khi chương trình đã tắt.
- **Kiểm chứng kết quả:** trước khi báo "xong", kết quả được đối chiếu với database.
- **Đo được:** 80 kịch bản, chấm bằng cách so sánh thay đổi trong database, có gây lỗi tool và tấn công thử.
- **So sánh với workflow cố định:** để biết agent có thực sự tốt hơn không, và mỗi thành phần tốn thêm bao nhiêu.

## Công nghệ

Python 3.12, LangGraph, MCP (FastMCP), Claude API, SQLite, Langfuse, FastAPI, Streamlit, pytest.

## Hạn chế

Agent chạy trong công ty giả lập, không kết nối Gmail hay ERP thật. Kết quả eval sẽ phản ánh dữ liệu giả lập và không đồng nghĩa với hiệu quả ở doanh nghiệp thật. Báo cáo cuối sẽ nêu rõ nguồn dữ liệu và cả những trường hợp agent còn thất bại.

## Tài liệu

Thiết kế chi tiết, kế hoạch và nhật ký quyết định: [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md)
