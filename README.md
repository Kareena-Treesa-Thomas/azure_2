# Review Analyser — Runbook (HTML frontend + Python backend)

A single self-contained HTML page that grades a pasted review for sentiment,
key phrases, and named entities using **Azure AI Language**, deployed on
**Azure Static Web Apps** (free tier) with a **Python** Azure Function as the
backend.


```
review-analyser/
├── src/
│   └── index.html                 ← entire frontend: HTML + CSS + JS, one file
├── api/                            ← Python Azure Functions backend
│   ├── analyze/
│   │   ├── __init__.py             ← POST /api/analyze handler
│   │   └── function.json           ← HTTP trigger binding
│   ├── host.json
│   ├── requirements.txt            ← just azure-functions
│   └── local.settings.json.example ← copy → local.settings.json for local dev
├── staticwebapp.config.json
├── .github/workflows/azure-static-web-apps.yml
└── .gitignore
```

No build step on either side: the frontend is plain HTML/CSS/JS in one file,
the backend is plain Python with only the `azure-functions` package.

---

## 1. Create the Azure resources

### Resource group + Azure AI Language (free F0 tier)

```bash
az login
az account set --subscription "<your-subscription-name-or-id>"

RG="rg-review-analyser"
LOCATION="eastus"
LANG_NAME="lang-review-analyser"
SWA_NAME="swa-review-analyser"

az group create --name $RG --location $LOCATION

az cognitiveservices account create \
  --name $LANG_NAME \
  --resource-group $RG \
  --kind TextAnalytics \
  --sku F0 \
  --location $LOCATION \
  --yes

# Endpoint and key — you'll paste these into the env variables in step 3
az cognitiveservices account show \
  --name $LANG_NAME --resource-group $RG \
  --query "properties.endpoint" -o tsv

az cognitiveservices account keys list \
  --name $LANG_NAME --resource-group $RG \
  --query "key1" -o tsv
```

> F0 (free) allows one instance per subscription per region. If creation
> fails on quota, reuse an existing F0 resource or use the paid `S` tier.

Portal equivalent: **Create a resource → "Language service"** → choose
Subscription/Resource group/Region → **Pricing tier: Free F0** → Review +
create → open the resource → **Keys and Endpoint** to copy them.

### Static Web App (Python API)

```bash
az staticwebapp create \
  --name $SWA_NAME \
  --resource-group $RG \
  --location $LOCATION \
  --source https://github.com/<your-username>/review-analyser \
  --branch main \
  --app-location "/src" \
  --api-location "/api" \
  --output-location "" \
  --login-with-github
```

`--login-with-github` authenticates, creates the GitHub Actions workflow in
your repo (you can keep the one already included here instead — see Step 2),
and stores the deployment token as a GitHub secret.

Portal equivalent: **Create a resource → "Static Web App"** → connect
GitHub/repo/branch → **App location**: `/src`, **Api location**: `/api`,
**Output location**: *(blank)* → Review + create.

> Python managed Functions on Static Web Apps work on both Free and Standard
> plans, but availability can vary by region — if the build fails to detect
> Python, double check the **Api location** is set to `/api` (it must contain
> `requirements.txt` at its root, which it does here) and that the function
> app's runtime stack shows **Python** under **Manage deployment token /
> Configuration**.

---

## 2. Set your endpoint and key as environment variables

This is the step you'll do by hand in the Azure portal:

1. Open the **Static Web App** resource → left menu → **Settings →
   Environment variables** (sometimes labeled **Configuration**).
2. Add two **Application settings**:
   - `LANGUAGE_ENDPOINT` = `https://<your-language-resource>.cognitiveservices.azure.com`
   - `LANGUAGE_KEY` = `<key1 from your Language resource>`
3. **Save.** These become `os.environ["LANGUAGE_ENDPOINT"]` and
   `os.environ["LANGUAGE_KEY"]` inside `api/analyze/__init__.py` automatically
   — no redeploy needed, they apply to the next function invocation.

CLI equivalent, if you'd rather script it:

```bash
az staticwebapp appsettings set \
  --name $SWA_NAME \
  --setting-names \
    LANGUAGE_ENDPOINT="https://<lang-name>.cognitiveservices.azure.com" \
    LANGUAGE_KEY="<key1>"
```

**Never commit the key to the repo.** It only ever lives in this
environment-variable setting (and, for local testing, in your git-ignored
`api/local.settings.json`).

---

## 3. Push the code to GitHub

```bash
cd review-analyser
git init
git add .
git commit -m "Review Analyser: single-file HTML frontend, Python backend"
git branch -M main
git remote add origin https://github.com/<your-username>/review-analyser.git
git push -u origin main
```

**What gets pushed:** everything in this folder except `local.settings.json`,
`.venv/`, `__pycache__/`, and `.env` — already excluded via `.gitignore`.

**Repo layout convention** (matches what the workflow / `az staticwebapp
create` expects):
- `app_location: /src` — the static frontend (just `index.html`)
- `api_location: /api` — the Python Functions backend
- `output_location: ""` — no build step, plain files

If you created the Static Web App via CLI/portal with GitHub login, it
already pushed its own workflow file. If you're using the workflow already
included in this repo instead, make sure a repo secret named
`AZURE_STATIC_WEB_APPS_API_TOKEN` exists:

```bash
az staticwebapp secrets list --name $SWA_NAME --query "properties.apiKey" -o tsv
```

Paste that value into **GitHub repo → Settings → Secrets and variables →
Actions → New repository secret** → name it `AZURE_STATIC_WEB_APPS_API_TOKEN`.

Every push to `main` now triggers
`.github/workflows/azure-static-web-apps.yml`, which uploads `/src` and
builds/uploads the Python app in `/api`.

---

## 4. Verify the deployment

1. GitHub repo → **Actions** tab → confirm the run is green (the Python
   build step installs `requirements.txt` during the Oryx build).
2. Azure Portal → Static Web App → **Overview** → open the URL
   (`https://<random-name>.azurestaticapps.net`).
3. Paste a review, click **Grade this review**. You should see a sentiment
   "stamp," confidence bars, the original text with key phrases highlighted
   in place, and a list of named-entity chips.

If `/api/analyze` returns a 500 with the "missing LANGUAGE_ENDPOINT /
LANGUAGE_KEY" message, the environment variables from Step 2 aren't set yet
(or were set with extra whitespace/typos) — check **Settings → Environment
variables** again.

---

## 5. Local development (optional)

```bash
# Frontend only — no API calls will work, but you can see the layout
cd src && npx serve .

# Full stack with the Functions emulator
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r api/requirements.txt
npm install -g azure-functions-core-tools@4 @azure/static-web-apps-cli

cp api/local.settings.json.example api/local.settings.json
# edit api/local.settings.json with your real endpoint/key

swa start src --api-location api
```

`swa start` serves the frontend and proxies `/api/*` to the local Python
Functions host, mirroring production routing.

---

## 6. Cost / quota notes

- **Azure AI Language F0**: 5,000 text records/month free, then blocked
  until next cycle (no overage charge), or upgrade to `S` tier.
- **Static Web Apps Free tier**: 100 GB bandwidth/month, no SLA — fine for a
  demo or workshop.
- Each "Grade this review" click costs **3 records** (sentiment, key
  phrases, entities) against the 5,000/month quota.

---

## 7. Cleanup

```bash
az group delete --name $RG --yes --no-wait
```

Removes the Language resource, the Static Web App, and everything else in
the resource group in one shot.
