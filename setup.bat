@echo off
pushd "%~dp0"
echo ================================================
echo  쿠팡 아이템위너 모니터링 — 초기 설치
echo ================================================
echo.

:: Python 설치 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo 아래 주소에서 Python을 설치해주세요.
    echo https://www.python.org/downloads
    echo.
    echo 설치 시 "Add Python to PATH" 체크 필수!
    pause
    exit /b 1
)

echo [1/3] Python 확인 완료
python --version
echo.

:: setuptools 먼저 설치 (Python 3.12+ distutils 오류 방지)
echo [2/3] 기본 패키지 설치 중...
pip install --upgrade setuptools pip >nul 2>&1
echo     완료
echo.

:: 나머지 패키지 설치
echo [3/3] 필요 패키지 설치 중... (1~3분 소요)
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [오류] 패키지 설치 실패. 화면을 캡처해서 담당자에게 보내주세요.
    pause
    exit /b 1
)

echo.
echo ================================================
echo  설치 완료! 이제 run.bat을 실행하세요.
echo ================================================
echo.
pause
