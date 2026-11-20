# AgentHub Dashboard (frontend)

Vite + React + TypeScript + Tailwind CSS + Motion. Minden felirat magyar.

```sh
npm install
npm run dev      # fejlesztői szerver; az /api a backendre proxyzik (AGENTHUB_BACKEND, alapértelmezés http://127.0.0.1:8000)
npm run build    # tsc + vite build -> ../agenthub/static (a backend onnan szolgálja ki SPA-ként)
```

Belépés: a Dashboard-kulccsal (HttpOnly sütit kap). A build után a backendet újra kell indítani, ha a `static/` könyvtár korábban nem létezett.
