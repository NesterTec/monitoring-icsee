# NVR local ICSEE — MVP Design

**Date:** 2026-11-01  
**Status:** Approved for planning (pending user review of this file)  
**Project path:** `/home/nestertec/Documentos/projects/icsee`

## Goal

Local DVR-style system for ICSEE/Xiongmai cameras: admin login, camera registry with device credentials, continuous MP4 recording with a configurable disk quota (ring buffer), live view in the browser. Replaces reliance on the stock ICSEE app for day-to-day monitoring. Remote access (ngrok), PTZ, and two-way audio are explicitly out of MVP scope.

## Decisions locked

| Topic | Choice |
|-------|--------|
| Approach | Custom app + existing go2rtc + FFmpeg recorder |
| Recording mode | Continuous 24/7 |
| Quota | User-defined size (GB); must not exceed available disk |
| When over quota / disk pressure | Delete oldest segments until under limit |
| Default storage | `recordings/` under the project; changeable in admin |
| Segment format | MP4 (~5 minute segments), H.264 for universal playback |
| Admin login | Email `ivanrank@yahoo.com.br` (password set at bootstrap, stored hashed) |
| Network | Same LAN/Wi-Fi as cameras (required for RTSP) |

## Architecture

```text
Browser → App (API + UI) → SQLite
                ├→ go2rtc (live WebRTC/MSE)
                └→ FFmpeg recorder → recordings/ (configurable path)
                         ↑
                   ICSEE cameras (RTSP / device user+pass)
```

### Components

1. **App (FastAPI + simple web UI)**  
   - Session auth for admin  
   - CRUD cameras, settings, recording index, playback/download  
   - Generates/updates go2rtc stream config from DB  
   - Supervises recorder processes  

2. **go2rtc** (already running)  
   - Live only; streams named per camera  

3. **Recorder**  
   - One FFmpeg process per camera with recording enabled  
   - Reads RTSP; writes MP4 segments under storage path  
   - Reconnect with backoff on camera disconnect  

4. **SQLite**  
   - Users, cameras (encrypted device secrets), settings, recording segment metadata  

5. **Docker Compose**  
   - Services: `app`, `go2rtc` (and FFmpeg available to recorder)  
   - Volume for `recordings/` and SQLite data  

## Admin UI (MVP screens)

1. Login  
2. Dashboard — live grid (go2rtc embeds)  
3. Cameras — list/add/edit/delete, enable/disable recording, “test connection”  
4. Recordings — filter by camera/date, play MP4, download  
5. Settings — storage path, quota GB, show free disk space  

### Camera fields

- Friendly name  
- Host/IP  
- Device username + password  
- RTSP port (default 554)  
- Channel / stream indices (defaults suitable for ICSEE)  
- Recording enabled flag  

Device passwords are stored encrypted at rest (not plaintext in committed files). go2rtc.yaml (or equivalent runtime config) is generated from the database.

## Recording & storage

- Path layout: `{storage}/cameras/{camera_id}/YYYY-MM-DD/HHMMSS.mp4`  
- Default storage: `/home/nestertec/Documentos/projects/icsee/recordings`  
- Quota: configurable GB; validated against filesystem free space on the storage volume  
- Safety margin: also trigger oldest-first deletion if free disk falls below a small reserve (~1–2 GB) even when under quota  
- On storage path change: new recordings use the new path; old segments remain listed if still reachable  

## Auth

- Single admin user seeded on first boot: `ivanrank@yahoo.com.br`  
- Password provided by operator at setup; stored with a strong password hash (e.g. argon2/bcrypt)  
- All admin routes require authenticated session  

## Out of scope (phase 2+)

- Ngrok / Tailscale / remote access hardening  
- PTZ  
- Two-way audio  
- Motion detection / alerts  
- Multi-user roles  

## Success criteria

1. Admin can log in with the seeded email  
2. Admin can register a camera with device credentials and see live video  
3. Enabled cameras record continuously to MP4 under the configured path  
4. Quota enforcement deletes oldest segments and never intentionally fills the disk past available space  
5. Admin can play and download recordings from the panel  

## Known risks

- Each ICSEE camera may have distinct device credentials  
- H.265 sources may require transcode to H.264 (CPU cost)  
- Fiberhome AP isolation: PC must be on the same Wi-Fi as cameras for local RTSP  
- Machine must stay powered for 24/7 DVR behavior  

## Non-goals for this document

Implementation task breakdown (belongs in the implementation plan after this spec is approved).
