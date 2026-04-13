"""
BillByte Seed Script
Run this to populate your menu with sample Indian restaurant data.

Usage:
  python seed.py --email your@email.com --password yourpassword
"""

import asyncio
import argparse
import httpx

BASE_URL = "http://localhost:8000/api"

CATEGORIES = [
    "Starters",
    "Main Course",
    "Rice & Biryani",
    "Breads",
    "Desserts",
    "Beverages",
]

MENU_ITEMS = [
    # Starters
    { "name": "Paneer Tikka",       "category": "Starters",       "price": 280, "emoji": "🧀", "food_type": "veg"     },
    { "name": "Chicken Tikka",      "category": "Starters",       "price": 320, "emoji": "🍖", "food_type": "non_veg" },
    { "name": "Onion Bhaji",        "category": "Starters",       "price": 120, "emoji": "🧅", "food_type": "veg"     },
    { "name": "Seekh Kebab",        "category": "Starters",       "price": 280, "emoji": "🥙", "food_type": "non_veg" },
    { "name": "Hara Bhara Kabab",   "category": "Starters",       "price": 200, "emoji": "🥗", "food_type": "veg"     },
    { "name": "Tandoori Chicken",   "category": "Starters",       "price": 380, "emoji": "🍗", "food_type": "non_veg" },

    # Main Course
    { "name": "Butter Chicken",     "category": "Main Course",    "price": 320, "emoji": "🍗", "food_type": "non_veg" },
    { "name": "Dal Makhani",        "category": "Main Course",    "price": 220, "emoji": "🫘", "food_type": "veg"     },
    { "name": "Palak Paneer",       "category": "Main Course",    "price": 240, "emoji": "🥗", "food_type": "veg"     },
    { "name": "Mutton Rogan Josh",  "category": "Main Course",    "price": 420, "emoji": "🥘", "food_type": "non_veg" },
    { "name": "Chicken Kadai",      "category": "Main Course",    "price": 300, "emoji": "🍛", "food_type": "non_veg" },
    { "name": "Shahi Paneer",       "category": "Main Course",    "price": 260, "emoji": "🧀", "food_type": "veg"     },
    { "name": "Chicken Korma",      "category": "Main Course",    "price": 340, "emoji": "🥘", "food_type": "non_veg" },
    { "name": "Aloo Gobi",          "category": "Main Course",    "price": 180, "emoji": "🥦", "food_type": "veg"     },
    { "name": "Mutton Keema",       "category": "Main Course",    "price": 380, "emoji": "🍲", "food_type": "non_veg" },
    { "name": "Chana Masala",       "category": "Main Course",    "price": 200, "emoji": "🫘", "food_type": "veg"     },

    # Rice & Biryani
    { "name": "Veg Biryani",        "category": "Rice & Biryani", "price": 260, "emoji": "🍚", "food_type": "veg"     },
    { "name": "Chicken Biryani",    "category": "Rice & Biryani", "price": 320, "emoji": "🍛", "food_type": "non_veg" },
    { "name": "Mutton Biryani",     "category": "Rice & Biryani", "price": 400, "emoji": "🍛", "food_type": "non_veg" },
    { "name": "Egg Biryani",        "category": "Rice & Biryani", "price": 280, "emoji": "🍳", "food_type": "non_veg" },
    { "name": "Jeera Rice",         "category": "Rice & Biryani", "price": 150, "emoji": "🍚", "food_type": "veg"     },

    # Breads
    { "name": "Butter Naan",        "category": "Breads",         "price": 50,  "emoji": "🫓", "food_type": "veg"     },
    { "name": "Garlic Naan",        "category": "Breads",         "price": 70,  "emoji": "🫓", "food_type": "veg"     },
    { "name": "Cheese Naan",        "category": "Breads",         "price": 90,  "emoji": "🧀", "food_type": "veg"     },
    { "name": "Tandoori Roti",      "category": "Breads",         "price": 35,  "emoji": "🫓", "food_type": "veg"     },
    { "name": "Paratha",            "category": "Breads",         "price": 60,  "emoji": "🫓", "food_type": "veg"     },

    # Desserts
    { "name": "Gulab Jamun",        "category": "Desserts",       "price": 80,  "emoji": "🍮", "food_type": "veg"     },
    { "name": "Kulfi",              "category": "Desserts",       "price": 120, "emoji": "🍦", "food_type": "veg"     },
    { "name": "Rasgulla",           "category": "Desserts",       "price": 80,  "emoji": "🍡", "food_type": "veg"     },
    { "name": "Kheer",              "category": "Desserts",       "price": 100, "emoji": "🍚", "food_type": "veg"     },
    { "name": "Gajar Halwa",        "category": "Desserts",       "price": 120, "emoji": "🥕", "food_type": "veg"     },

    # Beverages
    { "name": "Masala Chai",        "category": "Beverages",      "price": 40,  "emoji": "☕", "food_type": "veg"     },
    { "name": "Mango Lassi",        "category": "Beverages",      "price": 90,  "emoji": "🥭", "food_type": "veg"     },
    { "name": "Sweet Lassi",        "category": "Beverages",      "price": 70,  "emoji": "🥛", "food_type": "veg"     },
    { "name": "Nimbu Pani",         "category": "Beverages",      "price": 50,  "emoji": "🍋", "food_type": "veg"     },
    { "name": "Cold Coffee",        "category": "Beverages",      "price": 120, "emoji": "☕", "food_type": "veg"     },
    { "name": "Fresh Lime Soda",    "category": "Beverages",      "price": 60,  "emoji": "🍋", "food_type": "veg"     },
]

INVENTORY_ITEMS = [
    { "name": "Basmati Rice",     "quantity": 15,  "unit": "kg",  "min_quantity": 5,   "cost_per_unit": 65  },
    { "name": "Chicken (Fresh)",  "quantity": 8,   "unit": "kg",  "min_quantity": 5,   "cost_per_unit": 280 },
    { "name": "Mutton",           "quantity": 4,   "unit": "kg",  "min_quantity": 3,   "cost_per_unit": 650 },
    { "name": "Paneer",           "quantity": 3,   "unit": "kg",  "min_quantity": 2,   "cost_per_unit": 320 },
    { "name": "Cooking Oil",      "quantity": 10,  "unit": "L",   "min_quantity": 4,   "cost_per_unit": 140 },
    { "name": "Onions",           "quantity": 20,  "unit": "kg",  "min_quantity": 8,   "cost_per_unit": 30  },
    { "name": "Tomatoes",         "quantity": 8,   "unit": "kg",  "min_quantity": 4,   "cost_per_unit": 50  },
    { "name": "Garam Masala",     "quantity": 1,   "unit": "kg",  "min_quantity": 0.3, "cost_per_unit": 600 },
    { "name": "Butter",           "quantity": 4,   "unit": "kg",  "min_quantity": 2,   "cost_per_unit": 480 },
    { "name": "Cream",            "quantity": 3,   "unit": "L",   "min_quantity": 1,   "cost_per_unit": 360 },
    { "name": "Atta (Wheat)",     "quantity": 20,  "unit": "kg",  "min_quantity": 6,   "cost_per_unit": 42  },
    { "name": "Turmeric Powder",  "quantity": 0.5, "unit": "kg",  "min_quantity": 0.2, "cost_per_unit": 180 },
    { "name": "Cumin Seeds",      "quantity": 0.5, "unit": "kg",  "min_quantity": 0.2, "cost_per_unit": 220 },
    { "name": "Coriander Powder", "quantity": 0.8, "unit": "kg",  "min_quantity": 0.3, "cost_per_unit": 150 },
    { "name": "Milk",             "quantity": 10,  "unit": "L",   "min_quantity": 5,   "cost_per_unit": 60  },
    { "name": "Eggs",             "quantity": 30,  "unit": "pcs", "min_quantity": 12,  "cost_per_unit": 8   },
    { "name": "Garlic",           "quantity": 2,   "unit": "kg",  "min_quantity": 1,   "cost_per_unit": 120 },
    { "name": "Ginger",           "quantity": 1.5, "unit": "kg",  "min_quantity": 0.5, "cost_per_unit": 100 },
    { "name": "Green Chillies",   "quantity": 1,   "unit": "kg",  "min_quantity": 0.5, "cost_per_unit": 80  },
    { "name": "Mango Pulp",       "quantity": 5,   "unit": "L",   "min_quantity": 2,   "cost_per_unit": 90  },
]

STAFF = [
    { "name": "Anil Kumar",   "role": "Head Chef",  "phone": "+91 98765 00001", "salary": 35000 },
    { "name": "Rekha Devi",   "role": "Sous Chef",  "phone": "+91 98765 00002", "salary": 28000 },
    { "name": "Mohan Lal",    "role": "Waiter",     "phone": "+91 98765 00003", "salary": 18000 },
    { "name": "Priya S.",     "role": "Cashier",    "phone": "+91 98765 00004", "salary": 20000 },
    { "name": "Ravi B.",      "role": "Delivery",   "phone": "+91 98765 00005", "salary": 15000 },
    { "name": "Kavita M.",    "role": "Cleaner",    "phone": "+91 98765 00006", "salary": 12000 },
    { "name": "Suresh P.",    "role": "Waiter",     "phone": "+91 98765 00007", "salary": 18000 },
    { "name": "Deepak R.",    "role": "Waiter",     "phone": "+91 98765 00008", "salary": 18000 },
]

CUSTOMERS = [
    { "name": "Priya Sharma", "phone": "+91 98765 11001" },
    { "name": "Arjun Mehta",  "phone": "+91 98765 11002" },
    { "name": "Deepa Nair",   "phone": "+91 98765 11003" },
    { "name": "Raj Patel",    "phone": "+91 98765 11004" },
    { "name": "Sunita Rao",   "phone": "+91 98765 11005" },
]


async def seed(email: str, password: str):
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:

        # 1. Login
        print("\n🔐 Logging in...")
        res = await client.post("/auth/login", json={"email": email, "password": password})
        if res.status_code != 200:
            print(f"❌ Login failed: {res.text}")
            return
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"✅ Logged in successfully")

        # 2. Seed Categories
        print("\n📂 Creating categories...")
        cat_map = {}
        for cat_name in CATEGORIES:
            res = await client.post("/menu/categories", json={"name": cat_name}, headers=headers)
            if res.status_code == 201:
                cat = res.json()
                cat_map[cat_name] = cat["id"]
                print(f"  ✅ {cat_name}")
            else:
                print(f"  ⚠️  {cat_name} — {res.text}")

        # 3. Seed Menu Items
        print(f"\n🍽️  Adding {len(MENU_ITEMS)} menu items...")
        success = 0
        for item in MENU_ITEMS:
            cat_id = cat_map.get(item["category"])
            payload = {
                "name":        item["name"],
                "price":       item["price"],
                "emoji":       item["emoji"],
                "food_type":   item["food_type"],
                "category_id": cat_id,
                "is_active":   True,
            }
            res = await client.post("/menu/items", json=payload, headers=headers)
            if res.status_code == 201:
                success += 1
                print(f"  ✅ {item['emoji']} {item['name']} — ₹{item['price']}")
            else:
                print(f"  ❌ {item['name']} — {res.text}")
        print(f"\n  {success}/{len(MENU_ITEMS)} menu items added")

        # 4. Seed Inventory
        print(f"\n📦 Adding {len(INVENTORY_ITEMS)} inventory items...")
        success = 0
        for item in INVENTORY_ITEMS:
            res = await client.post("/inventory/items", json=item, headers=headers)
            if res.status_code == 201:
                success += 1
                print(f"  ✅ {item['name']}")
            else:
                print(f"  ❌ {item['name']} — {res.text}")
        print(f"\n  {success}/{len(INVENTORY_ITEMS)} inventory items added")

        # 5. Seed Staff
        print(f"\n🧑‍🍳 Adding {len(STAFF)} staff members...")
        success = 0
        for member in STAFF:
            res = await client.post("/staff/", json=member, headers=headers)
            if res.status_code == 201:
                success += 1
                print(f"  ✅ {member['name']} — {member['role']}")
            else:
                print(f"  ❌ {member['name']} — {res.text}")
        print(f"\n  {success}/{len(STAFF)} staff added")

        # 6. Seed Customers
        print(f"\n👥 Adding {len(CUSTOMERS)} customers...")
        success = 0
        for cust in CUSTOMERS:
            res = await client.post("/customers/", json=cust, headers=headers)
            if res.status_code == 201:
                success += 1
                print(f"  ✅ {cust['name']}")
            else:
                print(f"  ❌ {cust['name']} — {res.text}")
        print(f"\n  {success}/{len(CUSTOMERS)} customers added")

        print("\n🎉 Seeding complete! Refresh your app to see the data.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed BillByte with sample data")
    parser.add_argument("--email",    required=True, help="Your login email")
    parser.add_argument("--password", required=True, help="Your login password")
    args = parser.parse_args()

    asyncio.run(seed(args.email, args.password))
