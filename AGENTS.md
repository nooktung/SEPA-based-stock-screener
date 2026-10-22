# Project workflow preferences

## User-requested updates (2026-10-18)

When the user asks to update ("cập nhật", "chạy hôm nay", or equivalent), complete the entire workflow without asking for deployment confirmation again:

1. Run `python -u -m src.main start --update-only`. Respect the pipeline lock; wait for an existing update instead of starting concurrent writers.
2. Ensure news was actually fetched during this update, even if prices remain at the last closed trading session. If start skipped news due to its freshness interval, run `python -u -m src.main news` before exporting. Do not run news twice when start already collected it. News freshness follows the current collection time, independently of the stock-price session date.
3. Run `python -u -m src.main export-site --update`. Use its normal freshness and secret checks; do not bypass them with `--force` to publish stale data.
4. After export succeeds, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_site.ps1` to update the existing production Cloudflare Pages project.
5. Verify export/deployment succeeded and report the price session, news collection result, and publication status. If a source or deployment fails, retry a transient failure when appropriate and state remaining failures accurately; never claim the web is updated merely because the local database changed.

This is a standing workflow for user-triggered updates, not a request to create a recurring automation or run an update immediately when this preference is saved. A request explicitly scoped to local-only work overrides publication for that request.
