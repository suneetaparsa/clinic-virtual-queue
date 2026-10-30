# Clinic virtual queue (Phase 1)

A no-login wait-time and virtual-line service for one community clinic. Patients see the wait and join from any phone browser, or by text message (WAIT, JOIN, LEAVE). Front desk staff check patients in by scanning a code, and the clinic manager sees today's numbers and the weekly pattern.

This repository implements the Phase 1 technical specification and the patient web and front desk design specifications.

## What's included

| Area | Where |
| --- | --- |
| Patient pages: home, join, one-time code, my place, leave (English and Spanish, work without JavaScript) | `app/routes_patient.py`, `app/templates/` |
| Text commands and Twilio webhooks (text and "press 1" call) | `app/text_commands.py`, `app/routes_text.py` |
| Queue rules: confirmation text at 25 minutes, 20-minute reply window, reminder at 10 minutes, opening hours | `app/queue_service.py` |
| Wait-time estimator (rule-based, as specified) | `app/estimator.py` |
| Front desk console: check-in, line, today's dashboard, weekly pattern with explanation, estimate accuracy | `app/routes_staff.py`, `app/analytics.py`, `app/templates/staff.html` |
| Privacy: secret page tokens (only hashes stored), encrypted phone numbers, one-time code on web join, 48-hour purge | `app/security.py`, `app/one_time_code.py` |
| Demo mode: simulated clock, synthetic patients and 8 weeks of history, on-screen demo phone | `app/simulator.py`, `app/routes_demo.py` |
| Tests (24) | `tests/` |

## Run it on your laptop

You need Python 3.11 or newer.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open:

| Page | Address |
| --- | --- |
| Patient page | http://localhost:8000/c/sunrise |
| Demo phone (texts) | http://localhost:8000/demo/phone |
| Front desk (demo PIN 2468) | http://localhost:8000/staff |
| Demo controls (clock, restart at 7:00 AM / 8:15 AM / 4:20 PM / 5:05 PM) | http://localhost:8000/demo |

Each start of the server begins a fresh demo day at 8:15 AM on Friday, Oct 30, 2026, with the clock running at 10x.

### Test while the clinic is closed

Start the server with the clinic closed:

```bash
# macOS/Linux
DEMO_START_AT=after python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# Windows PowerShell
$env:DEMO_START_AT="after"; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

| `DEMO_START_AT` | Clinic time | What to check |
| --- | --- | --- |
| `early` | 7:00 AM | "Closed now · the line opens at 7:30 AM", no Join button, JOIN or WAIT by text gets a closed reply. Leave the clock running: at 7:30 joining opens, and estimates include the wait until doctors start at 8:00 |
| `late` | 4:20 PM | Joining still works until 4:30 PM; after that only people already in line see a wait |
| `after` | 5:05 PM Friday | "Closed for today · opens Monday at 8:00 AM"; texts say you can join Monday from 7:30 AM |

You can also switch while the server is running: open http://localhost:8000/demo, pick a time under "Restart the day", and tap Restart twice.

## Use your phone and laptop together

1. Connect the phone to the same Wi-Fi as the laptop.
2. Start the server with `--host 0.0.0.0`, as above.
3. Open http://localhost:8000/demo on the laptop. It lists the laptop's Wi-Fi address, for example `http://192.168.1.23:8000`.
4. On the phone, open `http://<that address>/c/sunrise` for the patient page and `http://<that address>/demo/phone` for the demo phone.
5. If the phone can't connect, allow Python through the laptop's firewall for private networks. On Windows, accept the "Allow access" prompt the first time uvicorn starts.

Every browser keeps its own patient cookie and its own demo phone number, so the phone and the laptop act as two different people. Camera scanning on the front desk works at `http://localhost:8000/staff` on the laptop; browsers only allow the camera on secure (HTTPS) or localhost addresses.

## Open it in Visual Studio

* **Visual Studio 2022:** install the "Python development" workload, then choose File → Open → Folder and select this folder. Create a Python environment from `requirements-dev.txt` (Python Environments window), then run the server and tests with the terminal commands above.
* **Visual Studio Code:** open the folder, install the Python extension, and pick the `.venv` interpreter. `.vscode/launch.json` has two run configurations: the demo server and the tests.

## Settings

Copy `.env.example` to `.env` and start the server with `--env-file .env` (for example `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env`), or set environment variables. Main ones:

| Setting | Default | Notes |
| --- | --- | --- |
| `DEMO_START_AT` | `morning` | `early`, `late` or `after` starts the demo day with the clinic closed or closing |
| `DEMO_MODE` | `true` | `false` turns off the simulator, demo phone and demo pages and uses the real clock |
| `JOIN_ONE_TIME_CODE` | `true` | `false` restores the old flow, which shows the "already in line" privacy leak; demo use only |
| `SECRET_KEY`, `PHONE_ENCRYPTION_KEY`, `STAFF_PIN` | demo values | must be set in production |
| `DATABASE_URL` | SQLite file | PostgreSQL in production |
| `TWILIO_*`, `PUBLIC_BASE_URL` | blank | real texts and signed webhooks |

## Not done yet (before real patients)

* **Epic integration:** visit start and end come from the simulator. The adapter for Epic's standards-based interface is the next increment.
* **Twilio:** needs a Business Associate Agreement and carrier registration of the sending number (2–4 weeks).
* **Staff accounts:** a single shared PIN today. Production needs named accounts, a one-time code, and an audit view.
* **Hosting:** one process with an in-memory rate limiter and a process lock. Running more than one instance needs PostgreSQL advisory locks and a shared rate limiter.
* **Undo for staff removal and a 10-second delay before the removal text:** in the design specification, not built yet.
* **Spanish:** needs review by a fluent speaker.
* **Demo phone:** it shows the texts for whatever number you type, so it is not private. It stands in for real phones, where texts reach only the owner.
