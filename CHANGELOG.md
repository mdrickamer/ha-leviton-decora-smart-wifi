# Changelog

## 2.3.0

Reliability release based on upstream 2.2.2. The domain is unchanged (`leviton_decora_smart_wifi`), so an existing config entry loads in place.

### Authentication
- A failed login during setup or polling raises `ConfigEntryAuthFailed`. Home Assistant stops retrying the bad login and opens a reauthentication flow.
- Reauthentication asks for the password and, when the account requires it, a new 2FA code.
- A bearer refreshed in the background is written back to the config entry, so a restart does not immediately log in again.
- The one-time 2FA code is not stored and is not sent on an automatic re-login. Accounts with 2FA need the reauthentication prompt after the bearer expires. That is intentional: replaying the old code only counts toward Leviton's lockout.

### Startup and cloud errors
- Setup uses `async_config_entry_first_refresh`. A timeout, connection error, or HTTP 5xx at startup raises `ConfigEntryNotReady` and Home Assistant retries. There is no grace period on that first refresh, because there is no last good snapshot yet.
- The same faults during a later poll keep the last good data for two consecutive failures. Entities stay available with that state. The third consecutive failure raises `UpdateFailed` and entities go unavailable until a poll succeeds. Any successful poll resets the counter. An authentication failure does not use this grace period.
- The first transient failure is logged at warning. Further ones are debug, so a long outage does not log an error on every poll.

### Devices, secrets, and small fixes
- The device page can delete a device only when the My Leviton account no longer reports it.
- Migration no longer debug-logs the config entry. The "save responses" option redacts login tokens and passwords before writing files. Diagnostics redact email, username, password, token, 2FA code, serial, MAC, and account ids.
- Login errors use the `login_*` keys already translated in `strings.json`.
- The options flow no longer stores `config_entry` itself (that assignment raises `TypeError` on current Home Assistant) and opens when the framework calls `async_step_init(None)`.
- Device entity unique ids that are already in the registry are kept, including MAC-based ids from 2.2.2. If the MAC is missing or changes, the existing registry entry for that device is reused instead of minting a new id. Entities created from this version on use `{residence_id}-{device_id}-{key}` and do not include the MAC. Device registry identifiers were already the Leviton device id and are not changed.

### Not in this release
- `requests.Session` calls are serialized with a lock. A per-command refresh that would avoid reloading the whole account after each service call was left as-is; state still updates on the next poll or websocket push.
