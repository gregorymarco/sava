# Sava Game - Production Deployment Guide

This guide explains how to deploy the Sava game application using Gunicorn for production.

## Prerequisites

- Python 3.11+ (recommended for best compatibility)
- pip package manager
- Virtual environment (recommended)

## Render.com Deployment

For easy deployment to Render.com:

### Build Settings
- **Build Command**: `./render_build.sh`
- **Start Command**: `./start_production.sh`
- **Python Version**: Use `runtime.txt` file (Python 3.11.9)

### Environment Variables in Render
Set these in your Render service settings:
- `SECRET_KEY`: Generate a secure random key
- `ALLOWED_ORIGINS`: Your domain (e.g., `https://yourdomain.onrender.com`)
- `LOG_LEVEL`: `info` (or `debug` for troubleshooting)

### Files for Render
- `runtime.txt`: Specifies Python version
- `render_build.sh`: Build script for Render
- `start_production.sh`: Start script for the application

## Installation

1. **Clone the repository and navigate to the project directory:**
   ```bash
   cd /path/to/sava
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. **Copy the example environment file:**
   ```bash
   cp env.example .env
   ```

2. **Edit the `.env` file with your production settings:**
   ```bash
   nano .env
   ```

   Important settings to configure:
   - `SECRET_KEY`: Generate a secure random key for production
   - `ALLOWED_ORIGINS`: Set specific domains for CORS (don't use * in production)
   - `PORT`: Set the port your application will run on
   - `GUNICORN_WORKERS`: Adjust based on your server resources

## Running in Production

### Option 1: Using the startup script (recommended)
```bash
./start_production.sh
```

### Option 2: Direct gunicorn command
```bash
gunicorn --config gunicorn.conf.py wsgi:application
```

### Option 3: Custom gunicorn command
```bash
gunicorn --workers 4 --worker-class eventlet --bind 0.0.0.0:5000 wsgi:application
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` | Flask environment mode |
| `SECRET_KEY` | Random | Flask secret key for sessions |
| `HOST` | `0.0.0.0` | Host to bind the server to |
| `PORT` | `5000` | Port to run the server on |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |
| `GUNICORN_WORKERS` | `auto` | Number of gunicorn worker processes |
| `LOG_LEVEL` | `info` | Logging level |

## SSL/HTTPS Configuration

To enable HTTPS, modify `gunicorn.conf.py`:

```python
keyfile = '/path/to/your/private.key'
certfile = '/path/to/your/certificate.crt'
```

## Process Management

For production deployment, consider using a process manager like:

### Systemd (Linux)
Create a service file at `/etc/systemd/system/sava-game.service`:

```ini
[Unit]
Description=Sava Game Application
After=network.target

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=/path/to/sava
Environment=PATH=/path/to/sava/venv/bin
EnvironmentFile=/path/to/sava/.env
ExecStart=/path/to/sava/venv/bin/gunicorn --config gunicorn.conf.py wsgi:application
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:
```bash
sudo systemctl enable sava-game.service
sudo systemctl start sava-game.service
```

### Supervisor
Install supervisor and create a configuration file:

```ini
[program:sava-game]
directory=/path/to/sava
command=/path/to/sava/venv/bin/gunicorn --config gunicorn.conf.py wsgi:application
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/sava-game.log
```

## Reverse Proxy (Nginx)

Example Nginx configuration:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support
    location /socket.io/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Monitoring and Logs

- Application logs are sent to stdout/stderr and can be captured by your process manager
- Gunicorn access logs are enabled by default
- Monitor the application with tools like htop, journalctl (systemd), or supervisor logs

## Scaling

To scale the application:

1. **Increase worker processes:** Modify `GUNICORN_WORKERS` environment variable
2. **Load balancing:** Use multiple application instances behind a load balancer
3. **Database:** Consider moving from in-memory storage to Redis or a database for session persistence

## Troubleshooting

### Common Issues

1. **Port already in use:**
   ```bash
   lsof -i :5000
   sudo kill -9 <PID>
   ```

2. **Permission denied:**
   - Ensure the application user has permission to bind to the port
   - Use ports > 1024 for non-root users

3. **WebSocket connection issues:**
   - Ensure your reverse proxy supports WebSocket upgrades
   - Check CORS settings in production

### Logs

Check application logs:
```bash
# If using systemd
sudo journalctl -u sava-game.service -f

# If using supervisor
tail -f /var/log/sava-game.log

# Direct gunicorn logs
./start_production.sh 2>&1 | tee app.log
```