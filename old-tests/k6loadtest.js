// to run this : BASE_URL=http://localhost:8000 k6 run --vus 10 --duration 30s ./k6loadtest.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

// Load data file (must be in same directory when running k6)
const users = new SharedArray('users', function() {
    return JSON.parse(open('./rag_test_data_messages_per_user.json'));
});

export let options = {
    // Override at runtime with: k6 run --vus 50 --duration 30s script.js
    vus: 20,
    duration: '20s',
    thresholds: {
        http_req_duration: ['p(95)<1000'], // 95% of requests should be below 1s (tweak as needed)
    }
};

export function setup() {
    const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';
    let tokens = [];
    for (let i = 0; i < users.length; i++) {
        const user = users[i];
        const loginRes = http.post(`${baseUrl}/api/v1/auth/login`, JSON.stringify({ login: user.email, password: user.password }), { headers: { 'Content-Type': 'application/json' } });
        if (loginRes.status === 200 || loginRes.status === 201) {
            try {
                const j = loginRes.json();
                const token = j.access_token || j.token || (j.data && j.data.access_token) || null;
                tokens.push(token);
            } catch (e) {
                tokens.push(null);
            }
        } else {
            tokens.push(null);
        }
    }
    return { tokens: tokens, baseUrl: baseUrl };
}

export default function(data) {
    const baseUrl = data.baseUrl;
    const vuIndex = (__VU - 1) % users.length;
    const user = users[vuIndex];
    const token = data.tokens[vuIndex];

    if (!token) {
        // If login failed in setup, attempt to login here
        const r = http.post(`${baseUrl}/api/v1/auth/login`, JSON.stringify({ login: user.email, password: user.password }), { headers: { 'Content-Type': 'application/json' } });
        if (r.status === 200 || r.status === 201) {
            try { data.tokens[vuIndex] = r.json().access_token || r.json().token || (r.json().data && r.json().data.access_token); } catch (e) {}
        }
    }

    const authHeaders = { headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${data.tokens[vuIndex]}` } };

    // 1) GET list chats
    let listRes = http.get(`${baseUrl}/api/v1/chats`, authHeaders);
    let chatId = null;
    if (listRes.status === 200) {
        try {
            const j = listRes.json();
            if (Array.isArray(j) && j.length > 0) {
                // use the last chat
                chatId = j[j.length - 1].id || j[j.length - 1].chat_id || null;
            } else if (j && j.data && Array.isArray(j.data) && j.data.length > 0) {
                chatId = j.data[j.data.length - 1].id || j.data[j.data.length - 1].chat_id || null;
            }
        } catch (e) {}
    }

    // 2) if no chat found, create one
    if (!chatId) {
        const createRes = http.post(`${baseUrl}/api/v1/chats`, JSON.stringify({ title: `chat-for-${user.username}` }), authHeaders);
        if (createRes.status === 200 || createRes.status === 201) {
            try {
                const cj = createRes.json();
                chatId = cj.id || cj.chat_id || (cj.data && cj.data.id) || null;
            } catch (e) {}
        }
    }

    if (!chatId) {
        // failed to get/create chat; record failure and return
        check(null, { 'chat-id-obtained': () => false });
        return;
    }

    // pick a random message from this user's messages
    const msgIdx = Math.floor(Math.random() * user.messages.length);
    const message = user.messages[msgIdx];

    // 3) send message
    const sendRes = http.post(`${baseUrl}/api/v1/chats/${chatId}/messages`, JSON.stringify({ content: message }), authHeaders);
    check(sendRes, {
        'message sent (200/201)': (r) => r.status === 200 || r.status === 201,
        'response time OK': (r) => r.timings && r.timings.duration < 5000
    });

    // small sleep to avoid hammering too tight in single VU loop; tune or remove for raw RPS testing
    sleep(0.1);
}