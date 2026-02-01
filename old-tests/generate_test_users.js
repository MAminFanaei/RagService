const fs = require('fs');

// ============================================================================
// CONFIGURATION
// ============================================================================
const CONFIG = {
    MESSAGES_PER_USER: 20,
    OUTPUT_FILE: 'rag_test_data_messages_per_user.json',
    PASSWORD: 'Password123!',
    EMAIL_DOMAIN: 'example123.com'
};

// ============================================================================
// DATA POOLS
// ============================================================================
const DATA_POOLS = {
    cities: ["تهران", "مشهد", "اصفهان", "شیراز", "تبریز", "کرمان", "یزد", "قم", "اهواز", "کرمانشاه",
        "رشت", "همدان", "اراک", "زاهدان", "ساری", "گرگان", "بندرعباس", "قزوین", "زنجان", "سنندج",
        "ارومیه", "کرج", "بوشهر", "خرم‌آباد", "ایلام", "اردبیل", "بجنورد", "بیرجند", "سمنان", "شهرکرد"],

    products: ["مرغ", "گوشت", "برنج", "روغن", "شکر", "نان", "تخم‌مرغ", "لبنیات", "سبزیجات", "میوه",
        "ماهی", "عدس", "لوبیا", "رب گوجه", "ماکارونی", "چای", "قهوه", "عسل", "کره", "پنیر"],

    databases: ["PostgreSQL", "MySQL", "MongoDB", "Redis", "SQLite", "MariaDB", "Oracle", "Cassandra", "Neo4j", "ClickHouse",
        "DynamoDB", "CouchDB", "Elasticsearch", "InfluxDB", "TimescaleDB"],

    operatingSystems: ["ویندوز", "لینوکس", "مک", "اوبونتو", "دبیان", "سنت‌اواس", "فدورا", "آرچ لینوکس", "کالی"],

    languages: ["پایتون", "جاوا", "جاوااسکریپت", "گو", "راست", "سی‌شارپ", "کاتلین", "سوئیفت", "تایپ‌اسکریپت", "روبی",
        "PHP", "سی‌پلاس‌پلاس", "اسکالا", "الکسیر", "کلوژر"],

    frameworks: ["FastAPI", "Django", "Flask", "Express", "Spring", "Laravel", "Rails", "Next.js", "Vue", "React",
        "Angular", "Svelte", "NestJS", "Gin", "Echo", "Fiber"],

    tools: ["Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins", "GitLab CI", "GitHub Actions", "Prometheus", "Grafana",
        "Nginx", "Apache", "HAProxy", "Consul", "Vault", "ArgoCD"],

    mlConcepts: ["RAG", "NLP", "Transformer", "Embedding", "Fine-tuning", "Transfer Learning", "BERT", "GPT", "LLM", "Vector DB",
        "Attention Mechanism", "Tokenization", "Semantic Search", "Knowledge Graph", "Neural Network"],

    protocols: ["HTTP/2", "HTTP/1.1", "WebSocket", "gRPC", "GraphQL", "REST", "SOAP", "MQTT", "AMQP", "TCP/IP"],

    authMethods: ["JWT", "OAuth", "OAuth2", "API Key", "Session", "SAML", "OpenID Connect", "Basic Auth", "mTLS"],

    cacheTools: ["Redis", "Memcached", "Varnish", "Nginx Cache", "CDN", "Hazelcast", "Apache Ignite"],

    gpus: ["RTX 4090", "RTX 3090", "A100", "H100", "V100", "RTX 4080", "Tesla T4", "RTX 3080", "A6000", "A40"],

    phrases: ["سلام دنیا", "خداحافظ", "صبح بخیر", "شب بخیر", "ممنون", "خوش آمدید", "موفق باشید",
        "چطور هستید", "به امید دیدار", "تشکر می‌کنم"],

    persianNames: ["علی", "محمد", "رضا", "حسین", "مهدی", "امیر", "سعید", "فاطمه", "زهرا", "مریم",
        "سارا", "نرگس", "پریسا", "آرش", "کیان", "پارسا", "نیما", "سینا", "دانیال", "یاسمن",
        "امین", "حمید", "جواد", "کامران", "بهرام", "شیما", "لیلا", "نازنین", "مینا", "سحر"],

    persianLastNames: ["محمدی", "احمدی", "حسینی", "رضایی", "کریمی", "موسوی", "هاشمی", "جعفری",
        "علوی", "صادقی", "نوری", "رحیمی", "امینی", "اکبری", "خانی", "مرادی", "قاسمی",
        "یوسفی", "عباسی", "طاهری", "فرهادی", "نجفی", "سلیمانی", "حیدری", "ملکی"]
};

// ============================================================================
// MESSAGE TEMPLATES
// ============================================================================
const MESSAGE_TEMPLATES = [
    // Weather (0-3)
    { template: "سلام، وضعیت آب و هوا در {city} امروز چطوره؟", placeholders: ['city'] },
    { template: "پیش‌بینی هوای {city} برای هفته آینده چیه؟", placeholders: ['city'] },
    { template: "آیا فردا در {city} بارون میاد؟", placeholders: ['city'] },
    { template: "دمای هوا در {city} الان چند درجه است؟", placeholders: ['city'] },

    // Travel (4-7)
    { template: "بهترین مسیر از {city1} به {city2} چیست؟", placeholders: ['city1', 'city2'] },
    { template: "هزینه سفر از {city1} به {city2} با اتوبوس چقدره؟", placeholders: ['city1', 'city2'] },
    { template: "جاهای دیدنی {city} کدومان؟", placeholders: ['city'] },
    { template: "بهترین زمان سفر به {city} کی هست؟", placeholders: ['city'] },

    // Prices (8-11)
    { template: "قیمت {product} امروز در بازار چنده؟", placeholders: ['product'] },
    { template: "چرا قیمت {product} اینقدر بالا رفته؟", placeholders: ['product'] },
    { template: "پیش‌بینی قیمت {product} برای ماه آینده چیه؟", placeholders: ['product'] },
    { template: "از کجا {product} ارزان‌تر بخرم؟", placeholders: ['product'] },

    // Database (12-15)
    { template: "چطور یک پایگاه داده {database} روی {os} نصب کنم؟", placeholders: ['database', 'os'] },
    { template: "تفاوت {database} با سایر دیتابیس‌ها چیه؟", placeholders: ['database'] },
    { template: "نحوه بک‌آپ گرفتن از {database} چطوره؟", placeholders: ['database'] },
    { template: "بهینه‌سازی کوئری‌ها در {database} چطور انجام میشه؟", placeholders: ['database'] },

    // Programming (16-19)
    { template: "چه کتاب‌هایی برای شروع برنامه‌نویس�� {language} پیشنهاد می‌کنی؟", placeholders: ['language'] },
    { template: "تفاوت {language} با زبان‌های دیگه چیه؟", placeholders: ['language'] },
    { template: "بهترین IDE برای {language} کدومه؟", placeholders: ['language'] },
    { template: "چطور با {language} یک API بسازم؟", placeholders: ['language'] },

    // DevOps (20-23)
    { template: "چطور توی {tool} حافظه کم نیاریم؟", placeholders: ['tool'] },
    { template: "نحوه نصب {tool} روی {os} چطوره؟", placeholders: ['tool', 'os'] },
    { template: "بهترین روش استفاده از {tool} در پروداکشن چیه؟", placeholders: ['tool'] },
    { template: "مقایسه {tool} با ابزارهای مشابه", placeholders: ['tool'] },

    // ML/AI (24-27)
    { template: "مفهوم {concept} در پردازش زبان طبیعی چیه؟", placeholders: ['concept'] },
    { template: "چطور از {concept} استفاده کنم؟", placeholders: ['concept'] },
    { template: "تفاوت {concept} با روش‌های سنتی چیه؟", placeholders: ['concept'] },
    { template: "بهترین منابع یادگیری {concept} کدومان؟", placeholders: ['concept'] },

    // Hardware (28-30)
    { template: "چه GPUهایی برای آموزش مدل مناسبن؟", placeholders: [] },
    { template: "مقایسه {gpu} با کارت‌های دیگه", placeholders: ['gpu'] },
    { template: "آیا {gpu} برای پروژه من مناسبه؟", placeholders: ['gpu'] },

    // Web/Framework (31-34)
    { template: "چطور با {framework} یک وب‌هوک بسازم؟", placeholders: ['framework'] },
    { template: "بهترین روش احراز هویت در {framework} چیه؟", placeholders: ['framework'] },
    { template: "نحوه deploy کردن {framework} روی سرور {os}", placeholders: ['framework', 'os'] },
    { template: "مقایسه {framework} با فریم‌ورک‌های دیگه", placeholders: ['framework'] },

    // Security (35-38)
    { template: "چطوری {auth} توی API امن نگه داشته میشه؟", placeholders: ['auth'] },
    { template: "بهترین روش پیاده‌سازی {auth} چیه؟", placeholders: ['auth'] },
    { template: "تفاوت {auth} با روش‌های دیگه احراز هویت", placeholders: ['auth'] },
    { template: "آسیب‌پذیری‌های رایج در {auth} کدومان؟", placeholders: ['auth'] },

    // Caching (39-41)
    { template: "چطور از {cache} برای کش استفاده کنیم؟", placeholders: ['cache'] },
    { template: "بهترین استراتژی کش با {cache} چیه؟", placeholders: ['cache'] },
    { template: "نحوه invalidate کردن کش در {cache}", placeholders: ['cache'] },

    // Performance (42-45)
    { template: "بهترین تنظیمات برای بهینه‌سازی تاخیر پاسخ‌دهی چیه؟", placeholders: [] },
    { template: "چطور bottleneck های سیستم رو پیدا کنم؟", placeholders: [] },
    { template: "روش‌های کاهش latency در API ها", placeholders: [] },
    { template: "چطور throughput سیستم رو افزایش بدم؟", placeholders: [] },

    // Translation (46-48)
    { template: "ترجمه جمله '{phrase}' به انگلیسی چیه؟", placeholders: ['phrase'] },
    { template: "معنی '{phrase}' به فارسی چی میشه؟", placeholders: ['phrase'] },
    { template: "ترجمه تخصصی '{phrase}' در حوزه فناوری", placeholders: ['phrase'] },

    // Testing (49-52)
    { template: "روش تست بار (load testing) برای API چیه؟", placeholders: [] },
    { template: "بهترین ابزارهای تست عملکرد کدومان؟", placeholders: [] },
    { template: "چطور unit test بنویسم؟", placeholders: [] },
    { template: "استراتژی‌های تست integration", placeholders: [] },

    // Protocols (53-55)
    { template: "فرق {protocol1} و {protocol2} چیه؟", placeholders: ['protocol1', 'protocol2'] },
    { template: "کی از {protocol} استفاده کنیم؟", placeholders: ['protocol'] },
    { template: "مزایای {protocol} چیه؟", placeholders: ['protocol'] },

    // Networking (56-59)
    { template: "سرعت اینترنت من چطور اندازه‌گیری میشه؟", placeholders: [] },
    { template: "چطور پینگ سرور رو کاهش بدم؟", placeholders: [] },
    { template: "تفاوت IPv4 و IPv6 چیه؟", placeholders: [] },
    { template: "بهترین DNS سرورها کدومان؟", placeholders: [] },

    // Backup (60-62)
    { template: "بهترین روش بک‌آپ گرفتن از سرور چی هست؟", placeholders: [] },
    { template: "چطور بک‌آپ اتوماتیک تنظیم کنم؟", placeholders: [] },
    { template: "استراتژی‌های disaster recovery چیه؟", placeholders: [] },

    // Learning (63-65)
    { template: "فرق یادگیری ماشین و یادگیری عمیق چیه؟", placeholders: [] },
    { template: "از کجا شروع کنم یادگیری هوش مصنوعی رو؟", placeholders: [] },
    { template: "بهترین دوره‌های آنلاین برای {concept} کدومان؟", placeholders: ['concept'] },
];

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

function seededRandom(seed) {
    const x = Math.sin(seed * 9999) * 10000;
    return x - Math.floor(x);
}

function getFromPool(pool, userId, offset = 0) {
    const index = Math.abs(Math.floor(seededRandom(userId * 31 + offset * 17) * pool.length));
    return pool[index % pool.length];
}

function getPlaceholderValue(placeholder, userId, msgIndex) {
    const offset = userId * 100 + msgIndex * 7;

    const mapping = {
        'city': () => getFromPool(DATA_POOLS.cities, userId, offset),
        'city1': () => getFromPool(DATA_POOLS.cities, userId, offset),
        'city2': () => getFromPool(DATA_POOLS.cities, userId, offset + 50),
        'product': () => getFromPool(DATA_POOLS.products, userId, offset),
        'database': () => getFromPool(DATA_POOLS.databases, userId, offset),
        'os': () => getFromPool(DATA_POOLS.operatingSystems, userId, offset),
        'language': () => getFromPool(DATA_POOLS.languages, userId, offset),
        'framework': () => getFromPool(DATA_POOLS.frameworks, userId, offset),
        'tool': () => getFromPool(DATA_POOLS.tools, userId, offset),
        'concept': () => getFromPool(DATA_POOLS.mlConcepts, userId, offset),
        'gpu': () => getFromPool(DATA_POOLS.gpus, userId, offset),
        'protocol': () => getFromPool(DATA_POOLS.protocols, userId, offset),
        'protocol1': () => getFromPool(DATA_POOLS.protocols, userId, offset),
        'protocol2': () => getFromPool(DATA_POOLS.protocols, userId, offset + 30),
        'auth': () => getFromPool(DATA_POOLS.authMethods, userId, offset),
        'cache': () => getFromPool(DATA_POOLS.cacheTools, userId, offset),
        'phrase': () => getFromPool(DATA_POOLS.phrases, userId, offset),
    };

    return mapping[placeholder] ? mapping[placeholder]() : `{${placeholder}}`;
}

function generateMessage(userId, msgIndex) {
    // Select template based on user and message index for variety
    const templateIndex = (userId * 3 + msgIndex * 7) % MESSAGE_TEMPLATES.length;
    const templateObj = MESSAGE_TEMPLATES[templateIndex];

    let message = templateObj.template;

    // Replace all placeholders
    for (const placeholder of templateObj.placeholders) {
        const value = getPlaceholderValue(placeholder, userId, msgIndex);
        message = message.replace(`{${placeholder}}`, value);
    }

    // Add unique suffix
    message += ` — کاربر ${userId} پیام ${msgIndex + 1}`;

    return message;
}

function generateUser(userIndex) {
    const userId = userIndex + 1;

    // Generate unique name
    const firstName = getFromPool(DATA_POOLS.persianNames, userId, 0);
    const lastName = getFromPool(DATA_POOLS.persianLastNames, userId, 100);

    // Generate messages
    const messages = [];
    for (let i = 0; i < CONFIG.MESSAGES_PER_USER; i++) {
        messages.push(generateMessage(userId, i));
    }

    return {
        username: `user${userId}`,
        email: `user${userId}@${CONFIG.EMAIL_DOMAIN}`,
        password: CONFIG.PASSWORD,
        full_name: `${firstName} ${lastName}`,
        messages: messages
    };
}

// ============================================================================
// MAIN
// ============================================================================

function main() {
    const args = process.argv.slice(2);
    let userCount = 10; // default
    let outputFile = CONFIG.OUTPUT_FILE;
    let messagesPerUser = CONFIG.MESSAGES_PER_USER;

    // Parse arguments
    for (let i = 0; i < args.length; i++) {
        if (args[i] === '-n' || args[i] === '--users') {
            userCount = parseInt(args[i + 1]) || 10;
            i++;
        } else if (args[i] === '-o' || args[i] === '--output') {
            outputFile = args[i + 1] || CONFIG.OUTPUT_FILE;
            i++;
        } else if (args[i] === '-m' || args[i] === '--messages') {
            messagesPerUser = parseInt(args[i + 1]) || CONFIG.MESSAGES_PER_USER;
            CONFIG.MESSAGES_PER_USER = messagesPerUser;
            i++;
        } else if (args[i] === '-h' || args[i] === '--help') {
            console.log(`
Usage: node generate_test_users.js [options]

Options:
  -n, --users <count>      Number of users to generate (default: 10)
  -m, --messages <count>   Messages per user (default: 20)
  -o, --output <file>      Output file path (default: rag_test_data_messages_per_user.json)
  -h, --help               Show this help message

Examples:
  node generate_test_users.js -n 50
  node generate_test_users.js -n 100 -m 30 -o test_users.json
            `);
            process.exit(0);
        }
    }

    console.log('═'.repeat(60));
    console.log('🔧 TEST USER GENERATOR');
    console.log('═'.repeat(60));
    console.log(`  Users to generate: ${userCount}`);
    console.log(`  Messages per user: ${messagesPerUser}`);
    console.log(`  Output file: ${outputFile}`);
    console.log('═'.repeat(60));

    const users = [];

    for (let i = 0; i < userCount; i++) {
        const user = generateUser(i);
        users.push(user);

        // Progress indicator
        if ((i + 1) % 10 === 0 || i === userCount - 1) {
            process.stdout.write(`\r  Generating users: ${i + 1}/${userCount}`);
        }
    }

    console.log('\n');

    // Write to file
    fs.writeFileSync(outputFile, JSON.stringify(users, null, 2), 'utf8');

    console.log(`✅ Generated ${userCount} users with ${messagesPerUser} messages each`);
    console.log(`📁 Saved to: ${outputFile}`);
    console.log('═'.repeat(60));

    // Show sample
    console.log('\n📋 Sample user:');
    console.log(`  Username: ${users[0].username}`);
    console.log(`  Email: ${users[0].email}`);
    console.log(`  Full Name: ${users[0].full_name}`);
    console.log(`  Sample Messages:`);
    console.log(`    1: ${users[0].messages[0]}`);
    console.log(`    2: ${users[0].messages[1]}`);
    console.log(`    3: ${users[0].messages[2]}`);

    if (userCount > 1) {
        console.log(`\n📋 Another sample (User ${Math.min(5, userCount)}):`);
        const sampleUser = users[Math.min(4, userCount - 1)];
        console.log(`  Username: ${sampleUser.username}`);
        console.log(`  Full Name: ${sampleUser.full_name}`);
        console.log(`  Sample Messages:`);
        console.log(`    1: ${sampleUser.messages[0]}`);
        console.log(`    2: ${sampleUser.messages[1]}`);
    }
}

main();