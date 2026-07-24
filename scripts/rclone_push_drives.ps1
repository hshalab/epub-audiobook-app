<#
Pushes the local staging folders used by the app's Drive sync targets (codex5-codex10)
up to their Google Drive account via rclone. Google Drive for Desktop only allows 4
signed-in accounts, so these 6 accounts are synced via rclone instead of being mounted.

The app itself just copies exported patch/batch packages into the local staging folder
(same as it does for a Drive-Desktop-mounted folder) - this script is what actually
gets that folder's contents up to Google Drive.

Usage:
  .\rclone_push_drives.ps1            # sync all 6 remotes
  .\rclone_push_drives.ps1 codex7      # sync just one remote
#>
param(
    [string]$Only
)

$ErrorActionPreference = "Stop"

$targets = @(
    @{ Remote = "codex5";  Local = "D:\RcloneDriveStaging\codex5" },
    @{ Remote = "codex6";  Local = "D:\RcloneDriveStaging\codex6" },
    @{ Remote = "codex7";  Local = "D:\RcloneDriveStaging\codex7" },
    @{ Remote = "codex8";  Local = "D:\RcloneDriveStaging\codex8" },
    @{ Remote = "codex9";  Local = "D:\RcloneDriveStaging\codex9" },
    @{ Remote = "codex10"; Local = "D:\RcloneDriveStaging\codex10" }
    @{ Remote = "huytd"; Local = "D:\RcloneDriveStaging\huytd" }
)

foreach ($t in $targets) {
    if ($Only -and $t.Remote -ne $Only) { continue }
    Write-Host "=== $($t.Remote) ==="
    # `copy`, never `sync`: each export is already a uniquely-named/timestamped folder
    # (see app/drive_export.py), so nothing needs deleting on the remote side - and a
    # `sync` here would delete anything on the Drive account that isn't in the local
    # staging folder, which is destructive if that account has any other content.
    rclone copy $t.Local "$($t.Remote):EPUB Audiobook Exports" -v
}
