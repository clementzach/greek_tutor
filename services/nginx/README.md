Nginx Example

Files
- greek-tutor.conf: Example HTTPS reverse proxy for the Flask app and optional /api proxy to FastAPI.

Steps
1) Install Nginx (varies by distro)
2) Copy config and enable
   sudo cp services/nginx/greek-tutor.conf /etc/nginx/sites-available/greek-tutor
   sudo ln -s /etc/nginx/sites-available/greek-tutor /etc/nginx/sites-enabled/greek-tutor
3) Edit config
   - Replace server_name with your domain(s)
   - Update TLS cert paths (e.g., from Certbot/Let’s Encrypt)
   - Update static alias path (/opt/greek_tutor/static/) to match your deployment path
4) Test and reload
   sudo nginx -t
   sudo systemctl reload nginx

TLS
- Use Certbot to obtain certificates, e.g.:
  sudo certbot --nginx -d example.com -d www.example.com

API Exposure
- The FastAPI service is bound to 127.0.0.1. By default, the config proxies /api/ to it.
- If you don’t want to expose the API publicly, comment out the /api/ location block or restrict:
  allow 127.0.0.1; deny all;

