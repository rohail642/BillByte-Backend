# BillByte Backend — FastAPI + Supabase

Full restaurant OS API. FastAPI · SQLAlchemy (async) · PostgreSQL on Supabase · JWT auth.

---

## Project layout

```
billbyte-backend/
├── app/
│   ├── core/
│   │   ├── config.py       env settings (pydantic-settings)
│   │   └── security.py     JWT helpers + password hashing
│   ├── db/
│   │   └── session.py      async SQLAlchemy engine + Base
│   ├── models/
│   │   ├── user.py         User, Restaurant
│   │   ├── menu.py         MenuItem, MenuCategory
│   │   ├── order.py        Order, OrderItem
│   │   ├── inventory.py    InventoryItem, PurchaseOrder
│   │   ├── customer.py     Customer, LoyaltyTransaction
│   │   └── staff.py        Staff
│   ├── schemas/            Pydantic request / response models
│   ├── routers/
│   │   ├── auth.py         POST /register  POST /login  GET /me
│   │   ├── menu.py         CRUD categories + items
│   │   ├── orders.py       create · list · status · pay
│   │   ├── inventory.py    stock + purchase orders
│   │   ├── customers.py    CRM + loyalty points
│   │   ├── staff.py        staff management
│   │   └── reports.py      sales · top dishes · revenue trend
│   └── main.py             app entry point, CORS, lifespan
├── alembic/                database migrations
├── requirements.txt
└── .env.example
```

---

## Step 1 — Supabase (5 min, free)

1. Go to **supabase.com** → New project
2. Name it `billbyte`, pick **Mumbai** region, set a strong DB password
3. Wait ~2 min for it to spin up
4. Go to **Settings → Database → Connection string → URI (asyncpg)**
5. Copy — it looks like:
   ```
   postgresql+asyncpg://postgres:[PASSWORD]@db.xxxxxxxxxxxx.supabase.co:5432/postgres
   ```

---

## Step 2 — Local setup

```bash
cd billbyte-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install packages
pip install -r requirements.txt

# Copy env template
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
DATABASE_URL=postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres
SECRET_KEY=<paste output of: python -c "import secrets; print(secrets.token_hex(32))">
ALLOWED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
```

---

## Step 3 — Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

- **Swagger UI** → http://localhost:8000/docs  ← use this to test every endpoint
- **ReDoc**      → http://localhost:8000/redoc

Tables are auto-created on first startup (dev mode).

---

## Step 4 — Test the API

### Register your restaurant
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Rahul Kumar",
    "email": "rahul@spicegarden.com",
    "password": "securepass123",
    "restaurant_name": "Spice Garden",
    "phone": "+91 98765 43210"
  }'
```
Response gives you `access_token`. Use it as `Bearer <token>` in all further requests.

### Add a menu item
```bash
curl -X POST http://localhost:8000/api/menu/items \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Butter Chicken","price":320,"emoji":"🍗","food_type":"non_veg"}'
```

### Create an order
```bash
curl -X POST http://localhost:8000/api/orders/ \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "order_type": "dine_in",
    "table_number": "4",
    "items": [{"name":"Butter Chicken","price":320,"quantity":2}]
  }'
```

### Collect payment
```bash
curl -X PATCH http://localhost:8000/api/orders/1/pay \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"payment_method":"upi","discount_percent":0}'
```

---

## Full API reference

| Method | Endpoint | What it does |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register restaurant + owner (creates both) |
| POST | `/api/auth/login` | Login, returns JWT token |
| GET | `/api/auth/me` | Current logged-in user |
| GET | `/api/menu/categories` | List categories |
| POST | `/api/menu/categories` | Add category |
| GET | `/api/menu/items` | List menu items (filter by category, active) |
| POST | `/api/menu/items` | Add item |
| PATCH | `/api/menu/items/{id}` | Update item (name, price, active, etc.) |
| DELETE | `/api/menu/items/{id}` | Delete item |
| POST | `/api/orders/` | Create new order |
| GET | `/api/orders/` | List orders (filter by status, type) |
| GET | `/api/orders/summary` | Dashboard KPIs (revenue, count, avg bill) |
| GET | `/api/orders/{id}` | Get single order with items |
| PATCH | `/api/orders/{id}/status` | Update order status (kot_sent, preparing, etc.) |
| PATCH | `/api/orders/{id}/pay` | Collect payment + award loyalty points |
| GET | `/api/inventory/items` | List stock (filter low_stock_only=true) |
| POST | `/api/inventory/items` | Add stock item |
| PATCH | `/api/inventory/items/{id}` | Update stock item |
| POST | `/api/inventory/items/{id}/restock` | Add qty to existing item |
| DELETE | `/api/inventory/items/{id}` | Soft-delete item |
| GET | `/api/inventory/purchase-orders` | List POs |
| POST | `/api/inventory/purchase-orders` | Create PO |
| PATCH | `/api/inventory/purchase-orders/{id}` | Update PO status |
| GET | `/api/customers/` | List customers (search by name/phone) |
| POST | `/api/customers/` | Add customer |
| GET | `/api/customers/{id}` | Customer profile |
| PATCH | `/api/customers/{id}` | Update customer |
| POST | `/api/customers/{id}/redeem` | Redeem loyalty points |
| GET | `/api/staff/` | List staff |
| POST | `/api/staff/` | Add staff member |
| PATCH | `/api/staff/{id}` | Update staff (status, salary, etc.) |
| DELETE | `/api/staff/{id}` | Soft-delete staff |
| GET | `/api/reports/sales` | Sales report (today/week/month) |
| GET | `/api/reports/top-dishes` | Top selling items by period |
| GET | `/api/reports/revenue-trend` | Daily revenue for chart (last N days) |

---

## Step 5 — Deploy to Render (free tier)

1. Push this folder to a GitHub repo
2. **render.com** → New Web Service → connect your repo
3. Settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables (copy from your `.env`)
5. Deploy → you get `https://billbyte-api.onrender.com`

---

## Step 6 — Connect to your frontend

In `billbyte-app-v2.html`, add this at the top of your `<script>` block:

```javascript
const API_URL = "http://localhost:8000/api"; // change to Render URL in production
let authToken = localStorage.getItem("bb_token") || "";

// Helper — authenticated fetch
async function apiFetch(path, options = {}) {
  const res = await fetch(API_URL + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${authToken}`,
      ...options.headers,
    },
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// Replace dummy DB calls — examples:
async function loadMenuFromAPI() {
  const items = await apiFetch("/menu/items");
  DB.menu = items.map(i => ({
    id: i.id, name: i.name, cat: i.category_id,
    price: i.price, emoji: i.emoji, active: i.is_active,
  }));
  renderMenuGrid();
}

async function loadDashboardKPIs() {
  const summary = await apiFetch("/orders/summary");
  document.querySelector(".kpi-val:nth-child(1)").textContent = "₹" + summary.today_revenue;
  // ... etc
}
```

---

## Migrations (production)

Once you go live, use Alembic instead of auto `create_all`:

```bash
# Generate migration after any model change
alembic revision --autogenerate -m "describe change"

# Apply to Supabase
alembic upgrade head
```
