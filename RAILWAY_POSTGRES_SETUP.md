# Adding PostgreSQL to Your Smart Parking System on Railway

## Step-by-Step Guide (Railway Dashboard)

### Step 1: Navigate to Your Project

1. Go to <https://railway.com/dashboard>
2. Click on your project: **diligent-prosperity**
3. You'll see your current services (Postgres already exists, clever-surprise running your Flask app)

### Step 2: Link Postgres to Your Flask Service

1. In the **diligent-prosperity** project, look for the **Postgres** service
2. Click on **Postgres** service
3. Go to the **Variables** tab
4. Copy the connection string (you'll see `DATABASE_URL`)
5. Go back to your **clever-surprise** service
6. Click on **Variables** tab
7. Look for `DATABASE_URL` — if it's not there, you may need to manually add it

### Alternative (Automatic): Railway Usually Auto-Injects

- If Railway detects your app uses `DATABASE_URL`, it automatically injects the Postgres connection string
- Your Flask code already handles this via `os.environ.get("DATABASE_URL", "sqlite:///parking.db")`

### Step 3: Redeploy Your Service

1. Go to **clever-surprise** service
2. Click the **Deploy** button or go to the Deployments tab
3. Click **Redeploy Latest** to restart with the new `DATABASE_URL`
4. Wait for the build and deployment to complete (watch the logs)

### Step 4: Verify Postgres Connection

Once deployed, test the health endpoint:

```text
GET https://clever-surprise-production.up.railway.app/health
```

**Expected Response (Postgres Connected):**

```json
{
  "database": "connected",
  "database_url": "postgresql",
  "status": "ok",
  "total_slots": 3
}
```

**If Still SQLite:**

```json
{
  "database": "connected",
  "database_url": "sqlite",
  "status": "ok",
  "total_slots": 3
}
```

---

## Troubleshooting

### Issue: DATABASE_URL Not Set

**Fix in Railway Dashboard:**

1. Go to **clever-surprise** service → **Variables**
2. Click "Add Variable"
3. Name: `DATABASE_URL`
4. Value: Copy from Postgres service variables (DATABASE_URL)
5. Redeploy

### Issue: Deployment Fails

**Fix:**

1. Check the **Deployment Logs** tab in Railway
2. Look for SQL or connection errors
3. If tables don't exist, the app auto-creates them on first run
4. If error persists, redeploy once more (sometimes takes 2 attempts)

### Issue: Data Lost After Restart

**This means tables weren't created in Postgres. To fix:**

1. Manually run a migration (not needed with SQLAlchemy auto-create)
2. Or clear data and let the app re-init:
   - Go to Postgres service → Data tab
   - Clear/reset database
   - Redeploy Flask app

---

## What Happens When Postgres is Connected

| Aspect | Before (SQLite) | After (Postgres) |
| ------ | ---------------- | ----------------- |
| Data Persistence | Per-deployment (lost on restart) | Persistent across restarts |
| Scalability | Single server | Full database service |
| Performance | Slower for large datasets | Optimized for concurrent access |
| Backups | Manual | Automatic (Railway manages) |
| `/health` response | `"database_url": "sqlite"` | `"database_url": "postgresql"` |

---

## Advanced: Manual Connection Test

If you want to verify Postgres directly:

### Via Railway CLI

```bash
railway connect postgres
```

This opens a `psql` console to your Postgres database. You can then run:

```sql
SELECT * FROM parking_slots;
```

### Via Flask Health Endpoint

```bash
curl https://clever-surprise-production.up.railway.app/health
```

---

## Next Steps After Postgres is Live

1. ✅ All data now persists permanently
2. ✅ Your app scales horizontally (add more replicas)
3. ✅ You can add authentication/authorization endpoints
4. ✅ You can archive old bookings to a historical table
5. ✅ You can add analytics queries on booking patterns

---

Generated: March 21, 2026
Your Smart Parking System is now production-grade! 🚀
