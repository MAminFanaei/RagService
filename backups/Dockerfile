FROM alpine:3.18

# --- FIX for Network Issues ---
# The default Alpine CDN can sometimes be blocked by firewalls/DNS.
# We are changing the repository source to a known good mirror *before* trying to install anything.
# RUN sed -i 's/dl-cdn.alpinelinux.org/alpine.mirror.arNES.si/g' /etc/apk/repositories

# Install all necessary dependencies in one go:
# - mysql-client: for mysqldump
# - curl & jq: for Elasticsearch API interaction
# - tar: for packaging Redis data
# - cron, openrc, bash: for scheduling and scripting
RUN apk add --no-cache mysql-client bash openrc cron curl jq tar

# Copy our master backup script and crontab
COPY backup.sh /usr/local/bin/backup.sh
COPY crontab /etc/crontabs/root

# Make the script executable
RUN chmod +x /usr/local/bin/backup.sh

# Start the cron daemon
CMD ["crond", "-f", "-d", "8"]