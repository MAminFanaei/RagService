// BASE_URL=http://localhost:8000 k6 run --vus 10 --duration 30s ./new_load_test.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

// ============================================================================
// Load users from JSON file (shared read-only data)
// ============================================================================
const allUsers = new SharedArray('users', function () {
    return JSON.parse(open('./rag_test_data_messages_per_user.json'));
});

// ============================================================================
// PER-VU STATE (module-level variables are isolated per VU in k6)
// ============================================================================
let vuSession = {
    initialized: false,
    token: null,
    chatId: null,
    messages: [],
    userIndex: -1
};

// ============================================================================
// OPTIONS
// ============================================================================
export let options = {
    thresholds: {
        http_req_failed: ['rate<0.05'],
        'http_req_duration{name:send_message}': ['p(95)<5000'],
        'http_req_duration{name:login}': ['p(95)<3000'],
        'http_req_duration{name:register}': ['p(95)<3000'],
    },
};

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

function extractUsername(email) {
    return email.split('@')[0].replace(/[^a-zA-Z0-9]/g, '').substring(0, 20) + '_test';
}

function doLogin(baseUrl, email, password) {
    return http.post(
        `${baseUrl}/api/v1/auth/login`,
        JSON.stringify({ login: email, password: password }),
        { headers: { 'Content-Type': 'application/json' }, tags: { name: 'login' } }
    );
}

function doRegister(baseUrl, email, username, password, fullName) {
    return http.post(
        `${baseUrl}/api/v1/auth/register`,
        JSON.stringify({
            email: email,
            username: username,
            password: password,
            full_name: fullName
        }),
        { headers: { 'Content-Type': 'application/json' }, tags: { name: 'register' } }
    );
}

function extractToken(res) {
    if (!res || (res.status !== 200 && res.status !== 201)) {
        return null;
    }
    try {
        const body = res.json();
        return body.access_token || null;
    } catch (e) {
        return null;
    }
}

function getOrCreateChat(baseUrl, token, username) {
    const authHeaders = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        }
    };

    // Try to list existing chats
    const listRes = http.get(`${baseUrl}/api/v1/chats`, authHeaders);

    if (listRes && listRes.status === 200) {
        try {
            const body = listRes.json();
            const chats = body.chats || [];
            if (chats.length > 0) {
                return chats[chats.length - 1].id || null;
            }
        } catch (e) {
            // continue to create
        }
    }

    // Create new chat
    const createRes = http.post(
        `${baseUrl}/api/v1/chats`,
        JSON.stringify({ title: `load-test-${username}` }),
        authHeaders
    );

    if (createRes && (createRes.status === 200 || createRes.status === 201)) {
        try {
            const body = createRes.json();
            return body.id || null;
        } catch (e) {
            // return null
        }
    }

    return null;
}

// ============================================================================
// Initialize VU Session (called once per VU on first iteration)
// ============================================================================
function initializeVuSession(baseUrl) {
    // Map this VU to a user (cycle if VUs > users)
    const userIndex = (__VU - 1) % allUsers.length;
    const user = allUsers[userIndex];
    
    const email = user.email;
    const password = user.password;
    const username = user.username || extractUsername(email);
    const fullName = user.full_name || `Test User ${userIndex + 1}`;

    console.log(`[VU ${__VU}] 🔐 Initializing session for ${username} (user index: ${userIndex})`);

    let token = null;
    let chatId = null;

    // Step 1: Try to login first
    let loginRes = doLogin(baseUrl, email, password);
    token = extractToken(loginRes);

    // Step 2: If login failed with 401, try to register
    if (!token && loginRes && loginRes.status === 401) {
        console.log(`[VU ${__VU}] Login failed, attempting registration...`);
        const registerRes = doRegister(baseUrl, email, username, password, fullName);

        if (registerRes && (registerRes.status === 200 || registerRes.status === 201)) {
            token = extractToken(registerRes);
            console.log(`[VU ${__VU}] ✅ Registration successful`);
        } else if (registerRes && registerRes.status === 409) {
            // User exists, retry login
            loginRes = doLogin(baseUrl, email, password);
            token = extractToken(loginRes);
            if (token) {
                console.log(`[VU ${__VU}] ✅ Login successful (after 409)`);
            }
        } else {
            console.log(`[VU ${__VU}] ❌ Registration failed: ${registerRes ? registerRes.status : 'no response'}`);
        }
    } else if (token) {
        console.log(`[VU ${__VU}] ✅ Login successful`);
    } else {
        console.log(`[VU ${__VU}] ❌ Login failed: ${loginRes ? loginRes.status : 'no response'}`);
    }

    // Step 3: Get or create chat
    if (token) {
        chatId = getOrCreateChat(baseUrl, token, username);
        if (chatId) {
            console.log(`[VU ${__VU}] 💬 Chat ready: ${chatId}`);
        } else {
            console.log(`[VU ${__VU}] ❌ Failed to create chat`);
        }
    }

    // Store in VU-local state
    vuSession.initialized = true;
    vuSession.token = token;
    vuSession.chatId = chatId;
    vuSession.messages = user.messages || ['Hello, this is a test message.'];
    vuSession.userIndex = userIndex;
    vuSession.username = username;
}

// ============================================================================
// SETUP - Minimal, just return config
// ============================================================================
export function setup() {
    const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';

    console.log('═'.repeat(60));
    console.log(`🚀 LOAD TEST STARTING`);
    console.log(`   Base URL: ${baseUrl}`);
    console.log(`   Users in file: ${allUsers.length}`);
    console.log(`   Each VU will login its own user on first iteration`);
    console.log('═'.repeat(60));

    return { baseUrl: baseUrl };
}

// ============================================================================
// DEFAULT - Main test iteration
// ============================================================================
export default function (data) {
    const baseUrl = data.baseUrl;

    // Initialize session on first iteration for this VU
    if (!vuSession.initialized) {
        initializeVuSession(baseUrl);
    }

    // Check if session is valid
    if (!vuSession.token || !vuSession.chatId) {
        check(null, { 'session-ready': () => false });
        console.log(`[VU ${__VU}] ⚠️ No valid session, skipping iteration`);
        sleep(1);
        return;
    }

    // Choose message based on iteration (rotate through all messages)
    const messageIndex = __ITER % vuSession.messages.length;
    const message = vuSession.messages[messageIndex];

    // Send message
    const res = http.post(
        `${baseUrl}/api/v1/chats/${vuSession.chatId}/messages`,
        JSON.stringify({ content: message }),
        {
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${vuSession.token}`
            },
            tags: { name: 'send_message' }
        }
    );

    const success = check(res, {
        'message sent (200/201)': (r) => r && (r.status === 200 || r.status === 201),
        'response has message_id': (r) => {
            if (!r || r.status < 200 || r.status >= 300) return false;
            try {
                return !!r.json().message_id;
            } catch (e) {
                return false;
            }
        }
    });

    if (!success && res) {
        console.log(`[VU ${__VU}] ❌ Message failed: ${res.status}`);
    }

    // Optional: think time
    // sleep(0.5);
}

// ============================================================================
// TEARDOWN
// ============================================================================
export function teardown(data) {
    console.log('═'.repeat(60));
    console.log(`🏁 TEST COMPLETE`);
    console.log('═'.repeat(60));
}