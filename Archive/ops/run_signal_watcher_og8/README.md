# SEN05 OG8 Signal Watcher Ops

This folder contains the OG8 Linux ops layer for running the realtime
`core_python.notify.signal_watcher` as a `systemd` service.

Target service:

```text
sen05-signal-watcher-combo-h4.service
```

Runtime shape:

```text
DP6 share     -> /srv/sen05
OG8 Redis     -> localhost / 10.11.12.8:6379
OG8 watcher   -> Combo H4 Indice
SQL Server    -> 10.11.12.6:1433 through FreeTDS
```

## 1. Install Persistent Mounts

Run this on OG8 after `/etc/sen05-dp6-share.cred` exists:

```bash
cd /srv/sen05
bash ops/run_signal_watcher_og8/install_mounts.sh
```

The script backs up `/etc/fstab`, adds idempotent DP6 share entries, and runs
`mount -a`. It does not store secrets; it reuses `/etc/sen05-dp6-share.cred`.

## 2. Install The Service

Run on OG8:

```bash
cd /srv/sen05
bash ops/run_signal_watcher_og8/install_signal_watcher_service.sh
```

The installer creates:

```text
/etc/sen05-signal-watcher.env
/etc/systemd/system/sen05-signal-watcher-combo-h4.service
```

It then enables and restarts the service.

## 3. Common Commands

```bash
sudo systemctl status sen05-signal-watcher-combo-h4.service --no-pager
sudo journalctl -u sen05-signal-watcher-combo-h4.service -f
sudo systemctl restart sen05-signal-watcher-combo-h4.service
sudo systemctl stop sen05-signal-watcher-combo-h4.service
```

## 4. Optional Periodic Restart

To make the watcher reload code changes from the DP6 share automatically, install
the daily restart timer:

```bash
cd /srv/sen05
bash ops/run_signal_watcher_og8/install_signal_watcher_restart_timer.sh
```

Default schedule:

```text
03:10 UTC every day
```

Override the schedule by passing a systemd `OnCalendar` expression:

```bash
bash ops/run_signal_watcher_og8/install_signal_watcher_restart_timer.sh "03:10,15:10"
```

Check timer status:

```bash
systemctl list-timers "sen05-signal-watcher-combo-h4*"
sudo journalctl -u sen05-signal-watcher-combo-h4-periodic-restart.service -n 50 --no-pager
```

## Notes

- Edit code on DP6/host as usual. OG8 sees it via `/srv/sen05`.
- Restart the service after code changes.
- The repo `.env` still carries Redis/Discord settings. The systemd env file only
  overrides Linux-specific SQL settings.
