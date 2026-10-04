# Deploying Alejandro Shroom Map

Same recipe as the Plant and Rock maps on the IONOS VPS (217.154.218.125, root, SSH key login). The code is
NOT a git checkout there (like the Plant and Rock maps): it is rsync'ed from a local clone of
https://github.com/drayac/AlejandroShroomMap to `/root/apps/AlejandroShroomMap/`. Snap Docker, so the
command is `docker-compose`. Frontend copied to `/var/www/html/alejandroshroommap/frontend/` (owned by
`www-data`, 755/644 - files come from the Mac as 600 and nginx then answers 403). One nginx site and
certificate per hostname. Never touch the other sites on that box.

Live since 2026-10-04: https://shroommap.217-154-218-125.sslip.io/ , API port **8012**.

## 1. First deploy (done 2026-10-04, for reference)

```bash
# from the Mac, in the parent folder
rsync -az --delete --exclude .env --exclude /data/ --exclude _to_delete/ --exclude __pycache__/ --exclude .DS_Store \
    AlejandroShroomMap/ root@217.154.218.125:/root/apps/AlejandroShroomMap/
# on the server, in /root/apps/AlejandroShroomMap
cp .env.example .env && chmod 600 .env   # POSTGRES_PASSWORD random, ADD_LEAF_PASSPHRASES, GEMINI_API_KEYS
                                         # (same shared key as the Plant Map), GEMINI_DAILY_LIMIT_PER_KEY=15
docker-compose up -d --build && curl http://127.0.0.1:8012/api/health
mkdir -p /var/www/html/alejandroshroommap && rsync -a --delete frontend/ /var/www/html/alejandroshroommap/frontend/
chown -R www-data:www-data /var/www/html/alejandroshroommap
find /var/www/html/alejandroshroommap -type d -exec chmod 755 {} + ; find /var/www/html/alejandroshroommap -type f -exec chmod 644 {} +
cp deploy/nginx.conf /etc/nginx/sites-available/alejandroshroommap
ln -s /etc/nginx/sites-available/alejandroshroommap /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d shroommap.217-154-218-125.sslip.io --redirect
```

## 2. Redeploy

Run the rsync above from the Mac, then on the server `cd /root/apps/AlejandroShroomMap && docker-compose up -d --build`
and the frontend rsync + chown/chmod lines. After editing `app.js`/`style.css`, bump `?v=N` in
`frontend/index.html`. Do not copy `deploy/nginx.conf` again: certbot added the TLS block to the live file.

## 3. Check

Open the hostname on a phone: add a mushroom with the photo, GPS and a Gemini identification; check the
gallery and the tree view. Back up `./data/postgres` and the `uploads_data` Docker volume.
