import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

// Load users file (each user has "email", "password", "messages"[], optionally "username")
const users = new SharedArray('users', function () {
    return JSON.parse(open('./rag_test_data_messages_per_user.json'));
});

export let options = {
    // Control VUs/duration from CLI: k6 run --vus 20 --duration 30s load_test.js
    thresholds: {
        http_req_failed: ['rate<0.05'],
        'http_req_duration{name:send_message}': ['p(95)<5000'],
    },
};

// Helper: extract username from email
function extractUsername(email) {
    return email.split('@')[0].replace(/[^a-zA-Z0-9]/g, '').substring(0, 20) + '_test';
}

// Helper: perform login
function doLogin(baseUrl, email, password) {
    const loginRes = http.post(
        `${baseUrl}/api/v1/auth/login`,
        JSON.stringify({ login: email, password: password }),
        { headers: { 'Content-Type': 'application/json' }, tags: { name: 'login' } }
    );
    return loginRes;
}

// Helper: perform registration
function doRegister(baseUrl, email, username, password) {
    const registerRes = http.post(
        `${baseUrl}/api/v1/auth/register`,
        JSON.stringify({ 
            email: email, 
            username: username, 
            password: password 
        }),
        { headers: { 'Content-Type': 'application/json' }, tags: { name: 'register' } }
    );
    return registerRes;
}

// Helper: extract token from response
function extractToken(res) {
    if (!res || (res.status !== 200 && res.status !== 201)) {
        return null;
    }
    try {
        const body = res.json();
        // API returns { access_token, refresh_token, token_type }
        return body.access_token || null;
    } catch (e) {
        console.log(`Token parse error: ${e}`);
        return null;
    }
}

// Helper: get or create chat
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
            // API returns ChatListResponse: { total, chats, skip, limit }
            const chats = body.chats || [];
            if (chats.length > 0) {
                // Return the last chat's id
                return chats[chats.length - 1].id || null;
            }
        } catch (e) {
            console.log(`List chats parse error: ${e}`);
        }
    }

    // No existing chat, create one
    const createRes = http.post(
        `${baseUrl}/api/v1/chats`,
        JSON.stringify({ title: `load-test-${username}` }),
        authHeaders
    );

    if (createRes && (createRes.status === 200 || createRes.status === 201)) {
        try {
            const body = createRes.json();
            // API returns ChatResponse: { id, user_id, title, ... }
            return body.id || null;
        } catch (e) {
            console.log(`Create chat parse error: ${e}`);
        }
    }

    return null;
}

export function setup() {
    const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';
    const sessions = [];

    console.log(`Setting up ${users.length} users...`);

    for (let i = 0; i < users.length; i++) {
        const u = users[i];
        const email = u.email;
        const password = u.password;
        const username = u.username || extractUsername(email);

        let token = null;
        let chatId = null;

        // Step 1: Try to login first
        console.log(`[${i + 1}/${users.length}] Attempting login for ${email}...`);
        let loginRes = doLogin(baseUrl, email, password);
        token = extractToken(loginRes);

        // Step 2: If login failed with 401, try to register
        if (!token && loginRes && loginRes.status === 401) {
            console.log(`[${i + 1}/${users.length}] Login failed (401), attempting registration...`);
            const registerRes = doRegister(baseUrl, email, username, password);
            
            if (registerRes && (registerRes.status === 200 || registerRes.status === 201)) {
                // Registration returns token directly
                token = extractToken(registerRes);
                console.log(`[${i + 1}/${users.length}] Registration successful`);
            } else if (registerRes && registerRes.status === 409) {
                // User exists but wrong password? Try login again
                console.log(`[${i + 1}/${users.length}] User exists, retrying login...`);
                loginRes = doLogin(baseUrl, email, password);
                token = extractToken(loginRes);
            } else {
                console.log(`[${i + 1}/${users.length}] Registration failed: ${registerRes ? registerRes.status : 'no response'}`);
                if (registerRes) {
                    console.log(`Response body: ${registerRes.body}`);
                }
            }
        } else if (!token) {
            console.log(`[${i + 1}/${users.length}] Login failed with status: ${loginRes ? loginRes.status : 'no response'}`);
            if (loginRes) {
                console.log(`Response body: ${loginRes.body}`);
            }
        } else {
            console.log(`[${i + 1}/${users.length}] Login successful`);
        }

        // Step 3: Get or create chat if we have a token
        if (token) {
            chatId = getOrCreateChat(baseUrl, token, username);
            if (chatId) {
                console.log(`[${i + 1}/${users.length}] Chat ready: ${chatId}`);
            } else {
                console.log(`[${i + 1}/${users.length}] Failed to get/create chat`);
            }
        }

        sessions.push({
            token: token,
            chat_id: chatId,
            email: email,
            username: username
        });

        // Small delay between setup requests to avoid rate limiting
        sleep(0.1);
    }

    const successCount = sessions.filter(s => s.token && s.chat_id).length;
    console.log(`Setup complete: ${successCount}/${users.length} users ready`);

    return { baseUrl: baseUrl, sessions: sessions };
}

export default function (data) {
    const baseUrl = data.baseUrl;
    const sessions = data.sessions;
    const userCount = users.length;

    // Map VU to user index (cycle if VUs > users)
    const vuIndex = (__VU - 1) % userCount;
    const u = users[vuIndex];
    const session = sessions[vuIndex];

    // Check if session is valid
    if (!session || !session.token || !session.chat_id) {
        check(null, { 'session-ready': () => false });
        console.log(`VU ${__VU}: No valid session for user index ${vuIndex}`);
        sleep(1); // Avoid tight loop
        return;
    }

    const token = session.token;
    const chatId = session.chat_id;

    // Choose message based on VU iteration (rotate through messages)
    const messages = u.messages || ['Hello, this is a test message.'];
    const messageIndex = __ITER % messages.length;
    const message = messages[messageIndex];

    const headers = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        tags: { name: 'send_message' }
    };

    // Send POST message - THIS IS THE CORE LOAD TEST OPERATION
    const res = http.post(
        `${baseUrl}/api/v1/chats/${chatId}/messages`,
        JSON.stringify({ content: message }),
        headers
    );

    const success = check(res, {
        'message sent (200/201)': (r) => r && (r.status === 200 || r.status === 201),
        'response has message_id': (r) => {
            if (!r || r.status < 200 || r.status >= 300) return false;
            try {
                const body = r.json();
                return !!body.message_id;
            } catch (e) {
                return false;
            }
        }
    });

    if (!success && res) {
        console.log(`VU ${__VU} message failed: ${res.status} - ${res.body}`);
    }

    // No sleep for maximum throughput. Add sleep(x) here for pacing if needed.
    // sleep(0.5);
}

export function teardown(data) {
    const sessions = data.sessions;
    const successCount = sessions.filter(s => s.token && s.chat_id).length;
    console.log(`Test complete. ${successCount} users were active.`);
}