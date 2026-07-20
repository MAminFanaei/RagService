#!/bin/bash

# Exit immediately if any command fails, ensuring a failed backup stops the script.
set -e

echo "=================================================="
echo "Unified backup process started at $(date)"
echo "=================================================="

# --- General Configuration ---
KEEP_DAYS=14

# ==================================================
# 1. MySQL BACKUP
# ==================================================
echo "--- Starting MySQL Backup ---"
MYSQL_BACKUP_DIR="/backups/mysql"
mkdir -p $MYSQL_BACKUP_DIR

MYSQL_FILENAME="dump-$(date +'%Y-%m-%dT%H-%M-%S').sql.gz"
MYSQL_FILEPATH="$MYSQL_BACKUP_DIR/$MYSQL_FILENAME"

echo "Dumping database '$MYSQL_DATABASE' to $MYSQL_FILEPATH..."
mysqldump -h "$MYSQL_HOST" -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" | gzip -c > "$MYSQL_FILEPATH"
echo "MySQL backup successful."

echo "Cleaning up old MySQL backups..."
find "$MYSQL_BACKUP_DIR" -type f -name "*.sql.gz" -mtime +"$KEEP_DAYS" -exec rm -f {} \;
echo "MySQL cleanup complete."


# ==================================================
# 2. REDIS (VALKEY) BACKUP
# ==================================================
echo "" # Newline for readability
echo "--- Starting Redis Backup ---"
REDIS_SRC_DIR="/redis-data" # Mounted from redis_data volume
REDIS_BACKUP_DIR="/backups/redis"
mkdir -p $REDIS_BACKUP_DIR

REDIS_FILENAME="redis-data-$(date +'%Y-%m-%dT%H-%M-%S').tar.gz"
REDIS_FILEPATH="$REDIS_BACKUP_DIR/$REDIS_FILENAME"

echo "Archiving Redis data from $REDIS_SRC_DIR to $REDIS_FILEPATH..."
tar -czf "$REDIS_FILEPATH" -C "$REDIS_SRC_DIR" .
echo "Redis backup successful."

echo "Cleaning up old Redis backups..."
find "$REDIS_BACKUP_DIR" -type f -name "*.tar.gz" -mtime +"$KEEP_DAYS" -exec rm -f {} \;
echo "Redis cleanup complete."


# ==================================================
# 3. ELASTICSEARCH BACKUP (via Snapshot API)
# ==================================================
echo "" # Newline for readability
echo "--- Starting Elasticsearch Snapshot ---"
ES_REPO_NAME="daily_snapshots"
ES_REPO_PATH="/usr/share/elasticsearch/snapshots" # Must match path.repo in ES config
ES_URL="http://elastic:$ELASTICSEARCH_PASSWORD@$ELASTICSEARCH_HOST:$ELASTICSEARCH_PORT"

# Step 3.1: Register Snapshot Repository (this is idempotent, safe to run every time)
echo "Registering snapshot repository '$ES_REPO_NAME'..."
curl -s -X PUT "$ES_URL/_snapshot/$ES_REPO_NAME" -H 'Content-Type: application/json' -d'
{
  "type": "fs",
  "settings": {
    "location": "'"$ES_REPO_PATH"'"
  }
}
'

# Step 3.2: Create a new snapshot with a timestamp
SNAPSHOT_NAME="snapshot-$(date +'%Y-%m-%d-%H-%M-%S')"
echo "Creating snapshot: $SNAPSHOT_NAME"
curl -s -X PUT "$ES_URL/_snapshot/$ES_REPO_NAME/$SNAPSHOT_NAME?wait_for_completion=true"
echo "Snapshot creation complete."

# Step 3.3: Cleanup old snapshots
echo "Cleaning up old Elasticsearch snapshots..."
# Get a list of snapshots to delete (in JSON format, sorted by time)
SNAPSHOTS_TO_DELETE=$(curl -s -X GET "$ES_URL/_snapshot/$ES_REPO_NAME/_all" | jq -r ".snapshots[] | select(.start_time_in_millis < ($(date +%s) - $KEEP_DAYS * 24 * 60 * 60) * 1000) | .snapshot")

if [ -z "$SNAPSHOTS_TO_DELETE" ]; then
  echo "No old snapshots to delete."
else
  for SNAPSHOT in $SNAPSHOTS_TO_DELETE; do
    echo "Deleting old snapshot: $SNAPSHOT"
    curl -s -X DELETE "$ES_URL/_snapshot/$ES_REPO_NAME/$SNAPSHOT"
  done
fi
echo "Elasticsearch cleanup complete."


echo "=================================================="
echo "Unified backup process finished at $(date)"
echo "=================================================="