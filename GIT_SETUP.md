# Git Setup Guide

## Initial Repository Setup

```bash
# 1. Initialize git repository
git init

# 2. Create .gitkeep files for empty directories
chmod +x create_gitkeep.sh
./create_gitkeep.sh

# 3. Add all files
git add .

# 4. Create initial commit
git commit -m "Initial commit: RAG Service setup

- FastAPI application structure
- Multi-auth (local + OAuth)
- Admin panel
- RAG pipeline integration
- Docker Compose setup
- Complete documentation"

# 5. Create main branch (if needed)
git branch -M main

# 6. Add remote repository
git remote add origin https://github.com/yourusername/rag-service.git

# 7. Push to remote
git push -u origin main
```

---

## What Gets Committed

### ✅ Committed Files (Safe to share)

**Application Code:**
- All `.py` files in `app/`
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `alembic.ini`
- `alembic/env.py`

**Documentation:**
- `README.md`
- `SETUP_GUIDE.md`
- `OAUTH_SETUP.md`
- `DEPLOYMENT_CHECKLIST.md`
- `GIT_SETUP.md`

**Scripts:**
- `verify_setup.py`
- `prepare_documents.py`
- `test_api.sh`
- `create_gitkeep.sh`

**Configuration Templates:**
- `.env.example`
- `.gitignore`

**Directory Structure:**
- `.gitkeep` files in empty folders

---

## ⛔ NOT Committed (Security & Privacy)

### Secrets & Credentials
- ❌ `.env` - Contains API keys, passwords, secrets
- ❌ `*.key`, `*.pem` - SSL certificates
- ❌ OAuth credential files

### User Data
- ❌ `docs/main/*.json` - Your processed documents
- ❌ `docs/main/*.docx` - Original documents
- ❌ User uploads (if implemented)

### Generated Files
- ❌ `models/*` - Downloaded AI models (2-5 GB)
- ❌ `__pycache__/` - Python bytecode
- ❌ `*.pyc` - Compiled Python
- ❌ Database files (`.db`, `.sqlite`)

### Runtime Data
- ❌ Docker volumes (MySQL, Redis, Elasticsearch data)
- ❌ Log files
- ❌ Temporary files

---

## Verifying .gitignore Works

```bash
# Check what will be committed
git status

# Should NOT show:
# - .env
# - docs/main/*.json (your documents)
# - models/ (except .gitkeep)
# - __pycache__/
# - *.pyc

# If sensitive files appear, add them to .gitignore immediately!
```

---

## Sensitive Files Accidentally Committed?

### Remove from Git History

```bash
# If you committed .env by mistake
git rm --cached .env
git commit -m "Remove .env from tracking"

# If already pushed, you need to rewrite history (DANGEROUS)
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all

# Force push (⚠️ Only if you're the only developer)
git push --force --all
```

### Rotate Compromised Secrets

If secrets were exposed in git history:

1. **Immediately rotate:**
   - Generate new `SECRET_KEY`
   - Regenerate `GEMINI_API_KEY`
   - Change `ADMIN_PASSWORD`
   - Revoke OAuth credentials, create new ones

2. **Update .env with new secrets**

3. **Restart services**

---

## Branching Strategy

### Development Workflow

```bash
# Create feature branch
git checkout -b feature/add-sse-support

# Make changes, commit frequently
git add .
git commit -m "Add SSE endpoint for streaming responses"

# Push feature branch
git push -u origin feature/add-sse-support

# Create Pull Request on GitHub
# After review, merge to main
```

### Recommended Branches

- `main` - Production-ready code
- `develop` - Development integration branch
- `feature/*` - New features
- `bugfix/*` - Bug fixes
- `hotfix/*` - Emergency production fixes

---

## Commit Message Guidelines

### Good Commit Messages

```bash
# Feature
git commit -m "Add rate limiting per user

- Implement Redis-based rate limiter
- Add admin endpoint to configure limits
- Update user schema with rate_limit_per_minute field"

# Bugfix
git commit -m "Fix RAG query timeout on large documents

- Increase proxy timeout to 300s
- Add processing_time_ms to response metadata
- Log slow queries for monitoring"

# Documentation
git commit -m "Update OAuth setup guide with screenshots"

# Configuration
git commit -m "Configure production Docker Compose

- Increase Elasticsearch memory to 8GB
- Add health checks for all services
- Set restart policy to always"
```

### Avoid

```bash
# Too vague
git commit -m "fix bug"
git commit -m "update code"
git commit -m "changes"
```

---

## Pre-Commit Checks (Recommended)

### Create `.git/hooks/pre-commit`

```bash
#!/bin/bash

# Check for sensitive files
if git diff --cached --name-only | grep -E "\.env$|.*\.key$|.*credentials.*"; then
    echo "❌ ERROR: Attempting to commit sensitive files!"
    echo "Files:"
    git diff --cached --name-only | grep -E "\.env$|.*\.key$|.*credentials.*"
    exit 1
fi

# Check for large files (models)
for file in $(git diff --cached --name-only); do
    if [ -f "$file" ]; then
        size=$(wc -c < "$file")
        if [ $size -gt 10000000 ]; then  # 10MB
            echo "❌ ERROR: File too large: $file ($(($size / 1000000))MB)"
            echo "Large files should be in .gitignore"
            exit 1
        fi
    fi
done

echo "✓ Pre-commit checks passed"
```

### Make executable

```bash
chmod +x .git/hooks/pre-commit
```

---

## Git LFS for Large Files (Optional)

If you want to track large model files:

```bash
# Install Git LFS
git lfs install

# Track model files
git lfs track "models/*.bin"
git lfs track "models/*.safetensors"

# Add .gitattributes
git add .gitattributes

# Now you can commit models
git add models/
git commit -m "Add embedding models via Git LFS"
```

**⚠️ Warning:** Git LFS has storage limits on most platforms

---

## Collaborating with Team

### Clone Repository

```bash
# Clone the repo
git clone https://github.com/yourusername/rag-service.git
cd rag-service

# Copy environment file
cp .env.example .env

# Fill in your own secrets (each developer has their own .env)
nano .env

# Add your documents
cp /path/to/your/docs/*.json ./docs/main/

# Start services
docker-compose up -d
```

### Pull Latest Changes

```bash
# Update main branch
git checkout main
git pull origin main

# Update your feature branch
git checkout feature/your-feature
git merge main

# Or rebase (cleaner history)
git rebase main
```

---

## GitHub Secrets (for CI/CD)

If using GitHub Actions, store secrets in:
`Repository Settings → Secrets and variables → Actions`

Add:
- `SECRET_KEY`
- `GEMINI_API_KEY`
- `ADMIN_PASSWORD`
- `DOCKER_HUB_TOKEN` (if pushing images)

---

## Best Practices

### ✅ DO

- Commit `.env.example` with placeholder values
- Document all environment variables
- Keep commits small and focused
- Write descriptive commit messages
- Review diffs before committing
- Use feature branches
- Test before pushing to main

### ❌ DON'T

- Never commit `.env`
- Never commit API keys or passwords
- Never commit user data
- Don't commit large binary files (models)
- Don't commit IDE-specific files
- Don't force push to main (unless emergency)
- Don't commit commented-out code

---

## Git Aliases (Optional)

Add to `~/.gitconfig`:

```ini
[alias]
    st = status
    co = checkout
    br = branch
    ci = commit
    unstage = reset HEAD --
    last = log -1 HEAD
    visual = log --oneline --graph --all --decorate
    
    # Undo last commit (keep changes)
    undo = reset --soft HEAD~1
    
    # Amend last commit
    amend = commit --amend --no-edit
```

Usage:
```bash
git st              # Same as git status
git visual          # Pretty commit history
git undo            # Undo last commit
```

---

## Troubleshooting

### "Permission denied (publickey)"

```bash
# Generate SSH key
ssh-keygen -t ed25519 -C "your_email@example.com"

# Add to GitHub: Settings → SSH and GPG keys
cat ~/.ssh/id_ed25519.pub
```

### Large file error

```bash
# Remove file from staging
git rm --cached path/to/large/file

# Add to .gitignore
echo "path/to/large/file" >> .gitignore

# Commit .gitignore
git add .gitignore
git commit -m "Update .gitignore to exclude large files"
```

### Merge conflicts

```bash
# Pull latest changes
git pull origin main

# Resolve conflicts in your editor
# Look for <<<<<<< HEAD markers

# After resolving
git add .
git commit -m "Resolve merge conflicts"
```

---

## Summary

✅ Always check `git status` before committing  
✅ Never commit secrets (use .env.example as template)  
✅ Keep sensitive data in .gitignore  
✅ Use meaningful commit messages  
✅ Test before pushing to main  
✅ Use branches for new features  

Your repository is now properly configured for secure collaboration!