@echo off
rem This script automates the versioning and building of the Docker image on Windows.

rem --- Configuration ---
set "IMAGE_NAME=fentanest/safetyreport"
set "VERSION_FILE=VERSION"

rem --- Version Handling ---
rem Check if VERSION file exists, if not, create it with a default version
if not exist "%VERSION_FILE%" (
    echo 1.0.0 > "%VERSION_FILE%"
)

rem Read the current version
set /p CURRENT_VERSION=<"%VERSION_FILE%"

rem Increment the patch version (e.g., 1.0.0 -> 1.0.1)
for /f "tokens=1-3 delims=." %%a in ("%CURRENT_VERSION%") do (
    set "major=%%a"
    set "minor=%%b"
    set "patch=%%c"
)
set /a "patch+=1"
set "NEW_VERSION=%major%.%minor%.%patch%"

echo Current version: %CURRENT_VERSION%
echo New version: %NEW_VERSION%

rem --- Docker Build ---
echo Building and pushing Docker image with tags: latest, %NEW_VERSION%

docker buildx build --platform linux/amd64,linux/arm64 ^
  -t "%IMAGE_NAME%:latest" ^
  -t "%IMAGE_NAME%:%NEW_VERSION%" ^
  --push ^
  .

rem Check if the build was successful
if %ERRORLEVEL% equ 0 (
    echo Docker image built and pushed successfully.
    rem Update the version file with the new version
    echo %NEW_VERSION% > "%VERSION_FILE%"
    echo Version updated to %NEW_VERSION%
) else (
    echo Error: Docker build failed.
    exit /b 1
)
