# OAuth Setup Guide

## Google OAuth Setup

### Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Name: "RAG Service" → Create

### Step 2: Enable Google+ API

1. In your project, go to **APIs & Services** → **Library**
2. Search for "Google+ API" or "People API"
3. Click **Enable**

### Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** (or Internal if using Workspace)
3. Fill in:
   - **App name**: RAG Service
   - **User support email**: your email
   - **Developer contact**: your email
4. **Scopes**: Add these scopes:
   - `openid`
   - `email`
   - `profile`
5. **Test users** (for External): Add your test email addresses
6. Save and Continue

### Step 4: Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Web application**
4. Name: "RAG Service Web Client"
5. **Authorized redirect URIs**:
   - Development: `http://localhost:8000/api/v1/auth/google/callback`
   - Production: `https://yourdomain.com/api/v1/auth/google/callback`
6. Click **Create**

### Step 5: Copy Credentials to .env

```bash
GOOGLE_CLIENT_ID=123456789-abcdefghijklmnop.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
```

---

## GitHub OAuth Setup

### Step 1: Create OAuth App

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **OAuth Apps** → **New OAuth App**

### Step 2: Fill Application Details

1. **Application name**: RAG Service
2. **Homepage URL**: `http://localhost:8000` (dev) or `https://yourdomain.com` (prod)
3. **Authorization callback URL**:
   - Development: `http://localhost:8000/api/v1/auth/github/callback`
   - Production: `https://yourdomain.com/api/v1/auth/github/callback`
4. Click **Register application**

### Step 3: Generate Client Secret

1. Click **Generate a new client secret**
2. Copy the secret immediately (it won't be shown again)

### Step 4: Copy Credentials to .env

```bash
GITHUB_CLIENT_ID=Iv1.abc123def456
GITHUB_CLIENT_SECRET=1234567890abcdef1234567890abcdef12345678
GITHUB_REDIRECT_URI=http://localhost:8000/api/v1/auth/github/callback
```

---

## Testing OAuth Flow

### Test Google OAuth

1. Start your service: `docker-compose up -d`
2. Open browser: http://localhost:8000/api/v1/auth/google/login
3. You'll be redirected to Google login
4. After authorization, you'll be redirected back with tokens

### Test GitHub OAuth

1. Open browser: http://localhost:8000/api/v1/auth/github/login
2. You'll be redirected to GitHub authorization
3. Click "Authorize"
4. You'll be redirected back with tokens

### Using OAuth Tokens in Your React App

```javascript
// Initiate OAuth
window.location.href = 'http://localhost:8000/api/v1/auth/google/login';

// Handle callback (your React app should catch this)
// URL will be: http://localhost:3000/?access_token=...&refresh_token=...

// Or use popup approach:
const popup = window.open(
  'http://localhost:8000/api/v1/auth/google/login',
  'oauth',
  'width=500,height=600'
);

// Listen for message from callback
window.addEventListener('message', (event) => {
  if (event.origin === 'http://localhost:8000') {
    const { access_token, refresh_token } = event.data;
    // Store tokens and close popup
    localStorage.setItem('access_token', access_token);
    popup.close();
  }
});
```

---

## Frontend Integration Example

### React Hook for OAuth

```typescript
// useOAuth.ts
import { useState } from 'react';

export const useOAuth = () => {
  const [loading, setLoading] = useState(false);
  
  const loginWithGoogle = () => {
    setLoading(true);
    const width = 500;
    const height = 600;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;
    
    const popup = window.open(
      'http://localhost:8000/api/v1/auth/google/login',
      'Google Login',
      `width=${width},height=${height},left=${left},top=${top}`
    );
    
    const checkPopup = setInterval(() => {
      try {
        if (popup?.location.href.includes('/api/v1/auth/google/callback')) {
          // Parse tokens from URL
          const params = new URLSearchParams(popup.location.search);
          const accessToken = params.get('access_token');
          const refreshToken = params.get('refresh_token');
          
          if (accessToken && refreshToken) {
            localStorage.setItem('access_token', accessToken);
            localStorage.setItem('refresh_token', refreshToken);
            popup.close();
            setLoading(false);
            clearInterval(checkPopup);
          }
        }
      } catch (e) {
        // Cross-origin error, ignore
      }
      
      if (popup?.closed) {
        setLoading(false);
        clearInterval(checkPopup);
      }
    }, 500);
  };
  
  return { loginWithGoogle, loading };
};
```

---

## Common Issues & Solutions

### Issue 1: "Redirect URI mismatch"

**Solution**: Ensure the callback URL in Google/GitHub matches EXACTLY:
- Include/exclude `http://` or `https://`
- Check trailing slashes
- Port numbers must match

### Issue 2: "Access blocked: This app's request is invalid"

**Solution**: 
- Add test users to OAuth consent screen (Google)
- App must be published or in testing mode with your email as test user

### Issue 3: OAuth works in browser but not in app

**Solution**:
- For mobile apps, use different OAuth flow (PKCE)
- For desktop apps, use `http://localhost` with dynamic port
- For web apps, ensure CORS is configured

### Issue 4: "Email already registered with local provider"

**Expected behavior**: Users cannot mix auth providers
**Solution**: Tell user to login with original method or link accounts (feature not implemented)

---

## Security Best Practices

1. **Never expose Client Secrets**: Keep in .env, never commit to git
2. **Use HTTPS in production**: OAuth without HTTPS is insecure
3. **Validate state parameter**: Prevent CSRF attacks (already handled by authlib)
4. **Short-lived tokens**: Access tokens expire in 30 minutes
5. **Rotate secrets regularly**: Change OAuth secrets every 6 months

---

## Production Deployment

### Update Redirect URIs

When deploying to production (e.g., `https://api.yourdomain.com`):

1. **Google Console**: Add production callback URL
2. **GitHub Settings**: Add production callback URL
3. **Update .env**:
```bash
GOOGLE_REDIRECT_URI=https://api.yourdomain.com/api/v1/auth/google/callback
GITHUB_REDIRECT_URI=https://api.yourdomain.com/api/v1/auth/github/callback
```

### Multiple Environments

Create separate OAuth apps for dev/staging/production:

```bash
# .env.development
GOOGLE_CLIENT_ID=dev-client-id
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback

# .env.production
GOOGLE_CLIENT_ID=prod-client-id
GOOGLE_REDIRECT_URI=https://api.yourdomain.com/api/v1/auth/google/callback
```

---

## Disable OAuth (Optional)

If you don't want OAuth, leave these empty in `.env`:

```bash
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
```

The OAuth endpoints will return 501 (Not Implemented) when accessed.