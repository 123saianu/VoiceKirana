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
- Gnani Prisma speech-to-text integration.
- Gnani Timbre text-to-speech integration.
- Spoken responses for inventory results.
- Integration foundation for multilingual conversational ordering through Gnani AI.

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
| AI and speech | Gnani AI |
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
| POST | `/orders/confirm/gnani?shop_id={id}` | Accept Gnani order payload |
| GET | `/orders/history?shop_id={id}` | Retrieve shop order history |
| GET | `/orders/{order_reference}?shop_id={id}` | Retrieve an individual order |
| POST | `/orders/{order_reference}/cancel?shop_id={id}` | Cancel a pending order |
| POST | `/auth/login` | Shop-owner login |
| GET | `/auth/me` | Retrieve authenticated profile |
| POST | `/admin/shops` | Register a shop |
| POST | `/admin/shops/owners` | Create a shop-owner account |
| POST | `/customers/orders/confirm` | Confirm an authenticated customer's order |
| GET | `/customers/orders` | Retrieve the authenticated customer's orders |
| GET | `/customers/orders/{order_reference}` | Retrieve one of the customer's own orders |

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

1. Customer enters grocery items.
2. Backend validates the request.
3. Backend checks the selected shop's inventory.
4. Frontend displays matching products, availability, and estimated prices.
5. Customer explicitly confirms the order.
6. Backend performs a fresh inventory check.
7. Backend saves the pending order and its items transactionally.
8. Customer can retrieve order history or look up an order.
9. A pending order can be cancelled.

Order confirmation does not reserve stock. Shop acceptance and payment must be handled separately.

## Automated Testing

Run the complete test suite:

```powershell
python -m pytest -q
```

The latest local regression run on **November 7, 2026**, completed with **226 tests passed and 3 warnings**.

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

The workflow has been validated locally using CI-style environment variables, with **226 tests passing**. The GitHub-hosted workflow result is pending its first execution after the workflow is pushed to the repository.

## Current Implementation Status

Implemented and tested:
- Multilingual grocery order data model.
- Inventory checking and product matching.
- FastAPI order management.
- PostgreSQL and SQLite database support.
- Idempotent order confirmation.
- Concurrent duplicate prevention.
- Order retrieval, history, and cancellation.
- Shop-owner authentication and active shop-membership authorization.
- Customer authentication and customer-scoped order access.
- Cross-shop and customer order-data isolation.
- Managed database connection lifecycle and transaction rollback.
- Database schema constraints and foreign-key indexes.
- Gnani Prisma speech-to-text and Timbre speech-generation integrations.
- Streamlit grocery interface.

Planned enhancements:
- Customer account management.
- Online payment integration.
- Delivery status and location tracking.
- Complete end-to-end Gnani Agent integration.

These planned enhancements are not yet claimed as production-ready features.

## Security Notes

- Keep `.env` outside version control.
- Use strong, unique authentication secrets.
- Never expose API keys or database passwords in logs or screenshots.
- Use a dedicated test database for destructive or failure-injection tests.
- Verify payment signatures server-side when payment processing is integrated.
- Enforce customer and shop authorization before exposing private order data.

## Future Development

VoiceKirana is being developed toward a complete multilingual grocery commerce experience, including customer accounts, secure payments, messaging notifications, and delivery tracking.