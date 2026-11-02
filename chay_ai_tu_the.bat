@echo off
REM Chay cua so xem thu AI Tu the 3D bang pythonw.exe
REM (tren may nay python.exe bi tu choi quyen Webcam, pythonw.exe thi mo binh thuong)
cd /d "%~dp0"
start "" pythonw "%~dp0posture_ai_engine.py"
