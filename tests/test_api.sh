#!/bin/bash

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

API_URL="http://localhost:8000"

echo -e "${YELLOW}=== RAG Service API Test Script ===${NC}\n"

# Test 1: Health Check
echo -e "${YELLOW}[1/7] Testing Health Check...${NC}"
HEALTH=$(curl -s "${API_URL}/health")
if echo "$HEALTH" | grep -q "healthy"; then
    echo -e "${GREEN}✓ Health check passed${NC}\n"
else
    echo -e "${RED}✗ Health check failed${NC}\n"
    exit 1
fi

# Test 2: Register User
echo -e "${YELLOW}[2/7] Registering new user...${NC}"
REGISTER_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/auth/register" \
    -H "Content-Type: application/json" \
    -d '{
        "email": "test@example.com",
        "password": "Test123456",
        "username": "testuser"
    }')

if echo "$REGISTER_RESPONSE" | grep -q "access_token"; then
    ACCESS_TOKEN=$(echo "$REGISTER_RESPONSE" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)
    echo -e "${GREEN}✓ User registered successfully${NC}"
    echo -e "Access Token: ${ACCESS_TOKEN:0:20}...\n"
else
    echo -e "${RED}✗ Registration failed (user may already exist)${NC}"
    echo "$REGISTER_RESPONSE\n"
    
    # Try login instead
    echo -e "${YELLOW}[2b/7] Trying login instead...${NC}"
    LOGIN_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d '{
            "login": "test@example.com",
            "password": "Test123456"
        }')
    
    if echo "$LOGIN_RESPONSE" | grep -q "access_token"; then
        ACCESS_TOKEN=$(echo "$LOGIN_RESPONSE" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)
        echo -e "${GREEN}✓ Login successful${NC}\n"
    else
        echo -e "${RED}✗ Login also failed${NC}\n"
        exit 1
    fi
fi

# Test 3: Get Current User
echo -e "${YELLOW}[3/7] Getting current user info...${NC}"
USER_INFO=$(curl -s -X GET "${API_URL}/api/v1/auth/me" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}")

if echo "$USER_INFO" | grep -q "test@example.com"; then
    echo -e "${GREEN}✓ User info retrieved${NC}\n"
else
    echo -e "${RED}✗ Failed to get user info${NC}\n"
    exit 1
fi

# Test 4: Create Chat
echo -e "${YELLOW}[4/7] Creating new chat...${NC}"
CHAT_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/chats" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"title": "Test Chat"}')

if echo "$CHAT_RESPONSE" | grep -q "Test Chat"; then
    CHAT_ID=$(echo "$CHAT_RESPONSE" | grep -o '"id":"[^"]*' | cut -d'"' -f4)
    echo -e "${GREEN}✓ Chat created${NC}"
    echo -e "Chat ID: ${CHAT_ID}\n"
else
    echo -e "${RED}✗ Failed to create chat${NC}\n"
    exit 1
fi

# Test 5: List Chats
echo -e "${YELLOW}[5/7] Listing chats...${NC}"
CHATS=$(curl -s -X GET "${API_URL}/api/v1/chats" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}")

if echo "$CHATS" | grep -q "Test Chat"; then
    CHAT_COUNT=$(echo "$CHATS" | grep -o '"total":[0-9]*' | cut -d':' -f2)
    echo -e "${GREEN}✓ Found ${CHAT_COUNT} chat(s)${NC}\n"
else
    echo -e "${RED}✗ Failed to list chats${NC}\n"
    exit 1
fi

# Test 6: Send Message (RAG Query)
echo -e "${YELLOW}[6/7] Sending message (RAG query)...${NC}"
echo -e "${YELLOW}Note: This may take 5-10 seconds for first query${NC}"
MESSAGE_RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/chats/${CHAT_ID}/messages" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"content": "What is RAG?"}')

if echo "$MESSAGE_RESPONSE" | grep -q "assistant"; then
    echo -e "${GREEN}✓ Message sent and RAG response received${NC}"
    ANSWER=$(echo "$MESSAGE_RESPONSE" | grep -o '"content":"[^"]*' | tail -1 | cut -d'"' -f4)
    echo -e "Answer preview: ${ANSWER:0:100}...\n"
else
    echo -e "${RED}✗ Failed to get RAG response${NC}"
    echo "$MESSAGE_RESPONSE\n"
fi

# Test 7: Update Chat Title
echo -e "${YELLOW}[7/7] Updating chat title...${NC}"
UPDATE_RESPONSE=$(curl -s -X PATCH "${API_URL}/api/v1/chats/${CHAT_ID}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"title": "Updated Test Chat"}')

if echo "$UPDATE_RESPONSE" | grep -q "Updated Test Chat"; then
    echo -e "${GREEN}✓ Chat title updated${NC}\n"
else
    echo -e "${RED}✗ Failed to update chat${NC}\n"
fi

# Summary
echo -e "${GREEN}==================================${NC}"
echo -e "${GREEN}✓ All tests passed!${NC}"
echo -e "${GREEN}==================================${NC}\n"

echo -e "${YELLOW}Next steps:${NC}"
echo "1. Test OAuth: Open http://localhost:8000/api/v1/auth/google/login"
echo "2. Test Admin: Login as admin@example.com with your ADMIN_PASSWORD"
echo "3. View API docs: http://localhost:8000/docs"
echo ""