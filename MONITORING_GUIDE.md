# Smart Parking System - Monitoring & Health Endpoints

## Overview

Your app now has two monitoring endpoints to track health and performance:

1. **`GET /health`** — Simple health check
2. **`GET /metrics`** — Advanced monitoring with pool stats

---

## Live Endpoints

### Health Endpoint
```
GET https://clever-surprise-production.up.railway.app/health
```

**Current Response (SQLite):**
```json
{
  "database": "connected",
  "database_url": "sqlite",
  "status": "ok",
  "total_slots": 3
}
```

**After Postgres is Added:**
```json
{
  "database": "connected",
  "database_url": "postgresql",
  "status": "ok",
  "total_slots": 3
}
```

### Metrics Endpoint
```
GET https://clever-surprise-production.up.railway.app/metrics
```

**Current Response (SQLite):**
```json
{
  "service": "smart-parking-system",
  "database_type": "sqlite",
  "db_pool": {},
  "db_version": "unknown",
  "slots_summary": {
    "total": 3,
    "free": 2,
    "occupied": 1,
    "availability_percentage": 66.67
  }
}
```

**After Postgres is Added:**
```json
{
  "service": "smart-parking-system",
  "database_type": "postgresql",
  "db_pool": {
    "checked_out": 0,
    "total_size": 10,
    "overflow": 0
  },
  "db_version": "PostgreSQL 15.x on...",
  "slots_summary": {
    "total": 3,
    "free": 2,
    "occupied": 1,
    "availability_percentage": 66.67
  }
}
```

---

## What the Metrics Tell You

| Metric | Meaning | Good Range |
|--------|---------|------------|
| `availability_percentage` | Percentage of free spots | > 20% for normal operation |
| `checked_out` | Active DB connections in use | < pool_size (usually < 10) |
| `total_size` | Max pooled connections | 10-20 for most apps |
| `overflow` | Connections beyond pool size | 0-5 (shouldn't spike) |
| `database_type` | Current DB engine | `postgresql` or `sqlite` |

---

## Monitoring Setup (Post-Postgres)

### Option 1: Manual Monitoring
Check the endpoints manually in your browser:
- `https://clever-surprise-production.up.railway.app/health` every 5 minutes
- `https://clever-surprise-production.up.railway.app/metrics` for detailed stats

### Option 2: Uptime Monitoring (Recommended)
Use a free service like **UptimeRobot**:
1. Go to https://uptimerobot.com
2. Sign up (free)
3. Add monitor: `https://clever-surprise-production.up.railway.app/health`
4. Set interval: 5 minutes
5. Get alerts if the endpoint returns non-200 status

### Option 3: Railway Built-in Monitoring
In your Railway dashboard:
1. Go to your `clever-surprise` service
2. Click **Monitoring** tab
3. View CPU, memory, and networking in real-time

---

## What Happens Next (After Adding Postgres)

### Before (SQLite - Current):
- Data resets on each restart
- `db_pool` is empty (no pooling for SQLite)
- `availability_percentage` shows real-time state

### After (Postgres - Next Step):
- Data persists across restarts
- `db_pool` shows active connections (10 available)
- Better performance for concurrent requests
- Automatic daily backups by Railway

---

## Quick Test Commands

### Test Health (Bash/PowerShell):
```bash
curl https://clever-surprise-production.up.railway.app/health
```

### Test Metrics:
```bash
curl https://clever-surprise-production.up.railway.app/metrics
```

### Check if Postgres Connected:
```bash
curl https://clever-surprise-production.up.railway.app/metrics | grep "database_type"
```

---

## Troubleshooting

### Issue: `/metrics` returns 404
**Fix:** Wait 2-3 minutes for Railway to finish deploying the latest code.

### Issue: `/health` shows "degraded"
**Fix:** 
- Check Railway logs for database errors
- Verify `DATABASE_URL` environment variable is set
- Redeploy: `railway up`

### Issue: `db_pool` shows very high `checked_out`
**Warning:** Too many simultaneous connections. Consider:
- Reducing concurrent requests
- Increasing `pool_size` in `app.py`
- Adding a load balancer

---

## Next Steps

1. ✅ **Monitor SQLite** - Use `/health` and `/metrics` today
2. 📋 **Follow RAILWAY_POSTGRES_SETUP.md** - Add Postgres to your project
3. 🔄 **Redeploy** - After adding Postgres, run `railway up`
4. 🎯 **Verify Postgres** - Check that `database_type` changes to `postgresql`
5. 📊 **Set Up UptimeRobot** - For 24/7 monitoring

---

Generated: March 21, 2026
Your Smart Parking System Backend is now monitored! 📈
