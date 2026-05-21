#!/bin/bash
# Nova Control Hub — SSH reverse tunnel to GCP VM
# Forwards local port 8766 → VM localhost:8766 → nginx → public
#
# Usage: ./scripts/tunnel.sh [start|stop|status]

VM_IP="136.115.212.16"
VM_USER="escipion"
SSH_KEY="$HOME/.ssh/google_compute_engine"
LOCAL_PORT=8766
REMOTE_PORT=8766
PIDFILE="/tmp/nova-tunnel.pid"
LOGFILE="/tmp/nova-tunnel.log"

# SSH options matching gcloud's config
SSH_OPTS="-o CheckHostIP=no -o HashKnownHosts=no -o HostKeyAlias=compute.8555829629827593975 -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=$HOME/.ssh/google_compute_known_hosts -o ServerAliveInterval=30 -o ServerAliveCountMax=3"

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Tunnel already running (PID $(cat "$PIDFILE"))"
        return 0
    fi

    echo "Starting Nova tunnel → $VM_IP..."
    AUTOSSH_PIDFILE="$PIDFILE" \
    AUTOSSH_LOGFILE="$LOGFILE" \
    AUTOSSH_GATETIME=0 \
    autossh -M 0 -f \
        -N \
        -R "127.0.0.1:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" \
        -i "$SSH_KEY" \
        $SSH_OPTS \
        "${VM_USER}@${VM_IP}"

    sleep 2
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "✅ Tunnel started (PID $(cat "$PIDFILE"))"
        echo "🌐 Nova Control Hub: http://${VM_IP}"
    else
        echo "❌ Tunnel failed to start. Check $LOGFILE"
        return 1
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm -f "$PIDFILE"
            echo "✅ Tunnel stopped (was PID $PID)"
        else
            rm -f "$PIDFILE"
            echo "Tunnel was not running (stale PID file removed)"
        fi
    else
        echo "No tunnel PID file found"
        # Try to find and kill anyway
        pkill -f "autossh.*${VM_IP}.*${REMOTE_PORT}" 2>/dev/null && echo "Killed orphan tunnel" || echo "No tunnel running"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "✅ Tunnel running (PID $(cat "$PIDFILE"))"
        echo "🌐 Public URL: http://${VM_IP}"

        # Check if local Control Hub is running
        if curl -s --max-time 2 http://localhost:${LOCAL_PORT}/api/status >/dev/null 2>&1; then
            echo "✅ Local Control Hub: running"
        else
            echo "⚠️  Local Control Hub: not running (start Nova first)"
        fi

        # Check if public endpoint works
        if curl -s --max-time 5 http://${VM_IP}/api/status >/dev/null 2>&1; then
            echo "✅ Public endpoint: reachable"
        else
            echo "⚠️  Public endpoint: not reachable (Control Hub may not be running)"
        fi
    else
        echo "❌ Tunnel not running"
    fi
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    *)      echo "Usage: $0 {start|stop|status}" ;;
esac
