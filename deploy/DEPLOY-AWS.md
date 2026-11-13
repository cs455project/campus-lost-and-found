# Deploying TraceBack to AWS

The whole app runs on **one EC2 server** with Docker:

```
browser ──HTTPS──► Caddy ──► Next.js frontend
                        └──► /api/* ──► FastAPI backend ──► PostgreSQL
                                        worker (timed jobs + AI queue) ──┘
```

Photos, the database and the AI model are kept in Docker volumes on the
server, so they survive restarts and updates.

**Cost:** about US$22 a month before tax (server ~$16.35, 20 GB disk ~$1.82,
public IP ~$3.65). On a **new AWS account on the Free plan** this is paid from the
free credits ($100 at sign-up, up to $100 more for short console activities), and
`t3.small` is Free-tier eligible, so it costs nothing for up to 6 months. Choose
the **Free plan** at sign-up and never click "Upgrade". Stopping the instance
between demos cuts the cost to ~$5.50 a month (disk + IP), so the credits last
longer. When the project is over, **terminate** the instance and **release** the
Elastic IP.

---

## 1. Launch the server (AWS console, once)

1. Sign in at <https://console.aws.amazon.com>, pick region **Asia Pacific (Mumbai) ap-south-1** (top right).
2. **EC2 → Launch instance**
   - Name: `lostfound`
   - Image: **Ubuntu Server 24.04 LTS** (64-bit x86)
   - Instance type: **t3.small** (2 GB RAM; `t3.micro` is too small for the AI models + build)
   - Key pair: **Create new key pair** → name `lostfound-key`, type RSA, format **.pem** → it downloads. Keep it safe.
   - Network settings → allow **SSH** (choose *My IP*), **HTTPS** and **HTTP** from the internet.
   - Storage: **20 GB** gp3.
   - **Launch instance**.
3. (Recommended) **EC2 → Elastic IPs → Allocate**, then **Associate** it with the instance.
   Without this, the address changes every time the instance is stopped and started.
4. Copy the instance's **Public IPv4 address** (e.g. `13.233.45.67`).

## 2. Fix the key's permissions (Windows, once)

PowerShell, in the folder where the `.pem` downloaded:

```powershell
icacls .\lostfound-key.pem /inheritance:r /grant:r "$($env:USERNAME):R"
```

(Otherwise `ssh` refuses the key as "unprotected".)

## 3. Upload and start the app

From the project folder (`lost-found`), in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\upload.ps1 -Server 13.233.45.67 -Key C:\path\to\lostfound-key.pem
```

The first run installs Docker, creates `deploy/.env` on the server with random
passwords, builds everything (5–10 minutes) and prints the address, like
`https://13-233-45-67.sslip.io`. sslip.io is a free name that points at your
IP, so HTTPS works without buying a domain.

## 4. Finish the settings on the server

Connect:

```powershell
ssh -i C:\path\to\lostfound-key.pem ubuntu@13.233.45.67
```

Then:

```bash
cd ~/lost-found/deploy
nano .env           # set SMTP_USER / SMTP_PASSWORD, EMAIL_BACKEND=smtp,
                    # LLM_API_KEY, GEMINI_API_KEY (same as your local backend/.env)
sudo docker compose up -d        # apply the changes
sudo docker compose exec backend python -m app.cli make-admin your-email@gmail.com
```

While `EMAIL_BACKEND=console`, sign-in codes are only in the logs:
`sudo docker compose logs backend | tail -50`.

## Everyday commands (on the server, in `~/lost-found/deploy`)

| Task | Command |
|---|---|
| See what's running | `sudo docker compose ps` |
| Follow the logs | `sudo docker compose logs -f backend worker` |
| Restart everything | `sudo docker compose restart` |
| Stop the app | `sudo docker compose down` (data is kept) |
| Add sample items | `sudo docker compose exec backend python -m app.cli demo-items --count 30` |
| Back up the database | `sudo docker compose exec db pg_dump -U lostfound lostfound > backup.sql` |

## Updating after code changes

Run the same `upload.ps1` command again. It sends the new code, rebuilds, and
runs any new migrations on start. `deploy/.env` and all data are kept.

## Using your own domain

Point an **A record** for your domain at the Elastic IP, then in `deploy/.env` set
`SITE_ADDRESS=yourdomain.com` and `FRONTEND_ORIGIN=https://yourdomain.com`, and run
`sudo docker compose up -d`.

## If something goes wrong

- **Site doesn't load:** check the security group allows ports 80 and 443, then `sudo docker compose logs caddy`.
- **"Can't reach the server" on the site:** `sudo docker compose logs backend` (often a typo in `.env`).
- **Build killed / out of memory:** make sure the instance is `t3.small` or larger.
