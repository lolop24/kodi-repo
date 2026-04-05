# TitDeepL LocalSub Helper

Remote HTTP helper for the `TitDeepL LocalSub` Kodi add-on. The add-on sends a translated Ukrainian `.srt` file to this helper, and the helper performs the browser upload to OpenSubtitles outside Kodi.

## What runs on LibreELEC

Use Docker on LibreELEC and run this helper container on the Raspberry Pi.

1. Copy this whole folder to the Raspberry, for example to `/storage/titdeepl-localsub-helper`.
2. Open SSH on the Raspberry and go to that folder.
3. Create the runtime config:

```sh
cp .env.example .env
nano .env
```

4. Fill at least:

```env
HELPER_TOKEN=change-me
OPENSUBTITLES_USERNAME=your_user
OPENSUBTITLES_PASSWORD=your_password
```

5. Start the helper:

```sh
chmod +x run-libreelec.sh
./run-libreelec.sh
```

If your LibreELEC image does not ship `docker compose`, use:

```sh
docker build -t titdeepl-localsub-helper .
docker run -d \
  --name titdeepl-localsub-helper \
  --restart unless-stopped \
  --env-file .env \
  -e HELPER_HOST=0.0.0.0 \
  -e HELPER_PORT=8097 \
  -e HELPER_DATA_DIR=/data \
  -p 8097:8097 \
  -v /storage/titdeepl-localsub-helper/data:/data \
  titdeepl-localsub-helper
```

## Host fallback for LibreELEC without Docker

The Raspberry in this setup may have no `docker` command installed. In that case you can run the helper directly on LibreELEC with the system Python:

```sh
cp .env.example .env
nano .env
chmod +x install-libreelec-host.sh
./install-libreelec-host.sh
```

This path creates `.venv` with `--without-pip`, bootstraps `pip` manually with `--no-compile`, installs Python packages, downloads Playwright Chromium, installs the bundled `titdeepl-localsub-helper.service` systemd unit, and starts it automatically.

Important:

- This host fallback forces `HELPER_HEADLESS=1` and `HELPER_USE_XVFB=0` in `.env`.
- On stock LibreELEC this is the most realistic non-Docker setup, but OpenSubtitles may still reject some headless browser sessions.
- Even if the upload fails, the received subtitles remain archived in `data/saved-subtitles/`.

## Kodi add-on settings

Set these in the `TitDeepL LocalSub` add-on:

- `Remote helper URL`: `http://IP_OF_YOUR_PI:8097`
- `Remote helper token`: the same value as `HELPER_TOKEN`
- `OpenSubtitles username` and `OpenSubtitles password`: optional override per request
- `Auto-upload after translation`: enabled
- `Auto-submit upload on helper`: enabled if you want fully automatic uploads

## Endpoints

- `GET /healthz`
- `POST /api/upload-jobs`
- `GET /api/upload-jobs/<job_id>`

## Notes

- The container installs Playwright Chromium and uses it by default.
- The default setup runs Chromium inside `xvfb`, which is better than headless for anti-bot pages.
- Job files, browser profile, screenshots, and logs are stored in the mounted `data/` folder.
- Every received subtitle is also archived in `data/saved-subtitles/`, even if the upload later fails.
- Set `HELPER_DRY_RUN=1` in `.env` if you want to test the queue without actually uploading.
