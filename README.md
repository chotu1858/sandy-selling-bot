# Python Telegram Bot Host

## Deploy on a Linux VPS
1. Install Docker Engine and Docker Compose on a dedicated Ubuntu VPS.
2. Copy this folder to the VPS, then run `cp .env.example .env`.
3. Edit `.env`: use a long random `APP_SECRET` and a unique `ADMIN_PASSWORD` (14+ characters). Never publish `.env`.
4. Run `docker compose up -d --build`.
5. Configure a domain and HTTPS reverse proxy to `127.0.0.1:8080`; expose only ports 80/443. The Compose config sets secure cookies for HTTPS.
6. Sign in, upload `.py`, specify packages and environment variables, then click Start. Use Logs to troubleshoot.

## Important security warning
This is a self-hosted starter project, not an already-running cloud service. You need a Linux VPS and persistent storage for 24/7 runtime. The dashboard mounts the host Docker socket, which effectively grants host-root control. Run it only on a dedicated server, keep the OS/Docker patched, firewall access, and never let untrusted people upload code. Python files can perform arbitrary actions within the container's remaining permissions; containers are not a perfect security boundary. For true public multi-user hosting, use hardened VM/microVM isolation, separate workers, CSRF protection, audit logs and quotas.

Each bot is limited to 256 MiB RAM, 0.5 CPU and 64 processes. Root filesystem is read-only; temporary writes can use `/tmp`.
