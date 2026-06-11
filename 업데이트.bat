@echo off
pushd "%~dp0"
echo 최신 코드를 받아오는 중...
git pull
if %errorlevel% == 0 (
    echo.
    echo ✅ 업데이트 완료! 이제 run.bat을 실행하세요.
) else (
    echo.
    echo ❌ 업데이트 실패. 인터넷 연결을 확인하거나 담당자에게 문의하세요.
)
echo.
pause
