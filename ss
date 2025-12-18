[33mcommit 63331c0cc48bc19cd5fa1177689c7646b8f5d9f6[m[33m ([m[1;36mHEAD[m[33m -> [m[1;32mmaster[m[33m)[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Wed Dec 3 01:36:15 2025 +0330

    fixed typo

[33mcommit 174d7497d015a33619f4cd5124076fa08ae7e8d4[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Wed Dec 3 01:31:41 2025 +0330

    add .env and model and dock folder into the dockerfile

[33mcommit ca5b9612ff0211b4c871e7e8df6f28b1a9975f88[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Wed Dec 3 01:08:13 2025 +0330

    disabled elastic gpu only

[33mcommit e802789fa26b6a862fe1a2c63682be1c5661c084[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Wed Dec 3 01:00:37 2025 +0330

    changed elastic and mysql version back to new

[33mcommit ad31958ee98ff5d7e7eebd7ad106f84eb786533a[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Wed Dec 3 00:49:43 2025 +0330

    add model downloader and used newest system prompts

[33mcommit 44ac4dcff52e711975e401b3c49b1d66b3ac6566[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 29 18:53:23 2025 +0330

    changed DB host url in .env and docker compose+ disabled the add usage column python file (to prevent migration)

[33mcommit 7188a301d860cc3ef68e6d889207a7015fb9755d[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Mon Nov 24 23:55:47 2025 +0330

    use older elastic and mysql images to fix vps problem

[33mcommit c308dda901ccddb95f5ab82bfb3374e2e8682cc8[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Mon Nov 24 23:12:28 2025 +0330

    now main docs are being tracked by git

[33mcommit d436de5691806ddf4e3467d26852d25368f11c17[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 16:30:36 2025 +0330

    Stop tracking models

[33mcommit e8d26c827a4a3e030b7c18e1dedf10138abcadef[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 16:29:03 2025 +0330

    Stop tracking docs folder

[33mcommit 2fa3b3906b5ffd3765a5c7e49ea97a8d3b5269ed[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 16:15:23 2025 +0330

    Ignore backup repo folder

[33mcommit 9eb091c5d1f363f35b1bdbffd45f606740645e1c[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 15:59:25 2025 +0330

    Track safetensors files with Git LFS

[33mcommit f6cda1febf7a7acf8c7ea3c4090a88c3116d9464[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 15:50:30 2025 +0330

    models directory added to gitignore

[33mcommit 274c1c3450297ee952a0a37f538bd8040967c7e7[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 15:05:57 2025 +0330

    ready for GITHUB (.env was missing before this)

[33mcommit c988a5da9ed2f8f8a880e26bfc5fa2dee275b2df[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sat Nov 22 14:58:12 2025 +0330

    ready to upload on GITHub

[33mcommit 2c2df4ccdb4d8a1c4df84029134d5dbc56ac3aeb[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Thu Nov 20 17:25:29 2025 +0330

    docker files and poetry files changes

[33mcommit ffe77dea3d5bb194c29940f75529336b78fafc3c[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Thu Nov 20 17:25:07 2025 +0330

    everything working , saved so i can safely works

[33mcommit aa9c3500dc28ca26789259e60b52f55572075fb6[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Tue Nov 18 13:27:12 2025 +0330

    avalai added  (langchain swaped with google sdk)
    fixed get chat api endpoint error

[33mcommit ed7d69c4f6e70975cda93cffed8f9a290e897632[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sun Nov 16 15:42:50 2025 +0330

    everything works before adding avalAI

[33mcommit 7eb8bc506fde6df44079fa617f88d9ca10fffeaa[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Tue Nov 11 12:31:35 2025 +0330

    token usage added

[33mcommit 00a2de90f82265107b68227edef974bc37f505e4[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Tue Nov 11 03:50:30 2025 +0330

    fix rate limiter problems , enhanced system prompts , add k6 virtual user tester pipeline

[33mcommit 3cfd602e464290b254370678592e435d4a8f385f[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Mon Nov 3 00:51:29 2025 +0330

    add debug mode prints , add llm on and off mode , fixed rag_pipeline , now works like the single .ipynb

[33mcommit 4af47e548ac2c3867e4db706d83974611b32ff73[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sun Nov 2 21:29:56 2025 +0330

    zone identifiers deleted

[33mcommit b6614de672dd78739916d9f9b257a6c32cc14d91[m
Author: amin <mohammadaminfanaie@gmail.com>
Date:   Sun Nov 2 02:13:30 2025 +0330

    Initial Commit - all endpoints were tested (Oauth Didnt) , just the add_chat has internal error
