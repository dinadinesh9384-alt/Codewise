$env:PYTHONPATH="D:\Projects ai\CodeWiseV2\backend"
$env:DJANGO_SETTINGS_MODULE="config.settings"
$env:DJANGO_DEBUG="True"
& "D:\Projects ai\venv\Scripts\python.exe" -m unittest discover -s backend\agents\03_code_quality_analyst\tests -p "test_*.py" -v
