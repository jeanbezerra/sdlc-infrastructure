@echo off
setlocal
pushd "%~dp0.."
uv run --project installer --locked python -m sdlc_installer
set "sdlc_installer_exit_code=%errorlevel%"
popd
if not "%sdlc_installer_exit_code%"=="0" pause
exit /b %sdlc_installer_exit_code%
