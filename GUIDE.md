# FieldMate — Step-by-step guide

Read this top to bottom once. Part A explains agentic AI using this code.
Part B gets it running in VS Code. Part C follows one message through every
file. Part D is what to try and what comes next.

---

## Part A — Agentic AI in five ideas

**1. A chatbot talks. An agent acts.**
A chatbot only produces text. An agent can *do things* in the world (create a
customer, move a job, queue a message) by calling functions you give it.

**2. Tools are the agent's hands.** (`app/tools/`)
A tool is an ordinary Python function. The model never touches the database;
it can only *ask* ADK to run a tool. The model decides which tool to call by
reading three things: the function's **name**, its **arguments** (with types)
and its **docstring**. That's why every docstring in `app/tools/` says *when*
to use the tool, not just what it does. The docstring is a prompt.

**3. The agent loop.** Every agent repeats:

```
PERCEIVE  read the message + conversation so far
DECIDE    "I should call find_contact(name='Aoife')"      ← the model
ACT       ADK runs the Python function                    ← your code
CHECK     the model reads the result {"matches": []}
          → decide again: "no match, so create_contact(...)"
          → ... until it decides it's finished and writes a reply
```

Nobody writes `if "new customer" in text: create_contact()`. The model plans
the steps itself. That is what makes it *agentic*.

**4. Several specialists beat one generalist.** (`app/agents.py`)
FieldMate has a **router** and four **specialists** (intake, vision, insights,
follow-up). The router has no CRM tools; its only tools are the specialists
(`AgentTool`). So the same loop runs at two levels: the router "calls"
`vision_agent` like a function, and inside that call the vision agent runs
its own loop with its own tools. Small instructions + few tools = fewer
mistakes, and each specialist can be tested alone.

**5. Safety lives in code, not in prompts.**
- The follow-up agent has **no send tool**. It can only `queue_for_approval`.
  Sending happens in `pipeline.py` after the owner types `approve 3`. No
  prompt trick can make it send, because the capability doesn't exist.
- **Identity** comes from Twilio's `From` field (`main.py`), never from the model.
- Vision fields below a **confidence threshold** must be confirmed, not saved.
- **Every tool call is logged** to `audit_log` (`tools/audit.py`).

---

## Part B — Run it in VS Code (about 15 minutes)

### 1. Open the project
Unzip `fieldmate.zip`, then in VS Code: **File → Open Folder… → fieldmate**.

### 2. Create a virtual environment
Open the terminal (**Terminal → New Terminal**):

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

When VS Code asks "select interpreter", pick the one in `.venv`.

### 3. Add your API key
- Copy `.env.example` to `.env`.
- Get a free Gemini key at https://aistudio.google.com/apikey and paste it
  after `GOOGLE_API_KEY=`.
- Put your own WhatsApp number in `OWNER_NUMBERS` (e.g. `+3538...`).
- Leave `DRY_RUN=true`.

### 4. Check the tools work (no AI, no key needed)
```bash
pytest -q
```
You should see `7 passed`. (Or use the flask icon in VS Code's left bar.)

### 5. Fill the CRM with example data
```bash
python -m scripts.seed --reset
```
This creates ~45 customers, including the people from the design doc:
Mary Byrne (€2,400 quote, no reply for 7 days), Tom Walsh (service due),
Seán Quinn (job done, not invoiced), Oakview Apartments (forgotten Block C
quote) and Brian Doyle (hasn't booked in almost two years).

### 6. Talk to it in the terminal
```bash
python -m scripts.chat --trace
```
Or press **F5** and choose *"FieldMate: terminal chat (with trace)"*.
`--trace` shows each step of the agent loop:
🧠 = the model **decided** to call something, 🔧 = a tool **ran** and returned.

Try these, in order:

```
What's on today?
New enquiry: Aoife Kelly, 087 555 1234, 22 Ash Grove. Boiler losing pressure. Mary Byrne referred her.
Draft Aoife a reply offering Thursday morning
drafts
approve 1
Chase Mary about her quote
What did I do at Oakview?
I quoted Aoife 220 for the pressure valve
/photo C:\path\to\a\boiler-plate.jpg aoife's boiler
Tom Walsh asked us not to contact him
```

(For `/photo`, any photo of a boiler label or a shop receipt works. Watch it
ask you about fields it isn't sure of.)

### 7. (Optional, later) Connect real WhatsApp
1. Create a free Twilio account → **Messaging → Try it out → WhatsApp
   Sandbox**. Join the sandbox from your phone.
2. Add `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` to `.env`.
3. Start the server: F5 → *"FieldMate: web server"* (or
   `uvicorn app.main:app --reload --port 8080`).
4. In another terminal: `ngrok http 8080`.
5. In the Twilio sandbox settings, set **When a message comes in** to
   `https://<your-ngrok-address>/twilio/whatsapp` (method POST).
6. Message the sandbox number from your phone. Same agents, real WhatsApp.

---

## Part C — Follow one message through the pipeline

The owner sends a WhatsApp photo with the caption *"aoife's boiler"*.

```
 Phone ──► Twilio ──► main.py ──► pipeline.py ──► router ──► vision_agent ──► tools ──► database
                                                                                          │
 Phone ◄── Twilio ◄── main.py ◄──────────────── reply ◄────────────────────────────────────┘
```

| # | Where | What happens | AI? |
|---|---|---|---|
| 1 | Twilio | Receives the photo, POSTs a form to our webhook: `From`, `Body="aoife's boiler"`, `NumMedia=1`, `MediaUrl0`. | No |
| 2 | `main.py` | Checks the **Twilio signature** (is this really Twilio?). | No |
| 3 | `main.py` | Checks `From` is in `OWNER_NUMBERS`. **Identity is decided here**, not by the model. | No |
| 4 | `main.py` | Replies "OK" to Twilio instantly, and continues in the background (Twilio only waits ~15 s). | No |
| 5 | `twilio_io.py` | Downloads the photo into `uploads/`. | No |
| 6 | `pipeline.py` (a) | Is it `approve N` / `reject N` / `drafts`? Then plain code handles it and stops. Not here. | No |
| 7 | `pipeline.py` (b) | Turns the attachment into text: `[photo: uploads/ab12.jpg]`. Voice notes are transcribed here. Adds `[now: …]` so the model knows the date. | Voice only |
| 8 | `pipeline.py` (c) | The **Runner** loads this owner's conversation (session) from the database and starts the router. | — |
| 9 | `agents.py` router | **Decides**: there's a photo marker → call `vision_agent`, passing the request. | **Yes** |
| 10 | vision agent | **Decides** → `read_photo(image_path=…)`. | **Yes** |
| 11 | `tools/vision.py` | Sends the image to Gemini with a fixed JSON schema → make, model, year, each with a confidence. Fields below 0.8 go in `ask_owner`. Saves a `documents` row. | **Yes (CV)** |
| 12 | vision agent | **Checks** the result → `find_contact(name="aoife")` → gets her property → `save_appliance(...)` with trusted fields only → `link_document(...)`. | **Yes** |
| 13 | `tools/crm.py` | Each tool writes to the database; `@audited` writes an `audit_log` row. | No |
| 14 | vision agent | Writes its summary: "Saved Ideal Logic Combi 30. Couldn't read the year — is it 2012?" | **Yes** |
| 15 | router | Reads that and writes a short WhatsApp reply. | **Yes** |
| 16 | `main.py` | Sends the reply to the owner via Twilio. | No |

Notice the shape: **AI only where understanding, deciding or writing is
needed; plain code at the edges** (security, identity, storage, sending).

---

## Part D — Reading order for the code

Read the files in this order; each starts with a comment saying which STEP it is.

1. `app/config.py` — settings from `.env`.
2. `app/db.py` — the CRM tables (contacts, properties, appliances, deals,
   interactions, documents, tasks, drafts, audit_log). No AI.
3. `app/tools/crm.py` — the intake tools. Read the docstrings as prompts.
4. `app/tools/vision.py` — the computer-vision tool and the confidence rule.
5. `app/tools/insights.py`, `app/tools/followup.py` — rules-based analysis
   and the draft queue (spot the missing send tool).
6. `app/agents.py` — the five agents: instructions + tool lists.
7. `app/pipeline.py` — one message, start to finish.
8. `app/main.py` — the WhatsApp webhook and the 7am briefing endpoint.
9. `tests/test_tools.py` — how to test tools without any AI.

### Experiments that teach you the most
- **Delete a tool's docstring** and run the chat. Watch the agent misuse or
  ignore it. Put it back.
- **Remove "Always find_contact before create_contact"** from `COMMON_RULES`
  and add the same customer twice. You'll get a duplicate.
- **Change `VISION_MIN_CONFIDENCE` to 0.99** and send a photo. Almost
  everything becomes a question.
- **Ask something ambiguous** ("chase her") with no context, and see whether
  the router asks back.
- Open `fieldmate.db` (VS Code extension *SQLite Viewer*) and read
  `audit_log`. That's the agent's full decision trail.

---

## What's built vs what's next

| Built in this version | Next (see the Implementation Guide) |
|---|---|
| Router + 4 specialist agents (Google ADK, Gemini) | An evaluation suite of ~100 scripted conversations |
| CRM database with pipeline stages | Postgres + Cloud Run deployment |
| Photo reading with per-field confidence (vision-language model) | **Approach A:** fine-tuned YOLOv8 + OpenCV + OCR, compared on a labelled test set |
| Voice notes transcribed before routing | Vector memory (ChromaDB) for "search by meaning" |
| Draft → approve → send, with opt-out checks | WhatsApp templates for reminders outside the 24-hour window |
| Rules-based morning briefing (`/jobs/morning-briefing`) | A learned churn model compared against the rules |
| Twilio webhook with signature check and owner allow-list | Tracing (time and cost per message) |
| Audit log, terminal chat with live trace, unit tests | Pilot with a real tradesperson |

## Troubleshooting
- **"Missing key inputs argument"** → `GOOGLE_API_KEY` isn't set in `.env`, or
  you ran from a different folder. Run commands from the `fieldmate` folder.
- **`ModuleNotFoundError: app`** → use `python -m scripts.chat`, not
  `python scripts/chat.py`.
- **403 from the webhook** → the Twilio auth token is wrong, or the ngrok URL
  changed. For a quick local test only, set `VALIDATE_TWILIO_SIGNATURE=false`.
- **Model name error** → set `FIELDMATE_MODEL` in `.env` to a model your key
  can use (check Google AI Studio).
