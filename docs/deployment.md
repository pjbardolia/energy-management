# Deployment Documentation

## Server

DigitalOcean Ubuntu

## Containers

fastapi
postgres
portainer

## Docker Network

energy-management_default

## FastAPI

External Port: 8001
Internal Port: 8000

## Database

postgres:17

## Volume

energy-management_fastapi_data

## Nginx

Nginx (`nginx:alpine`) sits in front of everything on port 80.

```
Browser → Nginx :80
            ├── /api/*  →  proxy → api container :8000
            └── /*      →  serve frontend/dist/ (React SPA)
```

Config file: `nginx.conf` in the repo root (mounted as
`/etc/nginx/conf.d/default.conf`). The `/api/` location strips the prefix
before forwarding — FastAPI receives `/login`, not `/api/login`.

### Deploying a frontend change

The Nginx container serves `frontend/dist/` as a bind mount — those built
files must be committed and on the server. The MacBook is the build machine;
no Node.js is required on the droplet.

1. `cd frontend && npm run build`
2. `git add frontend/dist frontend/src/App.jsx nginx.conf docker-compose.yml`
3. `git commit && git push origin main`
4. On the server: `git pull origin main && docker compose up -d`
   (Nginx serves the static files directly — no container rebuild needed for
   frontend-only changes; rebuild `api` for backend changes.)

**Follow-ups not in scope:**
- HTTPS / TLS via Let's Encrypt. Also fixes the gateway currently POSTing
  telemetry over plain HTTP.
- A domain name. Raw IP (`http://165.22.247.235`) is used for now.

