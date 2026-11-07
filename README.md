# VoiceKirana — Multilingual AI Grocery Ordering Assistant

VoiceKirana is a multilingual grocery ordering assistant designed to make grocery shopping easier through natural-language interactions, inventory verification, and order management.

The project combines a Python FastAPI backend, Streamlit frontend, PostgreSQL/SQLite database support, and Gnani AI speech services.

## Features

### Grocery ordering
- Create a cart containing multiple grocery items.
- Support product names, quantities, units, brands, and variants.
- Handle English, Telugu, Hindi, and multilingual grocery requests.
- Validate customer requests before inventory checking.
- Identify unavailable products, ambiguous matches, and insufficient stock.

### Inventory management
- Shop-specific product catalogues.
- Product aliases for alternate and regional names.
- Shop-specific pricing and stock information.
- Inventory verification before order confirmation.

### Order management
- Save pending grocery orders.
- Generate unique order references.
- Retrieve individual orders and order history.
- Cancel pending orders.
- Idempotent order confirmation using an optional `Idempotency-Key`.
- Prevent duplicate orders from simultaneous requests.
- Preserve order records when transactions fail.

**Important:** Saving an order does not reserve inventory or guarantee shop acceptance. Payment and delivery are separate workflows.

### Voice and AI
- Browser microphone input through the Streamlit frontend.
- Gnani Prisma v3 speech-to-text with automatic language identification (Auto-LID) for supported Indic languages and English.
- Google Gemini 3.5 Flash-Lite structured extraction of grocery requests.
- Extraction preserves original product names, optional normalized names, quantities, units, brands, variants, and additional instructions.
- The existing Pydantic GroceryOrder parser validates extracted data and flags missing or ambiguous details for clarification.
- Extracted items populate an editable cart for customer review before inventory validation.
- Gnani Timbre text-to-speech generates spoken inventory responses.
- Voice extraction does not check inventory or place orders. Inventory validation and explicit customer confirmation remain separate steps.

### Authentication and security
- API-key authentication.
- Shop-owner registration and login.
- Argon2 password hashing.
- JWT-based access tokens.
- Shop membership and role-based authorization.
- Customer authentication and order ownership checks.
- Separate administrator authentication.

### Database
- SQLite support for local development.
- PostgreSQL support for deployment.
- Managed database connection lifecycle.
- Database transactions and rollback.
- Foreign-key and check constraints.
- PostgreSQL idempotency and concurrency protection.

## Technology Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI |
| Frontend | Streamlit |
| Database | PostgreSQL, SQLite |
| Database driver | Psycopg |
| Validation | Pydantic |
| Authentication | JWT, Argon2 |
| Speech services | Gnani Prisma v3, Gnani Timbre |
| AI extraction | Google Gemini 3.5 Flash-Lite (`google-genai`) |
| HTTP requests | Requests |
| Testing | Pytest |
| Configuration | Python-dotenv |
| CI | GitHub Actions |

## Project Structure

```text
VoiceKirana/
|-- .github/
|   `-- workflows/
|       `-- tests.yml
|-- backend/
|   |-- main.py
|   |-- database.py
|   |-- auth_dependencies.py
|   |-- security.py
|   |-- models/
|   `-- services/
|-- frontend/
|   `-- app.py
|-- tests/
|-- data/
|-- logs/
|-- requirements.txt
|-- .env.example
|-- .gitignore
`-- README.md
```

## Prerequisites

- Python 3.11 or later.
- Git.
- Gnani AI credentials for speech services.
- A Google AI Studio Gemini API key for structured grocery extraction.
- A configured VoiceKirana API token.
- PostgreSQL connection details when using PostgreSQL.

## Local Installation

Open PowerShell in the project directory:

```powershell
cd C:\Users\DELL\Documents\VoiceKirana
```

Activate the virtual environment:

```powershell
.\venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Environment Configuration

Create a local `.env` file using `.env.example` as the reference.

Configure these variables:

```env
BACKEND_URL=http://localhost:8001
APP_NAME=VoiceKirana

GNANI_API_KEY_ID=your-gnani-key-id
GEMINI_API_KEY=your-gemini-api-key
VOICEKIRANA_API_TOKEN=your-backend-api-token

DATABASE_URL=

JWT_SECRET_KEY=your-generated-secret-at-least-32-characters
VOICEKIRANA_ADMIN_TOKEN=your-admin-token

RAZORPAY_MODE=test
RAZORPAY_KEY_ID=your-razorpay-key-id
RAZORPAY_KEY_SECRET=your-razorpay-key-secret
RAZORPAY_WEBHOOK_SECRET=your-razorpay-webhook-secret
```

When `DATABASE_URL` is empty, the backend uses its configured SQLite database.

When `DATABASE_URL` contains a PostgreSQL connection URL, the backend connects to PostgreSQL.

The backend uses `DATABASE_URL` to select its active database. PostgreSQL integration and verification scripts must explicitly select their intended test database connection.

Never commit `.env`, database passwords, or API credentials to GitHub.

## Running VoiceKirana

### 1. Start the FastAPI backend

Open the first PowerShell terminal:

```powershell
cd C:\Users\DELL\Documents\VoiceKirana
.\venv\Scripts\Activate.ps1

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

Verify backend health:

```text
http://127.0.0.1:8001/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "voicekirana-backend"
}
```

### 2. Start the Streamlit frontend

Open a second PowerShell terminal:

```powershell
cd C:\Users\DELL\Documents\VoiceKirana
.\venv\Scripts\Activate.ps1

python -m streamlit run frontend/app.py
```

Open the local Streamlit URL shown in the terminal, normally:

```text
http://localhost:8501
```

Keep both services running while using the application.

## Main API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Backend health |
| POST | `/orders/preview` | Validate an order request |
| POST | `/inventory/check?shop_id={id}` | Check shop inventory |
| POST | `/orders/confirm?shop_id={id}` | Save a pending order |
| POST | `/orders/{order_reference}/accept?shop_id={id}` | Accept an order and reserve inventory |
| POST | `/orders/{order_reference}/reject?shop_id={id}` | Reject a pending order with a reason |
| GET | `/orders/history?shop_id={id}` | Retrieve shop order history |
| GET | `/orders/{order_reference}?shop_id={id}` | Retrieve an individual order |
| POST | `/orders/{order_reference}/cancel?shop_id={id}` | Cancel a pending order |
| POST | `/auth/login` | Shop-owner login |
| GET | `/auth/me` | Retrieve authenticated profile |
| POST | `/admin/shops` | Register a shop |
| POST | `/admin/shops/owners` | Create a shop-owner account |
| POST | `/customers/register` | Register a customer account |
| POST | `/customers/login` | Customer login |
| GET | `/customers/me` | Retrieve the authenticated customer's profile |
| POST | `/customers/orders/confirm` | Confirm an authenticated customer's order |
| GET | `/customers/orders` | Retrieve the authenticated customer's orders |
| GET | `/customers/orders/{order_reference}` | Retrieve one of the customer's own orders |
| POST | `/customers/orders/{order_reference}/payments` | Create a Razorpay payment attempt |
| POST | `/customers/orders/{order_reference}/payments/verify` | Verify a Razorpay payment |
| POST | `/webhooks/razorpay` | Process verified Razorpay webhook events |

Protected API routes require the appropriate authentication mechanism.

- Shop order-history, retrieval, and cancellation endpoints require a valid shop-member JWT and active membership for the requested shop.
- Customer order endpoints require customer authentication and restrict access to that customer's orders.
- Internal service endpoints use the configured server-to-server API token.
- Administrator operations require administrator authentication.

The shared internal API token must remain server-side and must not be embedded in browser-side code.

Order confirmation supports an optional `Idempotency-Key` header to prevent duplicate order creation during retries.

Interactive API documentation is available at:

```text
http://127.0.0.1:8001/docs
```

## Order Workflow

1. Customer submits a structured grocery request.
2. Backend validates the order and checks the selected shop's inventory.
3. Frontend displays matched products, availability, and estimated prices.
4. Customer explicitly confirms the order; the backend saves it as pending.
5. An authorized shop member accepts or rejects the pending order. Acceptance reserves inventory transactionally; rejection records a reason.
6. The customer can retrieve order history or look up an order.
7. Eligible orders can be cancelled; applicable inventory reservations are released.
8. For an accepted order, the authenticated customer can create a Razorpay payment attempt and verify the checkout response.
9. Signed Razorpay webhooks update payment and refund lifecycle records.

Saving a pending order does not reserve inventory. Reservation occurs when the shop accepts the order. Online payment is handled separately from order acceptance.

## Automated Testing

Run the complete test suite:

```powershell
python -m pytest -q
```

The local regression suite includes unit, API, and workflow tests. Live Prisma and Gemini API checks are performed separately because they require external credentials and network access.

Additional PostgreSQL runtime verification has passed for:

- Order creation and persistence.
- Idempotent retries.
- Same-key requests with different order data.
- Concurrent duplicate-request prevention.
- Transaction rollback.
- Order history and individual order retrieval.
- Order cancellation and cancellation edge cases.
- Cross-shop data isolation.
- API authentication.
- Database schema and referential integrity.

The automated test suite and the separate PostgreSQL runtime checks are complementary verification results.

## Continuous Integration

VoiceKirana includes a GitHub Actions workflow at `.github/workflows/tests.yml`.

The workflow runs on pushes to `main`, pull requests targeting `main`, and manual execution.

It uses Ubuntu and Python 3.12, installs dependencies, compiles the backend and tests, and runs the complete Pytest suite.

CI uses dummy test-only environment values. Production credentials must never be used in automated test configuration.

The local full-suite result is reported separately from GitHub-hosted Actions. Check the repository's Actions tab for the status of the latest hosted workflow run.

## Current Implementation Status

Implemented and tested:
- Multilingual grocery order data model.
- Inventory checking and product matching.
- FastAPI order management.
- PostgreSQL and SQLite database support.
- Idempotent order confirmation.
- Concurrent duplicate prevention.
- Order retrieval, history, and cancellation.
- Shop acceptance and rejection with transactional inventory reservation and release.
- Customer-owned Razorpay payment creation and server-side payment verification.
- Signed, idempotent Razorpay payment and refund webhook processing.
- Shop-owner authentication and active shop-membership authorization.
- Customer authentication and customer-scoped order access.
- Cross-shop and customer order-data isolation.
- Managed database connection lifecycle and transaction rollback.
- Database schema constraints and foreign-key indexes.
- Gnani Prisma v3 Auto-LID and speech-to-text integration.
- Gemini 3.5 Flash-Lite structured grocery extraction.
- Streamlit microphone-to-transcript-to-editable-cart workflow with clarification handling.
- Gnani Timbre speech-generation integration.
- Streamlit grocery interface.

Planned enhancements:
- Customer-facing account-management interface.
- Customer-facing Razorpay checkout and payment experience.
- Delivery status and location tracking.
- Voice-driven order modification, cancellation, and grocery questions beyond the current new-order extraction workflow.

These planned enhancements are not yet claimed as production-ready features.

## Security Notes

- Keep `.env` outside version control.
- Use strong, unique authentication secrets.
- Never expose API keys or database passwords in logs or screenshots.
- Use a dedicated test database for destructive or failure-injection tests.
- Verify Razorpay checkout signatures and webhook signatures server-side.
- Enforce customer and shop authorization before exposing private order data.

## Future Development

VoiceKirana is being developed toward a complete multilingual grocery commerce experience, including customer-facing account and checkout interfaces, messaging notifications, and delivery tracking.