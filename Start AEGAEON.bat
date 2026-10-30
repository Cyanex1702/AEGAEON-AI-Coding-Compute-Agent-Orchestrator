@echo off
cd /d "%~dp0"
echo AEGAEON Lite: http://127.0.0.1:8787
echo Open that address in your browser after the server starts.
echo Keep this window open while using the app. Press Ctrl+C to stop.
python -m aegaeon_lite serve --demo
pause
