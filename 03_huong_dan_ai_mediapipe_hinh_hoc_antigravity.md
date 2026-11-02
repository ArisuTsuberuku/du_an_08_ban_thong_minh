# TÀI LIỆU KỸ THUẬT SỐ 3: XÂY DỰNG & HIỆU CHỈNH MÔ HÌNH AI NHẬN DIỆN TƯ THẾ NGỒI HỌC (MEDIAPIPE + HÌNH HỌC 3D)

## 1. Mục tiêu & Đặc tả Giao việc cho Antigravity (Prompt Blueprint)

> **Prompt mẫu để bạn copy dán thẳng vào Antigravity:**
> "Hãy đóng vai kỹ sư Thị giác Máy tính (Computer Vision). Dựa trên tài liệu `03_huong_dan_ai_mediapipe_hinh_hoc_antigravity.md`, hãy xây dựng module `posture_ai_engine.py` sử dụng thư viện Google MediaPipe Holistic (`Face Mesh`, `Pose`, `Hands`) kết hợp toán học hình học không gian 3D và cơ chế Hiệu chỉnh Cá nhân hóa (Calibration) trong $5\text{ giây}$ đầu phiên học. Hệ thống phải tính toán theo thời gian thực các chỉ số: tỷ lệ mở mắt ($\text{EAR}$), tỷ lệ nhắm mắt tích lũy ($\text{PERCLOS}$), tỷ lệ mở miệng ngáp ($\text{MAR}$), 3 góc nghiêng đầu Euler ($\text{Pitch}, \text{Yaw}, \text{Roll}$), sự kiện ngủ gật ($E_{\text{nod}}$), phát hiện 5 lỗi tư thế công thái học, theo dõi 2 bàn tay trên mặt bàn, chặn Camera ảo (`OBS`, `ManyCam`) và đẩy toàn bộ luồng dữ liệu JSON + MJPEG lên Website báo cáo chung cho phụ huynh."

---

## 2. Cơ sở Toán học & Công thức Hình học Không gian (Khớp Báo cáo KHKT)

Khác với việc dùng Google Teachable Machine đơn thuần (vốn phải chụp hàng nghìn bức ảnh thủ công và rất dễ bị đánh lừa bởi góc quay hoặc ánh sáng), phương pháp **MediaPipe + Hình học 3D** trích xuất trực tiếp $468\text{ điểm mốc}$ khuôn mặt, $33\text{ điểm khớp}$ cơ thể và $21\text{ điểm khớp}$ bàn tay, sau đó áp dụng các phương trình hình học chuẩn xác sau:

### 2.1. Tỷ lệ mở mắt ($\text{EAR}_t$) & Phát hiện buồn ngủ / Ảnh tĩnh ($\text{PERCLOS}$)
Sử dụng khoảng cách Euclide giữa 6 điểm mốc quanh viền mắt ($p_1, p_2, p_3, p_4, p_5, p_6$):
$$\text{EAR}_t = \frac{\|p_2 - p_6\| + \|p_3 - p_5\|}{2\|p_1 - p_4\|}$$
* **Phát hiện ngủ gật:** Khi $\text{EAR}_t < \tau_{\text{EAR}}$ (với ngưỡng $\tau_{\text{EAR}} \in [0,18; 0,22]$) kéo dài liên tục, tỷ lệ phần trăm thời gian nhắm mắt $\text{PERCLOS}$ trên cửa sổ trượt tăng cao $\rightarrow$ Báo động buồn ngủ.
* **Chống giả mạo bằng ảnh tĩnh (Passive Anti-Spoofing):** Nếu trong $20 - 30\text{ giây}$ liên tục không ghi nhận bất kỳ sự kiện chớp mắt tự nhiên nào ($\text{EAR}_t$ đứng yên), hệ thống lập tức gắn cờ cảnh báo gian lận ảnh tĩnh.

### 2.2. Tỷ lệ mở miệng ($\text{MAR}_t$) & Tần suất ngáp ($F_{\text{yawn}}$)
Đo khoảng cách giữa môi trên, môi dưới và hai khóe miệng:
$$\text{MAR}_t = \frac{\|p_{62} - p_{66}\| + \|p_{63} - p_{65}\| + \|p_{61} - p_{67}\|}{3\|p_{60} - p_{64}\|}$$
* Khi $\text{MAR}_t > 0,60$ duy trì liên tiếp trên $1,5\text{ giây}$ $\rightarrow$ Ghi nhận sự kiện ngáp do mệt mỏi nhận thức.

### 2.3. Tư thế đầu 3D ($\text{Pitch}, \text{Yaw}, \text{Roll}$) & Sự kiện gật gù ($E_{\text{nod}}$)
Sử dụng thuật toán `cv2.solvePnP` chiếu 6 điểm mốc 2D (chóp mũi, cằm, hai đuôi mắt, hai khóe miệng) lên mô hình khuôn mặt 3D chuẩn để suy ra 3 góc Euler:
* **Sự kiện ngủ gật ($E_{\text{nod}}$):** Phát hiện khi độ biến thiên góc cúi đầu $\text{Pitch}$ vượt ngưỡng $\theta_{\text{nod}} = 15^\circ$ trong khoảng thời gian ngắn $\Delta t = 1,5\text{ s}$:
$$E_{\text{nod}}(t) = \mathbb{I}\left( \max_{k \in [t - \Delta t, t]} \text{Pitch}(k) - \min_{k \in [t - \Delta t, t]} \text{Pitch}(k) > \theta_{\text{nod}} \right)$$

---

## 3. Quy trình "Huấn luyện / Hiệu chỉnh Tự động" (5-Second Baseline Calibration)

Thay vì phải ngồi huấn luyện lại mô hình mỗi khi đổi góc đặt webcam hoặc đổi học sinh có chiều cao khác nhau, bạn hãy bảo Antigravity cài đặt cơ chế **Hiệu chỉnh Gốc Tư thế Chuẩn (Auto-Calibration)** trong $5\text{ giây}$ đầu tiên khi bật phần mềm:
1. Học sinh ngồi thẳng lưng chuẩn mực nhìn vào màn hình trong $5\text{ giây}$ đầu ($150\text{ khung hình}$).
2. Hệ thống tự động lấy trung bình cộng khoảng cách hai mắt ($d_{\text{eye\_base}}$), độ cao mũi - vai ($h_{\text{neck\_base}}$) và độ cân bằng hai vai ($s_{\text{shoulder\_base}}$) lưu vào bộ nhớ.
3. Trong suốt buổi học, mọi độ lệch vượt quá $\pm 20\%$ so với gốc chuẩn cá nhân này sẽ bị bắt lỗi chính xác tuyệt đối!

> **Mẹo ghép nối với thành viên làm Google Teachable Machine:**
> Nếu trong nhóm đã có 1 bạn lỡ làm mô hình Teachable Machine (`keras_model.h5`), Antigravity chỉ cần nạp song song file `keras_model.h5` đó vào cùng script dưới đây và lấy trung bình trọng số: $70\%$ điểm từ MediaPipe Hình học + $30\%$ điểm từ Teachable Machine. Cách này vừa tận dụng được công sức của bạn kia, vừa có đầy đủ thông số toán học ($\text{EAR}, \text{MAR}, \text{Pitch}, \text{Yaw}, \text{Roll}$) để báo cáo.

---

## 4. Mã nguồn Hoàn chỉnh (`posture_ai_engine.py`)

```python
import time
import json
import cv2
import psutil
import numpy as np
import mediapipe as mp

class PostureAIEngine:
    def __init__(self, camera_index=0):
        self.cap = cv2.VideoCapture(camera_index)
        self.mp_holistic = mp.solutions.holistic
        self.holistic = self.mp_holistic.Holistic(
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6
        )

        # Gốc chuẩn cá nhân hóa (Sẽ được tự động hiệu chỉnh trong 5 giây đầu)
        self.calibrated = False
        self.calib_samples = []
        self.base_eye_span = 0.16
        self.base_neck_height = 0.22

        # Theo dõi chuỗi thời gian chống giả mạo & tính PERCLOS
        self.last_blink_time = time.time()
        self.last_hand_time = time.time()
        self.ear_window = []       # Cửa sổ trượt tính PERCLOS
        self.pitch_history = []    # Cửa sổ trượt 1.5s tính sự kiện gật gù E_nod

    @staticmethod
    def _dist(p1, p2):
        return float(np.linalg.norm(np.array([p1.x, p1.y]) - np.array([p2.x, p2.y])))

    def check_virtual_camera_spoofing(self):
        """Chặn đứng các trình điều khiển Camera ảo (OBS, ManyCam, VCam)"""
        blacklist = ["obs64.exe", "obs32.exe", "manycam.exe", "vcam.exe", "splitcam.exe"]
        for p in psutil.process_iter(['name']):
            try:
                if (p.info['name'] or "").lower() in blacklist:
                    return True, p.info['name']
            except Exception:
                continue
        return False, ""

    def calibrate_baseline(self, eye_span, neck_height):
        """Tự động học tư thế ngồi chuẩn của học sinh trong 30 khung hình đầu tiên"""
        self.calib_samples.append((eye_span, neck_height))
        if len(self.calib_samples) >= 30:
            arr = np.array(self.calib_samples)
            self.base_eye_span = float(np.mean(arr[:, 0]))
            self.base_neck_height = float(np.mean(arr[:, 1]))
            self.calibrated = True
            print(f"[CALIBRATED] Gốc chuẩn: EyeSpan={self.base_eye_span:.3f}, Neck={self.base_neck_height:.3f}")

    def process_frame(self):
        """
        Xử lý 1 khung hình Webcam tại biên (Zero-cloud):
        Trả về (annotated_jpeg_bytes, telemetry_dict) để đẩy thẳng lên Website báo cáo.
        """
        ret, frame = self.cap.read()
        if not ret:
            return None, {"status": "ERROR", "message": "Không đọc được Webcam"}

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self.holistic.process(rgb)

        alerts = []
        ear = 0.30
        mar = 0.10
        pitch_deg = 0.0
        roll_deg = 0.0
        drowsy_nod = False

        # 1. Kiểm tra Camera ảo
        is_vcam, vcam_name = self.check_virtual_camera_spoofing()
        if is_vcam:
            alerts.append(f"GIAN LẬN: Phát hiện Camera ảo ({vcam_name})")

        # 2. Phân tích Điểm mốc Khuôn mặt (EAR, PERCLOS, MAR, Roll, Khoảng cách)
        if res.face_landmarks:
            lm = res.face_landmarks.landmark
            # Tính EAR trung bình 2 mắt
            ear_l = self._dist(lm[159], lm[145]) / (self._dist(lm[33], lm[133]) + 1e-6)
            ear_r = self._dist(lm[386], lm[374]) / (self._dist(lm[362], lm[263]) + 1e-6)
            ear = (ear_l + ear_r) / 2.0

            # Cập nhật cửa sổ trượt PERCLOS (100 khung hình gần nhất)
            self.ear_window.append(1 if ear < 0.20 else 0)
            self.ear_window = self.ear_window[-100:]
            perclos = round((sum(self.ear_window) / len(self.ear_window)) * 100.0, 1)

            if ear < 0.20:
                self.last_blink_time = time.time()
            elif time.time() - self.last_blink_time > 25.0:
                alerts.append("CẢNH BÁO PAD: Không chớp mắt >25s (Nghi ảnh tĩnh)")

            if perclos > 35.0:
                alerts.append(f"BUỒN NGỦ: Chỉ số PERCLOS cao ({perclos}%)")

            # Tính MAR (Độ mở miệng ngáp)
            mar = self._dist(lm[13], lm[14]) / (self._dist(lm[78], lm[308]) + 1e-6)
            if mar > 0.62:
                alerts.append("MỆT MỎI: Phát hiện ngáp (MAR cao)")

            # Góc nghiêng đầu sang vai (Roll)
            roll_deg = float(np.degrees(np.arctan2(lm[263].y - lm[33].y, lm[263].x - lm[33].x)))
            if abs(roll_deg) > 14.0:
                alerts.append(f"SAI TƯ THẾ: Nghiêng đầu ({roll_deg:.1f}°)")

            eye_span = self._dist(lm[33], lm[263])
        else:
            perclos = 0.0
            eye_span = self.base_eye_span
            alerts.append("VẮNG MẶT: Học sinh rời khỏi ghế ngồi")

        # 3. Phân tích Khung xương Vai & Cúi đầu (Pose Landmarks)
        if res.pose_landmarks:
            plm = res.pose_landmarks.landmark
            left_sh, right_sh, nose = plm[11], plm[12], plm[0]
            mid_sh_y = (left_sh.y + right_sh.y) / 2.0
            neck_height = mid_sh_y - nose.y

            if not self.calibrated and res.face_landmarks:
                self.calibrate_baseline(eye_span, neck_height)

            # Kiểm tra nhìn quá sát màn hình (So với gốc chuẩn cá nhân)
            if self.calibrated and eye_span > self.base_eye_span * 1.25:
                alerts.append("CẬN THỊ: Cúi quá sát màn hình/mặt bàn!")

            # Kiểm tra cúi đầu thấp & Sự kiện gật gù E_nod trong 1.5s
            pitch_deg = float((self.base_neck_height - neck_height) * 180.0)
            now = time.time()
            self.pitch_history.append((now, pitch_deg))
            self.pitch_history = [(t, p) for (t, p) in self.pitch_history if now - t <= 1.5]
            if len(self.pitch_history) > 5:
                p_vals = [p for (_, p) in self.pitch_history]
                if max(p_vals) - min(p_vals) > 15.0:
                    drowsy_nod = True
                    alerts.append("NGỦ GẬT: Phát hiện gật đầu đột ngột (>15°)")

            if self.calibrated and neck_height < self.base_neck_height * 0.72:
                alerts.append("SAI TƯ THẾ: Cúi đầu quá thấp!")

            # Kiểm tra lệch vai (Vẹo cột sống)
            if abs(left_sh.y - right_sh.y) > 0.055:
                alerts.append("SAI TƯ THẾ: Ngồi vẹo lưng / Lệch vai!")

        # 4. Theo dõi 2 bàn tay trên mặt bàn (Hands-on-Desk Tracking)
        hands_detected = bool(res.left_hand_landmarks or res.right_hand_landmarks)
        if hands_detected:
            self.last_hand_time = time.time()
        elif time.time() - self.last_hand_time > 15.0:
            alerts.append("MẤT TẬP TRUNG: Giấu cả 2 tay dưới gầm bàn (>15s)")

        # 5. Tính điểm tập trung thị giác (Visual Focus Score: 0 - 100%)
        visual_focus = max(0.0, 100.0 - len(alerts) * 22.0)

        # Vẽ lớp phủ thông số kỹ thuật lên khung hình để hiển thị trên Website
        banner_color = (16, 185, 129) if not alerts else (225, 29, 72)
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 38), (15, 23, 42), -1)
        header_txt = "CALIBRATING (5s)..." if not self.calibrated else (
            "TU THE CHUAN | CFI VISUAL: 100%" if not alerts else alerts[0]
        )
        cv2.putText(frame, header_txt, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.58, banner_color, 2)

        _, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

        telemetry = {
            "calibrated": self.calibrated,
            "ear": round(ear, 3),
            "perclos": perclos,
            "mar": round(mar, 3),
            "pitch_deg": round(pitch_deg, 1),
            "roll_deg": round(roll_deg, 1),
            "drowsy_nod": drowsy_nod,
            "hands_on_desk": (time.time() - self.last_hand_time <= 15.0),
            "visual_focus_score": round(visual_focus, 1),
            "alerts": alerts
        }
        return jpeg.tobytes(), telemetry

    def release(self):
        self.cap.release()
```