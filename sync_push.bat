@echo off
:: sync_daily_picks.bat - 同步 daily_picks.json 到 vibe-dashboard 并 push
cd /d C:\Users\china\.qclaw\workspace\vibe-dashboard
copy /Y ..\daily_picks.json daily_picks.json
git add daily_picks.json
git commit -m "sync daily_picks from workspace"
git push origin main
