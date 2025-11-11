// to run this : BASE_URL=http://localhost:8000 k6 run --vus 20 --duration 30s ./rag_k6_send_only.js

import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';

// Load users file (20 users each with "email","password","messages"[])
const users = new SharedArray('users', function () {
    return JSON.parse(open('./rag_test_data_messages_per_user.json'));
});

export let options = {
    // Control VUs/duration from CLI e.g. k6 run --vus 20 --duration 30s script.js
    // No built-in sleep; this will push maximum throughput given VUs.
    thresholds: {
        // you can tweak thresholds or remove them
        http_req_failed: ['rate<0.01'], // fail if too many http errors
    },
};

export function setup() {
    const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';
    const sessions = []; // array of { token, chat_id }

    for (let i = 0; i < users.length; i++) {
        const u = users[i];

        // 1) login once
        const loginRes = http.post(`${baseUrl}/api/v1/auth/login`,
            JSON.stringify({ login: u.email, password: u.password }),
            { headers: { 'Content-Type': 'application/json' }, tags: { name: 'login' } }
        );

        let token = null;
        if (loginRes && (loginRes.status === 200 || loginRes.status === 201)) {
            try {
                const j = loginRes.json();
                token = j.access_token || j.token || (j.data && j.data.access_token) || null;
            } catch (e) {
                // parse error -> token remains null
            }
        }

        // 2) try to list chats and use last one
        let chatId = null;
        if (token) {
            const authHeaders = { headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` } };
            const listRes = http.get(`${baseUrl}/api/v1/chats`, authHeaders);
            if (listRes && listRes.status === 200) {
                try {
                    const j = listRes.json();
                    if (Array.isArray(j) && j.length > 0) {
                        chatId = j[j.length - 1].id || j[j.length - 1].chat_id || null;
                    } else if (j && j.data && Array.isArray(j.data) && j.data.length > 0) {
                        chatId = j.data[j.data.length - 1].id || j.data[j.data.length - 1].chat_id || null;
                    }
                } catch (e) {}
            }

            // 3) if no chat found, create one
            if (!chatId) {
                const createRes = http.post(`${baseUrl}/api/v1/chats`,
                    JSON.stringify({ title: `load-chat-${u.username}` }),
                    authHeaders
                );
                if (createRes && (createRes.status === 200 || createRes.status === 201)) {
                    try {
                        const cj = createRes.json();
                        chatId = cj.id || cj.chat_id || (cj.data && cj.data.id) || null;
                    } catch (e) {}
                }
            }
        }

        sessions.push({ token: token, chat_id: chatId });
    }

    // Return baseUrl and sessions array to VUs
    return { baseUrl: baseUrl, sessions: sessions };
}

export default function (data) {
    const baseUrl = data.baseUrl;
    const sessions = data.sessions;
    const userCount = users.length;

    // map VU -> user index (cycle if VUs > users)
    const vuIndex = ((__VU - 1) % userCount);
    const u = users[vuIndex];
    const session = sessions[vuIndex];

    // If token or chat_id missing, try a lightweight re-login or skip
    let token = session && session.token;
    let chatId = session && session.chat_id;

    if (!token) {
        const rl = http.post(`${baseUrl}/api/v1/auth/login`,
            JSON.stringify({ login: u.email, password: u.password }),
            { headers: { 'Content-Type': 'application/json' } }
        );
        if (rl && (rl.status === 200 || rl.status === 201)) {
            try { token = rl.json().access_token || rl.json().token || (rl.json().data && rl.json().data.access_token); } catch (e) {}
        }
    }

    // If chat missing, attempt to GET list chats quickly and reuse/create
    if (!chatId && token) {
        const authH = { headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` } };
        const listRes = http.get(`${baseUrl}/api/v1/chats`, authH);
        if (listRes && listRes.status === 200) {
            try {
                const j = listRes.json();
                if (Array.isArray(j) && j.length > 0) chatId = j[j.length - 1].id || j[j.length - 1].chat_id || null;
                else if (j && j.data && Array.isArray(j.data) && j.data.length > 0) chatId = j.data[j.data.length - 1].id || j.data[j.data.length - 1].chat_id || null;
            } catch (e) {}
        }
        if (!chatId) {
            const createRes = http.post(`${baseUrl}/api/v1/chats`, JSON.stringify({ title: `load-chat-${u.username}` }),
                { headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` } }
            );
            if (createRes && (createRes.status === 200 || createRes.status === 201)) {
                try { chatId = createRes.json().id || createRes.json().chat_id || (createRes.json().data && createRes.json().data.id); } catch (e) {}
            }
        }
    }

    if (!token || !chatId) {
        // can't proceed: mark an error metric by doing a failed request to a fake endpoint (or skip)
        // Do a check so results show failures
        check(null, { 'session-ready': () => false });
        return;
    }

    // Choose message based on VU iteration so messages rotate: __ITER is per-VU iteration count
    const messageIndex = (__ITER) % (u.messages.length || 1);
    const message = u.messages[messageIndex];

    const headers = { headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` } };

    // send POST message - THIS IS THE CORE OPERATION WE WANT BENCHMARKED (no additional sleeps)
    const res = http.post(`${baseUrl}/api/v1/chats/${chatId}/messages`,
        JSON.stringify({ content: message }), headers);

    check(res, {
        'message ok (200/201)': (r) => r && (r.status === 200 || r.status === 201),
    });

    // No sleep -> push maximum RPS. If you want some pacing, add sleep(x) here.
}
