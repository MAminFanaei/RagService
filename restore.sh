#!/bin/bash

# --- The Restore Wizard ---
# This script provides a simple, interactive menu to restore data from your backups.
# It handles all the complex Docker commands for you.

# Exit immediately if a command fails
set -e

# --- Configuration ---
# Docker Compose uses the directory name for the project name by default.
# This is used to find the correct volume names (e.g., my-project_backups).
PROJECT_NAME=$(basename "$(pwd)")
BACKUP_VOLUME="${PROJECT_NAME}_backups"
REDIS_VOLUME="${PROJECT_NAME}_redis_data"

# Function to display a scary "are you sure" prompt
confirm_action() {
    echo ""
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!! WARNING: This will PERMANENTLY OVERWRITE existing data.  !!"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    read -p "Type 'YES' to proceed: " confirmation
    if [ "$confirmation" != "YES" ]; then
        echo "Restore cancelled."
        exit 1
    fi
}

# --- Restore Functions ---

restore_mysql() {
    echo "--- MySQL Restore ---"
    
    # 1. Get a list of available backups
    echo "Fetching available MySQL backups..."
    mapfile -t backups < <(docker run --rm -v "$BACKUP_VOLUME":/backups alpine ls /backups/mysql/ | sort -r)

    if [ ${#backups[@]} -eq 0 ]; then
        echo "No MySQL backups found in volume '$BACKUP_VOLUME'."
        return
    fi

    # 2. Present the menu to the user
    echo "Please select a MySQL backup to restore:"
    PS3="Enter number: "
    select backup_file in "${backups[@]}"; do
        if [[ -n $backup_file ]]; then
            break
        else
            echo "Invalid selection."
        fi
    done

    # 3. Confirm and execute
    echo "You have chosen to restore: $backup_file"
    confirm_action

    echo "Stopping application service..."
    docker-compose stop app

    echo "Restoring MySQL database... This may take a while."
    # Copy the backup file from the volume to the mysql container and execute the restore
    docker run --rm -v "$BACKUP_VOLUME":/backups alpine cat "/backups/mysql/$backup_file" | \
    gzip -dc | \
    docker-compose exec -T mysql mysql -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" "${MYSQL_DATABASE}"

    echo "✅ MySQL restore complete!"
    echo "You can now restart your application: docker-compose up -d app"
}

restore_redis() {
    echo "--- Redis (Valkey) Restore ---"

    # 1. Get list of backups
    echo "Fetching available Redis backups..."
    mapfile -t backups < <(docker run --rm -v "$BACKUP_VOLUME":/backups alpine ls /backups/redis/ | sort -r)

    if [ ${#backups[@]} -eq 0 ]; then
        echo "No Redis backups found in volume '$BACKUP_VOLUME'."
        return
    fi

    # 2. Present the menu
    echo "Please select a Redis backup to restore:"
    PS3="Enter number: "
    select backup_file in "${backups[@]}"; do
        if [[ -n $backup_file ]]; then
            break
        else
            echo "Invalid selection."
        fi
    done

    # 3. Confirm and execute
    echo "You have chosen to restore: $backup_file"
    confirm_action

    echo "Stopping Redis and application services..."
    docker-compose stop redis app

    echo "Wiping current Redis data volume..."
    docker volume rm "$REDIS_VOLUME" > /dev/null
    docker volume create "$REDIS_VOLUME" > /dev/null

    echo "Restoring Redis data from backup..."
    # Use a temporary container to untar the backup into the new empty volume
    docker run --rm -v "$BACKUP_VOLUME":/backups -v "$REDIS_VOLUME":/data alpine \
    tar -xzf "/backups/redis/$backup_file" -C /data

    echo "✅ Redis restore complete!"
    echo "You can now restart your services: docker-compose up -d redis app"
}

restore_elasticsearch() {
    echo "--- Elasticsearch Restore ---"
    
    # 1. Get list of snapshots from the API
    echo "Fetching available Elasticsearch snapshots..."
    # We use jq to parse the JSON and get just the snapshot names
    mapfile -t snapshots < <(curl -s -X GET "http://localhost:9200/_snapshot/daily_snapshots/_all" -u elastic:"${ELASTICSEARCH_PASSWORD}" | jq -r '.snapshots[].snapshot' | sort -r)

    if [ ${#snapshots[@]} -eq 0 ]; then
        echo "No Elasticsearch snapshots found."
        return
    fi
    
    # 2. Present the menu
    echo "Please select an Elasticsearch snapshot to restore:"
    PS3="Enter number: "
    select snapshot_name in "${snapshots[@]}"; do
        if [[ -n $snapshot_name ]]; then
            break
        else
            echo "Invalid selection."
        fi
    done

    # 3. Confirm and execute
    echo "You have chosen to restore: $snapshot_name"
    confirm_action

    echo "Stopping application service..."
    docker-compose stop app
    
    echo "Closing all indices before restore..."
    curl -s -X POST "http://localhost:9200/_all/_close" -u elastic:"${ELASTICSEARCH_PASSWORD}" > /dev/null

    echo "Starting restore process (this runs in the background)..."
    curl -s -X POST "http://localhost:9200/_snapshot/daily_snapshots/$snapshot_name/_restore" -u elastic:"${ELASTICSEARCH_PASSWORD}"
    
    echo "✅ Elasticsearch restore initiated!"
    echo "Monitor progress with: curl -X GET \"http://localhost:9200/_cat/recovery?v\" -u elastic:\${ELASTICSEARCH_PASSWORD}"
    echo "Once complete, re-open indices with: curl -X POST \"http://localhost:9200/_all/_open\" -u elastic:\${ELASTICSEARCH_PASSWORD}"
    echo "Then restart your app: docker-compose up -d app"
}


# --- Main Menu ---
echo "=============================="
echo "  Data Restore Wizard"
echo "=============================="
PS3="What would you like to restore? "
options=("MySQL" "Redis (Valkey)" "Elasticsearch" "Exit")
select opt in "${options[@]}"; do
    case $opt in
        "MySQL")
            restore_mysql
            break
            ;;
        "Redis (Valkey)")
            restore_redis
            break
            ;;
        "Elasticsearch")
            restore_elasticsearch
            break
            ;;
        "Exit")
            break
            ;;
        *) echo "Invalid option $REPLY";;
    esac
done