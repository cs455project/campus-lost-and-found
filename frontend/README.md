# Frontend (Next.js)

The website students, guards and the admin use. It talks to the FastAPI
backend through a built-in proxy: the browser calls `/api/...` on this
site, and Next.js forwards it to the backend (see `next.config.mjs`).

## Run it

The backend must be running first (`uvicorn app.main:app --reload` in `backend/`).
Then, in a second terminal:

```
cd frontend
npm install        # first time only, and after package.json changes
npm run dev
```

Open http://localhost:3000

## Files

```
frontend/
├── app/                      one folder per page (the App Router)
│   ├── layout.js             wraps every page: fonts, styles, AuthProvider
│   ├── globals.css           colours, type sizes, forms and buttons
│   ├── page.js               home page  (/)
│   ├── login/page.js         sign in    (/login)
│   └── onboarding/page.js    profile    (/onboarding)
├── components/
│   ├── AuthProvider.js       remembers who is signed in; redirects
│   └── SiteHeader.js         top bar with name, role and sign out
├── lib/api.js                every backend call goes through apiFetch()
└── next.config.mjs           the /api proxy to FastAPI
```
