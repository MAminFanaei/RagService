# Deployment Checklist

## Pre-Deployment

### 1. Environment Configuration
- [ ] Copy `.env.example` to `.env`
- [ ] Generate strong `SECRET_KEY` (32+ characters): `openssl rand -hex 32`
- [ ] Set secure `ADMIN_PASSWORD`
- [ ] Add `GEMINI_API_KEY`
- [ ] Configure database passwords
- [ ] Set `DEBUG=false` for production
- [ ] Update `CORS_ORIGINS` with your frontend URLs

### 2. OAuth Setup (Optional)
- [ ] Create Google OAuth credentials (see OAUTH_SETUP.md)
- [ ] Create GitHub OAuth app (see OAUTH_SETUP.md)
- [ ] Add production callback URLs
- [ ] Test OAuth flow in development

### 3. RAG Pipeline
- [ ] Download embedding models to `./models/`
- [ ] Prepare your documents in `./docs/` (JSON format)
- [ ] Update `app/core/rag_engine.py` to load your documents (line 82)
- [ ] Test RAG queries locally

### 4. Database Setup
- [ ] Ensure MySQL container is healthy
- [ ] Run migrations: `docker-compose exec app alembic upgrade head`
- [ ] Verify admin user creation in logs
- [ ] Test database connection

### 5. Services Health Check
- [ ] MySQL: `docker-compose logs mysql`
- [ ] Redis: `docker-compose logs redis`
- [ ] Elasticsearch: `curl -u elastic:password http://localhost:9200/_cluster/health`
- [ ] Application: `curl http://localhost:8000/health`

---

## Testing Checklist

### Run Test Script
```bash
chmod +x test_api.sh
./test_api.sh
```

### Manual Tests
- [ ] Register new user
- [ ] Login with email/password
- [ ] Login with username/password
- [ ] Create chat session
- [ ] Send RAG query
- [ ] Update chat title
- [ ] Soft delete chat
- [ ] Restore deleted chat
- [ ] Test rate limiting (send 100+ requests)
- [ ] Test daily quota
- [ ] Admin login
- [ ] View system stats (admin)
- [ ] Update user limits (admin)
- [ ] Export user conversations (admin)

### OAuth Tests (if enabled)
- [ ] Google OAuth login
- [ ] GitHub OAuth login
- [ ] OAuth with existing email (should fail)
- [ ] OAuth callback works correctly

### Load Testing
- [ ] Test with 10 concurrent users
- [ ] Test with 100 concurrent requests
- [ ] Monitor memory usage
- [ ] Check database connection pooling
- [ ] Verify Redis caching works

---

## Production Deployment

### Infrastructure
- [ ] Set up reverse proxy (Nginx/Caddy)
- [ ] Configure SSL/TLS certificates
- [ ] Set up domain DNS records
- [ ] Configure firewall rules
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Configure log aggregation (ELK/Loki)
- [ ] Set up database backups
- [ ] Configure auto-scaling (if needed)

### Security Hardening
- [ ] Change all default passwords
- [ ] Restrict database access to application only
- [ ] Enable Elasticsearch security
- [ ] Set up Redis password
- [ ] Configure CORS properly
- [ ] Enable rate limiting
- [ ] Set up DDoS protection
- [ ] Configure security headers
- [ ] Enable HTTPS only
- [ ] Implement API key rotation

### Monitoring Setup
- [ ] Application health endpoint
- [ ] Database connection monitoring
- [ ] Redis connection monitoring
- [ ] Elasticsearch cluster health
- [ ] RAG query latency tracking
- [ ] Error rate monitoring
- [ ] Rate limit hit tracking
- [ ] User activity logs

### Docker Production Configuration

Create `docker-compose.prod.yml`:
```yaml
version: '3.8'

services:
  app:
    image: rag-service:latest
    restart: always
    environment:
      - DEBUG=false
      - WORKERS=4
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2'
          memory: 4G
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  mysql:
    restart: always
    environment:
      - MYSQL_ROOT_PASSWORD=${STRONG_ROOT_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G

  redis:
    restart: always
    command: redis-server --requirepass ${REDIS_PASSWORD}
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 2G

  elasticsearch:
    restart: always
    environment:
      - "ES_JAVA_OPTS=-Xms4g -Xmx4g"
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
```

### Nginx Configuration

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req zone=api burst=20 nodelay;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts for long RAG queries
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

---

## Post-Deployment

### Immediate Checks (First 24 hours)
- [ ] Monitor error logs
- [ ] Check memory/CPU usage
- [ ] Verify all endpoints responding
- [ ] Test OAuth flows
- [ ] Check database performance
- [ ] Monitor RAG query times
- [ ] Verify rate limiting working
- [ ] Check SSL certificate validity

### Week 1 Monitoring
- [ ] Review user registration patterns
- [ ] Check for unusual API usage
- [ ] Monitor database growth
- [ ] Review error rates
- [ ] Check RAG accuracy
- [ ] Verify backups working
- [ ] Test disaster recovery

### Ongoing Maintenance
- [ ] Weekly log review
- [ ] Monthly security updates
- [ ] Quarterly dependency updates
- [ ] Regular backup testing
- [ ] Performance optimization
- [ ] User feedback review

---

## Rollback Plan

### If Deployment Fails:
1. Check logs: `docker-compose logs app`
2. Verify environment variables: `docker-compose exec app env`
3. Check database: `docker-compose exec mysql mysql -u root -p`
4. Rollback to previous version:
   ```bash
   docker-compose down
   git checkout previous-stable-tag
   docker-compose up -d
   ```

### If Database Migration Fails:
```bash
# Rollback migration
docker-compose exec app alembic downgrade -1

# Check migration history
docker-compose exec app alembic history

# Fix migration and retry
docker-compose exec app alembic upgrade head
```

---

## Performance Optimization

### Database Tuning
```sql
-- Add indexes for common queries
CREATE INDEX idx_messages_chat_created ON messages(chat_session_id, created_at);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_last_login ON users(last_login_at);
```

### Redis Configuration
```redis
# redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru
```

### Application Tuning
```bash
# .env for production
WORKERS=4  # 2 * CPU cores
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=40
```

---

## Support & Maintenance

### Backup Strategy
- **Database**: Daily automated backups, 30-day retention
- **Elasticsearch**: Weekly snapshots
- **Environment files**: Secure encrypted storage
- **Logs**: 90-day retention

### Monitoring Alerts
- [ ] CPU usage > 80%
- [ ] Memory usage > 90%
- [ ] Disk usage > 85%
- [ ] Error rate > 1%
- [ ] API response time > 5s
- [ ] Database connections > 90%
- [ ] Failed login attempts > 10/min

### Contact
- **Critical Issues**: Page on-call engineer
- **General Issues**: Create GitHub issue
- **Security**: security@yourdomain.com

---

## Success Criteria

Deployment is successful when:
- [ ] All health checks passing
- [ ] < 0.1% error rate
- [ ] Average response time < 2s
- [ ] 99.9% uptime (measured)
- [ ] All OAuth providers working
- [ ] Admin panel accessible
- [ ] Rate limiting functioning
- [ ] Backups running automatically
- [ ] Monitoring alerts configured
- [ ] No security vulnerabilities

---

## Troubleshooting

### Common Issues

**"Connection refused" errors**
- Check if all containers are running: `docker-compose ps`
- Verify network connectivity: `docker network ls`

**"Rate limit exceeded" immediately**
- Clear Redis: `docker-compose exec redis redis-cli FLUSHALL`
- Check user limits in admin panel

**RAG queries timing out**
- Increase proxy timeout in Nginx
- Check Elasticsearch cluster health
- Verify model files are loaded

**High memory usage**
- Reduce database pool size
- Limit Elasticsearch heap size
- Scale horizontally instead of vertically