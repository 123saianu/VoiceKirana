<<<<<<< HEAD
# VoiceKirana
=======
\# VoiceKirana — Voice-Based Grocery Ordering Assistant



VoiceKirana is a Python-based grocery assistant that helps users check grocery availability and estimated prices through a Streamlit interface. It integrates a FastAPI backend, an inventory catalogue backed by SQLite, and Gnani Timbre text-to-speech for spoken responses.



\## Features



\- Interactive Streamlit interface for grocery requests.

\- FastAPI endpoints for order preview and inventory checks.

\- SQLite-based shop, product, price, and stock management.

\- Product matching using catalogue names and aliases.

\- Validation for unknown products, unit mismatches, and insufficient stock.

\- Gnani Timbre text-to-speech for audio responses.

\- API-key authentication for protected backend endpoints.

\- Automated tests for API behavior, inventory logic, and speech synthesis.



\*\*Important:\*\* VoiceKirana currently checks availability and estimated prices. It does not reserve inventory or place an actual grocery order.



\## Technology Stack



\- Python

\- FastAPI and Uvicorn

\- Streamlit

\- SQLite

\- Pydantic

\- Requests

\- Python-dotenv

\- Gnani Timbre TTS

\- Pytest and Coverage



\## Project Structure



```text

VoiceKirana/

├── backend/

│   ├── main.py

│   ├── database.py

│   ├── models/

│   └── services/

│       ├── inventory\_service.py

│       └── timbre\_service.py

├── frontend/

│   └── app.py

├── tests/

├── data/

├── .env

├── .gitignore

└── README.md

```



The structure above highlights the main application files; additional files may exist in the repository.



\## Prerequisites



\- Python installed on your computer.

\- A configured Gnani API key ID for Timbre TTS.

\- A configured VoiceKirana backend API token.



\## Local Setup



Open PowerShell in the project directory:



```powershell

cd C:\\Users\\DELL\\Documents\\VoiceKirana

```



Activate the existing virtual environment:



```powershell

.\\.venv\\Scripts\\Activate.ps1

```



If the virtual environment has not been created, create and activate it:



```powershell

python -m venv .venv

.\\.venv\\Scripts\\Activate.ps1

```



Install project dependencies:



```powershell

python -m pip install -r requirements.txt

```



\## Environment Variables



Create a local `.env` file in the project root and configure the required values:



```text

BACKEND\_URL=http://127.0.0.1:8000

APP\_NAME=VoiceKirana

GNANI\_API\_KEY\_ID=your\_gnani\_api\_key\_id

VOICEKIRANA\_API\_TOKEN=your\_backend\_api\_token

```



Replace the placeholders with your own credentials. Do not publish API keys or commit `.env` to version control.



\## Run the Application



\### 1. Start the FastAPI backend



In the first PowerShell terminal:



```powershell

cd C:\\Users\\DELL\\Documents\\VoiceKirana

.\\.venv\\Scripts\\Activate.ps1

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

```



Check backend health at:



http://127.0.0.1:8000/health



Expected response:



```json

{

&#x20; "status": "ok",

&#x20; "service": "voicekirana-backend"

}

```



\### 2. Start the Streamlit frontend



Open a second PowerShell terminal:



```powershell

cd C:\\Users\\DELL\\Documents\\VoiceKirana

.\\.venv\\Scripts\\Activate.ps1

python -m streamlit run frontend/app.py

```



Open the local URL printed by Streamlit, normally:



http://localhost:8501



Keep both terminals running while using the application.



\## Demo Walkthrough



The current test catalogue includes \*\*Smoke Test Shop (ID 189)\*\* with Rice priced at ₹65 per kg and 20 kg of stock.



\### Test 1: Available product



\- Shop ID: `189`

\- Product: `Rice`

\- Quantity: `2`

\- Unit: `kg`



Expected: Rice is matched, sufficient stock is reported, the estimated total is ₹130, and generated audio can be played.



\### Test 2: Unknown product



\- Shop ID: `189`

\- Product: `Dragon Fruit`

\- Quantity: `1`

\- Unit: `kg`



Expected: The product is reported as unavailable in the catalogue, with no misleading final total.



\### Test 3: Insufficient stock



\- Shop ID: `189`

\- Product: `Rice`

\- Quantity: `25`

\- Unit: `kg`



Expected: The application reports insufficient stock because only 20 kg is available. It must not report the request as successfully fulfilled.



For inventory checks, the application must clearly state that no stock has been reserved and no order has been placed.



\## Automated Tests



The latest recorded test run completed with:



\- \*\*47 tests passed\*\*

\- \*\*96% overall statement coverage\*\*

\- \*\*100% statement coverage\*\* for the inventory service and Timbre TTS service



Two dependency deprecation warnings were reported during that run. These did not cause the tests to fail.



Run the test suite from the project root:



```powershell

python -m pytest

```



To generate a coverage report, if `pytest-cov` is installed:



```powershell

python -m pytest --cov=backend --cov-report=term-missing

```



\## Security Notes



\- Keep `.env` and API credentials out of source control.

\- Use environment variables or your deployment platform's secrets manager.

\- Do not expose the backend API token in screenshots, public logs, or documentation.

\- The current inventory workflow is a preview/check flow, not a production order-placement or stock-reservation system.



\## Current Status



The main demo flow has been validated locally, including inventory success, unknown-product handling, insufficient-stock handling, and generated speech playback.



\## Future Improvements



\- Improve multilingual voice input and recognition.

\- Expand product catalogue and alias coverage.

\- Add further end-to-end tests.

\- Prepare deployment configuration and a recorded demo.

\- Integrate real order placement and stock reservation only when the required workflows are implemented and tested.



>>>>>>> b93a812 (These are the edited files)
